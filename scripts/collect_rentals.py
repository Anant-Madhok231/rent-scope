#!/usr/bin/env python3

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_PATH = ROOT / "data" / "sample_rentals.csv"
OFFCAMPUS_PINS_PATH = RAW_DIR / "offcampus_pins.csv"
OUT_PATH = RAW_DIR / "rentals.csv"


def _norm_addr_key(addr: str) -> str:
    line = (addr or "").split(",")[0].strip().lower()
    return " ".join(line.split())


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# Map distinct API slugs that refer to the same operator or housing program so we keep one pin.
_SLUG_BUCKET: dict[str, str] = {
    "the-green": "__west_village_green__",
    "greens-at-west-village": "__west_village_green__",
}


def _slug_bucket(slug: str) -> str:
    s = (slug or "").strip().lower()
    if not s:
        return ""
    return _SLUG_BUCKET.get(s, s)


def _drop_duplicate_operator_slugs(df: pd.DataFrame) -> pd.DataFrame:
    """Keep first row per non-empty OffCampus landlord slug (sample is always first)."""
    seen: set[str] = set()
    kept: list[pd.Series] = []
    for _, row in df.iterrows():
        raw = row.get("offcampus_slug")
        if pd.isna(raw) or raw is None:
            raw = ""
        else:
            raw = str(raw).strip()
        b = _slug_bucket(raw)
        if b:
            if b in seen:
                continue
            seen.add(b)
        kept.append(row)
    if not kept:
        return df.iloc[0:0].copy()
    return pd.DataFrame(kept).reset_index(drop=True)


def _drop_nearby_duplicate_pins(df: pd.DataFrame, max_m: float = 85.0) -> pd.DataFrame:
    """Keep row order; drop later rows whose coords fall within max_m of an already-kept pin."""
    kept: list[pd.Series] = []
    centers: list[tuple[float, float]] = []
    for _, row in df.iterrows():
        lat, lon = float(row["latitude"]), float(row["longitude"])
        if any(_haversine_m(lat, lon, la, lo) < max_m for la, lo in centers):
            continue
        centers.append((lat, lon))
        kept.append(row)
    if not kept:
        return df.iloc[0:0].copy()
    return pd.DataFrame(kept).reset_index(drop=True)


RENTCAST_URL = "https://api.rentcast.io/v1/listings/rental/long-term"


def _ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def _infer_room_type(desc: str) -> str:
    d = desc.lower()
    if any(
        w in d
        for w in (
            "shared room",
            "share a room",
            "shared bedroom",
            "roommate wanted",
            "double occupancy room",
        )
    ):
        return "shared"
    if any(
        w in d
        for w in (
            "private room",
            "private bedroom",
            "single room",
            "your own room",
            "private bed",
        )
    ):
        return "private"
    return "unknown"


def _geocode_missing(rows: list[dict]) -> None:
    locator = Nominatim(user_agent="RentScope/rent_scope_pipeline", timeout=30)
    geocode = RateLimiter(locator.geocode, min_delay_seconds=1.15)
    for rec in rows:
        if rec.get("latitude") is not None and rec.get("longitude") is not None:
            continue
        q = rec.get("address") or ""
        if not q:
            continue
        loc = geocode(q + ", Davis, CA, USA")
        if loc:
            rec["latitude"] = float(loc.latitude)
            rec["longitude"] = float(loc.longitude)


def _slug_to_listing_label(slug: str) -> str:
    s = str(slug or "").strip()
    if not s:
        return ""
    return " ".join(part.capitalize() for part in s.replace("-", " ").split())


