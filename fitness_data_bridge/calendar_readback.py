#!/usr/bin/env python3
"""Read Apple Calendar events from the Workout calendar for a date range."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import subprocess
import sys
from datetime import date
from typing import Any


APPLE_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

NOTE_LIMIT = 96


def applescript_month(month: int) -> str:
    return APPLE_MONTHS[month - 1]


def as_applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def build_script(calendar_name: str, start: date, end: date) -> str:
    return f'''
on twoDigits(n)
    set nText to n as text
    if n < 10 then return "0" & nText
    return nText
end twoDigits

on cleanText(valueText)
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to tab
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to return
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to linefeed
    set parts to text items of valueText
    set AppleScript's text item delimiters to " "
    set valueText to parts as text
    set AppleScript's text item delimiters to oldDelimiters
    return valueText
end cleanText

on isoDate(valueDate)
    set y to year of valueDate as integer
    set m to month of valueDate as integer
    set d to day of valueDate as integer
    return (y as text) & "-" & twoDigits(m) & "-" & twoDigits(d)
end isoDate

on isoDateTime(valueDate)
    set secondsValue to time of valueDate
    set hourValue to secondsValue div 3600
    set minuteValue to (secondsValue - (hourValue * 3600)) div 60
    return isoDate(valueDate) & " " & twoDigits(hourValue) & ":" & twoDigits(minuteValue)
end isoDateTime

set calendarName to {as_applescript_string(calendar_name)}
tell application "Calendar"
    launch
    if not (exists calendar calendarName) then error "Missing Apple Calendar: " & calendarName
    set targetCalendar to calendar calendarName

    set reportStart to current date
    set year of reportStart to {start.year}
    set month of reportStart to {applescript_month(start.month)}
    set day of reportStart to {start.day}
    set time of reportStart to 0

    set reportEnd to current date
    set year of reportEnd to {end.year}
    set month of reportEnd to {applescript_month(end.month)}
    set day of reportEnd to {end.day}
    set time of reportEnd to 0
    set reportEnd to reportEnd + (1 * days)

    set outputLines to {{}}
    repeat with calendarEvent in events of targetCalendar
        try
            set eventStart to start date of calendarEvent
            if eventStart is greater than or equal to reportStart then
                if eventStart is less than reportEnd then
                set noteText to ""
                try
                    set noteText to description of calendarEvent as text
                end try
                set eventTitle to ""
                try
                    set eventTitle to summary of calendarEvent as text
                end try
                set alarmParts to {{}}
                try
                    repeat with alarmItem in display alarms of calendarEvent
                        try
                            set end of alarmParts to "display:" & ((trigger interval of alarmItem) as text) & "m"
                        end try
                    end repeat
                end try
                if (count alarmParts) is 0 then
                    set alarmText to "none"
                else
                    set oldDelimiters to AppleScript's text item delimiters
                    set AppleScript's text item delimiters to ", "
                    set alarmText to alarmParts as text
                    set AppleScript's text item delimiters to oldDelimiters
                end if
                set rowText to calendarName & tab & my cleanText(eventTitle)
                set rowText to rowText & tab & my isoDate(eventStart)
                set rowText to rowText & tab & my isoDateTime(eventStart)
                set rowText to rowText & tab & my isoDateTime(end date of calendarEvent)
                set rowText to rowText & tab & alarmText & tab & my cleanText(noteText)
                set end of outputLines to rowText
                end if
            end if
        end try
    end repeat
    set AppleScript's text item delimiters to linefeed
    return outputLines as text
end tell
'''.strip()


def read_calendar(calendar_name: str, start: date, end: date) -> list[dict[str, Any]]:
    script = build_script(calendar_name, start, end)
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("osascript is not available on this machine") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        if not details:
            details = f"osascript exited with status {exc.returncode}"
        raise RuntimeError(details) from exc

    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise RuntimeError(f"unexpected Calendar readback row with {len(fields)} fields: {line}")
        calendar, title, event_date, start_text, end_text, alarms, notes = fields
        rows.append(
            {
                "date": event_date,
                "calendar": calendar,
                "title": title,
                "start": start_text,
                "end": end_text,
                "reminder_alarms": alarms,
                "notes": notes,
            }
        )
    rows.sort(key=lambda row: (row["start"], row["title"]))
    return rows


def truncate(value: str, limit: int = NOTE_LIMIT) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def print_table(rows: list[dict[str, Any]], calendar_name: str, start: date, end: date) -> None:
    print(f"Calendar readback: {calendar_name} {start.isoformat()}..{end.isoformat()}")
    print("date | calendar | title | start | end | reminder/alarms | notes")
    print("--- | --- | --- | --- | --- | --- | ---")
    for row in rows:
        print(
            " | ".join(
                [
                    row["date"],
                    row["calendar"],
                    truncate(row["title"], 36),
                    row["start"],
                    row["end"],
                    row["reminder_alarms"],
                    truncate(row["notes"]),
                ]
            )
        )
    if not rows:
        print("(no events found)")


def validate_rows(rows: list[dict[str, Any]], expected_count: int | None, required_titles: list[str]) -> list[str]:
    failures: list[str] = []
    if expected_count is not None and len(rows) != expected_count:
        failures.append(f"expected {expected_count} events, found {len(rows)}")

    title_counts = Counter(row["title"] for row in rows)
    for title, required_count in Counter(required_titles).items():
        actual_count = title_counts.get(title, 0)
        if actual_count < required_count:
            failures.append(f"required title {title!r} expected at least {required_count}, found {actual_count}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Read Workout events from Apple Calendar.")
    parser.add_argument("--calendar", default="Workout", help="Apple Calendar name to read. Default: Workout.")
    parser.add_argument("--start", required=True, type=parse_date, help="Start date, inclusive, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, type=parse_date, help="End date, inclusive, YYYY-MM-DD.")
    parser.add_argument("--json", action="store_true", help="Output JSON array instead of a summary table.")
    parser.add_argument("--expect-count", type=int, help="Fail unless this exact event count is found.")
    parser.add_argument(
        "--require-title",
        action="append",
        default=[],
        help="Fail unless at least one event has this exact title. Repeat for multiple titles.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("FAIL: Apple Calendar inspection requires macOS.", file=sys.stderr)
        return 1

    if args.end < args.start:
        print("FAIL: --end must be greater than or equal to --start", file=sys.stderr)
        return 2
    if args.expect_count is not None and args.expect_count < 0:
        print("FAIL: --expect-count must be non-negative", file=sys.stderr)
        return 2

    try:
        rows = read_calendar(args.calendar, args.start, args.end)
    except RuntimeError as exc:
        print(f"FAIL: unable to read Apple Calendar {args.calendar!r}: {exc}", file=sys.stderr)
        return 1

    failures = validate_rows(rows, args.expect_count, args.require_title)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_table(rows, args.calendar, args.start, args.end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
