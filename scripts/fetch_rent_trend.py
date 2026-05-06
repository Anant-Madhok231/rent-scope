#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "data" / "processed" / "davis_median_rent_acs.json"
DOCS_JSON = ROOT / "docs" / "market_trend.json"

STATE = "06"
COUNTY = "113"
YEARS = list(range(2015, 2025))

HEADERS = {
    "User-Agent": "RentScope/rent_scope (https://github.com/Anant-Madhok231/rent-scope; ACS B25064 Yolo County)",
}


def main() -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    series = []
    for year in YEARS:
        url = (
            f"https://api.census.gov/data/{year}/acs/acs5"
            f"?get=NAME,B25064_001E&for=county:{COUNTY}&in=state:{STATE}"
        )
        try:
            r = requests.get(url, timeout=45, headers=HEADERS)
            r.raise_for_status()
            data = r.json()
            if len(data) >= 2:
                val = data[1][1]
                if val and val not in ("-666666666", "-", None):
                    series.append({"year": year, "median_rent": int(float(val))})
        except (requests.RequestException, ValueError, IndexError, TypeError):
            continue
    series.sort(key=lambda x: x["year"])
    OUT_JSON.write_text(json.dumps(series, indent=2), encoding="utf-8")
    DOCS_JSON.parent.mkdir(parents=True, exist_ok=True)
    DOCS_JSON.write_text(json.dumps(series, indent=2), encoding="utf-8")
    print(f"Wrote {len(series)} ACS points to {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"fetch_rent_trend failed: {exc}", file=sys.stderr)
        sys.exit(1)
