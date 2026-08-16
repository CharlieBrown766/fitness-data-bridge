"""Read official Xunji PlatformPlan and UniversalPlan instances."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from .layout import WorkspaceLayout, resolve_workspace
from .xunji_open_api import PLAN_QUERY, query_with_cache
from .xunji_transport import XunjiOpenApiError


PLAN_SCHEMA_VERSION = "plan_open_api_v1"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(value: object, *, field: str) -> date:
    if not isinstance(value, str) or DATE_PATTERN.fullmatch(value) is None:
        raise XunjiOpenApiError(f"{field} must use strict YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise XunjiOpenApiError(f"{field} must use strict YYYY-MM-DD") from exc


def _validate_response(payload: dict[str, Any], *, action: str) -> None:
    res = payload.get("res")
    if not isinstance(res, dict):
        raise XunjiOpenApiError("Official-plan response 'res' must be an object")
    if action == "list":
        if not isinstance(res.get("plans"), list):
            raise XunjiOpenApiError(
                "Official-plan list response does not contain res.plans"
            )
        return
    if not isinstance(res.get("plan"), dict):
        raise XunjiOpenApiError(
            "Official-plan get response does not contain res.plan"
        )
    if "date_range" not in res or res["date_range"] is None:
        raise XunjiOpenApiError(
            "Official-plan get response does not contain res.date_range"
        )
    if not isinstance(res.get("days"), list):
        raise XunjiOpenApiError(
            "Official-plan get response does not contain res.days"
        )


def _plan_endpoint(action: str, plan_ref: str | None = None):
    if action == "list":
        return replace(PLAN_QUERY, name="plan.list")
    assert plan_ref is not None
    ref_digest = hashlib.sha256(plan_ref.encode("utf-8")).hexdigest()[:16]
    return replace(PLAN_QUERY, name=f"plan.get.{ref_digest}")


def list_plans(
    *,
    workspace: WorkspaceLayout | str | Path,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], str, Path]:
    body = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "action": "list",
    }
    return query_with_cache(
        endpoint=_plan_endpoint("list"),
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=lambda payload: _validate_response(payload, action="list"),
    )


def get_plan(
    plan_ref: str,
    *,
    workspace: WorkspaceLayout | str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    include_movements: bool = True,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
) -> tuple[dict[str, Any], str, Path]:
    if not isinstance(plan_ref, str) or not plan_ref.strip():
        raise XunjiOpenApiError("plan_ref must be a non-empty string")
    if (start_date is None) != (end_date is None):
        raise XunjiOpenApiError(
            "start_date and end_date must be provided together"
        )
    body: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "action": "get",
        "plan_ref": plan_ref.strip(),
        "include_movements": bool(include_movements),
    }
    if start_date is not None and end_date is not None:
        start = _parse_date(start_date, field="start_date")
        end = _parse_date(end_date, field="end_date")
        if start > end:
            raise XunjiOpenApiError("start_date must not be after end_date")
        if (end - start).days + 1 > 92:
            raise XunjiOpenApiError(
                "Official-plan custom date range may contain at most 92 days"
            )
        body["start_date"] = start_date
        body["end_date"] = end_date
    return query_with_cache(
        endpoint=_plan_endpoint("get", plan_ref.strip()),
        body=body,
        workspace=workspace,
        credential_file=credential_file,
        cache_dir=cache_dir,
        refresh=refresh,
        timeout=timeout,
        validator=lambda payload: _validate_response(payload, action="get"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read official Xunji PlatformPlan and UniversalPlan data.",
    )
    parser.add_argument("--workspace")
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List official plans.")
    get_parser = subparsers.add_parser("get", help="Read one official plan.")
    get_parser.add_argument("plan_ref")
    get_parser.add_argument("--start-date")
    get_parser.add_argument("--end-date")
    get_parser.add_argument(
        "--without-movements",
        action="store_true",
        help="Read only the plan calendar.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        layout = resolve_workspace(args.workspace)
        if args.action == "list":
            payload, source, cache_path = list_plans(
                workspace=layout,
                credential_file=args.credentials_file,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
                timeout=args.timeout,
            )
        else:
            payload, source, cache_path = get_plan(
                args.plan_ref,
                workspace=layout,
                start_date=args.start_date,
                end_date=args.end_date,
                include_movements=not args.without_movements,
                credential_file=args.credentials_file,
                cache_dir=args.cache_dir,
                refresh=args.refresh,
                timeout=args.timeout,
            )
    except (XunjiOpenApiError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "source": source,
                "cache": str(cache_path),
                "response": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
