# Nuyina Tracker — Design Document

## Overview

A browser-based vessel tracking tool for RSV *Nuyina*, combining live WFS data with pre-computed voyage metadata to enable exploration of current and historical voyages with configurable viewpoints.

## Current State

Single-page HTML app (`index.html`) that:
- Fetches live position data from AAD GeoServer WFS
- Displays vessel track with configurable record count and time interval
- Shows crow-fly lines (Mercator or great circle) from track points to a hardcoded "home" location
- Calculates actual vs geodesic distance metrics

## Data Architecture

### Live Data Source
```
WFS: https://data.aad.gov.au/geoserver/ows
Layer: underway:nuyina_underway
Fields: datetime, latitude, longitude, gml_id, ...
Update: per-minute
```

### Cached Data (uwy.new repo)
```
nuyina_underway.parquet    # bulk historical data
voyages.json               # auto-detected + hand-edited voyage metadata
```

### voyages.json Schema
```json
{
  "_generated": "ISO timestamp",
  "_note": "Auto-detected draft - review before publishing",
  "ports": {
    "Hobart": { "lat": -42.88, "lon": 147.33, "radius_km": 15 },
    "Mawson": { "lat": -67.60, "lon": 62.87, "radius_km": 80 },
    ...
  },
  "voyages": [
    {
      "id": "V3 2025-02",
      "note": "Mawson resupply, Davis retrieval",
      "start": "2025-02-12T00:00:00Z",
      "end": null,
      "stops": [
        {
          "port": "Hobart",
          "arrive": "2025-02-10T14:00:00Z",
          "depart": "2025-02-12T08:00:00Z",
          "arrive_gml_id": "nuyina_underway.xxxxx",
          "depart_gml_id": "nuyina_underway.xxxxx",
          "dwell_hours": 42
        },
        ...
      ]
    }
  ]
}
```

## Proposed UI Enhancements

### Target Location Selection

**Current:** Hardcoded home coordinates  
**Proposed:** Three-tier selection

1. **Default** — Last known port stop from `voyages.json`
2. **Port picker** — Scrollable list showing:
```
   ┌──────────────────────────────────┐
   │ Target Location            [▼]  │
   ├──────────────────────────────────┤
   │ ● Hobart (current)               │
   │   Mawson — V3 dep 2025-02-15     │
   │   Davis — V3 arr 2025-02-20      │
   │   Hobart — V2 arr 2025-02-03     │
   │   Casey — V2 dep 2025-01-20      │
   │   ...                            │
   │ ─────────────────────────────    │
   │   ⊕ Custom location...           │
   └──────────────────────────────────┘
```
3. **Manual entry** — Lon/lat input for arbitrary viewpoint (session only, not persisted)

### Time Window Selection

**Current:** Record count + interval sliders (implicit time window)  
**Proposed:** Add explicit options

1. **Voyage selector** — Dropdown of detected voyages, auto-sets time bounds
2. **Date range** — Optional start/end datetime pickers
3. **Current sliders** — Retained for fine control within selected window

### Controls Panel Mockup
```
┌─────────────────────────────────────┐
│ Nuyina Tracker                      │
│ 2025-02-11 09:07 AEDT               │
│ 43.0 km to target                   │
│ 34 points                           │
├─────────────────────────────────────┤
│ Voyage: [V3 2025-02 (current)  ▼]   │
├─────────────────────────────────────┤
│ Target: [Hobart (home)         ▼]   │
│         [-42.88, 147.33]  [Edit]    │
├─────────────────────────────────────┤
│ Records: ━━━━━━━━━━○━━━━━━━━  200   │
│ Interval: ━━○━━━━━━━━━━━━━━━  10m   │
├─────────────────────────────────────┤
│ [✓] Show crow lines                 │
│ [ ] Great circle                    │
├─────────────────────────────────────┤
│ Track: 4,521 km | GC: 4,032 km      │
│ Extra: +12.1%                       │
├─────────────────────────────────────┤
│ [Refresh]                           │
└─────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Voyage Detection Pipeline (uwy.new)

- [ ] Add `detect_voyages.R` script
- [ ] GitHub Action: runs on parquet updated cache, outputs `voyages_draft.json`
- [ ] Manual review step: copy/edit to `voyages.json` for publication
- [ ] Host `voyages.json` alongside parquet in releases

### Phase 2: Voyage-Aware UI

- [ ] Fetch `voyages.json` on page load
- [ ] Populate voyage dropdown, default to most recent
- [ ] Set time window from voyage start/end (or now if in progress)
- [ ] Populate target dropdown from voyage stops + all known ports

### Phase 3: Custom Location Entry

- [ ] "Custom location" option in target dropdown
- [ ] Modal/inline form: lon, lat, optional label
- [ ] Click-on-map to set target (stretch goal)
- [ ] Session storage only (no persistence)

### Phase 4: Polish

- [ ] URL query params for sharing specific views (`?voyage=V3&target=Mawson`)
- [ ] Responsive layout for mobile
- [ ] Loading states / error handling
- [ ] Optional: auto-refresh toggle for live tracking

## Possible Future Enhancements

### Analytical
- **Speed profile** — chart showing speed over time, coloured by segment
- **Ice extent overlay** — show sea ice at voyage date (via NSIDC WMS)
- **Weather overlay** — wind/wave data along track
- **Fuel efficiency proxy** — speed vs distance metrics per leg

### Comparative
- **Multi-voyage overlay** — compare routes across years
- **Planned vs actual** — if official voyage plans become available
- **Rhumb vs GC vs actual** — three-way comparison visualisation

### Social
- **Shareable snapshots** — generate static image/link of current view
- **Embedded widget** — iframe-friendly version for AAD/outreach sites

### Data
- **Other vessels** — generalise to Aurora Australis historical data, other Antarctic operators
- **AIS integration** — supplement with Marine Traffic / VesselFinder for approach timing

## Dependencies

| Component | Source | Notes |
|-----------|--------|-------|
| Leaflet | CDN | Mapping |
| Leaflet.Geodesic | CDN | Great circle lines |
| voyages.json | uwy.new releases | Generated + hand-edited |
| WFS feed | data.aad.gov.au | Live positions |

## File Structure (proposed)
```
nuyina-tracker/
├── index.html          # main app
├── voyages.json        # published voyage metadata (copied from uwy.new)
└── README.md

uwy.new/
├── .github/workflows/
│   └── update.yml         # scheduled parquet cache (no R) 
|   └── voyage-detect.yml  # schedule voyage detection (R, or could be alternate see R/detect_voyages.R)
├── R/
│   └── detect_voyages.R
├── releases/
│   ├── nuyina_underway.parquet
│   └── voyages_draft.json
└── README.md
```

## Open Questions

1. **Voyage numbering** — Match official AAD voyage IDs (V1, V2, V3...) or generate own?
2. **Port radius tuning** — Current values are guesses; may need adjustment for sea ice ops
3. **WFS limits** — Is there a hard cap on `count`? Need pagination for full history?
4. **CORS** — Currently works; any risk of policy change?
5. **Parquet in browser** — Could use `parquet-wasm` for client-side historical queries, avoiding WFS limits

---

*Draft: 2025-02-11*
