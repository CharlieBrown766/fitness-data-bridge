#!/usr/bin/env python3
"""Preview or repair Calendar notes and reminders for one half-Block release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fitness_data_bridge.calendar_repair import (  # noqa: E402
    dry_run_calendar_repair,
    repair_calendar_events,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--authorization", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.write:
        if not args.authorization:
            print("--write requires --authorization", file=sys.stderr)
            return 2
        result = repair_calendar_events(
            args.workspace,
            release_path=args.release,
            authorization_path=args.authorization,
        )
    else:
        result = dry_run_calendar_repair(
            args.workspace,
            release_path=args.release,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
