#!/usr/bin/env python3
"""Emit Leaflet-ready GeoJSON for the docs site."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCORED = ROOT / "data" / "processed" / "scored_rentals.csv"
OUT_PATH = ROOT / "docs" / "rentals.geojson"


def main() -> None:
    df = pd.read_csv(SCORED)
    features = []
    for _, row in df.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        props = {
            "address": str(row["address"]),
            "rent": float(row["rent"]),
            "beds": int(row["beds"]),
            "baths": float(row["baths"]),
            "sqft": float(row["sqft"]),
            "opportunity_score": float(row["opportunity_score"]),
            "score_explanation": str(row["score_explanation"]),
            "property_type": str(row["property_type"]),
            "source": str(row["source"]),
            "listing_url": str(row.get("listing_url") or ""),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    collection = {"type": "FeatureCollection", "features": features}
    OUT_PATH.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    print(f"Wrote {len(features)} features to {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"build_geojson failed: {exc}", file=sys.stderr)
        sys.exit(1)
