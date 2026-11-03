#!/usr/bin/env python3
"""
Build and update the yearly-partitioned underway store on the v0.0.1 release.

See docs/tasks/updater-improvements.md for the layout. Subcommands:

  keys NEW.parquet
      Print the partition keys (years, or "suspect") present in NEW, one per
      line, so the workflow knows which year files to download.

  merge --out DIR [--base F ...] [--new F] [--manifest F] [--track F]
      Union base partition files (and/or a monolith) with new WFS records,
      drop a second geometry column, dedupe on datetime (new wins), sort,
      and write one file per partition touched, with one row group per month
      and GeoParquet metadata carried over. Also writes nuyina_track.parquet,
      nuyina_track_hourly.json, manifest.json and latest.json into DIR.
      With --base MONOLITH --all this is the one-off bootstrap.

  monolith --out FILE F ...
      Concatenate partition files into the single nuyina_underway.parquet.

Needs pyarrow and duckdb only (no GDAL).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

RELEASE_BASE = os.environ.get(
    "RELEASE_BASE",
    "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1",
)
# Upstream carries a handful of records with impossible timestamps (years 24,
# 25, 2006, a lone 2020-04-11); Nuyina's underway record starts 2021-12. They
# are kept, but in one side file, not as year files, and not in the track.
FIRST_YEAR = 2021
SUSPECT = "suspect"
GEOM = "geom"
TRACK_COLS = [
    "gml_id", "datetime", "longitude", "latitude",
    "platform_speed_wrt_ground", "platform_course_true", "platform_heading_true",
]


def part_name(key):
    return f"nuyina_underway_{key}.parquet"


def utc_iso(ts):
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def partition_keys(table):
    years = pc.year(table["datetime"]).to_pylist()
    return [str(y) if y >= FIRST_YEAR else SUSPECT for y in years]


def read_clean(path):
    """Read a file, keep one geometry column, return (table, geo dict)."""
    t = pq.read_table(path)
    geo = json.loads((t.schema.metadata or {}).get(b"geo", b"{}") or b"{}")
    names = t.column_names
    if GEOM not in names and "geometry" in names:
        t = t.rename_columns([GEOM if n == "geometry" else
                              f"{GEOM}_bbox" if n == "geometry_bbox" else n for n in names])
        names = t.column_names
    extra = [n for n in ("geometry", "geometry_bbox") if n in names]
    if extra:
        print(f"  {os.path.basename(path)}: dropping duplicate geometry {extra}")
        t = t.drop_columns(extra)
    return t.replace_schema_metadata(None), geo


def unify(tables):
    """Concatenate, casting each table to the first one's types where shared."""
    ref = tables[0].schema
    out = []
    for t in tables:
        cols = []
        for f in t.schema:
            i = ref.get_field_index(f.name)
            col = t[f.name]
            if i >= 0 and ref.field(i).type != f.type:
                col = col.cast(ref.field(i).type)
            cols.append(col)
        out.append(pa.table(cols, names=t.column_names))
    return pa.concat_tables(out, promote_options="permissive")


def dedupe_sort(t, prio):
    """Sort by datetime; where datetime repeats keep the highest-priority row."""
    t = t.append_column("__prio", pa.array(prio, pa.int32()))
    t = t.filter(pc.is_valid(t["datetime"]))
    t = t.sort_by([("datetime", "ascending"), ("__prio", "descending")])
    dt = t["datetime"].combine_chunks()
    if len(t) > 1:
        same = pc.equal(dt.slice(1), dt.slice(0, len(t) - 1))
        keep = pa.concat_arrays([pa.array([True]), pc.invert(same.fill_null(False))])
        t = t.filter(keep)
    return t.drop_columns(["__prio"])


def geo_metadata(t, template):
    geo = dict(template) if template else {"version": "1.0.0"}
    col = dict((template or {}).get("columns", {}).get(GEOM, {"encoding": "WKB"}))
    bb = f"{GEOM}_bbox"
    if bb in t.column_names and len(t):
        s = t[bb]
        col["bbox"] = [pc.min(pc.struct_field(s, "xmin")).as_py(),
                       pc.min(pc.struct_field(s, "ymin")).as_py(),
                       pc.max(pc.struct_field(s, "xmax")).as_py(),
                       pc.max(pc.struct_field(s, "ymax")).as_py()]
    geo["primary_column"] = GEOM
    geo["columns"] = {GEOM: col}
    return json.dumps(geo).encode()


def write_monthly(t, path, geo):
    """One row group per calendar month (t must be sorted by datetime)."""
    md = {b"geo": geo_metadata(t, geo)} if GEOM in t.column_names else None
    t = t.replace_schema_metadata(md)
    tmp = path + ".tmp"
    with pq.ParquetWriter(tmp, t.schema, compression="zstd") as w:
        if len(t) == 0:
            w.write_table(t)
        ym = pc.add(pc.multiply(pc.year(t["datetime"]), 100), pc.month(t["datetime"]))
        ym = ym.to_numpy() if hasattr(ym, "to_numpy") else ym.combine_chunks().to_numpy()
        start = 0
        for i in range(1, len(t) + 1):
            if i == len(t) or ym[i] != ym[start]:
                w.write_table(t.slice(start, i - start), row_group_size=i - start)
                start = i
    os.replace(tmp, path)


