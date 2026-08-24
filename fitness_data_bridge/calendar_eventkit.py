"""Run the bundled EventKit helper for exact Calendar mutation and read-back."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from .calendar_reminders import reminder_at_values
from .errors import ValidationError, WriteGateError


EVENTKIT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calendar_eventkit.swift"


def eventkit_plan(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "calendar_events": [
            {
                "calendar": event["calendar"],
                "date": event["date"],
                "start_time": event["start_time"],
                "duration_minutes": int(event["duration_minutes"]),
                "title": event["title"],
                "reminder_at": reminder_at_values(event),
                "notes": str(event.get("notes", "")),
            }
            for event in artifact["calendar_events"]
        ],
        "calendar_delete_scope": [
            {
                "calendar": item["calendar"],
                "date": item["date"],
                "title": item["title"],
            }
            for item in artifact.get("calendar_delete_scope", [])
        ],
    }


def run_eventkit(
    artifact: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("EventKit operations require macOS")
    if not EVENTKIT_SCRIPT.is_file():
        raise ValidationError(f"Bundled EventKit helper is missing: {EVENTKIT_SCRIPT}")
    if mode not in {"status", "request-access", "inspect", "repair", "replace"}:
        raise ValidationError(f"Unsupported EventKit mode: {mode}")
    input_text = ""
    if mode not in {"status", "request-access"}:
        if artifact is None:
            raise ValidationError(f"EventKit mode {mode} requires a Calendar artifact")
        input_text = json.dumps(eventkit_plan(artifact), ensure_ascii=False)
    result = subprocess.run(
        ["/usr/bin/swift", str(EVENTKIT_SCRIPT), mode],
        input=input_text,
        check=False,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        try:
            parsed = json.loads(details.splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("error"):
            details = str(parsed["error"])
        raise WriteGateError(f"EventKit {mode} failed: {details}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"EventKit {mode} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"EventKit {mode} returned a non-object result")
    return payload


def eventkit_status() -> dict[str, Any]:
    return run_eventkit(None, "status")


def request_eventkit_access() -> dict[str, Any]:
    return run_eventkit(None, "request-access")
