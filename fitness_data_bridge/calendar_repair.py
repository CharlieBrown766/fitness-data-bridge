"""Dry-run and authorize Calendar notes-and-reminders repair for one release."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from .calendar_reminders import reminder_at_values
from .calendar_writer import readback_calendar, update_calendar_events
from .errors import ValidationError, WriteGateError
from .io import atomic_write_json, canonical_sha256, load_json, sha256_file
from .layout import WorkspaceLayout
from .release_bridge import project_release_path


CALENDAR_REPAIR_AUTHORIZATION_KEYS = {
    "authorization_schema_version",
    "operation",
    "phase_id",
    "block_id",
    "release_id",
    "revision",
    "release_sha256",
    "session_ids",
    "replacement_window",
    "projection_sha256",
    "confirmed",
    "one_time",
    "expires_at",
}


def _metadata(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["calendar"],
        item["date"],
        item["start_time"],
        item["duration_minutes"],
        item["title"],
    )


def _expected_events(projection: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "calendar": event["calendar"],
            "date": event["date"],
            "start_time": event["start_time"],
            "duration_minutes": event["duration_minutes"],
            "title": event["title"],
            "reminder_at": reminder_at_values(event),
            "notes": " ".join(str(event.get("notes", "")).split()),
            "default_alarm_suppressed": True,
        }
        for event in projection["calendar_events"]
    ]


def _calendar_preview(projection: dict[str, Any]) -> dict[str, Any]:
    current = readback_calendar(projection)
    expected = _expected_events(projection)
    metadata_matches = sorted((_metadata(item) for item in current["events"])) == sorted(
        _metadata(item) for item in expected
    )
    current_by_identity = {_metadata(item): item for item in current["events"]}
    changes = []
    reminders_match = metadata_matches
    notes_match = metadata_matches
    default_alarms_suppressed = metadata_matches
    alarm_counts_safe = metadata_matches
    for event in expected:
        existing = current_by_identity.get(_metadata(event))
        current_reminders = existing["reminder_at"] if existing else None
        current_notes = existing["notes"] if existing else None
        current_default_alarm_suppressed = (
            existing.get("default_alarm_suppressed") is True if existing else False
        )
        if current_reminders != event["reminder_at"]:
            reminders_match = False
        if current_notes != event["notes"]:
            notes_match = False
        if not current_default_alarm_suppressed:
            default_alarms_suppressed = False
        if current_reminders is None or len(current_reminders) > len(event["reminder_at"]):
            alarm_counts_safe = False
        changes.append(
            {
                "calendar": event["calendar"],
                "date": event["date"],
                "title": event["title"],
                "current_reminder_at": current_reminders,
                "target_reminder_at": event["reminder_at"],
                "current_notes": current_notes,
                "target_notes": event["notes"],
                "current_default_alarm_suppressed": current_default_alarm_suppressed,
                "target_default_alarm_suppressed": True,
            }
        )
    dry_run_passed = metadata_matches and alarm_counts_safe
    return {
        "status": "dry-run passed" if dry_run_passed else "dry-run blocked",
        "event_count": len(expected),
        "metadata_matches": metadata_matches,
        "alarm_counts_safe": alarm_counts_safe,
        "reminders_match": reminders_match,
        "notes_match": notes_match,
        "default_alarms_suppressed": default_alarms_suppressed,
        "changes": changes,
    }


def dry_run_calendar_repair(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Calendar event inspection requires the target macOS device")
    _, release, projection = project_release_path(workspace, release_path)
    preview = _calendar_preview(projection)
    return {
        "status": preview["status"],
        "release": {
            "phase_id": release["phase_id"],
            "block_id": release["block_id"],
            "release_id": release["release_id"],
            "revision": release["revision"],
            "session_count": len(release["sessions"]),
        },
        "release_sha256": projection["source"]["release_sha256"],
        "projection_sha256": canonical_sha256(projection),
        "session_ids": projection["source"]["session_ids"],
        "replacement_window": projection["replacement_window"],
        "calendar": preview,
        "live_write_performed": False,
    }


def _authorization(
    path: str | Path,
    *,
    release: dict[str, Any],
    projection: dict[str, Any],
    layout: WorkspaceLayout,
) -> str:
    source = Path(path).expanduser().resolve()
    value = load_json(source)
    if not isinstance(value, dict) or set(value) != CALENDAR_REPAIR_AUTHORIZATION_KEYS:
        raise WriteGateError("Calendar repair authorization must use the exact schema 1.1")
    expected = {
        "authorization_schema_version": "1.1",
        "operation": "repair_release_calendar_event_details",
        "phase_id": release["phase_id"],
        "block_id": release["block_id"],
        "release_id": release["release_id"],
        "revision": release["revision"],
        "release_sha256": projection["source"]["release_sha256"],
        "session_ids": projection["source"]["session_ids"],
        "replacement_window": projection["replacement_window"],
        "projection_sha256": canonical_sha256(projection),
        "confirmed": True,
        "one_time": True,
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise WriteGateError(f"Calendar repair authorization mismatch: {key}")
    try:
        expires = datetime.fromisoformat(str(value["expires_at"]))
    except ValueError as exc:
        raise ValidationError("Authorization expires_at must be ISO-8601") from exc
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc).astimezone(expires.tzinfo):
        raise WriteGateError("Authorization has expired")
    digest = sha256_file(source)
    if layout.receipts_dir.exists():
        for receipt_path in layout.receipts_dir.glob("*.json"):
            try:
                receipt = load_json(receipt_path)
            except (OSError, ValueError):
                continue
            if isinstance(receipt, dict) and receipt.get("authorization_sha256") == digest:
                raise WriteGateError("Authorization was already consumed")
    return digest


def repair_calendar_events(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
    authorization_path: str | Path,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Calendar event repair requires the target macOS device")
    layout, release, projection = project_release_path(workspace, release_path)
    preview = _calendar_preview(projection)
    if preview["status"] != "dry-run passed":
        raise WriteGateError("Calendar event repair dry-run is blocked")
    authorization_sha256 = _authorization(
        authorization_path,
        release=release,
        projection=projection,
        layout=layout,
    )
    release_label = f"{release['release_id']}-r{release['revision']:02d}"
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = layout.receipts_dir / f"{timestamp}-{release_label}-calendar-event-repair.json"
    receipt: dict[str, Any] = {
        "connector_calendar_repair_receipt_schema_version": "1.1",
        "status": "started",
        "phase_id": release["phase_id"],
        "block_id": release["block_id"],
        "release_id": release["release_id"],
        "revision": release["revision"],
        "session_ids": projection["source"]["session_ids"],
        "release_sha256": projection["source"]["release_sha256"],
        "projection_sha256": canonical_sha256(projection),
        "authorization_sha256": authorization_sha256,
        "calendar_dry_run": preview,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_write_json(receipt_path, receipt)
    changed = False
    try:
        if (
            not preview["reminders_match"]
            or not preview["notes_match"]
            or not preview["default_alarms_suppressed"]
        ):
            receipt["calendar"] = update_calendar_events(projection)
            changed = True
        else:
            receipt["calendar"] = {
                "status": "Calendar notes and alarms already matched",
                "event_count": len(projection["calendar_events"]),
            }
        receipt["calendar_readback"] = readback_calendar(projection)
        if receipt["calendar_readback"]["status"] != "Calendar read back":
            raise WriteGateError("Apple Calendar event read-back did not match")
        receipt["status"] = "succeeded"
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        return receipt
    except Exception as exc:
        receipt["status"] = "partial" if changed else "failed"
        receipt["error"] = str(exc)
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        raise


def dry_run_calendar_reminder_repair(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
) -> dict[str, Any]:
    """Compatibility alias for the full Calendar event repair preview."""

    return dry_run_calendar_repair(workspace, release_path=release_path)


def repair_calendar_reminders(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
    authorization_path: str | Path,
) -> dict[str, Any]:
    """Compatibility alias for the full Calendar event repair."""

    return repair_calendar_events(
        workspace,
        release_path=release_path,
        authorization_path=authorization_path,
    )
