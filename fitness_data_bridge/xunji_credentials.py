"""Resolve isolated Xunji credentials without exposing their values."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Final, Literal

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


class CredentialError(RuntimeError):
    """A credential is missing, malformed, or assigned to the wrong slot."""


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
    token = _validate_value(selected, credential)
    digest = hashlib.sha256(
        _ACCOUNT_SCOPE_DOMAIN
        + b"\0"
        + selected.encode("ascii")
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


def _load_json_credentials(path: Path) -> dict[str, object]:
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
    if payload.get("schema_version") != CREDENTIAL_SCHEMA_VERSION:
        raise CredentialError(
            "Unsupported Xunji credential schema; expected schema_version 1"
        )
    xunji = payload.get("xunji")
    if not isinstance(xunji, dict):
        raise CredentialError("Xunji credential file must contain an 'xunji' object")
    unknown = sorted(set(xunji) - set(CREDENTIAL_SLOTS))
    if unknown:
        raise CredentialError(
            "Xunji credential file contains unknown slots: " + ", ".join(unknown)
        )
    return xunji


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
        values = _load_json_credentials(path)
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
