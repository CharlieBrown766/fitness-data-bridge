#!/usr/bin/env python3
"""Inspect or request EventKit Full Calendar Access on the target Mac."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fitness_data_bridge.calendar_eventkit import (  # noqa: E402
    eventkit_status,
    request_eventkit_access,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", action="store_true")
    args = parser.parse_args()
    result = request_eventkit_access() if args.request else eventkit_status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
