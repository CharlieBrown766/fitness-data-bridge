#!/usr/bin/env python3
"""Preview or explicitly publish one exact Fitness v5.1 half-Block release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fitness_data_bridge.errors import FitnessDataBridgeError
from fitness_data_bridge.publisher import dry_run_release, publish_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--sync-timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    try:
        if args.write:
            if args.authorization is None:
                raise FitnessDataBridgeError("--write requires --authorization")
            result = publish_release(
                args.workspace,
                release_path=args.release,
                authorization_path=args.authorization,
                database=args.database,
                sync_timeout_seconds=args.sync_timeout_seconds,
            )
        else:
            result = dry_run_release(
                args.workspace,
                release_path=args.release,
                database=args.database,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, FitnessDataBridgeError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
