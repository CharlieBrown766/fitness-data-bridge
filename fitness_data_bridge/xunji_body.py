"""Read official Xunji body data and perform policy-safe dry runs."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Final

from .io import atomic_write_json
from .layout import (
    WorkspaceLayout,
    default_open_api_cache_dir,
    resolve_workspace,
)
from .xunji_open_api import (
    BODY_QUERY,
    BODY_UPSERT,
    canonical_body,
    mutate,
    query_with_cache,
)
from .xunji_transport import XunjiOpenApiError


BODY_SCHEMA_VERSION: Final[str] = "body_open_api_v1"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BODY_UNITS: Final[dict[str, str]] = {
    "weight": "kg",
    "bodyfat": "%",
    "neck": "cm",
    "chest": "cm",
    "weist": "cm",
    "shoulder": "cm",
    "bot": "cm",
    "arm_left": "cm",
    "arm_right": "cm",
    "forearm_left": "cm",
    "forearm_right": "cm",
    "leg_left": "cm",
    "leg_right": "cm",
    "cav_left": "cm",
    "cav_right": "cm",
}


class XunjiBodyError(XunjiOpenApiError):
    """Body request, response, or governance validation failed."""


def _parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        raise XunjiBodyError(f"{field} must use strict YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise XunjiBodyError(f"{field} must use strict YYYY-MM-DD") from exc


def _body_type(value: object, *, field: str) -> str:
    if value == "waist":
        raise XunjiBodyError(f"{field} must use historical spelling 'weist'")
    if not isinstance(value, str) or value not in BODY_UNITS:
        raise XunjiBodyError(f"{field} is not a supported Xunji body type")
    return value


def _numeric(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise XunjiBodyError(f"{field} must be finite numeric data")
    if isinstance(value, (int, float)):
        converted = float(value)
    elif isinstance(value, str):
        try:
            converted = float(value)
        except ValueError as exc:
            raise XunjiBodyError(f"{field} must be finite numeric data") from exc
    else:
        raise XunjiBodyError(f"{field} must be finite numeric data")
    if not math.isfinite(converted):
        raise XunjiBodyError(f"{field} must be finite numeric data")
    return converted


def _json_number(value: object, *, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise XunjiBodyError(f"{field} must be a finite JSON int or float")
    if isinstance(value, float) and not math.isfinite(value):
        raise XunjiBodyError(f"{field} must be a finite JSON int or float")
    return value


def _normalize_dry_run_summary(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise XunjiBodyError(
            "Body dry-run res.summary must be a non-empty array"
        )
    normalized = deepcopy(value)
    for index, item in enumerate(normalized):
        if not isinstance(item, dict):
            raise XunjiBodyError(
                f"Body dry-run res.summary[{index}] must be an object"
            )
        _parse_date(
            item.get("datestr"),
            field=f"Body dry-run res.summary[{index}].datestr",
        )
        type_name = _body_type(
            item.get("type"),
            field=f"Body dry-run res.summary[{index}].type",
        )
        item["value"] = _numeric(
            item.get("value"),
            field=f"Body dry-run res.summary[{index}].value",
        )
        unit = item.get("unit")
        if not isinstance(unit, str) or unit != BODY_UNITS[type_name]:
            raise XunjiBodyError(
                f"Body dry-run res.summary[{index}].unit does not match "
                f"{type_name}"
            )
    return normalized


def normalize_body_response(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    res = normalized.get("res")
    if not isinstance(res, dict):
        raise XunjiBodyError("Body API response 'res' must be an object")
    records = res.get("records")
    if records is not None:
        if not isinstance(records, list):
            raise XunjiBodyError("res.records must be an array")
        previous: date | None = None
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise XunjiBodyError(f"res.records[{index}] must be an object")
            observed = _parse_date(
                record.get("datestr"), field=f"res.records[{index}].datestr"
            )
            if previous is not None and observed > previous:
                raise XunjiBodyError("res.records must be ordered by date descending")
            previous = observed
            type_name = _body_type(
                record.get("type"), field=f"res.records[{index}].type"
            )
            record["value"] = _numeric(
                record.get("value"), field=f"res.records[{index}].value"
            )
            unit = record.get("unit")
            if not isinstance(unit, str) or unit != BODY_UNITS[type_name]:
                raise XunjiBodyError(
                    f"res.records[{index}].unit is required and must match "
                    f"{type_name}"
                )
    latest = res.get("latest")
    if latest is not None and not isinstance(latest, dict):
        raise XunjiBodyError("res.latest must be an object")
    by_type = res.get("by_type")
    if by_type is not None and not isinstance(by_type, dict):
        raise XunjiBodyError("res.by_type must be an object")
    return normalized


def query_body(
    start_date: str,
    end_date: str,
    *,
    workspace: WorkspaceLayout | str | Path,
    types: list[str] | tuple[str, ...] | None = None,
    include_latest: bool = True,
    include_records: bool = True,
    limit: int = 500,
    offset: int = 0,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], str, Path]:
    start = _parse_date(start_date, field="start_date")
    end = _parse_date(end_date, field="end_date")
    if start > end:
        raise XunjiBodyError("start_date must not be after end_date")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise XunjiBodyError("limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise XunjiBodyError("offset must be a non-negative integer")
    body: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "include_latest": bool(include_latest),
        "include_records": bool(include_records),
        "limit": limit,
        "offset": offset,
    }
    if types is not None:
        normalized_types = sorted(
            {_body_type(value, field="types[]") for value in types}
        )
        if not normalized_types:
            raise XunjiBodyError("types must not be empty when provided")
        body["types"] = normalized_types
    payload, source, cache_path = query_with_cache(
        endpoint=BODY_QUERY,
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=lambda value: normalize_body_response(value),
    )
    return normalize_body_response(payload), source, cache_path


def _validate_upsert_body(body: dict[str, Any]) -> None:
    if body.get("schema_version") != BODY_SCHEMA_VERSION:
        raise XunjiBodyError(
            f"schema_version must be {BODY_SCHEMA_VERSION}"
        )
    request_id = body.get("client_request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in request_id
        )
    ):
        raise XunjiBodyError(
            "client_request_id must use letters, digits, dot, underscore, or hyphen"
        )
    if body.get("dry_run") is not True:
        raise XunjiBodyError(
            "Body commit is disabled under the active external-write policy; "
            "only dry_run=true is available"
        )
    if body.get("confirmed") is True:
        raise XunjiBodyError("A body dry run must not contain confirmed=true")
    records = body.get("records")
    if not isinstance(records, list) or not records:
        raise XunjiBodyError("records must be a non-empty array")
    identities: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise XunjiBodyError(f"records[{index}] must be an object")
        datestr = record.get("datestr")
        if not isinstance(datestr, str):
            raise XunjiBodyError(f"records[{index}].datestr must be a string")
        _parse_date(datestr, field=f"records[{index}].datestr")
        type_name = _body_type(
            record.get("type"), field=f"records[{index}].type"
        )
        _json_number(record.get("value"), field=f"records[{index}].value")
        identity = (datestr, type_name)
        if identity in identities:
            raise XunjiBodyError(
                f"duplicate body upsert identity: {datestr} + {type_name}"
            )
        identities.add(identity)


def _preview_path(
    *,
    workspace: WorkspaceLayout | str | Path,
    cache_dir: str | Path | None,
    client_request_id: str,
) -> Path:
    resolved_cache = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir is not None
        else default_open_api_cache_dir(workspace).resolve()
    )
    return resolved_cache / "previews" / "body" / f"{client_request_id}.json"


def dry_run_upsert(
    body: dict[str, Any],
    *,
    workspace: WorkspaceLayout | str | Path,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], Path]:
    _validate_upsert_body(body)
    payload = mutate(
        endpoint=BODY_UPSERT,
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        timeout=timeout,
        validator=lambda value: normalize_body_response(value),
    )
    payload = normalize_body_response(payload)
    res = payload.get("res")
    if not isinstance(res, dict):
        raise XunjiBodyError(
            "Body dry-run response does not contain res.summary"
        )
    summary = _normalize_dry_run_summary(res.get("summary"))
    res["summary"] = summary
    request_id = str(body["client_request_id"])
    preview_path = _preview_path(
        workspace=workspace,
        cache_dir=cache_dir,
        client_request_id=request_id,
    )
    atomic_write_json(
        preview_path,
        {
            "schema_version": 1,
            "operation": "body.upsert",
            "client_request_id": request_id,
            "request_digest": hashlib.sha256(
                canonical_body(body).encode("utf-8")
            ).hexdigest(),
            "records": body["records"],
            "server_summary": summary,
            "status": "dry_run_only_external_commit_not_authorized",
        },
    )
    return payload, preview_path


def commit_upsert(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise XunjiBodyError(
        "Body commit is not authorized by the active Fitness external-write "
        "owner. Keep the server dry-run preview and wait for a policy version "
        "that explicitly covers body.upsert."
    )


def _load_request(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XunjiBodyError(f"Cannot read request JSON: {path}") from exc
    if not isinstance(value, dict):
        raise XunjiBodyError("Request JSON root must be an object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read official Xunji body data or request a server dry run. "
            "Real body writes remain disabled by the active write policy."
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
    query.add_argument("--types", nargs="+")
    query.add_argument("--without-latest", action="store_true")
    query.add_argument("--without-records", action="store_true")
    query.add_argument("--limit", type=int, default=500)
    query.add_argument("--offset", type=int, default=0)
    query.add_argument("--refresh", action="store_true")

    dry_run = subparsers.add_parser("dry-run-upsert")
    dry_run.add_argument("--file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        layout = resolve_workspace(args.workspace)
        if args.action == "query":
            payload, source, cache_path = query_body(
                args.start_date,
                args.end_date,
                workspace=layout,
                types=args.types,
                include_latest=not args.without_latest,
                include_records=not args.without_records,
                limit=args.limit,
                offset=args.offset,
                credential_file=args.credentials_file,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
                timeout=args.timeout,
            )
            output: object = {
                "source": source,
                "cache": str(cache_path),
                "response": payload,
            }
        else:
            payload, preview_path = dry_run_upsert(
                _load_request(args.file),
                workspace=layout,
                credential_file=args.credentials_file,
                cache_dir=args.cache_dir,
                timeout=args.timeout,
            )
            output = {
                "status": "dry_run_only",
                "preview": str(preview_path),
                "response": payload,
            }
    except (XunjiBodyError, XunjiOpenApiError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
