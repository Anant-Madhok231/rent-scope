#!/usr/bin/env python3
"""Fetch rental listings for Davis, CA or copy the local sample set."""

from __future__ import annotations

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
OUT_PATH = RAW_DIR / "rentals.csv"

RENTCAST_URL = "https://api.rentcast.io/v1/listings/rental/long-term"


def _ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)


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
        records.append(
            {
                "address": addr,
                "rent": float(item.get("price") or 0),
                "beds": int(item.get("bedrooms") or 0),
                "baths": float(item.get("bathrooms") or 0),
                "sqft": float(item.get("squareFootage") or 0),
                "latitude": float(lat) if lat is not None else None,
                "longitude": float(lon) if lon is not None else None,
                "source": "rentcast",
                "listing_url": "",
                "property_type": str(item.get("propertyType") or "Unknown"),
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

    cols = [
        "address",
        "rent",
        "beds",
        "baths",
        "sqft",
        "latitude",
        "longitude",
        "source",
        "listing_url",
        "property_type",
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
