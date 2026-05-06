#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

STEPS = [
    "collect_rentals.py",
    "collect_osm_amenities.py",
    "fetch_rent_trend.py",
    "score_rentals.py",
    "merge_offcampus.py",
    "build_geojson.py",
]


def main() -> None:
    for name in STEPS:
        path = SCRIPTS / name
        print(f"\n=== {name} ===")
        subprocess.run([sys.executable, str(path)], check=True, cwd=str(ROOT))
    print("\nPipeline finished.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Pipeline stopped: {exc}", file=sys.stderr)
        sys.exit(exc.returncode or 1)
