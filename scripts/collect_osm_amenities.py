#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = RAW_DIR / "amenities.geojson"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

USER_AGENT = "RentScope/rent_scope (https://github.com/Anant-Madhok231/rent-scope; Davis CA amenities fetch)"

BBOX_S, BBOX_W, BBOX_N, BBOX_E = 38.52, -121.79, 38.59, -121.68

QUERY = f"""
[out:json][timeout:180];
(
  nwr["shop"="supermarket"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["shop"="convenience"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="cafe"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["leisure"="fitness_centre"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["leisure"="park"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="library"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["public_transport"="stop_position"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["public_transport"="platform"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["highway"="bus_stop"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["railway"="tram_stop"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["railway"="station"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="bus_station"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="restaurant"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="fast_food"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
  nwr["amenity"="pharmacy"]({BBOX_S},{BBOX_W},{BBOX_N},{BBOX_E});
);
out center;
"""


def _is_unitrans(tags: dict) -> bool:
    if not tags:
        return False
    net = str(tags.get("network") or "").lower()
    op = str(tags.get("operator") or "").lower()
    name = str(tags.get("name") or "").lower()
    if "unitrans" in net or "unitrans" in op:
        return True
    if tags.get("operator") == "ASUCD" and "bus" in name:
        return True
    if net == "unitrans":
        return True
    return False


def _classify(tags: dict) -> str | None:
    if not tags:
        return None
    hw = tags.get("highway")
    pt = tags.get("public_transport")
    if hw == "bus_stop" or pt in ("stop_position", "platform"):
        return "unitrans" if _is_unitrans(tags) else "transit"
    if tags.get("railway") in ("tram_stop", "station") or tags.get("amenity") == "bus_station":
        return "transit"
    if tags.get("shop") == "supermarket":
        return "grocery"
    if tags.get("shop") == "convenience":
        return "convenience"
    if tags.get("amenity") == "cafe":
        return "cafe"
    if tags.get("leisure") == "fitness_centre":
        return "gym"
    if tags.get("leisure") == "park":
        return "park"
    if tags.get("amenity") == "library":
        return "library"
    if tags.get("amenity") in ("restaurant", "fast_food"):
        return "restaurant"
    if tags.get("amenity") == "pharmacy":
        return "pharmacy"
    return None


def _element_to_feature(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    kind = _classify(tags)
    if not kind:
        return None

    if "lat" in el and "lon" in el:
        lon, lat = el["lon"], el["lat"]
    elif "center" in el:
        c = el["center"]
        lon, lat = c["lon"], c["lat"]
    else:
        return None

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "amenity_type": kind,
            "name": tags.get("name") or "",
            "network": tags.get("network") or "",
            "operator": tags.get("operator") or "",
            "osm_type": el.get("type"),
            "osm_id": el.get("id"),
        },
    }


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    resp = None
    last_err = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": QUERY}, timeout=300, headers=headers)
            r.raise_for_status()
            resp = r
            break
        except requests.RequestException as exc:
            last_err = exc
            continue
    if resp is None:
        raise last_err or RuntimeError("Overpass request failed")
    payload = resp.json()
    elements = payload.get("elements") or []
    features = []
    for el in elements:
        feat = _element_to_feature(el)
        if feat:
            features.append(feat)

    collection = {"type": "FeatureCollection", "features": features}
    OUT_PATH.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    print(f"Wrote {len(features)} features to {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"collect_osm_amenities failed: {exc}", file=sys.stderr)
        sys.exit(1)
