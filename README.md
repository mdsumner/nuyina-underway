# nuyina-underway

Nuyina underway data pipeline: fetches from the AAD GeoServer WFS and caches
Parquet on this repo's v0.0.1 release, updated every 6 hours.

## Data Access

Release base: `https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/`

| file | what |
|---|---|
| `nuyina_track.parquet` | all positions, 1-minute: gml_id, datetime, longitude, latitude, speed, course, heading (~40 MB). Most readers want this. |
| `nuyina_underway_YYYY.parquet` | full width (108 columns, GeoParquet `geom`), one file per UTC calendar year, one row group per month. Only the current year is rewritten by routine updates. |
| `nuyina_underway_suspect.parquet` | the few upstream records dated before 2021 (e.g. year 0024), kept aside. |
| `nuyina_underway.parquet` | everything in one file; rebuilt **weekly**, so it lags the year files. |
| `nuyina_track_hourly.json` | positions every 10 minutes, `[{datetime, longitude, latitude}]`. |
| `voyages.parquet`, `voyages_draft.json` | auto-detected port stops and voyages. |
| `manifest.json` | URL, row count, min/max datetime of every file (Releases has no listing). |
| `latest.json` | `{"datetime", "rows", "updated"}`, written last: the store's high-water mark. |

### DuckDB

```sql
SELECT * FROM 'https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_track.parquet'
WHERE datetime > '2024-01-01';

-- full width, the years you need
SELECT * FROM read_parquet([
  'https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway_2025.parquet',
  'https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway_2026.parquet'
]) WHERE datetime > '2025-12-01';
```

### Python, all years via the manifest

```python
import duckdb, json, urllib.request
base = "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1"
m = json.load(urllib.request.urlopen(f"{base}/manifest.json"))
urls = [f["url"] for f in m["files"].values()]
df = duckdb.sql(f"SELECT * FROM read_parquet({urls}, union_by_name = true)").df()
```

### R

```r
base <- "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1"
track <- arrow::read_parquet(file.path(base, "nuyina_track.parquet"))

## full width: arrow reads one URL per call, so loop the manifest
m <- jsonlite::fromJSON(file.path(base, "manifest.json"))
d <- dplyr::bind_rows(lapply(m$files, \(f) arrow::read_parquet(f$url)))
```

### Up-to-the-minute data

Read `latest.json`, then ask the WFS for anything newer:

```python
import json, urllib.parse, urllib.request
import duckdb
base = "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1"
max_dt = json.load(urllib.request.urlopen(f"{base}/latest.json"))["datetime"]
wfs = ("https://data.aad.gov.au/geoserver/underway/ows?service=WFS&version=2.0.0"
       "&request=GetFeature&typeName=underway:nuyina_underway&outputFormat=csv"
       "&cql_filter=" + urllib.parse.quote(f"datetime>'{max_dt}'"))
recent = duckdb.read_csv(wfs).df()
```

In R, the bulk plus the rest:

``` r
get_underway <- function(x) {
    ## read the bulk
    d <- arrow::read_parquet("https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet")
    ## read the rest
    d1 <- tibble::as_tibble(vapour::vapour_read_fields("WFS:https://data.aad.gov.au/geoserver/ows?service=wfs&version=2.0.0&request=GetCapabilities",
                                                       sql = sprintf("SELECT * FROM \"underway:nuyina_underway\" WHERE datetime > '%s'",
                                                                     format(max(d$datetime, "%Y-%m-%dT%H:%M:%SZ")))))
    dplyr::bind_rows(d, d1)
}
d <- get_underway()
```

## Update Flow (`.github/workflows/update.yaml`)

```
1. latest.json + manifest.json from the release (none: bootstrap from the
   monolith, or a full WFS fetch)
2. WFS newest record; stop if not newer than latest.json
3. ogr2ogr -where "datetime > <latest>" -> new_records.parquet   (GDAL: fetch only)
4. scripts/build_store.py merge: touched year file(s) + new records, one
   geometry column, dedupe on datetime, sort, monthly row groups; track,
   10-minute JSON, manifest, latest
5. scripts/detect_voyages.py on the track
6. Sundays (or on request): scripts/build_store.py monolith
7. upload data, then manifest.json, then latest.json
8. keepalive: re-enable the workflow so GitHub's 60-day inactivity rule
   never switches the cron off
```

A failed step stops the job before upload, so the release keeps the last good
state. Details and rationale: `docs/tasks/updater-improvements.md`.

## Running Locally

With GDAL installed:

```bash
./scripts/update_underway.sh
```

Or with Docker:

```bash
docker run --rm -v "$PWD:/work" -w /work ghcr.io/osgeo/gdal:ubuntu-full-3.9.0 \
  ogr2ogr -f Parquet nuyina_underway.parquet \
  "WFS:https://data.aad.gov.au/geoserver/ows?service=wfs&version=2.0.0&request=GetCapabilities" \
  "underway:nuyina_underway" \
  -lco COMPRESSION=ZSTD
```

## WFS Source

- **Endpoint**: `https://data.aad.gov.au/geoserver/ows`
- **Layer**: `underway:nuyina_underway`
- **Service**: WFS 2.0.0

## Code of Conduct

Please note that the nuyina.underway project is released with a
[Contributor Code of
Conduct](https://contributor-covenant.org/version/2/1/CODE_OF_CONDUCT.html).
By contributing to this project, you agree to abide by its terms.

## Where things live

The landing page for everything Nuyina underway is
<https://mdsumner.github.io/nuyina-underway/>.

- [mdsumner/nuyina-underway](https://github.com/mdsumner/nuyina-underway): the
  aggregator. GitHub Actions pull the AAD GeoServer WFS into a Parquet file on
  its v0.0.1 release, and its `docs/` holds the landing page and the Proximal
  tracker app.
- [mdsumner/nuyina.underway](https://github.com/mdsumner/nuyina.underway): R
  package to read the data up to minutes-recent (Parquet cache plus live WFS).
  Its `envdata/` holds the satellite environmental-data extraction, folded in
  from nuyina.envdata.
- [mdsumner/seaice.map](https://github.com/mdsumner/seaice.map): targets
  pipeline rendering the sea ice and vessel map to Pawsey object storage
  (bucket nuyina.map).

Archived and folded: nuyina-proximal (now `docs/proximal/` in
nuyina-underway), nuyina.envdata (now `envdata/` in nuyina.underway).