def file_entry(path, t):
    return {
        "url": f"{RELEASE_BASE}/{os.path.basename(path)}",
        "rows": len(t),
        "min_datetime": utc_iso(pc.min(t["datetime"]).as_py()) if len(t) else None,
        "max_datetime": utc_iso(pc.max(t["datetime"]).as_py()) if len(t) else None,
        "bytes": os.path.getsize(path),
    }


def write_hourly_json(track, path):
    """Every 10th minute, same shape seaice.map's update_vessel.R produced."""
    con = duckdb.connect()
    con.sql("SET TimeZone = 'UTC'")
    con.register("track", track)
    rows = con.sql("""
      SELECT strftime(datetime, '%Y-%m-%d %H:%M:%S') AS datetime, longitude, latitude
      FROM track WHERE minute(datetime) % 10 = 0 AND second(datetime) = 0
      ORDER BY track.datetime
    """).fetchall()
    with open(path, "w") as f:
        json.dump([{"datetime": d, "longitude": x, "latitude": y} for d, x, y in rows], f,
                  separators=(",", ":"))
    return len(rows)


def cmd_keys(a):
    t = pq.read_table(a.new, columns=["datetime"])
    for k in sorted(set(partition_keys(t))):
        print(k)


def cmd_merge(a):
    os.makedirs(a.out, exist_ok=True)
    tables, prio, geo = [], [], None
    for i, f in enumerate((a.base or []) + ([a.new] if a.new else [])):
        t, g = read_clean(f)
        print(f"read {f}: {len(t)} rows")
        geo = geo or (g if g else None)
        tables.append(t)
        prio.extend([i] * len(t))
    if not tables:
        sys.exit("nothing to merge")
    t = dedupe_sort(unify(tables), prio)
    print(f"merged: {len(t)} rows")

    manifest = {"files": {}}
    if a.manifest and os.path.exists(a.manifest):
        with open(a.manifest) as f:
            manifest = json.load(f)

    keys = pa.array(partition_keys(t))
    new_keys = set(partition_keys(pq.read_table(a.new, columns=["datetime"]))) if a.new else None
    for k in sorted(set(keys.to_pylist())):
        if not a.all and new_keys is not None and k not in new_keys:
            continue  # base file we only read for context; leave it alone
        part = t.filter(pc.equal(keys, k))
        path = os.path.join(a.out, part_name(k))
        write_monthly(part, path, geo)
        manifest["files"][part_name(k)] = file_entry(path, part)
        print(f"wrote {path}: {len(part)} rows")

    # track: the previous track plus whatever is new, partitioned years only
    good = t.filter(pc.not_equal(keys, SUSPECT))
    cols = [c for c in TRACK_COLS if c in good.column_names]
    trk_parts, trk_prio = [], []
    if a.track and os.path.exists(a.track):
        old = pq.read_table(a.track, columns=cols)
        trk_parts.append(old)
        trk_prio.extend([0] * len(old))
    trk_parts.append(good.select(cols))
    trk_prio.extend([1] * len(good))
    track = dedupe_sort(unify(trk_parts), trk_prio)
    tpath = os.path.join(a.out, "nuyina_track.parquet")
    pq.write_table(track, tpath, compression="zstd", row_group_size=131072)
    print(f"wrote {tpath}: {len(track)} rows")
    n = write_hourly_json(track, os.path.join(a.out, "nuyina_track_hourly.json"))
    print(f"wrote nuyina_track_hourly.json: {n} positions")

    years = {k: v for k, v in manifest["files"].items() if k != part_name(SUSPECT)}
    max_dt = max(v["max_datetime"] for v in years.values() if v["max_datetime"])
    total = sum(v["rows"] for v in manifest["files"].values())
    now = utc_iso(datetime.now(timezone.utc))
    manifest.update({
        "updated": now,
        "rows": total,
        "max_datetime": max_dt,
        "track": file_entry(tpath, track),
        "note": "nuyina_underway_YYYY.parquet are calendar years (UTC); "
                f"{part_name(SUSPECT)} holds upstream records dated before {FIRST_YEAR}.",
    })
    manifest["files"] = dict(sorted(manifest["files"].items()))
    with open(os.path.join(a.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(a.out, "latest.json"), "w") as f:
        json.dump({"datetime": max_dt, "rows": total, "updated": now}, f)
    print(f"latest: {max_dt}, {total} rows")


def cmd_monolith(a):
    tables, geo = [], None
    for f in a.files:
        t, g = read_clean(f)
        geo = geo or (g if g else None)
        tables.append(t)
    t = dedupe_sort(unify(tables), [0] * sum(len(x) for x in tables))
    write_monthly(t, a.out, geo)
    print(f"wrote {a.out}: {len(t)} rows")


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    k = sp.add_parser("keys")
    k.add_argument("new")
    m = sp.add_parser("merge")
    m.add_argument("--out", required=True)
    m.add_argument("--base", nargs="*")
    m.add_argument("--new")
    m.add_argument("--manifest")
    m.add_argument("--track")
    m.add_argument("--all", action="store_true",
                   help="write every partition, not only those NEW touches (bootstrap)")
    o = sp.add_parser("monolith")
    o.add_argument("--out", required=True)
    o.add_argument("files", nargs="+")
    a = p.parse_args()
    {"keys": cmd_keys, "merge": cmd_merge, "monolith": cmd_monolith}[a.cmd](a)


if __name__ == "__main__":
    main()
