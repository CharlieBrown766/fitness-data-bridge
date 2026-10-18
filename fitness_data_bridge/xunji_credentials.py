"""Resolve isolated Xunji credentials without exposing their values."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Final, Literal
from datetime import datetime

from .io import atomic_write_json
from .layout import WorkspaceLayout, default_credentials_file


CredentialSlot = Literal["training", "food_records", "food_search", "body"]

CREDENTIAL_SCHEMA_VERSION: Final[int] = 1
CREDENTIAL_SLOTS: Final[tuple[CredentialSlot, ...]] = (
    "training",
    "food_records",
    "food_search",
    "body",
)
SLOT_ENVIRONMENT: Final[dict[CredentialSlot, tuple[str, ...]]] = {
    "training": ("XUNJI_TRAINING_API_KEY", "XUNJI_API_KEY"),
    "food_records": ("XUNJI_FOOD_RECORDS_API_KEY",),
    "food_search": ("XUNJI_FOOD_SEARCH_API_KEY",),
    "body": ("XUNJI_BODY_API_KEY",),
}
SLOT_PATTERNS: Final[dict[CredentialSlot, re.Pattern[str]]] = {
    "training": re.compile(r"^xjllm_[A-Za-z0-9]+$"),
    "food_records": re.compile(r"^xjfood_[A-Za-z0-9]+$"),
    "food_search": re.compile(r"^[A-Fa-f0-9]{32}$"),
    "body": re.compile(r"^xjbody_[A-Za-z0-9]+$"),
}
_ACCOUNT_SCOPE_DOMAIN: Final[bytes] = b"fitness-planner/xunji-account-scope/v1"
OPEN_READ_ENDPOINTS = {
    "training.query": ("training", "https://trains.xunjiapp.cn/api_trains_for_llm_v2"),
    "training.query_full": ("training", "https://trains.xunjiapp.cn/api_trains_for_llm_v2"),
    "plan.query": ("training", "https://api.xunjiapp.cn/open/plan/query_gzip"),
}


class CredentialError(RuntimeError):
    """A credential is missing, malformed, or assigned to the wrong slot."""


def validate_open_token(value: object) -> str:
    """Validate storage syntax, without claiming any undocumented key prefix."""
    if (not isinstance(value, str) or not 16 <= len(value.strip()) <= 512
            or any(ord(c) < 33 or ord(c) > 126 for c in value.strip())):
        raise CredentialError("Open API key must be a non-empty printable ASCII token")
    return value.strip()


def open_token_digest(value: object) -> str:
    return hashlib.sha256(b"xunji-open-api/v1\0" + validate_open_token(value).encode("ascii")).hexdigest()


class VerifiedReadCredential(str):
    """An Open API token usable only at individually verified read endpoints."""

    def __new__(cls, value: str, *, slot: str, allowed_urls: set[str]):
        obj = super().__new__(cls, validate_open_token(value))
        obj.slot = _validate_slot(slot)
        obj.allowed_urls = frozenset(allowed_urls)
        return obj


def _validate_slot(slot: str) -> CredentialSlot:
    if slot not in CREDENTIAL_SLOTS:
        raise CredentialError(f"Unknown Xunji credential slot: {slot}")
    return slot  # type: ignore[return-value]


def _validate_value(slot: CredentialSlot, value: object) -> str:
    if not isinstance(value, str):
        raise CredentialError(f"Xunji credential slot '{slot}' must be a string")
    token = value.strip()
    if not token or any(character.isspace() for character in token):
        raise CredentialError(f"Xunji credential slot '{slot}' is empty or malformed")
    if not SLOT_PATTERNS[slot].fullmatch(token):
        raise CredentialError(
            f"Xunji credential slot '{slot}' has the wrong credential type"
        )
    return token


def credential_account_scope(
    slot: CredentialSlot | str,
    credential: object,
) -> str:
    """Return a stable, non-secret cache scope for one validated credential.

    The credential itself is never returned or persisted. The slot is included
    in the domain-separated digest so equal-looking values cannot share cache
    state across API domains.
    """

    selected = _validate_slot(slot)
    if isinstance(credential, VerifiedReadCredential):
        if credential.slot != selected:
            raise CredentialError("Open API credential belongs to a different slot")
        token = validate_open_token(credential)
    else:
        token = _validate_value(selected, credential)
    digest = hashlib.sha256(
        _ACCOUNT_SCOPE_DOMAIN
        + b"\0"
        + selected.encode("ascii")
        + (b"\0open-api-read" if isinstance(credential, VerifiedReadCredential) else b"")
        + b"\0"
        + token.encode("ascii")
    ).hexdigest()
    return f"{selected}-{digest[:24]}"


def resolve_credentials_file(
    explicit: str | Path | None = None,
    *,
    workspace: WorkspaceLayout | str | Path | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    return default_credentials_file(workspace).expanduser().resolve()


def read_credentials_document(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CredentialError(f"Xunji credential file not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise CredentialError(f"Xunji credential file is not UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CredentialError(f"Xunji credential file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise CredentialError("Xunji credential file root must be an object")
    if payload.get("schema_version") not in (1, 2):
        raise CredentialError(
            "Unsupported Xunji credential schema; expected schema_version 1 or 2"
        )
    xunji = payload.get("xunji")
    if not isinstance(xunji, dict):
        raise CredentialError("Xunji credential file must contain an 'xunji' object")
    unknown = sorted(set(xunji) - set(CREDENTIAL_SLOTS))
    if unknown:
        raise CredentialError(
            "Xunji credential file contains unknown slots: " + ", ".join(unknown)
        )
    if payload["schema_version"] == 1 and any(k in payload for k in ("open_api", "read_bindings")):
        raise CredentialError("Open API bindings require credential schema 2")
    if "open_api" in payload and not isinstance(payload["open_api"], dict):
        raise CredentialError("Invalid Open API candidate object")
    bindings = payload.get("read_bindings", {})
    if not isinstance(bindings, dict) or any(k not in CREDENTIAL_SLOTS or v != "open_api" for k, v in bindings.items()):
        raise CredentialError("Invalid Open API read bindings")
    return payload


def _load_json_credentials(path: Path) -> dict[str, object]:
    return read_credentials_document(path)["xunji"]


def _open_read_credential(document: dict, slot: CredentialSlot) -> VerifiedReadCredential | None:
    bindings = document.get("read_bindings", {})
    if not isinstance(bindings, dict) or any(k not in CREDENTIAL_SLOTS or v != "open_api" for k, v in bindings.items()):
        raise CredentialError("Invalid Open API read bindings")
    if slot not in bindings:
        return None
    record = document.get("open_api")
    if not isinstance(record, dict):
        raise CredentialError("Open API candidate is missing")
    token, reads = _validated_open_record(record)
    urls = {OPEN_READ_ENDPOINTS[name][1] for name in reads if OPEN_READ_ENDPOINTS[name][0] == slot}
    if not urls:
        raise CredentialError("Open API key has no verified read endpoint for this slot")
    return VerifiedReadCredential(token, slot=slot, allowed_urls=urls)


def _validated_open_record(record: dict) -> tuple[str, dict]:
    """Check stored proofs even while a candidate has no active read binding."""
    token = validate_open_token(record.get("key"))
    digest = open_token_digest(token)
    reads = record.get("verified_reads", {})
    if not isinstance(reads, dict):
        raise CredentialError("Invalid Open API verification records")
    for name, proof in reads.items():
        if name not in OPEN_READ_ENDPOINTS or not isinstance(proof, dict):
            raise CredentialError("Unregistered Open API read verification")
        try:
            at = datetime.fromisoformat(proof["verified_at"])
        except (KeyError, ValueError, TypeError) as exc:
            raise CredentialError("Invalid Open API verification time") from exc
        if proof.get("credential_sha256") != digest or at.tzinfo is None:
            raise CredentialError("Open API verification does not match this key")
    return token, reads


def _environment_credential(slot: CredentialSlot) -> str | None:
    for name in SLOT_ENVIRONMENT[slot]:
        value = os.environ.get(name, "").strip()
        if value:
            return _validate_value(slot, value)
    return None


def load_credential(
    slot: CredentialSlot | str,
    *,
    credential_file: str | Path | None = None,
    workspace: WorkspaceLayout | str | Path | None = None,
    allow_environment: bool = True,
) -> str:
    """Load one credential slot.

    Environment variables are explicit runtime overrides. The legacy
    ``XUNJI_API_KEY`` variable is accepted only for the training slot.
    """

    selected = _validate_slot(slot)
    if allow_environment:
        environment_value = _environment_credential(selected)
        if environment_value is not None:
            return environment_value

    path = resolve_credentials_file(credential_file, workspace=workspace)
    if path.suffix.lower() == ".json":
        document = read_credentials_document(path)
        selected_open = _open_read_credential(document, selected)
        if selected_open is not None:
            return selected_open
        values = document["xunji"]
        if selected not in values:
            raise CredentialError(
                f"Xunji credential slot '{selected}' is missing from {path}"
            )
        return _validate_value(selected, values[selected])

    if selected != "training":
        raise CredentialError(
            "Legacy text credential files are supported only for the training slot"
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise CredentialError(f"Xunji credential file not found: {path}") from exc
    for line in text.splitlines():
        token = line.strip()
        if SLOT_PATTERNS["training"].fullmatch(token):
            return _validate_value("training", token)
        match = re.search(r"\bxjllm_[A-Za-z0-9]+\b", token)
        if match:
            return _validate_value("training", match.group(0))
    raise CredentialError(
        f"Xunji training credential file contains no valid training key: {path}"
    )


def validate_credentials_file(path: str | Path) -> tuple[CredentialSlot, ...]:
    values = _load_json_credentials(Path(path).expanduser().resolve())
    missing = [slot for slot in CREDENTIAL_SLOTS if slot not in values]
    if missing:
        raise CredentialError(
            "Xunji credential file is missing slots: " + ", ".join(missing)
        )
    for slot in CREDENTIAL_SLOTS:
        _validate_value(slot, values[slot])
    return CREDENTIAL_SLOTS


def write_credentials_file(
    path: str | Path,
    values: dict[str, object],
    *,
    replace: bool = False,
) -> Path:
    """Write the exact four-slot registry without logging credential values."""

    target = Path(path).expanduser().resolve()
    if target.exists() and not replace:
        raise CredentialError(
            f"Xunji credential file already exists; replacement was not authorized: {target}"
        )
    unknown = sorted(set(values) - set(CREDENTIAL_SLOTS))
    if unknown:
        raise CredentialError(
            "Cannot write unknown Xunji credential slots: " + ", ".join(unknown)
        )
    missing = [slot for slot in CREDENTIAL_SLOTS if slot not in values]
    if missing:
        raise CredentialError(
            "Cannot write Xunji credential file; missing slots: "
            + ", ".join(missing)
        )
    normalized = {
        slot: _validate_value(slot, values[slot]) for slot in CREDENTIAL_SLOTS
    }
    atomic_write_json(
        target,
        {
            "schema_version": CREDENTIAL_SCHEMA_VERSION,
            "xunji": normalized,
        },
    )
    return target


def import_credential(path: str | Path, slot: str, value: str, *, replace: bool = False) -> Path:
    """Atomically merge one legacy credential without replacing other slots."""
    selected = _validate_slot(slot)
    token = _validate_value(selected, value)
    target = Path(path).expanduser().resolve()
    document = read_credentials_document(target) if target.exists() else {"schema_version": 1, "xunji": {}}
    previous = document["xunji"].get(selected)
    if previous is not None and previous != token and not replace:
        raise CredentialError("Replacing an existing credential requires explicit --replace")
    if selected in document.get("read_bindings", {}) and not replace:
        raise CredentialError("Replacing an active Open API read binding requires explicit --replace")
    document["xunji"][selected] = token
    document.get("read_bindings", {}).pop(selected, None)
    atomic_write_json(target, document)
    return target


def stage_open_credential(path: str | Path, value: str, *, replace: bool = False) -> Path:
    """Stage an unverified key; preserve legacy credentials and clear old grants."""
    token = validate_open_token(value)
    target = Path(path).expanduser().resolve()
    document = read_credentials_document(target) if target.exists() else {"schema_version": 2, "xunji": {}}
    previous = document.get("open_api", {}).get("key")
    if previous and previous != token and not replace:
        raise CredentialError("Replacing a staged Open API key requires explicit --replace")
    if previous == token:
        return target
    document.update(schema_version=2, open_api={"key": token, "verified_reads": {}}, read_bindings={})
    atomic_write_json(target, document)
    return target


def credential_status(path: str | Path) -> dict:
    """Return presence and verified read names, never token text or fragments."""
    document = read_credentials_document(Path(path))
    candidate = document.get("open_api", {})
    if candidate:
        _validated_open_record(candidate)
    result = {"schema_version": document["schema_version"],
        "legacy_slots": sorted(document["xunji"]), "open_api_staged": bool(candidate),
        "verified_reads": sorted(candidate.get("verified_reads", {})),
        "active_read_slots": sorted(document.get("read_bindings", {})),
        "live_authentication_checked": False}
    result["legacy_validation"] = {}
    for slot, value in document["xunji"].items():
        try:
            _validate_value(slot, value)
            result["legacy_validation"][slot] = "format_valid"
        except CredentialError:
            result["legacy_validation"][slot] = "format_invalid"
    for slot in result["active_read_slots"]:
        _open_read_credential(document, _validate_slot(slot))
    return result
