"""Apple Calendar dry-run, scoped replacement, and read-back."""

from __future__ import annotations

from datetime import date, datetime
import json
import subprocess
import sys
from typing import Any

from .calendar_eventkit import run_eventkit
from .errors import ValidationError, WriteGateError
from .calendar_reminders import reminder_at_values, reminder_datetimes


def dry_run_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    events = artifact["calendar_events"]
    return {
        "status": "dry-run passed",
        "replacement_window": artifact["replacement_window"],
        "event_count": len(events),
        "calendars": sorted({event["calendar"] for event in events}),
        "titles": [event["title"] for event in events],
        "events": [
            {
                "calendar": event["calendar"],
                "date": event["date"],
                "title": event["title"],
                "reminder_at": reminder_at_values(event),
            }
            for event in events
        ],
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


def _datetime_expression(value: datetime) -> str:
    return (
        f"my makeDate({value.year}, {value.month}, {value.day}, "
        f"{value.hour}, {value.minute})"
    )


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
        "on joinValues(valuesList)",
        "  set oldDelimiters to AppleScript's text item delimiters",
        "  set AppleScript's text item delimiters to \",\"",
        "  set outputText to valuesList as text",
        "  set AppleScript's text item delimiters to oldDelimiters",
        "  return outputText",
        "end joinValues",
        "",
        "on cleanText(valueText)",
        "  set oldDelimiters to AppleScript's text item delimiters",
        "  set AppleScript's text item delimiters to tab",
        "  set partsList to text items of valueText",
        "  set AppleScript's text item delimiters to \" \"",
        "  set valueText to partsList as text",
        "  set AppleScript's text item delimiters to return",
        "  set partsList to text items of valueText",
        "  set AppleScript's text item delimiters to \" \"",
        "  set valueText to partsList as text",
        "  set AppleScript's text item delimiters to linefeed",
        "  set partsList to text items of valueText",
        "  set AppleScript's text item delimiters to \" \"",
        "  set valueText to partsList as text",
        "  set AppleScript's text item delimiters to oldDelimiters",
        "  return valueText",
        "end cleanText",
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
            ]
        )
        for reminder in reminder_datetimes(event):
            lines.append(
                "    tell newEvent to make new display alarm at end with properties "
                f"{{trigger date:{_datetime_expression(reminder)}}}"
            )
        lines.append("  end tell")
    lines.append("end tell")
    return "\n".join(lines)


def build_calendar_event_update_script(artifact: dict[str, Any]) -> str:
    """Build an in-place notes-and-reminders update for exact identities."""

    lines = _script_prelude() + ["tell application \"Calendar\""]
    for event in artifact["calendar_events"]:
        reminders = reminder_datetimes(event)
        lines.extend(
            [
                f"  tell calendar {_osa(event['calendar'])}",
                f"    set dayStart to {_date_expression(event['date'], '00:00')}",
                "    set dayEnd to dayStart + 1 * days",
                "    set matchedEvents to every event whose summary is "
                f"{_osa(event['title'])} and start date is greater than or equal to dayStart "
                "and start date is less than dayEnd",
                "    if (count matchedEvents) is not 1 then error "
                f"{_osa('Expected exactly one Calendar event: ' + event['date'] + ' ' + event['title'])}",
                "    set targetEvent to item 1 of matchedEvents",
                f"    set expectedStart to {_date_expression(event['date'], event['start_time'])}",
                "    if start date of targetEvent is not equal to expectedStart then error "
                f"{_osa('Calendar event start mismatch: ' + event['date'] + ' ' + event['title'])}",
                f"    set description of targetEvent to {_osa(event.get('notes', ''))}",
                "    set existingAlarms to display alarms of targetEvent",
                f"    if (count existingAlarms) > {len(reminders)} then error "
                f"{_osa('Calendar event has more explicit alarms than the authorized target: ' + event['date'] + ' ' + event['title'])}",
            ]
        )
        for index, reminder in enumerate(reminders, start=1):
            lines.extend(
                [
                    f"    if (count existingAlarms) is greater than or equal to {index} then",
                    f"      set trigger date of (item {index} of existingAlarms) to {_datetime_expression(reminder)}",
                    "    else",
                    "      tell targetEvent to make new display alarm at end with properties "
                    f"{{trigger date:{_datetime_expression(reminder)}}}",
                    "    end if",
                ]
            )
        lines.append("  end tell")
    lines.append("end tell")
    return "\n".join(lines)


