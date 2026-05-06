#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://www.offcampusreview.com/api/schools/uc-davis"
RAW_JSON = ROOT / "data" / "raw" / "offcampus_ucdavis.json"
SCORED = ROOT / "data" / "processed" / "scored_rentals.csv"
SCHOOL_PAGE = "https://www.offcampusreview.com/school/uc-davis"
BRAND = "https://www.offcampusreview.com/"
HEADERS = {
    "User-Agent": "RentScope/rent_scope (https://github.com/Anant-Madhok231/rent-scope; OffCampusReview public API)",
}
MIN_AUTO_SCORE = 5.5
PREVIEW_N = 45
TEXT_MAX = 420

# OffCampusReview sometimes lists the same operator twice (e.g. "Axis" vs "Axis at Davis").
# Absorb these slugs into the canonical record so one pin gets merged reviews + one profile URL.
_SLUG_ABSORB_INTO_CANONICAL = {
    "axis": "axis-at-davis",
}

# CSV hints that should resolve to the canonical slug after merges.
_HINT_SLUG_ALIASES = {
    "axis": "axis-at-davis",
}


def _avg_rating(reviews: list) -> float:
    rs = []
    for r in reviews or []:
        v = r.get("rating")
        if v is None:
            continue
        try:
            rs.append(float(v))
        except (TypeError, ValueError):
            continue
    if not rs:
        return math.nan
    return sum(rs) / len(rs)


def _merge_absorbed_landlords(landlords: list) -> list:
    by_slug = {str(x.get("slug")): x for x in landlords if x.get("slug")}
    remove_slugs: set[str] = set()
    for src_slug, dest_slug in _SLUG_ABSORB_INTO_CANONICAL.items():
        if src_slug not in by_slug or dest_slug not in by_slug:
            continue
        primary = by_slug[dest_slug]
        secondary = by_slug[src_slug]
        seen = {str(r.get("id")) for r in primary.get("reviews") or []}
        for r in secondary.get("reviews") or []:
            rid = str(r.get("id"))
            if rid in seen:
                continue
            seen.add(rid)
            primary.setdefault("reviews", []).append(r)
        revs = primary.get("reviews") or []
        primary["reviewCount"] = len(revs)
        ar = _avg_rating(revs)
        primary["averageRating"] = round(ar, 2) if not math.isnan(ar) else None
        remove_slugs.add(src_slug)
    if not remove_slugs:
        return landlords
    return [L for L in landlords if str(L.get("slug")) not in remove_slugs]

_SYNONYMS = {
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "cir": "circle",
    "ct": "court",
    "ln": "lane",
    "pkwy": "parkway",
    "pky": "parkway",
    "rd": "road",
    "wy": "way",
}

_STOP = frozenset(
    {
        "davis",
        "ca",
        "california",
        "usa",
        "unit",
        "apt",
        "apartment",
        "ste",
        "suite",
    }
)

_STREET_SUFFIXES = frozenset(_SYNONYMS.keys()) | frozenset(_SYNONYMS.values())

