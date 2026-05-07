#!/usr/bin/env python3

from __future__ import annotations

import json
import math
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


def _float_or_none(row: pd.Series, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool_val(row: pd.Series, key: str) -> bool:
    v = row.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("true", "1", "yes")


def _str_cell(row: pd.Series, key: str, default: str = "") -> str:
    v = row.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return default
    return s


def main() -> None:
    df = pd.read_csv(SCORED)
    features = []
    for _, row in df.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        uj = _loads_maybe(str(row.get("unitrans_stops_json") or ""))
        tj = _loads_maybe(str(row.get("listing_rent_trend_json") or ""))
        gj = _loads_maybe(str(row.get("nearby_grocery_json") or ""))
        fj = _loads_maybe(str(row.get("nearby_food_json") or ""))
        cn = row.get("convenience_nearest_km")
        try:
            cnear = float(cn) if cn is not None and str(cn) != "" else None
        except (TypeError, ValueError):
            cnear = None
        cnm = row.get("convenience_nearest_mi")
        try:
            cnear_mi = float(cnm) if cnm is not None and str(cnm) != "" else None
        except (TypeError, ValueError):
            cnear_mi = None
        oj = _loads_maybe(str(row.get("offcampus_reviews_json") or ""))
        oavg = _float_or_none(row, "offcampus_avg_rating")
        orc = row.get("offcampus_review_count")
        try:
            orc_i = int(orc) if orc is not None and str(orc) not in ("", "nan") else 0
        except (TypeError, ValueError):
            orc_i = 0
        def _fcol(key: str, default: float = 0.0) -> float:
            v = row.get(key)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return default
            if isinstance(v, str) and v.strip() == "":
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        bike_i = _fcol("bike_escooter_index", 0.0)
        traffic_i = _fcol("traffic_calm_index", 0.0)

        props = {
            "address": str(row["address"]),
            "listing_name": str(row.get("listing_name") or "").strip(),
            "rent": float(row["rent"]),
            "beds": int(row["beds"]),
            "baths": float(row["baths"]),
            "sqft": float(row["sqft"]),
            "opportunity_score": float(row["opportunity_score"]),
            "score_explanation": str(row["score_explanation"]),
            "property_type": str(row["property_type"]),
            "source": str(row["source"]),
            "listing_url": _str_cell(row, "listing_url"),
            "room_type": str(row.get("room_type") or "unknown"),
            "dist_km_memorial_union": float(row["dist_km_memorial_union"]),
            "dist_km_silo": float(row["dist_km_silo"]),
            "dist_mi_memorial_union": _fcol("dist_mi_memorial_union", float(row["dist_km_memorial_union"]) * 0.621371),
            "dist_mi_silo": _fcol("dist_mi_silo", float(row["dist_km_silo"]) * 0.621371),
            "dist_mi_campus": _fcol("dist_mi_campus", 0.0),
            "dist_mi_downtown": _fcol("dist_mi_downtown", 0.0),
            "convenience_800m_count": int(row["convenience_800m_count"]),
            "convenience_nearest_km": cnear,
            "convenience_nearest_mi": cnear_mi
            if cnear_mi is not None
            else (round(cnear * 0.621371, 4) if cnear is not None else None),
            "nearby_grocery": gj or [],
            "nearby_food": fj or [],
            "bike_escooter_index": bike_i,
            "traffic_calm_index": traffic_i,
            "parking_summary": str(row.get("parking_summary") or ""),
            "unitrans_stops": uj or [],
            "listing_rent_trend": tj or [],
            "offcampus_match": _bool_val(row, "offcampus_match"),
            "offcampus_brand_url": _str_cell(row, "offcampus_brand_url", "https://www.offcampusreview.com/"),
            "offcampus_school_url": _str_cell(
                row, "offcampus_school_url", "https://www.offcampusreview.com/school/uc-davis"
            ),
            "offcampus_landlord_url": _str_cell(row, "offcampus_landlord_url"),
            "offcampus_landlord_name": _str_cell(row, "offcampus_landlord_name"),
            "offcampus_landlord_slug": _str_cell(row, "offcampus_landlord_slug"),
            "offcampus_avg_rating": oavg,
            "offcampus_review_count": orc_i,
            "offcampus_reviews": oj or [],
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
