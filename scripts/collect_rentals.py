#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
from dotenv import load_dotenv
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_PATH = ROOT / "data" / "sample_rentals.csv"
OUT_PATH = RAW_DIR / "rentals.csv"
ZILLOW_DIRECT_OVERRIDES_PATH = RAW_DIR / "zillow_direct_urls.csv"

RENTCAST_URL = "https://api.rentcast.io/v1/listings/rental/long-term"

# Match RentCast rows to a curated sample pin within this radius (meters) when
# street addresses don't line up exactly (geocode / formatting differences).
_NEAR_MATCH_MAX_M = 160.0

# Zillow Davis rental search: ~5 statute miles around Memorial Union (not CHL).
_MU_LAT = 38.5414268
_MU_LON = -121.7494914
_ZILLOW_DAVIS_REGION_ID = 51659
_MI_TO_LAT_DEG = 1.0 / 69.0
_MI_TO_LON_DEG = 1.0 / (69.0 * math.cos(math.radians(_MU_LAT)))


def _zillow_map_bounds_5mi() -> dict[str, float]:
    d = 5.0
    return {
        "west": _MU_LON - d * _MI_TO_LON_DEG,
        "east": _MU_LON + d * _MI_TO_LON_DEG,
        "south": _MU_LAT - d * _MI_TO_LAT_DEG,
        "north": _MU_LAT + d * _MI_TO_LAT_DEG,
    }


def zillow_davis_rent_search_url(address: str, listing_name: str) -> str:
    """Zillow for-rent search scoped to Davis + ~5 mi window; search term = this pin's address."""
    term = str(address or "").strip() or str(listing_name or "").strip() or "Davis, CA"
    b = _zillow_map_bounds_5mi()
    search_query_state = {
        "pagination": {},
        "isMapVisible": True,
        "mapBounds": {
            "west": b["west"],
            "east": b["east"],
            "south": b["south"],
            "north": b["north"],
        },
        "regionSelection": [{"regionId": _ZILLOW_DAVIS_REGION_ID, "regionType": 6}],
        "filterState": {
            "sort": {"value": "priorityscore"},
            "fr": {"value": True},
            "fsba": {"value": False},
            "fsbo": {"value": False},
            "nc": {"value": False},
            "cmsn": {"value": False},
            "auc": {"value": False},
            "fore": {"value": False},
            "mf": {"value": False},
            "land": {"value": False},
            "manu": {"value": False},
        },
        "isListVisible": True,
        "mapZoom": 12,
        "usersSearchTerm": term,
    }
    payload = json.dumps(search_query_state, separators=(",", ":"))
    return (
        "https://www.zillow.com/davis-ca/rentals/?searchQueryState="
        + quote(payload, safe="")
    )


