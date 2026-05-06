# RentScope (`rent_scope`)

Geospatial rental analysis for **Davis, California**: listings (RentCast or a local sample), OSM amenities via Overpass, a batch-relative **opportunity score** (0–100), and a Leaflet map in `docs/`.

## What’s in the box

- Python pipeline: `collect_rentals.py`, `collect_osm_amenities.py`, `score_rentals.py`, `build_geojson.py`, orchestrated by `run_pipeline.py`.
- Map UI: `docs/index.html`, `docs/style.css`, `docs/app.js`, data in `docs/rentals.geojson`.
- Fallback data: `data/sample_rentals.csv` when `RENTCAST_API_KEY` is not set (optional `.env` from `.env.example`).

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

## Pipeline (short)

RentCast pull or sample copy → raw `rentals.csv`. Overpass query → `amenities.geojson`. Scorer writes `scored_rentals.csv` with `opportunity_score` and `score_explanation`. `build_geojson.py` emits `docs/rentals.geojson` for the map.

Nominatim (geopy, rate-limited) fills missing coordinates when RentCast omits them.

## Scoring (short)

Within-file percentiles for rent per sq ft and per bedroom; distance decay toward UC Davis and downtown; amenity and transit counts from OSM inside fixed radii; peer comparison vs median rent for the same bedroom count nearby. Weights: rent 0.22, campus 0.18, downtown 0.12, amenities 0.22, transit 0.13, peers 0.13.

Heuristic only—not financial or legal advice. OSM and listing coverage vary.

## License

[LICENSE](LICENSE) (MIT). [NOTICE](NOTICE).

**Copyright © 2026 Anant Madhok**
