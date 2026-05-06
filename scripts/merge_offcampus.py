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
PREVIEW_N = 8
TEXT_MAX = 220


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _norm_hint(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip().lower()
    return s


def _score_row(address: str, L: dict) -> float:
    line = (address or "").split(",")[0].lower()
    ad_t = _tok(line)
    sc = 0.0
    slug_phrase = (L.get("slug") or "").replace("-", " ").lower()
    name = (L.get("name") or "").lower()
    sc += len(ad_t & _tok(slug_phrase)) * 3.2
    sc += len(ad_t & _tok(name)) * 2.4
    if name and name in line:
        sc += 9.0
    if slug_phrase and slug_phrase in line:
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
    RAW_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    uni_slug = str(payload.get("slug") or "uc-davis")
    landlords = payload.get("landlords") or []
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
        hint = _norm_hint(row.get("offcampus_slug"))
        chosen = None
        if hint and hint in by_slug:
            chosen = by_slug[hint]
        else:
            best = None
            best_s = 0.0
            for L in landlords:
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
    print(f"OffCampusReview: matched {n}/{len(df)} rows; raw API saved to {RAW_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"merge_offcampus failed: {exc}", file=sys.stderr)
        sys.exit(1)
