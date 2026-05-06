#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCORED = ROOT / "data" / "processed" / "scored_rentals.csv"
OUT_PATH = ROOT / "docs" / "rentals.geojson"


def _loads_maybe(s: str) -> list | None:
    if not s or not str(s).strip():
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None


def main() -> None:
    df = pd.read_csv(SCORED)
    features = []
    for _, row in df.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        uj = _loads_maybe(str(row.get("unitrans_stops_json") or ""))
        tj = _loads_maybe(str(row.get("listing_rent_trend_json") or ""))
        cn = row.get("convenience_nearest_km")
        try:
            cnear = float(cn) if cn is not None and str(cn) != "" else None
        except (TypeError, ValueError):
            cnear = None
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
            "room_type": str(row.get("room_type") or "unknown"),
            "dist_km_memorial_union": float(row["dist_km_memorial_union"]),
            "dist_km_silo": float(row["dist_km_silo"]),
            "convenience_800m_count": int(row["convenience_800m_count"]),
            "convenience_nearest_km": cnear,
            "unitrans_stops": uj or [],
            "listing_rent_trend": tj or [],
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
