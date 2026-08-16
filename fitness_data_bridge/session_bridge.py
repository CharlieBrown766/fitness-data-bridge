"""Translate one governed Fitness v5 session into connector target payloads."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .errors import ValidationError, WriteGateError
from .io import canonical_sha256, load_json, sha256_file
from .layout import WorkspaceLayout, resolve_workspace


TERMINAL_STATES = {"completed", "skipped", "cancelled"}
SESSION_KEYS = {
    "session_schema_version",
    "training_rule_schema_version",
    "phase_id",
    "block_id",
    "session_id",
    "sequence",
    "session_type",
    "prescription",
    "prescription_sha256",
    "schedule",
    "execution",
}


def _state_scalar(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if raw.startswith(f"{key}:"):
            return raw.split(":", 1)[1].strip().strip("'\"")
    raise ValidationError(f"state.yml is missing {key}")


def _workspace_file(root: Path, relative: str, *, field: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValidationError(f"{field} resolves outside the workspace")
    if not path.is_file():
        raise ValidationError(f"{field} does not exist: {path}")
    return path


def load_connector_registry(layout: WorkspaceLayout) -> dict[str, Any]:
    path = _workspace_file(
        layout.root, "个人/数据源/connectors.json", field="connector registry"
    )
    value = load_json(path)
    if not isinstance(value, dict) or value.get("connector_registry_schema_version") != "1.0":
        raise ValidationError("Unsupported connector registry")
    if value.get("selected_connector", {}).get("plugin") != "fitness-data-bridge":
        raise ValidationError("fitness-data-bridge is not the selected connector")
    return value


def load_action_capabilities(layout: WorkspaceLayout) -> dict[str, dict[str, Any]]:
    state_path = layout.root / "当前" / "state.yml"
    personal_index_path = _workspace_file(
        layout.root,
        _state_scalar(state_path, "personal_data_path"),
        field="personal_data_path",
    )
    personal = load_json(personal_index_path)
    binding = personal.get("action_capabilities") if isinstance(personal, dict) else None
    if not isinstance(binding, dict):
        raise ValidationError("Personal-data index has no action_capabilities binding")
    capabilities_path = _workspace_file(
        layout.root, str(binding.get("path", "")), field="action_capabilities.path"
    )
    if sha256_file(capabilities_path) != binding.get("sha256"):
        raise ValidationError("Personal action-capabilities hash does not match its index")
    payload = load_json(capabilities_path)
    actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(actions, list):
        raise ValidationError("Personal action capabilities must contain actions")
    return {str(item["key"]): item for item in actions if isinstance(item, dict) and item.get("key")}


def validate_session(session: Any) -> dict[str, Any]:
    if not isinstance(session, dict) or set(session) != SESSION_KEYS:
        raise ValidationError("Session must use the exact Fitness v5 top-level keys")
    if session["session_schema_version"] != "5.0" or session["training_rule_schema_version"] != 5:
        raise ValidationError("Only Fitness v5 sessions are supported")
    if session["session_type"] not in {"strength", "cardio", "recovery"}:
        raise ValidationError("Unsupported session_type")
    if not isinstance(session["sequence"], int) or session["sequence"] < 1:
        raise ValidationError("session.sequence must be a positive integer")
    prescription = session["prescription"]
    if not isinstance(prescription, dict):
        raise ValidationError("session.prescription must be an object")
    if canonical_sha256(prescription) != session["prescription_sha256"]:
        raise ValidationError("Session prescription hash mismatch")
    schedule = session.get("schedule")
    if not isinstance(schedule, dict) or set(schedule) != {"state", "scheduled_for", "revision", "history"}:
        raise ValidationError("Session schedule is malformed")
    scheduled_for = schedule.get("scheduled_for")
    if schedule.get("state") == "scheduled":
        try:
            date.fromisoformat(str(scheduled_for))
        except ValueError as exc:
            raise ValidationError("Scheduled session requires an ISO date") from exc
    return session


def next_scheduled_session(sessions: Any) -> dict[str, Any] | None:
    if not isinstance(sessions, list):
        raise ValidationError("session-index-v5.json must contain an array")
    ordered = sorted((validate_session(item) for item in sessions), key=lambda item: item["sequence"])
    for session in ordered:
        state = session["schedule"]["state"]
        if state in TERMINAL_STATES:
            continue
        if state == "scheduled" and session["schedule"]["scheduled_for"] is not None:
            return session
        return None
    return None


def load_next_session(workspace: str | Path | WorkspaceLayout | None = None) -> tuple[WorkspaceLayout, dict[str, Any]]:
    layout = resolve_workspace(workspace)
    state_path = layout.root / "当前" / "state.yml"
    index_path = _workspace_file(
        layout.root, _state_scalar(state_path, "session_index_path"), field="session_index_path"
    )
    session = next_scheduled_session(load_json(index_path))
    if session is None:
        raise WriteGateError("There is no next scheduled Fitness v5 session")
    return layout, session


def _number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _target_set(value: Any, *, movement_section: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("Movement set must be an object")
    reps = value.get("reps")
    if reps is None:
        raise ValidationError("Movement set is missing reps")
    load = value.get("load")
    source_unit = str(value.get("unit") or "kg")
    repetition_only = movement_section in {"dynamic_warmup", "cooldown"}
    result = {
        # This is Xunji's internal no-weight-field flag. It does not select
        # the user-facing "自身加重" record type when exetype is empty.
        "selfWeight": load is None,
        # Xunji uses kg as the storage sentinel for repetition-only records.
        # Writing rule-model units such as
        # "repetitions" makes the app render internal text like
        # "+repetitions" in the weight control.
        "unit": "kg",
        "reps": "1" if source_unit == "seconds" else _number(reps),
        "weight": _number(load),
        "done": False,
        "user_rep": True,
        "user_weight": load is not None,
        "time": reps if source_unit == "seconds" and not repetition_only else 0,
    }
    # A dedicated dynamic-warmup movement is a normal unloaded action in
    # Xunji. setType=热 is reserved for warm-up sets nested in a main lift.
    if movement_section == "main" and value.get("set_role") == "warmup":
        result["setType"] = "热"
    return result


def _source_movements(prescription: dict[str, Any]) -> list[dict[str, Any]]:
    movements = prescription.get("movements")
    if isinstance(movements, list):
        return movements
    if prescription.get("action_key"):
        return [prescription]
    raise ValidationError("Strength prescription has no movements")


def _xunji_movements(
    prescription: dict[str, Any], capabilities: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for movement in _source_movements(prescription):
        if not isinstance(movement, dict) or not movement.get("action_key"):
            raise ValidationError("Each strength movement requires action_key")
        key = str(movement["action_key"])
        capability = capabilities.get(key)
        if capability is None:
            raise WriteGateError(f"Action is absent from the personal database: {key}")
        availability = capability.get("availability", {})
        if not isinstance(availability, dict) or not availability.get("planning_eligible"):
            raise WriteGateError(f"Action is not planning-eligible: {key}")
        semantics = capability.get("write_semantics", {})
        if not isinstance(semantics, dict):
            raise ValidationError(f"Action has no write semantics: {key}")
        sets = movement.get("sets")
        if not isinstance(sets, list) or not sets:
            raise ValidationError(f"Action has no prescribed sets: {key}")
        settings = movement.get("settings") if isinstance(movement.get("settings"), dict) else {}
        projected_sets = [
            _target_set(item, movement_section=str(movement.get("section") or ""))
            for item in sets
        ]
        movement_section = str(movement.get("section") or "")
        exetype = str(semantics.get("exetype", ""))
        if movement_section in {"dynamic_warmup", "cooldown"}:
            exetype = ""
        elif exetype in {"plus_weight", "times"} and all(
            item.get("load") is None for item in sets if isinstance(item, dict)
        ):
            exetype = ""
        note = str(settings.get("note") or "")
        if movement_section in {"dynamic_warmup", "cooldown"}:
            seconds = sorted(
                {
                    _number(item.get("reps"))
                    for item in sets
                    if isinstance(item, dict) and item.get("unit") == "seconds"
                }
            )
            if seconds:
                duration_note = f"每组保持 {'/'.join(seconds)} 秒"
                note = f"{note}；{duration_note}" if note else duration_note
        result.append(
            {
                "key": key,
                "sets": projected_sets,
                "type": str(settings.get("xunji_type", "")),
                "exetype": exetype,
                "label": str(capability.get("label") or movement.get("label") or key),
                # intent is an internal planning field, not a user-facing
                # Xunji note. Only explicit or translated instructions are
                # exported.
                "note": note,
            }
        )
    return result


def project_session(
    session: dict[str, Any],
    *,
    layout: WorkspaceLayout,
    registry: dict[str, Any] | None = None,
    capabilities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_session(session)
    if session["schedule"]["state"] != "scheduled":
        raise WriteGateError("Only the next scheduled session may be projected")
    registry = registry or load_connector_registry(layout)
    defaults = registry.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValidationError("Connector defaults must be an object")
    target_date = str(session["schedule"]["scheduled_for"])
    title = str(session["prescription"].get("title") or session["session_id"])
    duration = int(session["prescription"].get("estimated_minutes") or defaults.get("duration_minutes") or 75)
    notes = (
        f"Fitness v5 | phase={session['phase_id']} | block={session['block_id']} | "
        f"session={session['session_id']} | prescription_sha256={session['prescription_sha256']}"
    )
    app_records: list[dict[str, Any]] = []
    database_scope: list[dict[str, str]] = []
    if session["session_type"] == "strength":
        app_records = [
            {
                "date": target_date,
                "title": title,
                "category": "main",
                "note": notes,
                "movements": _xunji_movements(
                    session["prescription"],
                    capabilities or load_action_capabilities(layout),
                ),
            }
        ]
        database_scope = [{"date": target_date, "title": title}]
    calendar = str(defaults.get("calendar") or "Workout")
    event = {
        "calendar": calendar,
        "date": target_date,
        "title": title,
        "start_time": str(defaults.get("start_time") or "20:00"),
        "duration_minutes": duration,
        "reminder_minutes_before": int(defaults.get("reminder_minutes_before") or 30),
        "notes": notes,
    }
    return {
        "connector_contract_version": "1.0",
        "source": {
            "session_id": session["session_id"],
            "sequence": session["sequence"],
            "schedule_revision": session["schedule"]["revision"],
            "prescription_sha256": session["prescription_sha256"],
        },
        "replacement_window": {"start": target_date, "end": target_date},
        "database_delete_scope": database_scope,
        "database_readback_scope": database_scope,
        "app_records": app_records,
        "calendar_delete_scope": [
            {"calendar": calendar, "date": target_date, "title": title}
        ],
        "calendar_events": [event],
    }


def project_next_session(workspace: str | Path | WorkspaceLayout | None = None) -> dict[str, Any]:
    layout, session = load_next_session(workspace)
    return project_session(session, layout=layout)