def build_calendar_alarm_update_script(artifact: dict[str, Any]) -> str:
    """Compatibility alias for the event-details repair script."""

    return build_calendar_event_update_script(artifact)


def write_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    result = run_eventkit(artifact, "replace")
    return {
        "status": str(result.get("status") or "Calendar written"),
        "event_count": int(result.get("event_count", 0)),
    }


def update_calendar_events(artifact: dict[str, Any]) -> dict[str, Any]:
    result = run_eventkit(artifact, "repair")
    return {
        "status": str(result.get("status") or "Calendar notes and alarms updated"),
        "event_count": int(result.get("event_count", 0)),
    }


def update_calendar_alarms(artifact: dict[str, Any]) -> dict[str, Any]:
    """Compatibility alias for updating Calendar event details."""

    return update_calendar_events(artifact)


def build_calendar_readback_script(artifact: dict[str, Any]) -> str:
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
                "      set itemNotes to \"\"",
                "      try",
                "        set itemNotes to description of itemRef as text",
                "      end try",
                "      set alarmParts to {}",
                "      repeat with alarmRef in display alarms of itemRef",
                "        set alarmDate to missing value",
                "        try",
                "          set alarmDate to trigger date of alarmRef",
                "        end try",
                "        if alarmDate is missing value then",
                "          set alarmDate to itemStart + ((trigger interval of alarmRef) * minutes)",
                "        end if",
                "        set alarmStampText to ((year of alarmDate) as text) & \":\" & "
                "(((month of alarmDate) as integer) as text) & \":\" & "
                "((day of alarmDate) as text) & \":\" & "
                "((hours of alarmDate) as text) & \":\" & "
                "((minutes of alarmDate) as text)",
                "        set end of alarmParts to alarmStampText",
                "      end repeat",
                "      set alarmText to my joinValues(alarmParts)",
                "      set itemLine to "
                f"{_osa(calendar)} & (ASCII character 9) & "
                "(year of itemStart as integer) & (ASCII character 9) & "
                "(month of itemStart as integer) & (ASCII character 9) & "
                "(day of itemStart as integer) & (ASCII character 9) & "
                "(hours of itemStart as integer) & (ASCII character 9) & "
                "(minutes of itemStart as integer) & (ASCII character 9) & "
                "itemDuration & (ASCII character 9) & (summary of itemRef as text) & "
                "(ASCII character 9) & alarmText & (ASCII character 9) & "
                "my cleanText(itemNotes)",
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
    return "\n".join(lines)


def readback_calendar(artifact: dict[str, Any]) -> dict[str, Any]:
    result = run_eventkit(artifact, "inspect")
    raw_events = result.get("events")
    if not isinstance(raw_events, list):
        raise ValidationError("EventKit Calendar read-back has no events array")
    actual: list[dict[str, Any]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            raise ValidationError("EventKit Calendar read-back contains a non-object event")
        try:
            actual.append(
                {
                    "calendar": str(item["calendar"]),
                    "date": str(item["date"]),
                    "start_time": str(item["start_time"]),
                    "duration_minutes": int(item["duration_minutes"]),
                    "title": str(item["title"]),
                    "reminder_at": sorted(str(value) for value in item["reminder_at"]),
                    "notes": " ".join(str(item.get("notes", "")).split()),
                    "default_alarm_suppressed": item.get("default_alarm_suppressed") is True,
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Malformed EventKit Calendar read-back event") from exc
    expected = [
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
        for event in artifact["calendar_events"]
    ]
    key = lambda item: (
        item["calendar"],
        item["date"],
        item["start_time"],
        item["duration_minutes"],
        item["title"],
        tuple(item["reminder_at"]),
        item["notes"],
        item["default_alarm_suppressed"],
    )
    status = "Calendar read back" if sorted(actual, key=key) == sorted(expected, key=key) else "Calendar mismatch"
    return {"status": status, "event_count": len(actual), "events": actual}