# Single-word review addresses without a house number are only used when the
# word is distinctive (not a generic street name that would match many pins).
_BLOCKLIST_SINGLE_STREET_TOKEN = frozenset(
    {
        "olive",
        "main",
        "park",
        "elm",
        "oak",
        "maple",
        "pine",
        "cedar",
        "walnut",
        "first",
        "second",
        "third",
        "fourth",
        "fifth",
        "university",
        "memorial",
        "research",
        "state",
        "washington",
        "jefferson",
        "lincoln",
        "adams",
        "franklin",
        "madison",
        "monroe",
        "jackson",
        "grant",
        "webster",
        "college",
        "campus",
    }
)


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _norm_hint(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip().lower()


def _expand_token(t: str) -> str:
    t = t.lower().strip(".,#")
    return _SYNONYMS.get(t, t)


def _tokenize_address_line(line: str) -> list[str]:
    parts = re.findall(r"[a-z0-9]+", (line or "").lower())
    return [_expand_token(p) for p in parts]


def _leading_house_number(tokens: list[str]) -> tuple[str | None, list[str]]:
    if not tokens:
        return None, []
    t0 = tokens[0]
    if re.match(r"^\d+[a-z]?$", t0, re.I):
        return t0, tokens[1:]
    if re.match(r"^\d+(st|nd|rd|th)$", t0, re.I):
        return t0, tokens[1:]
    return None, tokens


def _content_tokens(tokens: list[str]) -> list[str]:
    out = []
    for t in tokens:
        if t in _STOP:
            continue
        if t in _STREET_SUFFIXES and len(tokens) > 1:
            continue
        out.append(t)
    return out


def _pair_compatible(listing_line: str, review_addr: str) -> bool:
    """
    True if a rental's street line (before city) could be the same place as an
    OffCampusReview review propertyAddress.
    """
    a = _tokenize_address_line(listing_line)
    b = _tokenize_address_line(review_addr)
    if not a or not b:
        return False

    na, ra = _leading_house_number(a)
    nb, rb = _leading_house_number(b)

    if na is not None and nb is not None:
        if na != nb:
            return False
        ca = [t for t in _content_tokens(ra) if len(t) > 1]
        cb = [t for t in _content_tokens(rb) if len(t) > 1]
        sa, sb = set(ca), set(cb)
        if sa & sb:
            return True
        if sa <= sb or sb <= sa:
            return True
        return False

    if na is not None and nb is None:
        rb_rest = _content_tokens(rb)
        meaningful = [t for t in rb_rest if len(t) >= 3]
        listing_set = set(a)
        if len(meaningful) >= 2:
            return all(t in listing_set for t in meaningful)
        if len(meaningful) == 1:
            t = meaningful[0]
            if t in _BLOCKLIST_SINGLE_STREET_TOKEN or len(t) < 4:
                return False
            return t in listing_set
        return False

    if na is None and nb is not None:
        return False

    ca = [t for t in _content_tokens(a) if len(t) >= 3]
    cb = [t for t in _content_tokens(b) if len(t) >= 3]
    if len(cb) >= 2:
        return all(t in set(ca) for t in cb)
    if len(cb) == 1 and len(cb[0]) >= 4:
        return cb[0] in set(ca)
    return False


def _review_property_addresses(L: dict) -> list[str]:
    out: list[str] = []
    for r in L.get("reviews") or []:
        pa = str(r.get("propertyAddress") or "").strip()
        if pa:
            out.append(pa)
    return out


def address_aligns_with_landlord(street_line: str, L: dict) -> bool:
    line = (street_line or "").strip()
    if not line:
        return False
    for pa in _review_property_addresses(L):
        if _pair_compatible(line, pa):
            return True
    return False


def _score_row(address: str, L: dict) -> float:
    line = (address or "").split(",")[0].lower()
    ad_t = _tok(line)
    sc = 0.0
    slug_phrase = (L.get("slug") or "").replace("-", " ").lower()
    name = (L.get("name") or "").lower()
    sc += len(ad_t & _tok(slug_phrase)) * 3.2
    sc += len(ad_t & _tok(name)) * 2.4
    name_words = [w for w in name.split() if len(w) > 3 and w != "davis"]
    if name_words and any(w in line for w in name_words):
        sc += 9.0
    slug_words = [w for w in slug_phrase.split() if len(w) > 3 and w != "davis"]
    if slug_words and any(w in line for w in slug_words):
        sc += 11.0
    for rev in L.get("reviews") or []:
        pa = str(rev.get("propertyAddress") or "")
        if not pa:
            continue
        pl = pa.lower()
        pt = _tok(pa)
        if len(pt & ad_t) >= 2:
            sc += 7.0
        if pl in line or line in pl:
            sc += 8.0
        for w in pa.split():
            wl = w.lower()
            if len(wl) > 4 and wl in line:
                sc += 3.5
    return sc


def _preview_reviews(L: dict) -> list[dict]:
    out = []
    for r in (L.get("reviews") or [])[:PREVIEW_N]:
        t = str(r.get("reviewText") or "").replace("\n", " ").strip()
        if len(t) > TEXT_MAX:
            t = t[: TEXT_MAX - 3] + "..."
        out.append(
            {
                "rating": r.get("rating"),
                "date": r.get("date"),
                "text": t,
                "propertyAddress": r.get("propertyAddress"),
            }
        )
    return out


def main() -> None:
    RAW_JSON.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(API_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    payload = r.json()
    landlords = list(payload.get("landlords") or [])
    landlords = _merge_absorbed_landlords(landlords)
    payload["landlords"] = landlords
    RAW_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    uni_slug = str(payload.get("slug") or "uc-davis")
    by_slug = {str(x.get("slug")): x for x in landlords if x.get("slug")}

    df = pd.read_csv(SCORED)
    if "offcampus_slug" not in df.columns:
        df["offcampus_slug"] = ""
    df["offcampus_slug"] = df["offcampus_slug"].fillna("").astype(str)

    urls = []
    names = []
    slugs = []
    avgs = []
    counts = []
    previews = []
    matched = []

    for _, row in df.iterrows():
        addr = str(row.get("address") or "")
        street_line = addr.split(",")[0].strip()
        hint = _norm_hint(row.get("offcampus_slug"))
        if hint in _HINT_SLUG_ALIASES:
            hint = _HINT_SLUG_ALIASES[hint]
        chosen = None
        if hint and hint in by_slug:
            cand = by_slug[hint]
            if address_aligns_with_landlord(street_line, cand):
                chosen = cand
        if chosen is None:
            aligned = [
                L for L in landlords if address_aligns_with_landlord(street_line, L)
            ]
            best = None
            best_s = 0.0
            for L in aligned:
                s = _score_row(addr, L)
                if s > best_s:
                    best_s = s
                    best = L
            if best is not None and best_s >= MIN_AUTO_SCORE:
                chosen = best

        if chosen is None:
            urls.append("")
            names.append("")
            slugs.append("")
            avgs.append(math.nan)
            counts.append(0)
            previews.append("[]")
            matched.append(False)
            continue

        slug = str(chosen.get("slug"))
        name = str(chosen.get("name"))
        url = f"https://www.offcampusreview.com/landlord/{uni_slug}/{slug}"
        avg = chosen.get("averageRating")
        rc = int(chosen.get("reviewCount") or 0)
        pv = json.dumps(_preview_reviews(chosen))
        urls.append(url)
        names.append(name)
        slugs.append(slug)
        avgs.append(float(avg) if avg is not None else math.nan)
        counts.append(rc)
        previews.append(pv)
        matched.append(True)

    df["offcampus_school_url"] = SCHOOL_PAGE
    df["offcampus_brand_url"] = BRAND
    df["offcampus_landlord_url"] = urls
    df["offcampus_landlord_name"] = names
    df["offcampus_landlord_slug"] = slugs
    df["offcampus_avg_rating"] = avgs
    df["offcampus_review_count"] = counts
    df["offcampus_reviews_json"] = previews
    df["offcampus_match"] = matched

    df.to_csv(SCORED, index=False)
    n = sum(matched)
    print(
        f"OffCampusReview: matched {n}/{len(df)} rows (address-aligned); raw API saved to {RAW_JSON.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"merge_offcampus failed: {exc}", file=sys.stderr)
        sys.exit(1)
