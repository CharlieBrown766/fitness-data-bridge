#!/usr/bin/env python3
"""Fetch Xunji training records with local date-based caching."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

from .layout import default_api_cache_dir, default_api_key_file
from .xunji_credentials import (
    CredentialError,
    credential_account_scope,
    load_credential,
)
from .xunji_transport import (
    XunjiOpenApiError,
    post_json,
    redact_secrets,
)


BASE_URL = "https://trains.xunjiapp.cn"
TRAINS_PATH = "/api_trains_for_llm_v2"
SCHEMA_VERSION = "train_open_api_v2"
MIN_REFETCH_SECONDS = 15
MIN_REFETCH_FULL_SECONDS = 30
ACCOUNT_SCOPE_RE = re.compile(r"^training-[a-f0-9]{24}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHANGHAI_TZ = timezone(timedelta(hours=8))

DEFAULT_API_KEY_FILE = default_api_key_file()
DEFAULT_CACHE_DIR = default_api_cache_dir()


class XunjiError(RuntimeError):
    """A user-facing Xunji client error."""


def shanghai_today() -> str:
    return datetime.now(SHANGHAI_TZ).date().isoformat()


def validate_datestr(datestr: str) -> str:
    if not isinstance(datestr, str) or DATE_PATTERN.fullmatch(datestr) is None:
        raise XunjiError(
            "datestr must use strict YYYY-MM-DD, for example 2026-04-02"
        )
    try:
        date.fromisoformat(datestr)
    except ValueError as exc:
        raise XunjiError(
            "datestr must use strict YYYY-MM-DD, for example 2026-04-02"
        ) from exc
    return datestr


def load_api_key(path: Path) -> str:
    try:
        return load_credential("training", credential_file=path)
    except CredentialError as exc:
        raise XunjiError(str(exc)) from exc


def cache_file_for(
    cache_dir: Path,
    datestr: str,
    include_full_data: bool = False,
    *,
    account_scope: str,
) -> Path:
    datestr = validate_datestr(datestr)
    if not isinstance(account_scope, str) or not ACCOUNT_SCOPE_RE.fullmatch(
        account_scope
    ):
        raise XunjiError("Invalid Xunji training account cache scope")
    suffix = ".full" if include_full_data else ""
    return (
        cache_dir
        / "account-scopes"
        / account_scope
        / f"{datestr}{suffix}.json"
    )


def load_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def decode_response(raw: bytes, encoding: str | None) -> str:
    if encoding and "gzip" in encoding.lower():
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")


def parse_error_body(error: HTTPError) -> str:
    try:
        body = decode_response(error.read(), error.headers.get("Content-Encoding"))
    except Exception:
        return str(error)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return redact_secrets(body.strip() or str(error))

    message = (
        payload.get("message")
        or payload.get("error")
        or json.dumps(payload, ensure_ascii=False)
    )
    return redact_secrets(message)


def fetch_from_api(datestr: str, api_key: str, timeout: float, include_full_data: bool = False) -> dict:
    try:
        payload = post_json(
            url=BASE_URL + TRAINS_PATH,
            body={
                "schema_version": SCHEMA_VERSION,
                "datestr": datestr,
                "include_full_data": include_full_data,
            },
            credential=api_key,
            timeout=timeout,
            user_agent="codex-xunji-client/2.0",
            success_required=False,
        ).payload
    except XunjiOpenApiError as exc:
        raise XunjiError(str(exc)) from exc

    res = payload.get("res")
    if not isinstance(res, dict) or not isinstance(res.get("trains"), list):
        raise XunjiError("Xunji API response does not contain a list field named 'res.trains'")

    return payload


def fetch_with_cache(
    datestr: str,
    api_key_file: Path,
    cache_dir: Path,
    refresh: bool,
    timeout: float,
    include_full_data: bool = False,
) -> tuple[dict, str, Path]:
    datestr = validate_datestr(datestr)
    api_key = load_api_key(api_key_file)
    account_scope = credential_account_scope("training", api_key)
    cache_path = cache_file_for(
        cache_dir,
        datestr,
        include_full_data=include_full_data,
        account_scope=account_scope,
    )
    cached = load_cache(cache_path)

    if cached and not refresh:
        return cached, "cache", cache_path

    if cached and refresh:
        fetched_at_epoch = cached.get("fetched_at_epoch")
        if isinstance(fetched_at_epoch, (int, float)):
            age = time.time() - fetched_at_epoch
            min_refetch = MIN_REFETCH_FULL_SECONDS if include_full_data else MIN_REFETCH_SECONDS
            if age < min_refetch:
                wait = int(min_refetch - age) + 1
                raise XunjiError(
                    f"cached result is only {int(age)}s old; use the cache or retry refresh after {wait}s"
                )

    response = fetch_from_api(
        datestr,
        api_key,
        timeout=timeout,
        include_full_data=include_full_data,
    )
    payload = {
        "endpoint": TRAINS_PATH,
        "schema_version": SCHEMA_VERSION,
        "datestr": datestr,
        "include_full_data": include_full_data,
        "fetched_at": datetime.now(SHANGHAI_TZ).strftime(
            "%Y-%m-%d %H:%M:%S %z"
        ),
        "fetched_at_epoch": time.time(),
        "response": response,
    }
    write_cache(cache_path, payload)
    return payload, "api", cache_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Xunji app training records and cache them locally by training date.",
    )
    parser.add_argument(
        "--workspace",
        help=(
            "Fitness workspace root. Defaults to FITNESS_WORKSPACE or discovery "
            "from the current directory."
        ),
    )
    parser.add_argument(
        "datestr",
        nargs="?",
        default=shanghai_today(),
        help="Training date in YYYY-MM-DD format. Defaults to today's Asia/Shanghai date.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Request the API again instead of using a cached response. Refuses too-frequent same-day refreshes.",
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
        "--json",
        action="store_true",
        dest="print_json",
        help="Print the cached wrapper JSON instead of only training lines.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        api_key_file = args.api_key_file or default_api_key_file(args.workspace)
        cache_dir = args.cache_dir or default_api_cache_dir(args.workspace)
        payload, source, cache_path = fetch_with_cache(
            datestr=args.datestr,
            api_key_file=api_key_file,
            cache_dir=cache_dir,
            refresh=args.refresh,
            timeout=args.timeout,
            include_full_data=args.include_full_data,
        )
    except XunjiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.print_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    response = payload["response"]
    trains = response["res"]["trains"]
    print(f"source: {source}")
    print(f"cache: {cache_path}")
    print(f"datestr: {payload['datestr']}")
    print(f"include_full_data: {payload['include_full_data']}")
    print(f"count: {len(trains)}")
    for train in trains:
        if not isinstance(train, dict):
            print(json.dumps(train, ensure_ascii=False))
            continue
        movements = train.get("movements") if isinstance(train.get("movements"), list) else []
        print(
            "\t".join(
                [
                    str(train.get("datestr", payload["datestr"])),
                    f"localid={train.get('localid', '')}",
                    f"title={train.get('title', '')}",
                    f"movements={len(movements)}",
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
