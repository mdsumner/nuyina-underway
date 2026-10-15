# uwy.new (Refactored)

Nuyina underway data pipeline - fetches from AAD WFS geoserver and caches in Parquet.

## Data Access

The underway data is stored as Parquet at:

```
https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet
```

### Reading with DuckDB (no dependencies)

```sql
-- DuckDB can read directly from URL
SELECT * FROM 'https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet'
WHERE datetime > '2024-01-01'
LIMIT 10;
```

### Reading with Python

```python
import duckdb
# or: import pandas as pd; pd.read_parquet(url)

url = "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet"
df = duckdb.sql(f"SELECT * FROM '{url}'").df()
```

### Reading with R

```r
# Using arrow
d <- arrow::read_parquet("https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet")

# Using duckdb
library(duckdb)
con <- dbConnect(duckdb())
d <- dbGetQuery(con, "SELECT * FROM 'https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet'")
```

### Up-to-the-minute data

The cached parquet is updated every 6 hours. For real-time data, query the WFS directly for records newer than your cache:

```python
import duckdb

cache_url = "https://github.com/mdsumner/nuyina-underway/releases/download/v0.0.1/nuyina_underway.parquet"
wfs_url = "https://data.aad.gov.au/geoserver/underway/ows?service=WFS&version=2.0.0&request=GetFeature&typeName=underway:nuyina_underway&outputFormat=csv"

con = duckdb.connect()

# Get max datetime from cache
max_dt = con.sql(f"SELECT MAX(datetime) FROM '{cache_url}'").fetchone()[0]

# Fetch newer records from WFS (add datetime filter)
# ... combine as needed
```

## Architecture

### Previous Design (R-based)

- Required R + vapour package
- Vulnerability: parquet deleted then recreated - if recreation fails, downstream assumes file exists

### New Design (GDAL-native)

- Uses GDAL Docker image directly (no R dependency)
- Atomic updates: write to temp → validate → rename
- Incremental updates: only fetch new records
- Automatic backup and rollback on failure

### Update Flow

```
1. Download existing parquet from release (if exists)
2. Query max(datetime) from existing data
3. Fetch new records from WFS where datetime > max_datetime
4. If new records exist:
   a. Backup existing file
   b. Merge new records with existing
   c. Validate merged file
   d. Atomic replace (or rollback to backup on failure)
5. Upload updated file to release
```

### Error Handling

- **No existing data**: Full fetch from WFS
- **WFS unavailable**: Keep existing data, log error
- **Merge failure**: Rollback to backup
- **Validation failure**: Rollback to backup

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

## Schedule

GitHub Actions runs every 6 hours via cron: `0 */6 * * *`

Can also be triggered manually via workflow_dispatch.
