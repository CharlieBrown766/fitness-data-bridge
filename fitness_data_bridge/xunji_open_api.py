"""Endpoint registry, cache, and limiter for Xunji Open API clients."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Final, Literal

from .io import atomic_write_json
from .layout import (
    WorkspaceLayout,
    default_open_api_cache_dir,
)
from .xunji_credentials import (
    CredentialSlot,
    credential_account_scope,
    load_credential,
)
from .xunji_transport import HttpResponse, XunjiOpenApiError, post_json


SHANGHAI_TZ: Final[timezone] = timezone(timedelta(hours=8))
EndpointKind = Literal["read", "dry_run", "mutation"]


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    path: str
    credential_slot: CredentialSlot
    kind: EndpointKind
    min_interval_seconds: int
    success_required: bool

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.path.lstrip("/")


TRAINING_QUERY = Endpoint(
    "training.query",
    "https://trains.xunjiapp.cn",
    "/api_trains_for_llm_v2",
    "training",
    "read",
    15,
    False,
)
TRAINING_QUERY_FULL = Endpoint(
    "training.query_full",
    "https://trains.xunjiapp.cn",
    "/api_trains_for_llm_v2",
    "training",
    "read",
    30,
    False,
)
TRAINING_UPSERT = Endpoint(
    "training.upsert",
    "https://trains.xunjiapp.cn",
    "/api_upsert_trains_for_llm_v2",
    "training",
    "dry_run",
    45,
    False,
)
PLAN_QUERY = Endpoint(
    "plan.query",
    "https://api.xunjiapp.cn",
    "/open/plan/query_gzip",
    "training",
    "read",
    15,
    False,
)
FOOD_RECORDS_QUERY = Endpoint(
    "food.records.query",
    "https://eatings.xunjiapp.cn",
    "/open/food/query_gzip",
    "food_records",
    "read",
    15,
    True,
)
FOOD_RECORDS_UPSERT = Endpoint(
    "food.records.upsert",
    "https://eatings.xunjiapp.cn",
    "/open/food/upsert_gzip",
    "food_records",
    "mutation",
    15,
    True,
)
FOOD_CUSTOM_UPSERT = Endpoint(
    "food.custom.upsert",
    "https://eatings.xunjiapp.cn",
    "/open/food/custom/upsert_gzip",
    "food_records",
    "mutation",
    15,
    True,
)
FOOD_TEMPLATES_LIST = Endpoint(
    "food.templates.list",
    "https://eatings.xunjiapp.cn",
    "/open/food/templates/list_gzip",
    "food_records",
    "read",
    15,
    True,
)
FOOD_TEMPLATES_APPLY = Endpoint(
    "food.templates.apply",
    "https://eatings.xunjiapp.cn",
    "/open/food/templates/apply_gzip",
    "food_records",
    "mutation",
    15,
    True,
)
FOOD_SEARCH = Endpoint(
    "food.search",
    "https://api.xunjiapp.cn",
    "/open_agent/food/search_gzip",
    "food_search",
    "read",
    15,
    True,
)
BODY_QUERY = Endpoint(
    "body.query",
    "https://api.xunjiapp.cn",
    "/open/body/query_gzip",
    "body",
    "read",
    15,
    True,
)
BODY_UPSERT = Endpoint(
    "body.upsert",
    "https://api.xunjiapp.cn",
    "/open/body/upsert_gzip",
    "body",
    "dry_run",
    15,
    True,
)

_ENDPOINT_NAME = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$"
)
_ACCOUNT_SCOPE = re.compile(
    r"^(?:training|food_records|food_search|body)-[a-f0-9]{24}$"
)
_PLAN_SCOPED_NAME = re.compile(r"^plan\.(?:list|get\.[a-f0-9]{16})$")
_REGISTERED_ENDPOINTS: Final[tuple[Endpoint, ...]] = (
    TRAINING_QUERY,
    TRAINING_QUERY_FULL,
    TRAINING_UPSERT,
    PLAN_QUERY,
    FOOD_RECORDS_QUERY,
    FOOD_RECORDS_UPSERT,
    FOOD_CUSTOM_UPSERT,
    FOOD_TEMPLATES_LIST,
    FOOD_TEMPLATES_APPLY,
    FOOD_SEARCH,
    BODY_QUERY,
    BODY_UPSERT,
)


def _endpoint_contract(
    endpoint: Endpoint,
) -> tuple[str, str, CredentialSlot, EndpointKind, int, bool]:
    return (
        endpoint.base_url,
        endpoint.path,
        endpoint.credential_slot,
        endpoint.kind,
        endpoint.min_interval_seconds,
        endpoint.success_required,
    )


_REGISTERED_ENDPOINT_CONTRACTS: Final[
    frozenset[tuple[str, str, CredentialSlot, EndpointKind, int, bool]]
] = frozenset(_endpoint_contract(endpoint) for endpoint in _REGISTERED_ENDPOINTS)
_REGISTERED_ENDPOINT_NAMES: Final[
    dict[
        tuple[str, str, CredentialSlot, EndpointKind, int, bool],
        frozenset[str],
    ]
] = {
    _endpoint_contract(endpoint): frozenset({endpoint.name})
    for endpoint in _REGISTERED_ENDPOINTS
}


def _require_endpoint_kind(endpoint: Endpoint, expected: EndpointKind) -> None:
    if not isinstance(endpoint, Endpoint):
        raise XunjiOpenApiError("Xunji endpoint must be a registered Endpoint")
    if not _ENDPOINT_NAME.fullmatch(endpoint.name):
        raise XunjiOpenApiError("Xunji endpoint name is not cache-path safe")
    contract = _endpoint_contract(endpoint)
    if contract not in _REGISTERED_ENDPOINT_CONTRACTS:
        raise XunjiOpenApiError(
            "Xunji endpoint host, path, credential slot, or kind is not registered"
        )
    names = _REGISTERED_ENDPOINT_NAMES[contract]
    dynamic_plan_name = (
        contract == _endpoint_contract(PLAN_QUERY)
        and _PLAN_SCOPED_NAME.fullmatch(endpoint.name) is not None
    )
    if endpoint.name not in names and not dynamic_plan_name:
        raise XunjiOpenApiError(
            "Xunji endpoint operation name is not registered"
        )
    if endpoint.kind != expected:
        raise XunjiOpenApiError(
            f"Xunji endpoint {endpoint.name} is {endpoint.kind}, not {expected}"
        )


def _safe_account_scope(account_scope: str) -> str:
    if not isinstance(account_scope, str) or not _ACCOUNT_SCOPE.fullmatch(
        account_scope
    ):
        raise XunjiOpenApiError("Invalid Xunji account cache scope")
    return account_scope


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def canonical_body(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def request_digest(endpoint: Endpoint, body: dict[str, Any]) -> str:
    payload = f"{endpoint.name}\n{canonical_body(body)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_operation_path(endpoint: Endpoint) -> Path:
    return Path(*endpoint.name.split("."))


def cache_file_for(
    cache_dir: Path,
    endpoint: Endpoint,
    body: dict[str, Any],
    *,
    account_scope: str,
) -> Path:
    return (
        cache_dir
        / "account-scopes"
        / _safe_account_scope(account_scope)
        / "queries"
        / _safe_operation_path(endpoint)
        / f"{request_digest(endpoint, body)}.json"
    )


def limiter_file_for(
    cache_dir: Path,
    endpoint: Endpoint,
    *,
    account_scope: str,
) -> Path:
    return (
        cache_dir
        / "account-scopes"
        / _safe_account_scope(account_scope)
        / "rate-limits"
        / _safe_operation_path(endpoint)
        / "last-request.json"
    )


def _load_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _enforce_local_interval(
    cache_dir: Path,
    endpoint: Endpoint,
    *,
    account_scope: str,
    clock: Callable[[], float],
) -> Path:
    marker_path = limiter_file_for(
        cache_dir,
        endpoint,
        account_scope=account_scope,
    )
    marker = _load_json_if_present(marker_path)
    if marker:
        last_epoch = marker.get("requested_at_epoch")
        if isinstance(last_epoch, (int, float)):
            age = clock() - float(last_epoch)
            if age < endpoint.min_interval_seconds:
                wait_ms = int((endpoint.min_interval_seconds - age) * 1000) + 1
                raise XunjiOpenApiError(
                    f"Local rate limit for {endpoint.name}; retry after {wait_ms} ms",
                    retry_after_ms=wait_ms,
                )
    return marker_path


def _write_limiter_marker(
    marker_path: Path,
    endpoint: Endpoint,
    *,
    epoch: float,
) -> None:
    atomic_write_json(
        marker_path,
        {
            "schema_version": 1,
            "endpoint": endpoint.name,
            "requested_at": datetime.fromtimestamp(epoch, SHANGHAI_TZ).isoformat(
                timespec="seconds"
            ),
            "requested_at_epoch": epoch,
        },
    )


def query_with_cache(
    *,
    endpoint: Endpoint,
    body: dict[str, Any],
    workspace: WorkspaceLayout | str | Path | None = None,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    timeout: float = 30.0,
    validator: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.time,
) -> tuple[dict[str, Any], str, Path]:
    _require_endpoint_kind(endpoint, "read")
    resolved_cache = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir is not None
        else default_open_api_cache_dir(workspace).resolve()
    )
    credential = load_credential(
        endpoint.credential_slot,
        credential_file=credential_file,
        workspace=workspace,
    )
    account_scope = credential_account_scope(
        endpoint.credential_slot,
        credential,
    )
    cache_path = cache_file_for(
        resolved_cache,
        endpoint,
        body,
        account_scope=account_scope,
    )
    cached = _load_json_if_present(cache_path)
    if cached is not None and not refresh:
        response = cached.get("response")
        if isinstance(response, dict):
            if validator:
                validator(response)
            return response, "cache", cache_path

    marker_path = _enforce_local_interval(
        resolved_cache,
        endpoint,
        account_scope=account_scope,
        clock=clock,
    )
    requested_at_epoch = clock()
    _write_limiter_marker(
        marker_path,
        endpoint,
        epoch=requested_at_epoch,
    )
    response: HttpResponse = post_json(
        url=endpoint.url,
        body=body,
        credential=credential,
        timeout=timeout,
        success_required=endpoint.success_required,
    )
    if validator:
        validator(response.payload)
    atomic_write_json(
        cache_path,
        {
            "schema_version": 1,
            "endpoint": endpoint.name,
            "request": body,
            "request_digest": request_digest(endpoint, body),
            "fetched_at": datetime.fromtimestamp(
                requested_at_epoch, SHANGHAI_TZ
            ).isoformat(timespec="seconds"),
            "fetched_at_epoch": requested_at_epoch,
            "response": response.payload,
        },
    )
    return response.payload, "api", cache_path


def mutate(
    *,
    endpoint: Endpoint,
    body: dict[str, Any],
    workspace: WorkspaceLayout | str | Path | None = None,
    credential_file: str | Path | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 30.0,
    validator: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Call a registered endpoint only in its official dry-run mode."""

    _require_endpoint_kind(endpoint, "dry_run")
    if body.get("dry_run") is not True:
        raise XunjiOpenApiError(
            "Registered Xunji dry-run endpoints require dry_run=true"
        )
    resolved_cache = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir is not None
        else default_open_api_cache_dir(workspace).resolve()
    )
    credential = load_credential(
        endpoint.credential_slot,
        credential_file=credential_file,
        workspace=workspace,
    )
    account_scope = credential_account_scope(
        endpoint.credential_slot,
        credential,
    )
    marker_path = _enforce_local_interval(
        resolved_cache,
        endpoint,
        account_scope=account_scope,
        clock=clock,
    )
    requested_at_epoch = clock()
    _write_limiter_marker(
        marker_path,
        endpoint,
        epoch=requested_at_epoch,
    )
    response = post_json(
        url=endpoint.url,
        body=body,
        credential=credential,
        timeout=timeout,
        success_required=endpoint.success_required,
    )
    if validator:
        validator(response.payload)
    return response.payload
