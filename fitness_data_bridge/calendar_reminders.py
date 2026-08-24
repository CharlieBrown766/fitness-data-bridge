"""Normalize Calendar reminder intent into exact local trigger times."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .errors import ValidationError


DEFAULT_REMINDER_MINUTES_BEFORE = 30


def _event_start(event: dict[str, Any]) -> datetime:
    try:
        return datetime.strptime(
            f"{event['date']} {event['start_time']}", "%Y-%m-%d %H:%M"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Calendar event requires valid date and start_time") from exc


def reminder_datetimes(event: dict[str, Any]) -> list[datetime]:
    """Return the exact local datetimes at which an event should alert."""

    start = _event_start(event)
    reminder_times = event.get("reminder_times")
    relative_minutes = event.get("reminder_minutes_before")
    if reminder_times is not None and relative_minutes is not None:
        raise ValidationError(
            "Calendar event cannot combine reminder_times and reminder_minutes_before"
        )
    if reminder_times is not None:
        if not isinstance(reminder_times, list) or not reminder_times:
            raise ValidationError("reminder_times must be a non-empty list")
        reminders: list[datetime] = []
        for value in reminder_times:
            if not isinstance(value, str):
                raise ValidationError("reminder_times entries must use HH:MM strings")
            try:
                clock = datetime.strptime(value, "%H:%M").time()
            except ValueError as exc:
                raise ValidationError(
                    f"Invalid Calendar reminder time {value!r}; expected HH:MM"
                ) from exc
            reminder = datetime.combine(start.date(), clock)
            if reminder >= start:
                raise ValidationError(
                    f"Calendar reminder {value} must be before event start {event['start_time']}"
                )
            reminders.append(reminder)
        if len(set(reminders)) != len(reminders):
            raise ValidationError("Calendar reminder_times must be unique")
        return sorted(reminders)

    minutes = DEFAULT_REMINDER_MINUTES_BEFORE if relative_minutes is None else relative_minutes
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValidationError("reminder_minutes_before must be a positive integer")
    return [start - timedelta(minutes=minutes)]


def reminder_at_values(event: dict[str, Any]) -> list[str]:
    return [value.strftime("%Y-%m-%d %H:%M") for value in reminder_datetimes(event)]


def reminder_intervals(event: dict[str, Any]) -> list[int]:
    """Return Calendar AppleScript trigger intervals in minutes."""

    start = _event_start(event)
    return [int((value - start).total_seconds() // 60) for value in reminder_datetimes(event)]
