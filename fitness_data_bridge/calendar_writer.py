"""Apple Calendar dry-run, scoped replacement, and read-back."""

from __future__ import annotations

from datetime import date
import json
import subprocess
import sys
from typing import Any

from .errors import ValidationError, WriteGateError


def dry_run_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    events = artifact["calendar_events"]
    return {
        "status": "dry-run passed",
        "replacement_window": artifact["replacement_window"],
        "event_count": len(events),
        "calendars": sorted({event["calendar"] for event in events}),
        "titles": [event["title"] for event in events],
        "delete_scope": [
            {"calendar": calendar, "date": event_date, "title": title}
            for calendar, event_date, title in _delete_identities(artifact)
        ],
    }


def _osa(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _date_parts(value: str) -> tuple[int, int, int]:
    parsed = date.fromisoformat(value)
    return parsed.year, parsed.month, parsed.day


def _date_expression(value: str, time_value: str) -> str:
    year, month, day = _date_parts(value)
    hour, minute = (int(part) for part in time_value.split(":"))
    return f"my makeDate({year}, {month}, {day}, {hour}, {minute})"


def _script_prelude() -> list[str]:
    return [
        "on makeDate(y, m, d, h, minValue)",
        "  set valueDate to current date",
        "  set year of valueDate to y",
        "  set month of valueDate to m",
        "  set day of valueDate to d",
        "  set time of valueDate to (h * hours + minValue * minutes)",
        "  return valueDate",
        "end makeDate",
        "",
    ]


def _replacement_identities(artifact: dict[str, Any]) -> list[tuple[str, str, str]]:
    return sorted(
        {
            (event["calendar"], event["date"], event["title"])
            for event in artifact["calendar_events"]
        }
    )


def _delete_identities(artifact: dict[str, Any]) -> list[tuple[str, str, str]]:
    scope = artifact.get("calendar_delete_scope")
    if not isinstance(scope, list):
        return _replacement_identities(artifact)
    return sorted(
        {
            (item["calendar"], item["date"], item["title"])
            for item in scope
        }
    )


def build_calendar_script(artifact: dict[str, Any]) -> str:
    lines = _script_prelude() + ["tell application \"Calendar\""]
    # Delete only the exact date/title identities present in the artifact. Other
    # user events in the replacement window remain outside the authorized scope.
    for calendar, event_date, title in _delete_identities(artifact):
        lines.extend(
            [
                f"  tell calendar {_osa(calendar)}",
                f"    set dayStart to {_date_expression(event_date, '00:00')}",
                "    set dayEnd to dayStart + 1 * days",
                "    delete (every event whose summary is "
                f"{_osa(title)} and start date is greater than or equal to dayStart "
                "and start date is less than dayEnd)",
                "  end tell",
            ]
        )
    for event in artifact["calendar_events"]:
        start_expression = _date_expression(event["date"], event["start_time"])
        lines.extend(
            [
                f"  tell calendar {_osa(event['calendar'])}",
                f"    set eventStart to {start_expression}",
                f"    set eventEnd to eventStart + {int(event['duration_minutes']) * 60}",
                "    set newEvent to make new event with properties "
                f"{{summary:{_osa(event['title'])}, start date:eventStart, end date:eventEnd, "
                f"description:{_osa(event.get('notes', ''))}}}",
                "    tell newEvent to make new display alarm at end with properties "
                f"{{trigger interval:-{int(event['reminder_minutes_before']) * 60}}}",
                "  end tell",
            ]
        )
    lines.append("end tell")
    return "\n".join(lines)


def write_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Apple Calendar writes require macOS")
    script = build_calendar_script(artifact)
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise WriteGateError(f"Apple Calendar write failed: {result.stderr.strip()}")
    return {"status": "Calendar written", "event_count": len(artifact["calendar_events"])}


def readback_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Apple Calendar read-back requires macOS")
    lines = _script_prelude() + ["set outputLines to {}", "tell application \"Calendar\""]
    for calendar, event_date, title in _replacement_identities(artifact):
        lines.extend(
            [
                f"  tell calendar {_osa(calendar)}",
                f"    set dayStart to {_date_expression(event_date, '00:00')}",
                "    set dayEnd to dayStart + 1 * days",
                "    set matchedEvents to every event whose summary is "
                f"{_osa(title)} and start date is greater than or equal to dayStart "
                "and start date is less than dayEnd",
                "    repeat with itemRef in matchedEvents",
                "      set itemStart to start date of itemRef",
                "      set itemEnd to end date of itemRef",
                "      set itemDuration to ((itemEnd - itemStart) div 60)",
                "      set itemLine to "
                f"{_osa(calendar)} & (ASCII character 9) & "
                "(year of itemStart as integer) & (ASCII character 9) & "
                "(month of itemStart as integer) & (ASCII character 9) & "
                "(day of itemStart as integer) & (ASCII character 9) & "
                "(hours of itemStart as integer) & (ASCII character 9) & "
                "(minutes of itemStart as integer) & (ASCII character 9) & "
                "itemDuration & (ASCII character 9) & (summary of itemRef as text)",
                "      set end of outputLines to itemLine",
                "    end repeat",
                "  end tell",
            ]
        )
    lines.extend(
        [
            "end tell",
            "set AppleScript's text item delimiters to linefeed",
            "set outputText to outputLines as text",
            "set AppleScript's text item delimiters to \"\"",
            "return outputText",
        ]
    )
    result = subprocess.run(
        ["osascript", "-e", "\n".join(lines)],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValidationError(f"Apple Calendar read-back failed: {result.stderr.strip()}")
    actual: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t", 7)
        if len(fields) != 8:
            raise ValidationError(f"Malformed Apple Calendar read-back row: {line}")
        calendar, year, month, day, hour, minute, duration, title = fields
        actual.append(
            {
                "calendar": calendar,
                "date": f"{int(year):04d}-{int(month):02d}-{int(day):02d}",
                "start_time": f"{int(hour):02d}:{int(minute):02d}",
                "duration_minutes": int(duration),
                "title": title,
            }
        )
    expected = [
        {
            "calendar": event["calendar"],
            "date": event["date"],
            "start_time": event["start_time"],
            "duration_minutes": event["duration_minutes"],
            "title": event["title"],
        }
        for event in artifact["calendar_events"]
    ]
    key = lambda item: (
        item["calendar"],
        item["date"],
        item["start_time"],
        item["duration_minutes"],
        item["title"],
    )
    status = "Calendar read back" if sorted(actual, key=key) == sorted(expected, key=key) else "Calendar mismatch"
    return {"status": status, "event_count": len(actual), "events": actual}
