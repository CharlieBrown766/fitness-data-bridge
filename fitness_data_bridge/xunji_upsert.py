#!/usr/bin/env python3
"""Upsert Xunji training records with Open API v2 JSON payloads."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import WriteGateError
from .layout import (
    default_api_key_file,
    default_upsert_cache_dir,
)
from .xunji_credentials import credential_account_scope
from .xunji_client import (
    BASE_URL,
    SCHEMA_VERSION,
    XunjiError,
    cache_file_for,
    load_cache,
    load_api_key,
    validate_datestr,
    write_cache,
)
from .xunji_transport import XunjiOpenApiError, post_json


UPSERT_PATH = "/api_upsert_trains_for_llm_v2"
MAX_TRAINS = 4
MAX_MOVEMENTS = 15
MAX_SETS = 20
MIN_UPSERT_SECONDS = 45
SHANGHAI_TZ = timezone(timedelta(hours=8))
RPE_VALUES = {
    "6",
    "6.5",
    "7",
    "7.5",
    "8",
    "8.5",
    "9",
    "9.5",
    "10",
    "",
}
DIFFICULTY_VALUES = {"easy", "normal", "hard"}
SET_MEASUREMENT_FIELDS = {
    "weight",
    "weight_kg",
    "reps",
    "time",
    "duration_s",
    "selfWeight",
}


def _validate_set_payload(
    item: object,
    *,
    context: str,
    depth: int = 0,
) -> None:
    if not isinstance(item, dict):
        raise XunjiError(f"{context} must be an object")
    if depth > 4:
        raise XunjiError(f"{context} nesting is too deep")

    rpe = item.get("rpe")
    if rpe is not None and rpe not in RPE_VALUES:
        raise XunjiError(f"{context} has invalid rpe")

    has_measurement = any(
        field in item and item[field] not in (None, "")
        for field in SET_MEASUREMENT_FIELDS
    )
    has_valid_nested_measurement = False

    if "items" in item:
        nested_items = item["items"]
        if not isinstance(nested_items, list) or not nested_items:
            raise XunjiError(f"{context}.items must be a non-empty list")
        for item_index, nested in enumerate(nested_items):
            if not isinstance(nested, dict) or not isinstance(
                nested.get("set"), dict
            ):
                raise XunjiError(
                    f"{context}.items[{item_index}].set must be an object"
                )
            _validate_set_payload(
                nested["set"],
                context=f"{context}.items[{item_index}].set",
                depth=depth + 1,
            )
        has_valid_nested_measurement = True

    if "dropSets" in item:
        drop_sets = item["dropSets"]
        if not isinstance(drop_sets, list) or not drop_sets:
            raise XunjiError(f"{context}.dropSets must be a non-empty list")
        for drop_index, nested in enumerate(drop_sets):
            if not isinstance(nested, dict):
                raise XunjiError(
                    f"{context}.dropSets[{drop_index}] must be an object"
                )
            nested_set = nested.get("set", nested)
            _validate_set_payload(
                nested_set,
                context=f"{context}.dropSets[{drop_index}]",
                depth=depth + 1,
            )
        has_valid_nested_measurement = True

    if not has_measurement and not has_valid_nested_measurement:
        raise XunjiError(f"{context} needs a measurement field")


def read_json_source(args: argparse.Namespace) -> object:
    if bool(args.file) == bool(args.stdin):
        raise XunjiError("choose exactly one input source: --file or --stdin")

    if args.file:
        if not args.file.exists():
            raise XunjiError(f"input file not found: {args.file}")
        text = args.file.read_text(encoding="utf-8-sig")
    else:
        text = sys.stdin.read()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise XunjiError(f"input is not valid JSON: {exc}") from exc


def extract_trains(payload: object) -> list[dict]:
    trains: object
    if isinstance(payload, list):
        trains = payload
    elif isinstance(payload, dict):
        res = payload.get("res")
        if isinstance(res, list):
            trains = res
        elif isinstance(res, dict) and isinstance(res.get("trains"), list):
            trains = res["trains"]
        elif isinstance(payload.get("trains"), list):
            trains = payload["trains"]
        else:
            raise XunjiError(
                "JSON must be a train array, {'res': [...]}, "
                "{'res': {'trains': [...]}} or {'trains': [...]}"
            )
    else:
        raise XunjiError("JSON root must be an object or array")

    if not trains:
        raise XunjiError("res must contain at least one train")
    if len(trains) > MAX_TRAINS:
        raise XunjiError(f"single v2 upsert can contain at most {MAX_TRAINS} trains")
    if not all(isinstance(train, dict) for train in trains):
        raise XunjiError("each train must be a JSON object")
    return trains


def validate_trains(trains: list[dict]) -> str:
    dates = []
    for train in trains:
        datestr = train.get("datestr")
        if not isinstance(datestr, str):
            raise XunjiError("each train must contain a string datestr")
        dates.append(validate_datestr(datestr))

        movements = train.get("movements", [])
        if movements is None:
            movements = []
        if not isinstance(movements, list):
            raise XunjiError(f"{datestr}: movements must be a list")
        if len(movements) > MAX_MOVEMENTS:
            raise XunjiError(f"{datestr}: each train can contain at most {MAX_MOVEMENTS} movements")

        for movement in movements:
            if not isinstance(movement, dict):
                raise XunjiError(f"{datestr}: each movement must be an object")
            if not isinstance(movement.get("name"), str) or not movement["name"].strip():
                raise XunjiError(f"{datestr}: each movement must contain a Chinese name")
            if "key" in movement:
                raise XunjiError(
                    f"{datestr}: movement {movement.get('name')} must not contain an internal key"
                )
            difficulty = movement.get("difficulty")
            if difficulty is not None and difficulty not in DIFFICULTY_VALUES:
                raise XunjiError(
                    f"{datestr}: movement {movement.get('name')} difficulty must be easy, normal, or hard"
                )
            if movement.get("cardio") is True:
                if "sets" in movement:
                    raise XunjiError(
                        f"{datestr}: native cardio movement {movement.get('name')} must not contain sets"
                    )
                if not isinstance(movement.get("metrics"), dict):
                    raise XunjiError(
                        f"{datestr}: native cardio movement {movement.get('name')} requires top-level metrics"
                    )
                continue
            sets = movement.get("sets", [])
            if sets is None:
                sets = []
            if not isinstance(sets, list):
                raise XunjiError(f"{datestr}: movement {movement.get('name')} sets must be a list")
            if len(sets) > MAX_SETS:
                raise XunjiError(f"{datestr}: movement {movement.get('name')} can contain at most {MAX_SETS} sets")
            for set_index, item in enumerate(sets):
                _validate_set_payload(
                    item,
                    context=(
                        f"{datestr}: movement {movement.get('name')} "
                        f"set {set_index + 1}"
                    ),
                )

    unique_dates = sorted(set(dates))
    if len(unique_dates) != 1:
        raise XunjiError(f"all trains must be in the same datestr: {', '.join(unique_dates)}")
    return unique_dates[0]


def build_request_body(
    trains: list[dict],
    client_request_id: str,
    dry_run: bool,
    include_full_data: bool,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "client_request_id": client_request_id,
        "dry_run": dry_run,
        "include_full_data": include_full_data,
        "res": trains,
    }


def post_upsert(body: dict, api_key: str, timeout: float) -> dict:
    if body.get("dry_run") is not True:
        raise XunjiError(
            "Real training API writes are disabled in Fitness Planner 0.8.6; "
            "only dry_run=true may reach the official upsert endpoint"
        )
    try:
        payload = post_json(
            url=BASE_URL + UPSERT_PATH,
            body=body,
            credential=api_key,
            timeout=timeout,
            user_agent="codex-xunji-upsert/3.0",
            success_required=False,
        ).payload
    except XunjiOpenApiError as exc:
        raise XunjiError(str(exc)) from exc
    response_trains(payload)
    return payload


def response_trains(response: dict) -> list[dict]:
    res = response.get("res")
    trains: object
    if isinstance(res, list):
        trains = res
    elif isinstance(res, dict):
        trains = res.get("trains")
    else:
        trains = None
    if not isinstance(trains, list) or not all(
        isinstance(item, dict) for item in trains
    ):
        raise XunjiError(
            "Xunji upsert response does not contain standardized training records"
        )
    return trains


def response_train_count(response: dict) -> int:
    return len(response_trains(response))


def enforce_upsert_interval(
    cache_dir: Path,
    datestr: str,
    *,
    account_scope: str,
    clock=time.time,
) -> Path:
    scoped_root = cache_file_for(
        cache_dir,
        datestr,
        account_scope=account_scope,
    ).parent
    marker_path = scoped_root / "rate-limits" / f"{datestr}.json"
    marker = load_cache(marker_path)
    now_epoch = float(clock())
    if marker:
        previous = marker.get("requested_at_epoch")
        if isinstance(previous, (int, float)):
            age = now_epoch - float(previous)
            if age < MIN_UPSERT_SECONDS:
                wait_ms = int((MIN_UPSERT_SECONDS - age) * 1000) + 1
                raise XunjiError(
                    f"training upsert for {datestr} is locally rate-limited; "
                    f"retry after {wait_ms} ms"
                )
    write_cache(
        marker_path,
        {
            "schema_version": 1,
            "endpoint": UPSERT_PATH,
            "datestr": datestr,
            "requested_at_epoch": now_epoch,
        },
    )
    return marker_path


def cache_upsert_result(
    datestr: str,
    response: dict,
    request_body: dict,
    upsert_cache_dir: Path,
    *,
    account_scope: str,
) -> Path:
    if request_body.get("dry_run") is not True:
        raise XunjiError(
            "Only dry-run training responses may enter the upsert cache"
        )
    now = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S %z")
    now_epoch = time.time()
    include_full_data = bool(request_body.get("include_full_data"))
    wrapper = {
        "endpoint": UPSERT_PATH,
        "schema_version": SCHEMA_VERSION,
        "datestr": datestr,
        "include_full_data": include_full_data,
        "dry_run": bool(request_body.get("dry_run")),
        "fetched_at": now,
        "fetched_at_epoch": now_epoch,
        "response": response,
    }

    upsert_path = cache_file_for(
        upsert_cache_dir,
        datestr,
        include_full_data=include_full_data,
        account_scope=account_scope,
    )
    write_cache(upsert_path, wrapper)
    return upsert_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upsert Xunji app training records from Open API v2 JSON.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="UTF-8 JSON file containing a train array or v2 payload.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON from stdin.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate locally without calling the API. This is also the default.",
    )
    parser.add_argument(
        "--api-dry-run",
        action="store_true",
        help="Call the API with dry_run=true. Use before a confirmed write when practical.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Reserved compatibility flag. Real training API writes are disabled "
            "in 0.8.6 because no current executable artifact projection is authorized."
        ),
    )
    parser.add_argument("--workspace")
    parser.add_argument("--manifest", help=argparse.SUPPRESS)
    parser.add_argument("--authorization", help=argparse.SUPPRESS)
    parser.add_argument("--operation-id", help=argparse.SUPPRESS)
    parser.add_argument("--sync-timeout", type=int, default=180, help=argparse.SUPPRESS)
    parser.add_argument(
        "--include-full-data",
        action="store_true",
        help="Ask the API to return full standardized data after upsert.",
    )
    parser.add_argument(
        "--client-request-id",
        help="Stable request id. Defaults to codex-<datestr>-<epoch>.",
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
        "--upsert-cache-dir",
        type=Path,
        help=(
            "Directory for dry-run response cache. Defaults to the resolved "
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
        help="Print the full response wrapper JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.write:
            raise WriteGateError(
                "Real training API writes are disabled in Fitness Planner 0.8.6. "
                "The active profile has no executable artifact-to-Open-API "
                "projection, and the protected Phase2 is read-only."
            )
        payload = read_json_source(args)
        trains = extract_trains(payload)
        datestr = validate_trains(trains)
        client_request_id = args.client_request_id or f"codex-{datestr}-{int(time.time())}"
        request_body = build_request_body(
            trains=trains,
            client_request_id=client_request_id,
            dry_run=True,
            include_full_data=bool(args.include_full_data),
        )

        if not args.api_dry_run:
            print(json.dumps(request_body, ensure_ascii=False, indent=2))
            return 0

        api_key_path = args.api_key_file or default_api_key_file(args.workspace)
        api_key = load_api_key(api_key_path)
        account_scope = credential_account_scope("training", api_key)
        upsert_cache_dir = (
            args.upsert_cache_dir or default_upsert_cache_dir(args.workspace)
        )
        enforce_upsert_interval(
            upsert_cache_dir,
            datestr,
            account_scope=account_scope,
        )
        response = post_upsert(request_body, api_key=api_key, timeout=args.timeout)
        upsert_path = cache_upsert_result(
            datestr=datestr,
            response=response,
            request_body=request_body,
            upsert_cache_dir=upsert_cache_dir,
            account_scope=account_scope,
        )
    except (XunjiError, WriteGateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    wrapper = {
        "endpoint": UPSERT_PATH,
        "schema_version": SCHEMA_VERSION,
        "datestr": datestr,
        "dry_run": request_body["dry_run"],
        "response": response,
        "upsert_cache": str(upsert_path),
        "read_cache": None,
    }
    if args.print_json:
        print(json.dumps(wrapper, ensure_ascii=False, indent=2))
        return 0

    print(f"datestr: {datestr}")
    print(f"dry_run: {request_body['dry_run']}")
    print(f"count: {response_train_count(response)}")
    print(f"upsert_cache: {upsert_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