def _from_rentcast(api_key: str) -> pd.DataFrame:
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}
    params = {"city": "Davis", "state": "CA", "limit": 500}
    resp = requests.get(RENTCAST_URL, headers=headers, params=params, timeout=120)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected RentCast response shape")

    records = []
    for item in rows:
        lat = item.get("latitude")
        lon = item.get("longitude")
        addr = item.get("formattedAddress") or item.get("addressLine1") or ""
        desc = str(item.get("description") or item.get("remarks") or "")
        url = str(
            item.get("url")
            or item.get("listingUrl")
            or item.get("listing_url")
            or item.get("sourceUrl")
            or ""
        )
        listing_name = (
            item.get("propertyName")
            or item.get("communityName")
            or item.get("name")
            or item.get("buildingName")
            or ""
        )
        if listing_name is not None:
            listing_name = str(listing_name).strip()
        else:
            listing_name = ""
        records.append(
            {
                "address": addr,
                "listing_name": listing_name,
                "rent": float(item.get("price") or 0),
                "beds": int(item.get("bedrooms") or 0),
                "baths": float(item.get("bathrooms") or 0),
                "sqft": float(item.get("squareFootage") or 0),
                "latitude": float(lat) if lat is not None else None,
                "longitude": float(lon) if lon is not None else None,
                "source": "rentcast",
                "listing_url": url,
                "property_type": str(item.get("propertyType") or "Unknown"),
                "room_type": _infer_room_type(desc),
                "offcampus_slug": "",
            }
        )
    _geocode_missing(records)
    records = [r for r in records if r.get("latitude") is not None and r.get("longitude") is not None]
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("RentCast returned no Davis listings with coordinates")
    return df


def _from_sample() -> pd.DataFrame:
    return pd.read_csv(SAMPLE_PATH)


def main() -> None:
    load_dotenv(ROOT / ".env")
    _ensure_dirs()
    key = os.environ.get("RENTCAST_API_KEY", "").strip()
    if key:
        df = _from_rentcast(key)
    else:
        df = _from_sample()
        df["source"] = "sample_davis_ca"

    if OFFCAMPUS_PINS_PATH.is_file():
        try:
            ocr = pd.read_csv(OFFCAMPUS_PINS_PATH)
        except (pd.errors.EmptyDataError, FileNotFoundError):
            ocr = pd.DataFrame()
        if len(ocr) > 0:
            for col in (
                "address",
                "listing_name",
                "rent",
                "beds",
                "baths",
                "sqft",
                "latitude",
                "longitude",
                "source",
                "listing_url",
                "property_type",
                "room_type",
                "offcampus_slug",
            ):
                if col not in ocr.columns:
                    if col == "offcampus_slug":
                        ocr[col] = ""
                    elif col == "room_type":
                        ocr[col] = "unknown"
                    elif col in ("beds",):
                        ocr[col] = 2
                    elif col in ("baths", "sqft", "rent"):
                        ocr[col] = 0
                    else:
                        ocr[col] = ""
            df = pd.concat([df, ocr], ignore_index=True)
            df["offcampus_slug"] = df["offcampus_slug"].fillna("").astype(str)
            df = _drop_duplicate_operator_slugs(df)
            df["_dedupe_addr"] = df["address"].map(_norm_addr_key)
            df = df.drop_duplicates(subset=["_dedupe_addr"], keep="first")
            df = df.drop(columns=["_dedupe_addr"])
            df = _drop_nearby_duplicate_pins(df, max_m=85.0)

    if "room_type" not in df.columns:
        df["room_type"] = "unknown"
    df["room_type"] = df["room_type"].fillna("unknown").astype(str).str.lower()
    if "offcampus_slug" not in df.columns:
        df["offcampus_slug"] = ""
    df["offcampus_slug"] = df["offcampus_slug"].fillna("").astype(str)
    if "listing_name" not in df.columns:
        df["listing_name"] = ""
    df["listing_name"] = df["listing_name"].fillna("").astype(str)
    empty_name = df["listing_name"].str.strip() == ""
    if empty_name.any():
        df.loc[empty_name, "listing_name"] = df.loc[empty_name, "offcampus_slug"].map(
            _slug_to_listing_label
        )

    cols = [
        "address",
        "listing_name",
        "rent",
        "beds",
        "baths",
        "sqft",
        "latitude",
        "longitude",
        "source",
        "listing_url",
        "property_type",
        "room_type",
        "offcampus_slug",
    ]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(df)} rows to {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"collect_rentals failed: {exc}", file=sys.stderr)
        sys.exit(1)
