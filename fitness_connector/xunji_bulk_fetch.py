#!/usr/bin/env python3
"""Fetch Xunji training records for a date range into the local cache."""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

from .xunji_client import (
    XunjiError,
    fetch_with_cache,
)
from .layout import default_api_cache_dir, default_api_key_file


DEFAULT_START = "2020-01-01"
SHANGHAI_TZ = timezone(timedelta(hours=8))
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def shanghai_today() -> str:
    return datetime.now(SHANGHAI_TZ).date().isoformat()


def parse_date(value: str) -> date:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        raise XunjiError(f"date must use strict YYYY-MM-DD: {value}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise XunjiError(f"date must use strict YYYY-MM-DD: {value}") from exc


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current.isoformat()
        current += timedelta(days=1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Xunji app training records for every day in a date range.",
    )
    parser.add_argument(
        "--workspace",
        help=(
            "Fitness workspace root. Defaults to FITNESS_WORKSPACE or discovery "
            "from the current directory."
        ),
    )
    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        help=f"Start date in YYYY-MM-DD format. Default: {DEFAULT_START}",
    )
    parser.add_argument(
        "--end",
        default=shanghai_today(),
        help="End date in YYYY-MM-DD format. Default: today's Asia/Shanghai date.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh cached days too. Too-frequent same-day refreshes are refused by the client.",
    )
    parser.add_argument(
        "--include-full-data",
        action="store_true",
        help="Request full v2 data, including unchecked sets, RPE, notes, side weights, durations, and rest seconds.",
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help=(
            "Training-only compatibility path. By default use the training slot "
            "from <workspace>/运行/private/credentials.json."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "Directory for cached API results. Defaults to the resolved "
            "workspace runtime cache."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Optional delay between API requests in seconds.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N days. Default: 100.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        start = parse_date(args.start)
        end = parse_date(args.end)
        if start > end:
            raise XunjiError("start date must be before or equal to end date")
    except XunjiError as exc:
        print(f"error: {exc}")
        return 1

    total_days = (end - start).days + 1
    api_key_file = args.api_key_file or default_api_key_file(args.workspace)
    cache_dir = args.cache_dir or default_api_cache_dir(args.workspace)
    stats = {
        "cache": 0,
        "api": 0,
        "errors": 0,
        "nonempty_days": 0,
        "records": 0,
    }
    nonempty_dates: list[str] = []
    errors: list[tuple[str, str]] = []

    for index, datestr in enumerate(iter_dates(start, end), start=1):
        try:
            payload, source, _ = fetch_with_cache(
                datestr=datestr,
                api_key_file=api_key_file,
                cache_dir=cache_dir,
                refresh=args.refresh,
                timeout=args.timeout,
                include_full_data=args.include_full_data,
            )
            res = payload.get("response", {}).get("res", {})
            trains = res.get("trains", []) if isinstance(res, dict) else []
            count = len(trains)
            stats[source] = stats.get(source, 0) + 1
            stats["records"] += count
            if count:
                stats["nonempty_days"] += 1
                nonempty_dates.append(datestr)
                print(f"{datestr}\t{source}\t{count}")
            if source == "api" and args.sleep > 0:
                time.sleep(args.sleep)
        except XunjiError as exc:
            stats["errors"] += 1
            errors.append((datestr, str(exc)))
            print(f"{datestr}\terror\t{exc}")

        if args.progress_every > 0 and index % args.progress_every == 0:
            print(
                f"progress\t{index}/{total_days}\tapi={stats['api']}\tcache={stats['cache']}\t"
                f"nonempty={stats['nonempty_days']}\terrors={stats['errors']}"
            )

    print(
        f"done\t{total_days} days\tapi={stats['api']}\tcache={stats['cache']}\t"
        f"nonempty_days={stats['nonempty_days']}\trecords={stats['records']}\terrors={stats['errors']}"
    )
    if nonempty_dates:
        print(f"nonempty_range\t{nonempty_dates[0]}\t{nonempty_dates[-1]}")
    if errors:
        print("errors")
        for datestr, message in errors:
            print(f"{datestr}\t{message}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
