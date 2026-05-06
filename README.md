# RentScope (`rent_scope`)

Geospatial rental analysis for **Davis, California**: listings (RentCast or `data/sample_rentals.csv`), OSM amenities via Overpass, batch-relative scores, Leaflet UI in `docs/`.

## Features

**Data**

- Rentals: RentCast `GET /v1/listings/rental/long-term` when `RENTCAST_API_KEY` is set; else the sample CSV. Columns written to `data/raw/rentals.csv`: address, rent, beds, baths, sqft, latitude, longitude, source, listing_url, property_type.
- Amenities: Overpass query over a Davis bbox; types tagged as grocery, cafe, gym, park, library, transit, restaurant, pharmacy → `data/raw/amenities.geojson`.
- Scored table: `data/processed/scored_rentals.csv` adds `opportunity_score`, `score_explanation`.
- Map layer: `docs/rentals.geojson` from that CSV.

**Web app (`docs/`)**

- Leaflet, CARTO dark basemap, circle markers by score.
- Popup fields: address, rent, beds, baths, sqft, `opportunity_score`, `score_explanation`, property type.
- Sidebar: top 10 by score for the active filter set.
- Filters: max rent, bedrooms, min score, property type.
- Summary: count, mean rent, max score, min rent over filtered features.
- Static HTML/CSS/JS; `app.js` fetches `rentals.geojson` beside `index.html`.

**Pipeline**

- `run_pipeline.py` invokes in order: `collect_rentals.py`, `collect_osm_amenities.py`, `score_rentals.py`, `build_geojson.py`.
- `RENTCAST_API_KEY` is read from the process environment (variable name in `.env.example`). Missing coordinates on RentCast rows: Nominatim through geopy with `RateLimiter` (~1.15 s between lookups).

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

## Behavior

1. `collect_rentals.py` writes `data/raw/rentals.csv`.
2. `collect_osm_amenities.py` POSTs Overpass QL (custom `User-Agent`; fallback host if the first times out).
3. `score_rentals.py` reads `rentals.csv` and `amenities.geojson`, writes `scored_rentals.csv`.
4. `build_geojson.py` maps each row to a Point feature in `docs/rentals.geojson` with the popup properties.
5. The site reads that GeoJSON at runtime and applies client-side filters.

Scores are **relative to the current listing batch** (percentiles and peer medians recomputed when the input CSV changes).

## Scoring model

Distances use haversine on WGS84 (earth radius 6371.0088 km in code).

**Anchors**

- Campus point: 38.538223°N, 121.761712°W (UC Davis core).
- Downtown point: 38.544907°N, 121.740528°W.

**Rent efficiency**

- Square footage floored at 350 for ratios.
- `rent_per_sqft` and `rent_per_bed` (beds ≥ 1) compared across all listings: percentile rank where lower $/unit is better; combined 0.55 / 0.45.

**Campus / downtown**

- `100 * exp(-distance_km / scale)` with campus scale 1.35 km, downtown scale 0.95 km.

**Amenity density**

- Non-transit types only: grocery, cafe, gym, park, library, restaurant, pharmacy.
- Count within **0.8 km** → `min(100, count * 3.2 + 8)`.

**Transit**

- `amenity_type == transit` only.
- Count within **0.65 km** → `min(100, 18 + count * 16)`.

**Peers**

- Same `beds`, within **1.5 km** haversine; needs ≥2 other rents to compute a median comparison; score function maps rent vs that median to 0–100 (implementation in `scripts/score_rentals.py`).

**Blend**

- Weighted sum → `opportunity_score` (two decimal places in output): rent 0.22, campus 0.18, downtown 0.12, amenities 0.22, transit 0.13, peers 0.13.

`score_explanation` is assembled from the same intermediate values (thresholds on efficiency, campus, downtown, counts, peer line).

## License

[LICENSE](LICENSE) (MIT). [NOTICE](NOTICE).

**Copyright © 2026 Anant Madhok**
