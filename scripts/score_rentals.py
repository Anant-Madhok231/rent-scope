#!/usr/bin/env python3
"""Spatial scoring for rental opportunity index (0-100)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_RENTALS = ROOT / "data" / "raw" / "rentals.csv"
RAW_AMENITIES = ROOT / "data" / "raw" / "amenities.geojson"
PROCESSED_DIR = ROOT / "data" / "processed"
OUT_CSV = PROCESSED_DIR / "scored_rentals.csv"

UC_DAVIS = (38.538223, -121.761712)
DOWNTOWN_DAVIS = (38.544907, -121.740528)

WALK_KM = 0.8
TRANSIT_KM = 0.65
PEER_KM = 1.5

W_RENT = 0.22
W_CAMPUS = 0.18
W_DOWNTOWN = 0.12
W_AMENITY = 0.22
W_TRANSIT = 0.13
W_PEER = 0.13


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def decay_score(distance_km: float, scale_km: float) -> float:
    return 100.0 * math.exp(-max(0.0, distance_km) / scale_km)


def percentile_rank_lower_is_better(values: list[float], x: float) -> float:
    if not values:
        return 50.0
    sorted_v = sorted(values)
    below = sum(1 for v in sorted_v if v < x)
    return 100.0 * below / len(sorted_v)


def load_amenities(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features") or []
    out = []
    for f in feats:
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        props = f.get("properties") or {}
        out.append({"lat": lat, "lon": lon, "amenity_type": props.get("amenity_type", "")})
    return out


def count_in_radius(
    lat: float,
    lon: float,
    amenities: list[dict],
    radius_km: float,
    type_filter: set[str] | None = None,
    exclude_types: set[str] | None = None,
) -> int:
    n = 0
    for a in amenities:
        t = a["amenity_type"]
        if type_filter is not None and t not in type_filter:
            continue
        if exclude_types and t in exclude_types:
            continue
        if haversine_km(lat, lon, a["lat"], a["lon"]) <= radius_km:
            n += 1
    return n


def peer_adjustment(
    df: pd.DataFrame,
    idx: int,
    lat: float,
    lon: float,
    rent: float,
    beds: int,
) -> tuple[float, str]:
    peers = []
    for j, row in df.iterrows():
        if j == idx:
            continue
        if int(row["beds"]) != int(beds):
            continue
        d = haversine_km(lat, lon, float(row["latitude"]), float(row["longitude"]))
        if d <= PEER_KM:
            peers.append(float(row["rent"]))
    if len(peers) < 2:
        return 50.0, "limited peer comps nearby"
    med = sorted(peers)[len(peers) // 2]
    if med <= 0:
        return 50.0, ""
    ratio = rent / med
    if ratio <= 0.85:
        score = 90 + min(10, (0.85 - ratio) * 40)
    elif ratio <= 0.95:
        score = 75 + (0.95 - ratio) * 150
    elif ratio <= 1.05:
        score = 55 + (1.05 - ratio) * 200
    elif ratio <= 1.2:
        score = 35 + (1.2 - ratio) * 133
    else:
        score = max(5, 35 - (ratio - 1.2) * 80)
    note = f"versus median ${med:.0f} for {beds}br nearby"
    return float(min(100, max(0, score))), note


def build_explanation(
    rent_eff: float,
    campus: float,
    downtown: float,
    amenity_count: int,
    transit_count: int,
    peer_note: str,
    dist_campus_km: float,
) -> str:
    parts = []
    if rent_eff >= 72:
        parts.append("Rent efficiency looks strong for the layout")
    elif rent_eff >= 55:
        parts.append("Rent is reasonable on a per-bedroom basis")
    else:
        parts.append("Rent is on the high side versus interior space")

    if campus >= 75:
        parts.append("very close to the UC Davis core")
    elif campus >= 55:
        parts.append("a practical distance from campus")
    else:
        parts.append(f"about {dist_campus_km:.1f} km from campus")

    if downtown >= 70:
        parts.append("steps from downtown dining and errands")
    elif downtown >= 45:
        parts.append("reachable to downtown without a long drive")

    if amenity_count >= 18:
        parts.append(f"walkable cluster with {amenity_count} food and daily-life spots nearby")
    elif amenity_count >= 8:
        parts.append(f"{amenity_count} walkable amenities in the corridor")
    else:
        parts.append("lighter immediate amenity density")

    if transit_count >= 4:
        parts.append("several transit stops within a short walk")
    elif transit_count >= 1:
        parts.append("some nearby transit access")
    else:
        parts.append("limited nearby transit in OSM for this block")

    if peer_note:
        parts.append(peer_note)

    text = "; ".join(parts)
    if len(text) > 420:
        return text[:417] + "..."
    return text


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(RAW_RENTALS)
    amenities = load_amenities(RAW_AMENITIES)

    rent_per_sqft = []
    rent_per_bed = []
    for _, row in df.iterrows():
        sq = max(float(row["sqft"]), 350.0)
        beds = max(int(row["beds"]), 1)
        rent_per_sqft.append(float(row["rent"]) / sq)
        rent_per_bed.append(float(row["rent"]) / beds)

    scores_rent = []
    scores_campus = []
    scores_dt = []
    scores_amenity = []
    scores_transit = []
    scores_peer = []
    explanations = []
    opportunity_scores = []

    non_transit = {"grocery", "cafe", "gym", "park", "library", "restaurant", "pharmacy"}
    transit_only = {"transit"}

    for idx, row in df.iterrows():
        lat, lon = float(row["latitude"]), float(row["longitude"])
        rent = float(row["rent"])
        beds = int(row["beds"])
        sq = max(float(row["sqft"]), 350.0)

        rps = rent / sq
        rpb = rent / max(beds, 1)
        eff = (
            percentile_rank_lower_is_better(rent_per_sqft, rps) * 0.55
            + percentile_rank_lower_is_better(rent_per_bed, rpb) * 0.45
        )
        scores_rent.append(eff)

        d_campus = haversine_km(lat, lon, UC_DAVIS[0], UC_DAVIS[1])
        d_dt = haversine_km(lat, lon, DOWNTOWN_DAVIS[0], DOWNTOWN_DAVIS[1])
        sc = decay_score(d_campus, 1.35)
        sd = decay_score(d_dt, 0.95)
        scores_campus.append(sc)
        scores_dt.append(sd)

        amenity_n = count_in_radius(lat, lon, amenities, WALK_KM, type_filter=non_transit)
        amenity_score = min(100.0, amenity_n * 3.2 + 8.0)
        scores_amenity.append(amenity_score)

        transit_n = count_in_radius(lat, lon, amenities, TRANSIT_KM, type_filter=transit_only)
        transit_score = min(100.0, 18.0 + transit_n * 16.0)
        scores_transit.append(transit_score)

        peer_s, peer_note = peer_adjustment(df, idx, lat, lon, rent, beds)
        scores_peer.append(peer_s)

        raw = (
            W_RENT * eff
            + W_CAMPUS * sc
            + W_DOWNTOWN * sd
            + W_AMENITY * amenity_score
            + W_TRANSIT * transit_score
            + W_PEER * peer_s
        )
        opportunity_scores.append(round(raw, 2))

        explanations.append(
            build_explanation(
                eff,
                sc,
                sd,
                amenity_n,
                transit_n,
                peer_note,
                d_campus,
            )
        )

    df = df.copy()
    df["opportunity_score"] = opportunity_scores
    df["score_explanation"] = explanations

    df = df.sort_values("opportunity_score", ascending=False)
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(df)} scored rows to {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"score_rentals failed: {exc}", file=sys.stderr)
        sys.exit(1)
