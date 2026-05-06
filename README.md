# RentScope (`rent_scope`)

RentScope is a geospatial analytics project focused on **Davis, California**. It combines rental listing data, **OpenStreetMap** amenities from the **Overpass API**, and a transparent **opportunity score** (0–100) to highlight listings that look comparatively strong on rent efficiency, campus and downtown access, walkable services, transit, and peer rent levels.

Live site (GitHub Pages): after you enable Pages on the `main` branch with the `/docs` folder, the app is served from the repository’s GitHub Pages URL (for example `https://<user>.github.io/rent-scope/`).

## Features

- **Data pipeline (Python)**  
  - Rentals: **RentCast** when `RENTCAST_API_KEY` is set; otherwise a curated **Davis sample** CSV.  
  - Amenities: groceries, cafés, gyms, parks, libraries, transit stops, restaurants, pharmacies.  
  - **Scored** listings with a short **natural-language explanation** per row.  
  - **GeoJSON** output for the web map.
- **Web app (`docs/`)**  
  - **Leaflet** map (dark **CARTO** basemap), markers colored by score.  
  - Popups: address, rent, beds, baths, sq ft, score, explanation.  
  - Sidebar: top 10 by score.  
  - Filters: max rent, bedrooms, minimum score, property type.  
  - Summary cards: listing count, average rent, best score, cheapest filtered listing.  
  - Responsive layout and dark UI.

## Architecture

```text
rent_scope/
  data/
    raw/                  ← rentals.csv, amenities.geojson (from collectors)
    processed/            ← scored_rentals.csv
    sample_rentals.csv    ← fallback when no RentCast key
  scripts/
    collect_rentals.py    ← RentCast or sample → data/raw/rentals.csv
    collect_osm_amenities.py
    score_rentals.py
    build_geojson.py      ← docs/rentals.geojson
    run_pipeline.py
  docs/                   ← static GitHub Pages site
    index.html
    style.css
    app.js
    rentals.geojson       ← generated; commit after pipeline for instant Pages
```

**Frontend** loads `./rentals.geojson` next to `index.html` so paths stay valid on GitHub Pages. **No build step** is required for the site.

## Data pipeline

1. **`collect_rentals.py`**  
   - If `RENTCAST_API_KEY` exists: `GET https://api.rentcast.io/v1/listings/rental/long-term` with `city=Davis`, `state=CA`.  
   - Rows without coordinates are filled using **Nominatim** via **geopy** with a rate limiter (polite usage).  
   - Else: copies fields from `data/sample_rentals.csv` into `data/raw/rentals.csv`.

2. **`collect_osm_amenities.py`**  
   - POST to a public Overpass endpoint with a Davis bounding box.  
   - Normalizes tags into `amenity_type` values used by the scorer.  
   - Writes `data/raw/amenities.geojson`.

3. **`score_rentals.py`**  
   - Reads raw rentals and amenities.  
   - Computes component scores (see below), blends them with fixed weights, rounds to **`opportunity_score`**, and writes **`score_explanation`**.  
   - Output: `data/processed/scored_rentals.csv`.

4. **`build_geojson.py`**  
   - Converts the scored CSV to `docs/rentals.geojson` for Leaflet.

Run everything:

```bash
cd rent_scope
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/run_pipeline.py
```

## Scoring methodology

Scores are **comparative within the current Davis batch**, not an absolute market appraisal.

| Component | Role |
|-----------|------|
| **Rent efficiency** | Lower **rent per sq ft** and **rent per bedroom** rank higher vs other listings in the file. |
| **UC Davis proximity** | Distance to a fixed campus point; exponential decay so closer listings score higher. |
| **Downtown Davis** | Same idea toward a downtown anchor. |
| **Amenity density** | Count of non-transit amenities (food, grocery, gym, park, library, pharmacy) within **~800 m**. |
| **Transit** | Transit-related OSM features within **~650 m**. |
| **Peer rent** | Among same bedroom count within **~1.5 km**, rent **below** the local median increases the component. |

Weights (sum 1.0): rent efficiency **0.22**, campus **0.18**, downtown **0.12**, amenities **0.22**, transit **0.13**, peers **0.13**. The final **`opportunity_score`** is the weighted blend, clamped implicitly by construction into a practical 0–100 band.

**Disclaimer:** This is a **heuristic model** for exploration and learning. It is not investment, legal, or rental advice. Listing data may be incomplete or stale; OSM coverage varies by area.

## Setup

- **Python 3.10+** recommended.  
- Copy `.env.example` to `.env` and add `RENTCAST_API_KEY` if you have one.  
- Respect **Nominatim** and **Overpass** usage policies; the collectors use modest timeouts and rate limiting where applicable.

## GitHub Pages deployment

1. Push this repository to GitHub (`rent_scope` layout at repo root).  
2. Repository **Settings → Pages**.  
3. **Build and deployment**: Source = **Deploy from a branch**.  
4. Branch = **`main`** (or your default), folder = **`/docs`**.  
5. Save. After the build, open the site URL GitHub shows (often `https://<username>.github.io/<repo>/`).  

Ensure `docs/index.html`, `docs/style.css`, `docs/app.js`, and **`docs/rentals.geojson`** are present. Regenerate GeoJSON with `python scripts/run_pipeline.py` after data changes, then commit `docs/rentals.geojson`.

## Limitations and future work

- Scores depend on **which listings** are in the batch; small samples shift percentiles.  
- **RentCast** coverage and fields may differ by market; the sample path is Davis-specific.  
- OSM is **community-maintained**; amenity and transit completeness varies.  
- No real-time MLS feed; refresh the pipeline on your own cadence.  
- Possible extensions: time-on-market, bike network distance, noise layers, campus building targets per major, exportable reports, and a small API for live demos.

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE).  
See [NOTICE](NOTICE) for attribution and project purpose.

---

**Copyright © 2026 Anant Madhok**
