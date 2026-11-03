#!/usr/bin/env python3
"""
Detect Nuyina voyages from the underway Parquet.

Pure DuckDB SQL (window functions); no GDAL, no R, no per-row Python.
Runs in seconds on the full 1-minute record.

Usage:
  python3 scripts/detect_voyages.py [PARQUET_PATH_OR_URL] [OUT_DIR]

Writes OUT_DIR/voyages.parquet (one row per port stop, voyage columns
repeated) and OUT_DIR/voyages_draft.json (nested, same shape as before).
"""

import json
import sys
from datetime import datetime, timezone

import duckdb

PARQUET = sys.argv[1] if len(sys.argv) > 1 else \
    "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "."

PORTS = [
    ("Hobart", -42.88, 147.33, 15),
    ("Burnie", -41.05, 145.91, 8),
    ("Macquarie Island", -54.50, 158.94, 40),
    ("Heard Island", -53.10, 73.51, 50),
    ("Casey", -66.28, 110.53, 80),
    ("Davis", -68.58, 77.97, 80),
    ("Mawson", -67.60, 62.87, 80),
]
MIN_DWELL_HOURS = 2


def main():
    con = duckdb.connect()
    if PARQUET.startswith("http"):
        con.sql("INSTALL httpfs; LOAD httpfs;")
    con.sql("CREATE TABLE ports (name VARCHAR, lat DOUBLE, lon DOUBLE, radius_km DOUBLE)")
    con.executemany("INSERT INTO ports VALUES (?, ?, ?, ?)", PORTS)

    con.sql(f"""
    CREATE TABLE pts AS
    SELECT gml_id, datetime, latitude AS lat, longitude AS lon
    FROM read_parquet('{PARQUET}')
    WHERE datetime >= '2020-01-01' AND latitude IS NOT NULL AND longitude IS NOT NULL
    ORDER BY datetime
    """)

    # nearest port within its radius, else NULL (haversine, km)
    con.sql("""
    CREATE TABLE tagged AS
    WITH d AS (
      SELECT p.gml_id, p.datetime, p.lat, p.lon, q.name, q.radius_km,
             2 * 6371 * asin(sqrt(
               pow(sin(radians(q.lat - p.lat) / 2), 2) +
               cos(radians(p.lat)) * cos(radians(q.lat)) * pow(sin(radians(q.lon - p.lon) / 2), 2))) AS km
      FROM pts p CROSS JOIN ports q
    ),
    best AS (
      SELECT *, row_number() OVER (PARTITION BY gml_id ORDER BY km) AS rn FROM d
    )
    SELECT gml_id, datetime, lat, lon,
           CASE WHEN km <= radius_km THEN name END AS port
    FROM best WHERE rn = 1
    """)

    # runs of consecutive records with the same port (NULL = at sea)
    con.sql("""
    CREATE TABLE visits AS
    WITH r AS (
      SELECT *, CASE WHEN port IS DISTINCT FROM lag(port) OVER (ORDER BY datetime) THEN 1 ELSE 0 END AS brk
      FROM tagged
    ),
    runs AS (
      SELECT *, sum(brk) OVER (ORDER BY datetime ROWS UNBOUNDED PRECEDING) AS run_id FROM r
    ),
    v AS (
      SELECT run_id, any_value(port) AS port,
             min(datetime) AS arrive, max(datetime) AS depart,
             arg_min(gml_id, datetime) AS arrive_gml_id, arg_max(gml_id, datetime) AS depart_gml_id,
             count(*) AS n_points
      FROM runs WHERE port IS NOT NULL GROUP BY run_id
    )
    SELECT *, round(date_diff('second', arrive, depart) / 3600.0, 1) AS dwell_hours
    FROM v WHERE date_diff('second', arrive, depart) / 3600.0 >= ?
    ORDER BY arrive
    """, params=[MIN_DWELL_HOURS])

    # a voyage starts on arrival at Hobart after being somewhere else
    con.sql("""
    CREATE TABLE stops AS
    WITH s AS (
      SELECT *, CASE WHEN port = 'Hobart' AND lag(port) OVER (ORDER BY arrive) IS DISTINCT FROM 'Hobart'
                      AND lag(port) OVER (ORDER BY arrive) IS NOT NULL THEN 1 ELSE 0 END AS new_voyage
      FROM visits
    ),
    g AS (
      SELECT *, (1 + sum(new_voyage) OVER (ORDER BY arrive ROWS UNBOUNDED PRECEDING))::INTEGER AS voyage_num FROM s
    ),
    vv AS (
      SELECT voyage_num, min(arrive) AS voyage_start, max(depart) AS voyage_end FROM g GROUP BY voyage_num
    )
    SELECT 'V' || g.voyage_num || ' ' || strftime(vv.voyage_start, '%Y-%m') AS voyage_id,
           g.voyage_num, vv.voyage_start, vv.voyage_end,
           row_number() OVER (PARTITION BY g.voyage_num ORDER BY g.arrive) AS stop_num,
           g.port, g.arrive, g.depart, g.dwell_hours, g.n_points, g.arrive_gml_id, g.depart_gml_id
    FROM g JOIN vv USING (voyage_num)
    ORDER BY g.arrive
    """)

    con.sql(f"COPY stops TO '{OUT_DIR}/voyages.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")

    # timestamps come out as ISO strings so no tz library is needed on the runner
    con.sql("SET TimeZone = 'UTC'")
    q = """
    SELECT voyage_id, voyage_num,
           strftime(voyage_start, '%Y-%m-%dT%H:%M:%SZ') AS voyage_start,
           strftime(voyage_end, '%Y-%m-%dT%H:%M:%SZ') AS voyage_end,
           port, strftime(arrive, '%Y-%m-%dT%H:%M:%SZ') AS arrive,
           strftime(depart, '%Y-%m-%dT%H:%M:%SZ') AS depart,
           dwell_hours, n_points, arrive_gml_id, depart_gml_id
    FROM stops ORDER BY arrive
    """
    rel = con.sql(q)
    cols = rel.columns
    rows = rel.fetchall()
    iso = lambda t: t
    voyages = {}
    for r in rows:
        d = dict(zip(cols, r))
        v = voyages.setdefault(d["voyage_num"], {
            "id": d["voyage_id"], "note": "", "start": iso(d["voyage_start"]), "end": iso(d["voyage_end"]), "stops": []})
        v["stops"].append({k: (iso(d[k]) if k in ("arrive", "depart") else d[k])
                           for k in ("port", "arrive", "depart", "arrive_gml_id", "depart_gml_id", "dwell_hours")})
    out = {
        "_generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_note": "Auto-detected draft - review and edit before publishing",
        "_source": PARQUET,
        "ports": {n: {"lat": a, "lon": o, "radius_km": r} for n, a, o, r in PORTS},
        "voyages": [voyages[k] for k in sorted(voyages)],
    }
    with open(f"{OUT_DIR}/voyages_draft.json", "w") as f:
        json.dump(out, f, indent=2)

    print(f"{len(rows)} stops in {len(voyages)} voyages -> {OUT_DIR}/voyages.parquet, voyages_draft.json")
    for v in out["voyages"]:
        print(f"  {v['id']}: " + ", ".join(s["port"] for s in v["stops"]))


if __name__ == "__main__":
    main()
