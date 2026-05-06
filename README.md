# RentScope (`rent_scope`)

Geospatial rental analysis for **Davis, California**: listings (RentCast or a local sample), OSM amenities via Overpass, a batch-relative **opportunity score** (0–100), and a Leaflet map in `docs/`.

## Features

**Data**

- Rental listings: RentCast API when `RENTCAST_API_KEY` is present; otherwise `data/sample_rentals.csv` (Davis-shaped fields: address, rent, beds, baths, sqft, coordinates, source, listing URL, property type). Output: `data/raw/rentals.csv`.
- Amenities from OpenStreetMap (Overpass): groceries, cafés, gyms, parks, libraries, transit stops, restaurants, pharmacies. Output: `data/raw/amenities.geojson`.
- Scored export: `data/processed/scored_rentals.csv` with `opportunity_score` (0–100) and `score_explanation` per row.
- Map data: `docs/rentals.geojson` built from the scored CSV.

**Web app (`docs/`)**

- Leaflet map centered on Davis, dark CARTO basemap, markers colored by score.
- Popups: address, rent, beds, baths, sqft, score, explanation text.
- Sidebar: top ten listings by score (updates with filters).
- Filters: max rent, bedrooms, minimum score, property type.
- Summary strip: listing count, average rent, best score, lowest rent (for the current filter set).
- Responsive layout; static assets only (no bundler).

**Pipeline**

- `run_pipeline.py` runs: `collect_rentals.py` → `collect_osm_amenities.py` → `score_rentals.py` → `build_geojson.py`.
- Optional `.env` from `.env.example` for RentCast. Nominatim via geopy (rate-limited) can backfill coordinates on RentCast rows that lack lat/lon.

## Layout

```text
rent_scope/
  data/
    raw/
    processed/
    sample_rentals.csv
  scripts/
  docs/
```

## Scoring (short)

Within-file percentiles for rent per sq ft and per bedroom; distance decay toward UC Davis and downtown; amenity and transit counts from OSM inside fixed radii; peer comparison vs median rent for the same bedroom count nearby. Weights: rent 0.22, campus 0.18, downtown 0.12, amenities 0.22, transit 0.13, peers 0.13.

Heuristic only—not financial or legal advice. OSM and listing coverage vary.

## License

[LICENSE](LICENSE) (MIT). [NOTICE](NOTICE).

**Copyright © 2026 Anant Madhok**
