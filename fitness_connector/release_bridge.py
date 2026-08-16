"""Translate one governed Fitness v5.1 half-Block release into one batch."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .errors import ValidationError, WriteGateError
from .io import canonical_sha256, load_json
from .layout import WorkspaceLayout, resolve_workspace
from .session_bridge import (
    load_action_capabilities,
    load_connector_registry,
    project_session,
    validate_session,
)


RELEASE_KEYS = {
    "half_block_release_schema_version",
    "training_rule_schema_version",
    "phase_id",
    "block_id",
    "release_id",
    "revision",
    "status",
    "cycle_positions",
    "evidence_refs",
    "basis",
    "sessions",
}
HALVES = {"first_half": ["A1", "B1"], "second_half": ["A2", "B2"]}
SLOTS = {"Push", "Pull", "Legs"}


def _nonempty_strings(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValidationError(f"{field} must contain non-empty strings")
    return value


def _validate_strength_loads(session: dict[str, Any], field: str) -> None:
    if session["session_type"] != "strength":
        return
    movements = session["prescription"].get("movements")
    if not isinstance(movements, list):
        raise ValidationError(f"{field} has no movements")
    for index, movement in enumerate(movements):
        if not isinstance(movement, dict) or movement.get("section") != "main":
            continue
        working = [
            item
            for item in movement.get("sets", [])
            if isinstance(item, dict)
            and item.get("set_role") in {"technique", "effective"}
            and item.get("unit") == "kg"
        ]
        if not working:
            continue
        basis = movement.get("load_basis")
        if not isinstance(basis, dict):
            raise ValidationError(f"{field}.movements[{index}] has no load_basis")
        if basis.get("kind") == "comparable":
            _nonempty_strings(basis.get("evidence_refs"), "comparable evidence_refs")
            if not isinstance(basis.get("comparison_signature"), str) or not basis[
                "comparison_signature"
            ].strip():
                raise ValidationError("comparable load requires comparison_signature")
            if any(item.get("load") is None for item in working):
                raise WriteGateError("comparable kilogram working sets require numeric loads")
        elif basis.get("kind") == "calibration":
            settings = movement.get("settings")
            if not isinstance(settings, dict):
                raise ValidationError("calibration requires settings")
            if settings.get("calibration_reason") != "no_comparable_completed_record":
                raise WriteGateError("calibration lacks a documented no-match search")
            _nonempty_strings(settings.get("evidence_search_refs"), "evidence_search_refs")
            if not isinstance(settings.get("calibration_rule"), str) or not settings[
                "calibration_rule"
            ].strip():
                raise WriteGateError("calibration lacks an executable calibration_rule")
            if any(item.get("load") is not None for item in working):
                raise WriteGateError("calibration may not guess a kilogram load")
        else:
            raise WriteGateError("kilogram working sets require comparable or calibration load_basis")


def validate_release(release: Any, *, require_publishable: bool = True) -> dict[str, Any]:
    if not isinstance(release, dict) or set(release) != RELEASE_KEYS:
        raise ValidationError("Release must use the exact Fitness v5.1 top-level keys")
    if release["half_block_release_schema_version"] != "5.1" or release[
        "training_rule_schema_version"
    ] != 5:
        raise ValidationError("Only Fitness half-Block release v5.1 is supported")
    release_id = release.get("release_id")
    if release_id not in HALVES or release.get("cycle_positions") != HALVES[release_id]:
        raise ValidationError("Release identity and Cycle positions disagree")
    if not isinstance(release.get("revision"), int) or release["revision"] < 1:
        raise ValidationError("Release revision must be positive")
    if require_publishable and release.get("status") != "confirmed":
        raise WriteGateError("Only a confirmed half-Block release may be published")
    _nonempty_strings(release.get("evidence_refs"), "release.evidence_refs")
    sessions = release.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValidationError("Release sessions may not be empty")
    identities: set[str] = set()
    sequences: list[int] = []
    dates: list[date] = []
    slots = {position: set() for position in HALVES[release_id]}
    for index, raw in enumerate(sessions):
        field = f"release.sessions[{index}]"
        session = validate_session(raw)
        if session["phase_id"] != release["phase_id"] or session["block_id"] != release["block_id"]:
            raise ValidationError(f"{field} is outside the release Phase/Block")
        position = session["prescription"].get("cycle_position")
        if position not in HALVES[release_id]:
            raise ValidationError(f"{field} is outside the release half")
        if require_publishable and session["schedule"]["state"] != "scheduled":
            raise WriteGateError(f"{field} is not scheduled for publication")
        try:
            dates.append(date.fromisoformat(str(session["schedule"]["scheduled_for"])))
        except ValueError as exc:
            raise ValidationError(f"{field} requires an ISO date") from exc
        if session["session_id"] in identities:
            raise ValidationError("Release session_id values must be unique")
        identities.add(session["session_id"])
        sequences.append(session["sequence"])
        if session["session_type"] == "strength":
            slot = session["prescription"].get("slot")
            if slot not in SLOTS:
                raise ValidationError(f"{field} has an invalid strength slot")
            slots[position].add(slot)
            _validate_strength_loads(session, field)
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValidationError("Release sessions must have unique ascending sequences")
    if dates != sorted(dates):
        raise ValidationError("Release sessions must have ascending dates")
    if any(value != SLOTS for value in slots.values()):
        raise ValidationError("Each released Cycle requires Push, Pull, and Legs")
    return release


def load_release(
    workspace: str | Path | WorkspaceLayout | None,
    release_path: str | Path,
) -> tuple[WorkspaceLayout, dict[str, Any]]:
    layout = resolve_workspace(workspace)
    source = Path(release_path)
    if not source.is_absolute():
        source = layout.root / source
    source = source.expanduser().resolve()
    if not source.is_relative_to(layout.root.resolve()):
        raise ValidationError("Release path must stay inside the Fitness workspace")
    return layout, validate_release(load_json(source))


def project_release(
    release: dict[str, Any],
    *,
    layout: WorkspaceLayout,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_release(release)
    registry = registry or load_connector_registry(layout)
    capabilities = load_action_capabilities(layout)
    projected = [
        project_session(
            session,
            layout=layout,
            registry=registry,
            capabilities=capabilities,
        )
        for session in release["sessions"]
    ]
    dates = [event["date"] for item in projected for event in item["calendar_events"]]
    database_scope = [item for batch in projected for item in batch["database_delete_scope"]]
    calendar_scope = [item for batch in projected for item in batch["calendar_delete_scope"]]
    app_records = [item for batch in projected for item in batch["app_records"]]
    events = [item for batch in projected for item in batch["calendar_events"]]
    if len({(item["date"], item["title"]) for item in database_scope}) != len(database_scope):
        raise ValidationError("Release contains duplicate Xunji date/title identities")
    if len({(item["calendar"], item["date"], item["title"]) for item in calendar_scope}) != len(calendar_scope):
        raise ValidationError("Release contains duplicate Calendar identities")
    return {
        "connector_contract_version": "2.0",
        "source": {
            "phase_id": release["phase_id"],
            "block_id": release["block_id"],
            "release_id": release["release_id"],
            "revision": release["revision"],
            "release_sha256": canonical_sha256(release),
            "session_ids": [item["session_id"] for item in release["sessions"]],
        },
        "replacement_window": {"start": min(dates), "end": max(dates)},
        "database_delete_scope": database_scope,
        "database_readback_scope": list(database_scope),
        "app_records": app_records,
        "calendar_delete_scope": calendar_scope,
        "calendar_events": events,
    }


def project_release_path(
    workspace: str | Path | WorkspaceLayout | None,
    release_path: str | Path,
) -> tuple[WorkspaceLayout, dict[str, Any], dict[str, Any]]:
    layout, release = load_release(workspace, release_path)
    return layout, release, project_release(release, layout=layout)