def _normalize_zillow_host(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    for host in (
        "zillow.industry-data-nonprod.zg-int.net",
        "zillow.industry-data-prod.zg-int.net",
    ):
        u = u.replace(f"https://{host}", "https://www.zillow.com")
        u = u.replace(f"http://{host}", "https://www.zillow.com")
    return u


def _is_zillow_rentals_search_url(url: str) -> bool:
    """True for Zillow map/search rentals URLs, not /homedetails/ or /apartments/ building pages."""
    u = str(url or "").strip().lower()
    if "zillow.com" not in u:
        return False
    if "searchquerystate" in u:
        return True
    if "/homedetails/" in u or "/apartments/" in u:
        return False
    if "/davis-ca/rentals" in u:
        return True
    return False


def _collect_http_urls_from_object(obj: object) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def walk(x: object) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            s = x.strip()
            if s.startswith("http://") or s.startswith("https://"):
                if s not in seen:
                    seen.add(s)
                    found.append(s)

    walk(obj)
    return found


def _score_listing_url_candidate(url: str) -> int:
    """Prefer Zillow property/building pages over generic search or non-Zillow links."""
    u = _normalize_zillow_host(str(url or "").strip())
    if not u:
        return -10_000
    if _is_placeholder_listing_url(u):
        return -9000
    if _is_zillow_rentals_search_url(u):
        return -8000
    ul = u.lower()
    if "zillow.com" in ul:
        if "/homedetails/" in ul:
            return 1000
        if "/apartments/" in ul:
            return 900
        if "/b/" in ul:
            return 850
        return 800
    return 500


def _pick_best_listing_url_from_candidates(candidates: list[str]) -> str:
    best = ""
    best_score = -10_000_000
    for c in candidates:
        c2 = _normalize_zillow_host(c)
        sc = _score_listing_url_candidate(c2)
        if sc > best_score:
            best_score = sc
            best = c2
    return best if best_score > 0 else ""


def _listing_url_from_rentcast_item(item: dict) -> str:
    keys = (
        "url",
        "listingUrl",
        "listing_url",
        "sourceUrl",
        "applicationUrl",
        "detailUrl",
        "providerUrl",
        "listingProviderUrl",
        "listingDetailUrl",
    )
    field_urls: list[str] = []
    for k in keys:
        v = item.get(k)
        if v is not None and str(v).strip():
            field_urls.append(str(v).strip())
    all_urls = field_urls + _collect_http_urls_from_object(item)
    dedup: list[str] = []
    seen: set[str] = set()
    for u in all_urls:
        nu = _normalize_zillow_host(u)
        if nu and nu not in seen:
            seen.add(nu)
            dedup.append(nu)
    picked = _pick_best_listing_url_from_candidates(dedup)
    if _is_placeholder_listing_url(picked) or _is_zillow_rentals_search_url(picked):
        return ""
    return picked


def _needs_listing_url_backfill(url: str) -> bool:
    u = str(url or "").strip()
    if _is_placeholder_listing_url(u):
        return True
    if _is_zillow_rentals_search_url(u):
        return True
    return False


def _apply_zillow_when_no_direct_link(df: pd.DataFrame) -> pd.DataFrame:
    """Fill empty/placeholder listing_url or Zillow *search* URLs with scoped Zillow search (never CHL)."""
    out = df.copy()
    if "listing_url" not in out.columns:
        out["listing_url"] = ""
    for i in out.index:
        u = str(out.loc[i, "listing_url"] if pd.notna(out.loc[i, "listing_url"]) else "").strip()
        u = _normalize_zillow_host(u)
        out.loc[i, "listing_url"] = u
        if _needs_listing_url_backfill(u):
            addr = str(out.loc[i, "address"] if pd.notna(out.loc[i, "address"]) else "")
            name = str(out.loc[i, "listing_name"] if pd.notna(out.loc[i, "listing_name"]) else "")
            out.loc[i, "listing_url"] = zillow_davis_rent_search_url(addr, name)
    return out


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


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _is_placeholder_listing_url(url: str) -> bool:
    u = str(url or "").strip().lower()
    if not u:
        return True
    if "google.com/maps" in u or "maps.google.com" in u:
        return True
    if "goo.gl/maps" in u or "maps.app.goo.gl" in u:
        return True
    if "chl.ucdavis.edu" in u:
        return True
    return False


def _street_compact(addr: str) -> str:
    """Normalize street portion for comparison (Davis rentals)."""
    s = str(addr or "").lower().strip()
    s = re.sub(r",\s*davis.*$", "", s, flags=re.I)
    s = re.sub(r"\b(street|str\.?)\b", "st", s)
    s = re.sub(r"\b(avenue|ave\.?)\b", "ave", s)
    s = re.sub(r"\b(boulevard|blvd\.?)\b", "blvd", s)
    s = re.sub(r"\b(drive|dr\.?)\b", "dr", s)
    s = re.sub(r"\b(lane|ln\.?)\b", "ln", s)
    s = re.sub(r"\b(court|ct\.?)\b", "ct", s)
    s = re.sub(r"\b(road|rd\.?)\b", "rd", s)
    s = re.sub(r"\b(circle|cir\.?)\b", "cir", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _load_zillow_direct_overrides() -> dict[str, str]:
    """Optional CSV: columns address, zillow_url — curated homedetails / apartments links."""
    path = ZILLOW_DIRECT_OVERRIDES_PATH
    if not path.is_file():
        return {}
    try:
        sub = pd.read_csv(path, dtype=str)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return {}
    out: dict[str, str] = {}
    for _, row in sub.iterrows():
        addr = str(row.get("address") or "").strip()
        zu = _normalize_zillow_host(str(row.get("zillow_url") or "").strip())
        if (
            not addr
            or not zu
            or not zu.lower().startswith("http")
            or _is_zillow_rentals_search_url(zu)
        ):
            continue
        out[_street_compact(addr)] = zu
        out[addr.lower().strip()] = zu
    return out


def _merge_zillow_direct_urls(df: pd.DataFrame, overrides: dict[str, str]) -> pd.DataFrame:
    """Apply curated Zillow URLs where the row still has no real listing link or only a Zillow search URL."""
    if not overrides:
        return df
    out = df.copy()
    for i in out.index:
        addr = str(out.loc[i, "address"] if pd.notna(out.loc[i, "address"]) else "")
        if not addr.strip():
            continue
        zu = overrides.get(_street_compact(addr)) or overrides.get(addr.strip().lower())
        if not zu or _is_placeholder_listing_url(zu) or _is_zillow_rentals_search_url(zu):
            continue
        cur = _normalize_zillow_host(
            str(out.loc[i, "listing_url"] if pd.notna(out.loc[i, "listing_url"]) else "").strip()
        )
        if _is_placeholder_listing_url(cur) or _is_zillow_rentals_search_url(cur):
            out.loc[i, "listing_url"] = zu
    return out


def _match_rentcast_row(sample: pd.Series, rc: pd.DataFrame) -> pd.Series | None:
    sig = _street_compact(sample.get("address", ""))
    try:
        s_lat = float(sample["latitude"])
        s_lon = float(sample["longitude"])
    except (TypeError, ValueError):
        return None
    try:
        s_beds = int(sample["beds"])
    except (TypeError, ValueError):
        s_beds = -1

    exact: list[tuple[float, int, pd.Series]] = []
    for _, r in rc.iterrows():
        if sig and _street_compact(r.get("address", "")) == sig:
            d = _haversine_m(s_lat, s_lon, float(r["latitude"]), float(r["longitude"]))
            bed_pen = abs(int(r["beds"]) - s_beds) if s_beds >= 0 else 0
            exact.append((d, bed_pen, r))

    if exact:
        exact.sort(key=lambda t: (t[1], t[0]))
        return exact[0][2]

    near: list[tuple[float, int, pd.Series]] = []
    for _, r in rc.iterrows():
        d = _haversine_m(s_lat, s_lon, float(r["latitude"]), float(r["longitude"]))
        if d <= _NEAR_MATCH_MAX_M:
            try:
                bed_pen = abs(int(r["beds"]) - s_beds) if s_beds >= 0 else 0
            except (TypeError, ValueError):
                bed_pen = 0
            near.append((d, bed_pen, r))

    if not near:
        return None
    near.sort(key=lambda t: (t[1], t[0]))
    return near[0][2]


def _enrich_sample_with_rentcast(df_sample: pd.DataFrame, api_key: str) -> pd.DataFrame:
    """Keep curated sample coordinates/addresses; overlay live URL/rent from RentCast when matched."""
    try:
        rc = _from_rentcast(api_key)
    except Exception as exc:
        print(f"RentCast enrich skipped (using sample CSV only): {exc}", file=sys.stderr)
        return df_sample
    out = df_sample.copy()
    n_hit = 0
    n_url = 0
    for i in out.index:
        sample_row = out.loc[i]
        hit = _match_rentcast_row(sample_row, rc)
        if hit is None:
            continue
        n_hit += 1
        u = _normalize_zillow_host(str(hit.get("listing_url") or "").strip())
        if u and not _is_placeholder_listing_url(u) and not _is_zillow_rentals_search_url(u):
            out.loc[i, "listing_url"] = u
            n_url += 1
        try:
            rnt = float(hit.get("rent") or 0)
            if rnt > 0:
                out.loc[i, "rent"] = rnt
        except (TypeError, ValueError):
            pass
        try:
            out.loc[i, "beds"] = int(hit["beds"])
            out.loc[i, "baths"] = float(hit["baths"])
            sf = float(hit.get("sqft") or 0)
            if sf > 0:
                out.loc[i, "sqft"] = sf
        except (TypeError, ValueError):
            pass
        pt = str(hit.get("property_type") or "").strip()
        if pt and pt.lower() not in ("unknown", "nan"):
            out.loc[i, "property_type"] = pt
        ln = str(hit.get("listing_name") or "").strip()
        if ln:
            cur = str(out.loc[i].get("listing_name") or "").strip()
            if not cur or "(sample)" in cur.lower():
                out.loc[i, "listing_name"] = ln
    print(
        f"RentCast enrich: matched {n_hit}/{len(out)} sample rows; "
        f"{n_url} got a non-placeholder listing URL"
    )
    return out


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
        url = _listing_url_from_rentcast_item(item) if isinstance(item, dict) else ""
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
    zillow_overrides = _load_zillow_direct_overrides()
    df = _from_sample()
    if key:
        df = _enrich_sample_with_rentcast(df, key)
    df = _merge_zillow_direct_urls(df, zillow_overrides)
    df["source"] = "sample_davis_ca"

    # OffCampusReview: reviews attach in merge_offcampus.py to existing rows only — do not add
    # extra map pins from review propertyAddress (duplicates curated listings).

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

    df = _apply_zillow_when_no_direct_link(df)

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
