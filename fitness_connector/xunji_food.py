"""Read Xunji food data through one food capability and two API clients."""

from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from .io import atomic_write_json
from .layout import WorkspaceLayout, resolve_workspace
from .xunji_open_api import (
    FOOD_RECORDS_QUERY,
    FOOD_SEARCH,
    FOOD_TEMPLATES_LIST,
    SHANGHAI_TZ,
    query_with_cache,
)
from .xunji_transport import XunjiOpenApiError


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_SEARCH_LIMIT = 50


class XunjiFoodError(XunjiOpenApiError):
    """Food request, response, or governance validation failed."""


def _parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        raise XunjiFoodError(f"{field} must use strict YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise XunjiFoodError(f"{field} must use strict YYYY-MM-DD") from exc


def _calendar_year_before(value: date) -> date:
    year = value.year - 1
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return date(year, value.month, day)


def _calendar_months_after(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _validate_query_window(
    start_date: str,
    end_date: str,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    start = _parse_date(start_date, field="start_date")
    end = _parse_date(end_date, field="end_date")
    if start > end:
        raise XunjiFoodError("start_date must not be after end_date")
    current = today or datetime.now(SHANGHAI_TZ).date()
    earliest = _calendar_year_before(current)
    latest = _calendar_months_after(current, 3)
    if start < earliest:
        raise XunjiFoodError(
            f"Food query start_date must not be earlier than {earliest.isoformat()}"
        )
    if end > latest:
        raise XunjiFoodError(
            f"Food query end_date must not be later than {latest.isoformat()}"
        )
    return start, end


def _validate_food_response(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("res"), (dict, list)):
        raise XunjiFoodError("Food API response 'res' must be an object or array")


def query_records(
    start_date: str,
    end_date: str,
    *,
    workspace: WorkspaceLayout | str | Path,
    include_detail: bool = True,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
    today: date | None = None,
) -> tuple[dict[str, Any], str, Path]:
    _validate_query_window(start_date, end_date, today=today)
    body = {
        "start_date": start_date,
        "end_date": end_date,
        "include_detail": bool(include_detail),
    }
    payload, source, cache_path = query_with_cache(
        endpoint=FOOD_RECORDS_QUERY,
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=_validate_food_response,
    )
    atomic_write_json(
        cache_path.with_suffix(".index.json"),
        build_record_index(payload),
    )
    return payload, source, cache_path


def build_record_index(payload: dict[str, Any]) -> dict[str, Any]:
    """Create non-authoritative lookup paths without guessing response nesting."""

    indexes: dict[str, dict[str, list[str]]] = {
        "by_date": {},
        "by_meal": {},
        "by_name": {},
        "by_id": {},
    }

    def add(index: str, value: object, pointer: str) -> None:
        if value is None:
            return
        key = str(value).strip()
        if not key:
            return
        indexes[index].setdefault(key, []).append(pointer)

    def walk(value: object, pointer: str) -> None:
        if isinstance(value, list):
            for item_index, item in enumerate(value):
                walk(item, f"{pointer}/{item_index}")
            return
        if not isinstance(value, dict):
            return
        name = value.get("name") or value.get("food_name")
        record_id = value.get("id") or value.get("record_id")
        date_value = value.get("date") or value.get("datestr")
        meal = value.get("meal_type") or value.get("meal")
        if name is not None and any(
            item is not None for item in (record_id, date_value, meal)
        ):
            add("by_date", date_value, pointer)
            add("by_meal", meal, pointer)
            add("by_name", name, pointer)
            add("by_id", record_id, pointer)
        for key, nested in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            walk(nested, f"{pointer}/{escaped}")

    walk(payload.get("res"), "/res")
    return {
        "schema_version": 1,
        "source": "derived lookup paths into the cached official response",
        **indexes,
    }


def _normalize_compressed_food(item: object) -> dict[str, Any]:
    if not isinstance(item, list) or len(item) < 9:
        raise XunjiFoodError(
            "Compressed food rows must contain nine fields"
        )
    return {
        "id": item[0],
        "name": item[1],
        "ntr": {
            "cal": item[2],
            "carb": item[3],
            "fat": item[4],
            "protein": item[5],
            "foodpic": item[6],
        },
        "uniquekey": item[7],
        "units": item[8],
    }


def normalized_search_foods(payload: dict[str, Any]) -> list[dict[str, Any]]:
    res = payload.get("res")
    if not isinstance(res, dict):
        raise XunjiFoodError("Food-search response 'res' must be an object")
    foods = res.get("foods")
    if isinstance(foods, list):
        if not all(isinstance(item, dict) for item in foods):
            raise XunjiFoodError("res.foods must contain objects")
        return foods
    compressed = res.get("d")
    if not isinstance(compressed, list):
        raise XunjiFoodError(
            "Food-search response contains neither res.foods nor res.d"
        )
    return [_normalize_compressed_food(item) for item in compressed]


def search_foods(
    keyword: str,
    *,
    workspace: WorkspaceLayout | str | Path,
    limit: int = 8,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, Path]:
    if not isinstance(keyword, str) or not keyword.strip():
        raise XunjiFoodError("keyword must be a non-empty string")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_SEARCH_LIMIT
    ):
        raise XunjiFoodError(
            f"limit must be an integer from 1 through {MAX_SEARCH_LIMIT}"
        )
    body = {"keyword": keyword.strip(), "limit": limit}
    payload, source, cache_path = query_with_cache(
        endpoint=FOOD_SEARCH,
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=lambda value: normalized_search_foods(value),
    )
    return normalized_search_foods(payload), payload, source, cache_path


def list_templates(
    request_body: dict[str, Any],
    *,
    workspace: WorkspaceLayout | str | Path,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], str, Path]:
    if not isinstance(request_body, dict):
        raise XunjiFoodError(
            "Template-list request must be an official request object"
        )
    return query_with_cache(
        endpoint=FOOD_TEMPLATES_LIST,
        body=request_body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=_validate_food_response,
    )


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise XunjiFoodError(f"{field} must be finite numeric data")
    converted = float(value)
    if not math.isfinite(converted):
        raise XunjiFoodError(f"{field} must be finite numeric data")
    return converted


def _validate_ntr(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise XunjiFoodError(f"{field} must be an object")
    for nutrient in ("cal", "protein", "fat", "carb"):
        if _number(value.get(nutrient), field=f"{field}.{nutrient}") < 0:
            raise XunjiFoodError(f"{field}.{nutrient} must not be negative")
    return value


def _validate_food_upsert_body(body: dict[str, Any]) -> None:
    if not isinstance(body.get("client_request_id"), str) or not body[
        "client_request_id"
    ].strip():
        raise XunjiFoodError("client_request_id must be a non-empty string")
    if body.get("dry_run") is not True:
        raise XunjiFoodError(
            "Food commit is disabled under the active external-write policy; "
            "only dry_run=true is available"
        )
    foods = body.get("foods")
    if not isinstance(foods, list) or not foods:
        raise XunjiFoodError("foods must be a non-empty array")
    for index, food in enumerate(foods):
        if not isinstance(food, dict):
            raise XunjiFoodError(f"foods[{index}] must be an object")
        for field in ("date", "meal_type", "name", "unit", "uniquekey"):
            if not isinstance(food.get(field), str) or not food[field].strip():
                raise XunjiFoodError(
                    f"foods[{index}].{field} must be a non-empty string"
                )
        _parse_date(food["date"], field=f"foods[{index}].date")
        if _number(food.get("amount"), field=f"foods[{index}].amount") <= 0:
            raise XunjiFoodError(f"foods[{index}].amount must be positive")
        _validate_ntr(food.get("ntr"), field=f"foods[{index}].ntr")


def dry_run_upsert_records(
    body: dict[str, Any],
    *,
    workspace: WorkspaceLayout | str | Path,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    del workspace, credential_file, cache_dir, timeout
    _validate_food_upsert_body(body)
    foods = body["foods"]
    return {
        "status": "local_validation_passed",
        "operation": "food.records.upsert",
        "external_request_sent": False,
        "external_commit_authorized": False,
        "client_request_id": body["client_request_id"],
        "summary": [
            {
                "date": food["date"],
                "meal_type": food["meal_type"],
                "name": food["name"],
                "amount": food["amount"],
                "unit": food["unit"],
                "record_id": food.get("id"),
            }
            for food in foods
        ],
    }


def _validate_custom_food_body(body: dict[str, Any]) -> None:
    if not isinstance(body.get("client_request_id"), str) or not body[
        "client_request_id"
    ].strip():
        raise XunjiFoodError("client_request_id must be a non-empty string")
    if body.get("dry_run") is not True:
        raise XunjiFoodError(
            "Custom-food commit is disabled under the active external-write "
            "policy; only dry_run=true is available"
        )
    food = body.get("food")
    if not isinstance(food, dict):
        raise XunjiFoodError("food must be an object")
    if not isinstance(food.get("name"), str) or not food["name"].strip():
        raise XunjiFoodError("food.name must be a non-empty string")
    ntr = _validate_ntr(food.get("ntr"), field="food.ntr")
    units = food.get("units", [])
    if not isinstance(units, list):
        raise XunjiFoodError("food.units must be an array")
    ntr_units = ntr.get("foodUnit", [])
    if ntr_units != units:
        raise XunjiFoodError("food.units must equal food.ntr.foodUnit")


def dry_run_upsert_custom_food(
    body: dict[str, Any],
    *,
    workspace: WorkspaceLayout | str | Path,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    del workspace, credential_file, cache_dir, timeout
    _validate_custom_food_body(body)
    food = body["food"]
    ntr = food["ntr"]
    return {
        "status": "local_validation_passed",
        "operation": "food.custom.upsert",
        "external_request_sent": False,
        "external_commit_authorized": False,
        "client_request_id": body["client_request_id"],
        "summary": {
            "name": food["name"],
            "per_100g": {
                nutrient: ntr[nutrient]
                for nutrient in ("cal", "protein", "fat", "carb")
            },
            "units": food.get("units", []),
        },
    }


def _load_request(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XunjiFoodError(f"Cannot read request JSON: {path}") from exc
    if not isinstance(value, dict):
        raise XunjiFoodError("Request JSON root must be an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read Xunji food records and official-food search. "
            "Real food mutations remain disabled by the active write policy."
        )
    )
    parser.add_argument("--workspace")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    subparsers = parser.add_subparsers(dest="action", required=True)

    query = subparsers.add_parser("query")
    query.add_argument("start_date")
    query.add_argument("end_date")
    query.add_argument("--without-detail", action="store_true")
    query.add_argument("--refresh", action="store_true")

    search = subparsers.add_parser("search")
    search.add_argument("keyword")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--refresh", action="store_true")

    templates = subparsers.add_parser("templates-list")
    templates.add_argument("--file", type=Path, required=True)
    templates.add_argument("--refresh", action="store_true")

    upsert = subparsers.add_parser("dry-run-upsert")
    upsert.add_argument("--file", type=Path, required=True)

    custom = subparsers.add_parser("dry-run-custom-food")
    custom.add_argument("--file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        layout = resolve_workspace(args.workspace)
        common = {
            "workspace": layout,
            "credential_file": args.credentials_file,
            "cache_dir": args.cache_dir,
            "timeout": args.timeout,
        }
        if args.action == "query":
            payload, source, cache_path = query_records(
                args.start_date,
                args.end_date,
                include_detail=not args.without_detail,
                refresh=args.refresh,
                **common,
            )
            output: object = {
                "source": source,
                "cache": str(cache_path),
                "response": payload,
            }
        elif args.action == "search":
            foods, payload, source, cache_path = search_foods(
                args.keyword,
                limit=args.limit,
                refresh=args.refresh,
                **common,
            )
            output = {
                "source": source,
                "cache": str(cache_path),
                "foods": foods,
                "response": payload,
            }
        elif args.action == "templates-list":
            payload, source, cache_path = list_templates(
                _load_request(args.file),
                refresh=args.refresh,
                **common,
            )
            output = {
                "source": source,
                "cache": str(cache_path),
                "response": payload,
            }
        elif args.action == "dry-run-upsert":
            output = dry_run_upsert_records(_load_request(args.file), **common)
        else:
            output = dry_run_upsert_custom_food(
                _load_request(args.file), **common
            )
    except (XunjiFoodError, XunjiOpenApiError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
