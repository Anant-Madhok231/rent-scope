#!/usr/bin/env python3

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
ACS_PATH = PROCESSED_DIR / "davis_median_rent_acs.json"

UC_DAVIS = (38.538223, -121.761712)
DOWNTOWN_DAVIS = (38.544907, -121.740528)
MEMORIAL_UNION = (38.5414268, -121.7494914)
SILO = (38.5406379, -121.7522383)

WALK_KM = 0.8
TRANSIT_KM = 0.65
PEER_KM = 1.5
UNITRANS_LIST_KM = 1.5

W_RENT = 0.22
W_CAMPUS = 0.18
W_DOWNTOWN = 0.12
W_AMENITY = 0.22
W_TRANSIT = 0.13
W_PEER = 0.13

KM_TO_MI = 0.621371


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def decay_score(distance_km: float, scale_km: float) -> float:
    return 100.0 * math.exp(-max(0.0, distance_km) / scale_km)


def nearest_km_among_types(
    lat: float, lon: float, amenities: list[dict], types: set[str]
) -> float | None:
    best = None
    for a in amenities:
        if a.get("amenity_type") not in types:
            continue
        d = haversine_km(lat, lon, a["lat"], a["lon"])
        if best is None or d < best:
            best = d
    return best


def nearest_named_places(
    lat: float,
    lon: float,
    amenities: list[dict],
    types: set[str],
    limit: int = 6,
    max_km: float = 2.5,
) -> list[dict]:
    hits = []
    for a in amenities:
        t = a.get("amenity_type")
        if t not in types:
            continue
        d = haversine_km(lat, lon, a["lat"], a["lon"])
        if d > max_km:
            continue
        name = (a.get("name") or "").strip() or "(unnamed on OSM)"
        hits.append({"name": name, "kind": t, "mi": round(d * KM_TO_MI, 2)})
    hits.sort(key=lambda x: x["mi"])
    out = []
    seen = set()
    for h in hits:
        key = (h["name"].lower(), h["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def parking_summary_for_listing(
    lat: float, lon: float, amenities: list[dict], limit: int = 4
) -> str:
    hits = []
    for a in amenities:
        if a.get("amenity_type") != "parking":
            continue
        d = haversine_km(lat, lon, a["lat"], a["lon"])
        if d > 1.5:
            continue
        hits.append((d, a))
    hits.sort(key=lambda x: x[0])
    parts = []
    for d, a in hits[:limit]:
        fee = (a.get("fee") or "").strip().lower()
        charge = (a.get("charge") or "").strip()
        pname = (a.get("name") or "").strip()
        label = pname or "Parking"
        bits = []
        if fee == "yes":
            bits.append("fee likely")
        elif fee == "no":
            bits.append("tagged no fee")
        if charge:
            bits.append(charge)
        mi = round(d * KM_TO_MI, 2)
        if bits:
            parts.append(f"{label} ~{mi} mi ({', '.join(bits)})")
        else:
            parts.append(f"{label} ~{mi} mi (OSM has no fee/charge tag)")
    if not parts:
        return (
            "No mapped parking with fee data within ~0.9 mi in OpenStreetMap—"
            "assume paid permits or street rules locally."
        )
    return "; ".join(parts)


def bike_escooter_index(lat: float, lon: float, amenities: list[dict]) -> float:
    d_cw = nearest_km_among_types(lat, lon, amenities, {"cycleway"})
    d_br = nearest_km_among_types(lat, lon, amenities, {"bike_rental"})
    n_bp = count_in_radius(lat, lon, amenities, 0.45, type_filter={"bike_parking"})
    n_br_near = count_in_radius(lat, lon, amenities, 0.9, type_filter={"bike_rental"})
    s = 22.0
    if d_cw is not None:
        s += 0.48 * decay_score(d_cw, 0.38)
    else:
        s += 18.0
    s += min(28.0, n_bp * 4.2)
    s += min(22.0, n_br_near * 11.0)
    if d_br is not None:
        s += 0.15 * decay_score(d_br, 0.55)
    return float(min(100.0, max(0.0, s)))


def traffic_calm_index(lat: float, lon: float, amenities: list[dict]) -> float:
    d_m = nearest_km_among_types(lat, lon, amenities, {"arterial_motorway"})
    d_t = nearest_km_among_types(lat, lon, amenities, {"arterial_trunk"})
    d_p = nearest_km_among_types(lat, lon, amenities, {"arterial_primary"})
    cands = [x for x in (d_m, d_t, d_p) if x is not None]
    if not cands:
        return 62.0
    d_art = min(cands)
    return float(min(100.0, 22.0 + 78.0 * (1.0 - math.exp(-d_art / 0.42))))


def percentile_rank_lower_is_better(values: list[float], x: float) -> float:
    if not values:
        return 50.0
    sorted_v = sorted(values)
    below = sum(1 for v in sorted_v if v < x)
    return 100.0 * below / len(sorted_v)


def load_acs_series() -> list[dict]:
    if not ACS_PATH.is_file():
        return []
    try:
        data = json.loads(ACS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


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
        rec = {
            "lat": lat,
            "lon": lon,
            "amenity_type": props.get("amenity_type", ""),
            "name": props.get("name") or "",
            "network": props.get("network") or "",
            "operator": props.get("operator") or "",
        }
        if props.get("amenity_type") == "parking":
            rec["fee"] = props.get("fee") or ""
            rec["charge"] = props.get("charge") or ""
        out.append(rec)
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


def nearest_convenience_km(lat: float, lon: float, amenities: list[dict]) -> float | None:
    best = None
    for a in amenities:
        if a["amenity_type"] != "convenience":
            continue
        d = haversine_km(lat, lon, a["lat"], a["lon"])
        if best is None or d < best:
            best = d
    return best


def unitrans_nearby(lat: float, lon: float, amenities: list[dict], limit: int = 14) -> list[dict]:
    hits = []
    for a in amenities:
        if a["amenity_type"] != "unitrans":
            continue
        d = haversine_km(lat, lon, a["lat"], a["lon"])
        if d <= UNITRANS_LIST_KM:
            label = a["name"] or a["network"] or "Unitrans stop"
            hits.append({"name": label, "mi": round(d * KM_TO_MI, 2)})
    hits.sort(key=lambda x: x["mi"])
    return hits[:limit]


def listing_trend_from_acs(rent: float, acs: list[dict]) -> list[dict]:
    if not acs:
        return []
    vals = [x["median_rent"] for x in acs if x.get("median_rent")]
    if not vals:
        return []
    m_last = float(acs[-1]["median_rent"])
    if m_last <= 0:
        return []
    out = []
    for row in acs:
        y = row.get("year")
        m = row.get("median_rent")
        if y is None or m is None:
            continue
        est = rent * (float(m) / m_last)
        out.append({"year": int(y), "rent_est": round(est, 0)})
    return out


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
    dist_campus_mi: float,
    d_mu_mi: float,
    d_silo_mi: float,
    conv_n: int,
    unitrans_n: int,
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
        parts.append(f"about {dist_campus_mi:.1f} mi straight-line from campus core")

    parts.append(
        f"Memorial Union {d_mu_mi:.2f} mi · Silo {d_silo_mi:.2f} mi · "
        f"{conv_n} convenience stores ≤800 m · {unitrans_n} Unitrans stops ≤~0.9 mi"
    )

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
    if len(text) > 480:
        return text[:477] + "..."
    return text


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(RAW_RENTALS)
    if "room_type" not in df.columns:
        df["room_type"] = "unknown"
    df["room_type"] = df["room_type"].fillna("unknown").astype(str).str.lower()
    df.loc[~df["room_type"].isin(["shared", "private", "unknown"]), "room_type"] = "unknown"

    amenities = load_amenities(RAW_AMENITIES)
    for a in amenities:
        if a.get("amenity_type") == "transit":
            d = haversine_km(a["lat"], a["lon"], UC_DAVIS[0], UC_DAVIS[1])
            if d <= 2.4:
                a["amenity_type"] = "unitrans"
    acs = load_acs_series()

    rent_per_sqft = []
    rent_per_bed = []
    for _, row in df.iterrows():
        sq = max(float(row["sqft"]), 350.0)
        beds = max(int(row["beds"]), 1)
        rent_per_sqft.append(float(row["rent"]) / sq)
        rent_per_bed.append(float(row["rent"]) / beds)

    non_transit = {"grocery", "cafe", "gym", "park", "library", "restaurant", "pharmacy", "convenience"}
    transit_types = {"transit", "unitrans"}

    scores_rent = []
    scores_campus = []
    scores_dt = []
    scores_amenity = []
    scores_transit = []
    scores_peer = []
    explanations = []
    opportunity_scores = []
    dist_mu_col = []
    dist_silo_col = []
    conv_count_col = []
    conv_nearest_col = []
    unitrans_json_col = []
    trend_json_col = []
    dist_mi_mu_col = []
    dist_mi_silo_col = []
    dist_mi_campus_col = []
    dist_mi_dt_col = []
    conv_nearest_mi_col = []
    grocery_json_col = []
    food_json_col = []
    bike_idx_col = []
    traffic_idx_col = []
    parking_sum_col = []

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
        d_mu = haversine_km(lat, lon, MEMORIAL_UNION[0], MEMORIAL_UNION[1])
        d_silo = haversine_km(lat, lon, SILO[0], SILO[1])
        dist_mu_col.append(round(d_mu, 4))
        dist_silo_col.append(round(d_silo, 4))
        dist_mi_mu_col.append(round(d_mu * KM_TO_MI, 4))
        dist_mi_silo_col.append(round(d_silo * KM_TO_MI, 4))
        dist_mi_campus_col.append(round(d_campus * KM_TO_MI, 4))
        dist_mi_dt_col.append(round(d_dt * KM_TO_MI, 4))

        sc = decay_score(d_campus, 1.35)
        sd = decay_score(d_dt, 0.95)
        scores_campus.append(sc)
        scores_dt.append(sd)

        amenity_n = count_in_radius(lat, lon, amenities, WALK_KM, type_filter=non_transit)
        amenity_score = min(100.0, amenity_n * 3.2 + 8.0)
        scores_amenity.append(amenity_score)

        transit_n = count_in_radius(lat, lon, amenities, TRANSIT_KM, type_filter=transit_types)
        transit_score = min(100.0, 18.0 + transit_n * 16.0)
        scores_transit.append(transit_score)

        peer_s, peer_note = peer_adjustment(df, idx, lat, lon, rent, beds)
        scores_peer.append(peer_s)

        conv_n = count_in_radius(lat, lon, amenities, WALK_KM, type_filter={"convenience"})
        conv_count_col.append(conv_n)
        cnear = nearest_convenience_km(lat, lon, amenities)
        conv_nearest_col.append(round(cnear, 4) if cnear is not None else "")
        conv_nearest_mi_col.append(
            round(cnear * KM_TO_MI, 4) if cnear is not None else ""
        )

        ulist = unitrans_nearby(lat, lon, amenities)
        unitrans_json_col.append(json.dumps(ulist))
        trend_json_col.append(json.dumps(listing_trend_from_acs(rent, acs)))

        grocery_json_col.append(
            json.dumps(
                nearest_named_places(lat, lon, amenities, {"grocery", "convenience"})
            )
        )
        food_json_col.append(
            json.dumps(
                nearest_named_places(lat, lon, amenities, {"restaurant", "cafe"})
            )
        )
        bike_idx_col.append(round(bike_escooter_index(lat, lon, amenities), 2))
        traffic_idx_col.append(round(traffic_calm_index(lat, lon, amenities), 2))
        parking_sum_col.append(parking_summary_for_listing(lat, lon, amenities))

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
                d_campus * KM_TO_MI,
                d_mu * KM_TO_MI,
                d_silo * KM_TO_MI,
                conv_n,
                len(ulist),
            )
        )

    df = df.copy()
    df["opportunity_score"] = opportunity_scores
    df["score_explanation"] = explanations
    df["dist_km_memorial_union"] = dist_mu_col
    df["dist_km_silo"] = dist_silo_col
    df["convenience_800m_count"] = conv_count_col
    df["convenience_nearest_km"] = conv_nearest_col
    df["convenience_nearest_mi"] = conv_nearest_mi_col
    df["dist_mi_memorial_union"] = dist_mi_mu_col
    df["dist_mi_silo"] = dist_mi_silo_col
    df["dist_mi_campus"] = dist_mi_campus_col
    df["dist_mi_downtown"] = dist_mi_dt_col
    df["nearby_grocery_json"] = grocery_json_col
    df["nearby_food_json"] = food_json_col
    df["bike_escooter_index"] = bike_idx_col
    df["traffic_calm_index"] = traffic_idx_col
    df["parking_summary"] = parking_sum_col
    df["unitrans_stops_json"] = unitrans_json_col
    df["listing_rent_trend_json"] = trend_json_col

    df = df.sort_values("opportunity_score", ascending=False)
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(df)} scored rows to {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"score_rentals failed: {exc}", file=sys.stderr)
        sys.exit(1)
