# Task: nuyina-underway updater, as-is plus fixes

Status: draft, 2026-11-03. Scope: this repo, the v0.0.1 release as the store,
the existing GitHub Actions. No new hosting.

## Found

- `Update Underway Data` (update.yaml) was `disabled_inactivity`: GitHub turns
  a cron off after 60 days without pushes. Last scheduled run 2026-10-03;
  the asset sat at 2026-10-03 until the manual Simple run on 2026-11-03.
  `Detect Voyages` is disabled the same way. Commits do not re-enable a
  disabled workflow; only the Actions page or `gh workflow enable` does.
- Asset after the 2026-11-03 full refetch: 1,671,685 rows, 1-minute cadence,
  2020-04-11 to 2026-10-19, 108 columns, 177 MB, 26 row groups of 65,536,
  one geometry column `geom` with GeoParquet metadata. Row groups are not
  time-aligned (row group 0 spans 2020-04 to 2022-02), so
  `WHERE datetime BETWEEN` cannot prune.
- The incremental path (ogrmerge of old + new) is what produced the duplicate
  `geom` / `geometry` columns seen in the 10-03 asset; a full fetch is clean.
- Each incremental cycle downloads and re-uploads the whole asset to append a
  few thousand rows.
- `scripts/detect_voyages.py` pointed at the old `mdsumner/uwy.new` release
  URL and looped 1.6 M rows in Python. Rewritten 2026-11-03 as DuckDB SQL
  (window functions), 2.4 s on the full record, same port list and rules;
  writes `voyages.parquet` (one row per stop, voyage columns repeated) and the
  nested `voyages_draft.json` as before. Result on the current asset: 107
  stops, 24 voyages. Differences from the Feb draft are data (new months, a
  gap filled between two Mawson stops), not logic.

## Store layout on the release (yearly partitions)

```
v0.0.1/
  nuyina_underway_2020.parquet   full width, sorted by datetime, one geometry,
  nuyina_underway_2021.parquet   row group = 1 month (ROW_GROUP_SIZE=43200)
  ...
  nuyina_underway_2026.parquet   the only file a routine update rewrites
  nuyina_track.parquet           all years: datetime, longitude, latitude,
                                 platform_speed_wrt_ground, platform_course_true,
                                 platform_heading_true; ~10 MB
  nuyina_track_hourly.json       what nuyina.map serves today
  voyages.parquet                detect_voyages output
  voyages_draft.json             same, nested, for the proximal tracker
  manifest.json                  the per-year URLs, min/max datetime and row
                                 count per file, so a reader has a listing
  latest.json                    {"datetime": max, "rows": n, "updated": now}
                                 written last; the high-water mark
  nuyina_underway.parquet        monolith, kept for a transition period and
                                 rebuilt weekly, not 6-hourly
```

Why yearly on Releases rather than object storage: the writer gets the whole
benefit (a 6-hourly update touches one ~25 MB file) with no new credentials or
hosting. The reader-side cost is that Releases has no listing, hence
`manifest.json`. DuckDB reads a list of URLs directly; R arrow reads one URL
per call, so R readers either loop the manifest or go through duckdb. Most
readers want `nuyina_track.parquet`, which stays one file.

A straddling update (new rows in December and January) touches two year
files; the merge step must split new records by year, not assume one.

## Fixes, in order

1. Re-enable update.yaml and detect-voyages.yaml (manual, once).
2. Keep the cron alive: a final step in update.yaml commits `.last-run` when
   the merge succeeded (or a keepalive action). The repo then never goes 60
   days without a push.
3. Replace ogrmerge + ogrinfo greps in the merge step with a small Python
   step (`pip install duckdb pyarrow`): read the touched year file(s) and
   `new_records.parquet`, union by name, drop any second geometry column,
   dedupe on `datetime`, sort, write with month row groups and the GeoParquet
   `geo` metadata carried over. GDAL stays for the WFS fetch only
   (`ogr2ogr -where "datetime > <max>"`), which is the part it is good at.
4. `<max>` comes from `latest.json`, not from an ogrinfo pass over the
   monolith; fall back to a full fetch when it is absent.
5. One-off: build the year files from the current clean monolith (same
   Python step with no new records), upload them plus `manifest.json`,
   `latest.json`, `nuyina_track.parquet`, `nuyina_track_hourly.json`.
6. Fold voyage detection into update.yaml as a final step reading the local
   merged file, so detect-voyages.yaml and its separate cron go away. Upload
   `voyages.parquet` and `voyages_draft.json` with the rest.
7. README: document the layout above, the `latest.json` then WFS pattern
   already shown, a DuckDB manifest example, and the fact that the monolith
   is weekly.

## Checks

- Year files: row counts sum to the monolith's count; `datetime` unique and
  sorted within each file; `ogrinfo -al -so` shows one geometry column with
  CRS; `parquet_metadata()` shows monotone datetime min/max per row group.
- `voyages.parquet` stop count equals the nested JSON's; the last voyage's
  end equals `latest.json` datetime when the ship is at sea.
- A scheduled run after an idle fortnight still fires.

## Later (not this task)

- The same layout on object storage, where the manifest becomes a listing.
- envdata extraction reads `nuyina_track.parquet`.
