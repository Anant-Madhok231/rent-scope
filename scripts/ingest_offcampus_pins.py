#!/usr/bin/env python3
"""
Build rental rows from OffCampusReview UC Davis review property addresses.
Geocodes each unique (address, landlord slug), keeps pins within MAX_RADIUS_MI
of Memorial Union, dedupes against sample_rentals.csv.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
import pandas as pd
import requests
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://www.offcampusreview.com/api/schools/uc-davis"
SAMPLE_PATH = ROOT / "data" / "sample_rentals.csv"
ACS_PATH = ROOT / "data" / "processed" / "davis_median_rent_acs.json"
OUT_CSV = ROOT / "data" / "raw" / "offcampus_pins.csv"
CACHE_PATH = ROOT / "data" / "processed" / "offcampus_geocode_cache.json"

HEADERS = {
    "User-Agent": "RentScope/rent_scope (https://github.com/Anant-Madhok231/rent-scope; OffCampus ingest)",
}

MEMORIAL_UNION = (38.5414268, -121.7494914)
MAX_RADIUS_MI = 5.0
KM_TO_MI = 0.621371

# OSM/Nominatim often misses UC Davis internal street names; use known-good coords.
_LINE_FALLBACK: dict[str, tuple[float, float]] = {
    "310 parkway cir": (38.5447, -121.7612),
    "400 parkway cir": (38.54465, -121.7610),
    "320 parkway cir": (38.54485, -121.76095),
    "310 parkway": (38.5447, -121.7612),
    "301 citron st": (38.54135, -121.77585),
    "298 citron st": (38.5414, -121.7759),
    "298 celadon st": (38.54125, -121.7760),
    "298 horizon st": (38.5415, -121.7757),
    "184 horizon st": (38.54145, -121.77575),
    "2228 tilia st": (38.5419127, -121.7762152),
    "511 s main st": (38.5449, -121.7398),
}

_BLOCK_SUBSTR = (
    "maryland",
    "college park",
    "bloomsburg",
    "university of maryland",
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _load_cache() -> dict:
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_cache(c: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(c, indent=2), encoding="utf-8")


def _default_rent() -> float:
    data = json.loads(ACS_PATH.read_text(encoding="utf-8"))
    return float(data[-1]["median_rent"])


def _norm_addr_key(addr: str) -> str:
    line = (addr or "").split(",")[0].strip().lower()
    line = " ".join(line.split())
    return line


def _fallback_coord(street: str) -> tuple[float, float] | None:
    s = _expand_line(street)
    key = re.sub(r"[^\w\s]", "", s.lower()).strip()
    key = " ".join(key.split())
    return _LINE_FALLBACK.get(key)


def _expand_line(street: str) -> str:
    s = street.strip()
    s = re.sub(r"\bolive\s+ryder\b", "Olive Dr", s, flags=re.I)
    s = re.sub(r"\bpkwy\b", "Parkway", s, flags=re.I)
    low = s.lower()
    if re.match(r"^\d+\s+parkway\s*$", low):
        s = s + " Cir"
    return s


def _full_query(street: str) -> str:
    s = _expand_line(street)
    low = s.lower()
    if "davis" in low and ("ca" in low or "california" in low):
        return s + ", USA"
    return s + ", Davis, CA, USA"


def _photon_geocode(query: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            "https://photon.komoot.io/api/",
            params={"q": query, "limit": 1},
            headers=HEADERS,
            timeout=25,
        )
        r.raise_for_status()
        feats = (r.json() or {}).get("features") or []
        if not feats:
            return None
        c = feats[0].get("geometry", {}).get("coordinates") or []
        if len(c) < 2:
            return None
        lon, lat = float(c[0]), float(c[1])
        return lat, lon
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return None


def _reject_garbage(street: str) -> bool:
    low = street.strip().lower()
    if not low or len(low) < 3:
        return True
    for b in _BLOCK_SUBSTR:
        if b in low:
            return True
    if low in ("jade", "olive dr", "olive drive"):
        return True
    if low == "j st":
        return True
    return False


def _sample_addr_keys() -> set[str]:
    if not SAMPLE_PATH.is_file():
        return set()
    df = pd.read_csv(SAMPLE_PATH)
    return {_norm_addr_key(str(a)) for a in df["address"].fillna("")}


def main() -> None:
    ACS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    default_rent = _default_rent()
    sample_keys = _sample_addr_keys()

    r = requests.get(API_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    payload = r.json()
    uni_slug = str(payload.get("slug") or "uc-davis")
    landlords = list(payload.get("landlords") or [])

    pairs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for L in landlords:
        slug = str(L.get("slug") or "").strip()
        name = str(L.get("name") or "").strip()
        if not slug:
            continue
        for rev in L.get("reviews") or []:
            pa = str(rev.get("propertyAddress") or "").strip()
            if not pa:
                continue
            key = (pa.lower(), slug)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((pa, slug, name))

    cache = _load_cache()
    locator = Nominatim(user_agent="RentScope/offcampus_ingest", timeout=35)
    geocode = RateLimiter(locator.geocode, min_delay_seconds=1.18)

    def _geocode_line(query: str):
        tries = [
            query,
            query.replace(" Cir,", " Circle,").replace(" cir,", " Circle,"),
            query.replace(", Davis, CA, USA", ", Davis, CA 95616, USA"),
            query.replace(", Davis, CA, USA", ", Davis, CA 95618, USA"),
        ]
        seen_q = set()
        for t in tries:
            t = t.strip()
            if t.lower() in seen_q:
                continue
            seen_q.add(t.lower())
            loc = geocode(t)
            if loc:
                return loc
        latlon = _photon_geocode(query.replace(", USA", "").replace("USA", ""))
        if latlon:
            class _Loc:
                def __init__(self, la, lo):
                    self.latitude = la
                    self.longitude = lo

            return _Loc(latlon[0], latlon[1])
        return None

    rows: list[dict] = []
    for street_raw, slug, landlord_name in pairs:
        if _reject_garbage(street_raw):
            continue
        q = _full_query(street_raw)
        ck = q.strip().lower()
        lat = lon = None
        fb = _fallback_coord(street_raw)
        if fb:
            lat, lon = fb
        elif ck in cache:
            hit = cache[ck]
            if hit.get("skip"):
                latlon = _photon_geocode(q.replace(", USA", ""))
                if latlon:
                    lat, lon = latlon
                    cache[ck] = {"lat": lat, "lon": lon}
                    _save_cache(cache)
                else:
                    continue
            elif hit.get("lat") is None or hit.get("lon") is None:
                continue
            else:
                lat, lon = float(hit["lat"]), float(hit["lon"])
        else:
            loc = _geocode_line(q)
            if not loc:
                latlon = _photon_geocode(q.replace(", USA", ""))
                if latlon:
                    lat, lon = latlon
                else:
                    cache[ck] = {"lat": None, "lon": None, "skip": True}
                    _save_cache(cache)
                    continue
            else:
                lat, lon = float(loc.latitude), float(loc.longitude)
            cache[ck] = {"lat": lat, "lon": lon}
            _save_cache(cache)

        if lat is None or lon is None:
            continue
        d_mi = haversine_km(lat, lon, MEMORIAL_UNION[0], MEMORIAL_UNION[1]) * KM_TO_MI
        if d_mi > MAX_RADIUS_MI:
            continue

        street_disp = _expand_line(street_raw)
        full_addr = f"{street_disp}, Davis, CA"
        if _norm_addr_key(full_addr) in sample_keys:
            continue

        listing_url = f"https://www.offcampusreview.com/landlord/{uni_slug}/{slug}"

        rows.append(
            {
                "address": full_addr,
                "listing_name": f"{landlord_name} @ {street_disp}",
                "rent": int(round(default_rent)),
                "beds": 2,
                "baths": 1,
                "sqft": 800,
                "latitude": lat,
                "longitude": lon,
                "source": "offcampus_review",
                "listing_url": listing_url,
                "property_type": "Apartment",
                "room_type": "unknown",
                "offcampus_slug": slug,
            }
        )

    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(subset=["address", "offcampus_slug"], keep="first")
    df.to_csv(OUT_CSV, index=False)
    print(
        f"OffCampus ingest: {len(df)} pins within {MAX_RADIUS_MI} mi (excl. sample dupes); "
        f"wrote {OUT_CSV.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ingest_offcampus_pins failed: {exc}", file=sys.stderr)
        sys.exit(1)
