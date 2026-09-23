#!/usr/bin/env python3
"""Collect Mac-local Xunji facts for the coach without doing training analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .backup import discover_xunji_database
from .errors import FitnessDataBridgeError
from .layout import resolve_workspace


def resolve_timezone() -> tzinfo:
    """Use the IANA zone when available and a fixed Shanghai offset otherwise."""

    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


TZ = resolve_timezone()
SCRIPT_PATH = Path(__file__).resolve()
PLUGIN_ROOT = SCRIPT_PATH.parent.parent
FITNESS_ROOT = Path.cwd()
CACHE_DIR = FITNESS_ROOT / "数据" / "训练" / "xunji" / "mac_student_facts"
RAW_CACHE_DIR = FITNESS_ROOT / "运行" / "cache" / "xunji" / "mac_localtrains"
LOG_PATH = FITNESS_ROOT / "运行" / "logs" / "fitness-planner" / "weekly_training_refresh.log"
SYNC_DOC = PLUGIN_ROOT / "skills" / "fitness-data-bridge" / "references" / "operations.md"
SYNFIT_APP = Path("/Applications/SynFit.app")
REQUIRED_LOCALTRAINS_COLUMNS = {
    "id",
    "datestr",
    "start",
    "end",
    "movement",
    "title",
    "note",
    "version",
    "sync_type",
    "delflag",
}
CARDIO_WORDS = ("跑", "步行", "走路", "骑", "自行车", "椭圆", "爬楼", "有氧", "游泳")
DIFFICULTY_LABELS = {
    "easy": "简单",
    "normal": "一般",
    "medium": "一般",
    "hard": "困难",
}
STRENGTH_EXETYPES = {"weight", "help"}


class RefreshError(RuntimeError):
    pass


def configure_workspace(value: str | Path | None) -> None:
    """Bind workspace-owned outputs without coupling them to the plugin location."""
    global FITNESS_ROOT, CACHE_DIR, RAW_CACHE_DIR, LOG_PATH
    try:
        layout = resolve_workspace(value)
    except FitnessDataBridgeError as exc:
        raise RefreshError(str(exc)) from exc
    FITNESS_ROOT = layout.root
    CACHE_DIR = layout.training_facts_dir
    RAW_CACHE_DIR = layout.retained_cache_dir
    LOG_PATH = layout.logs_dir / "weekly_training_refresh.log"


@dataclass(frozen=True)
class WeekRange:
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.start.isoformat()}_to_{self.end.isoformat()}"


def now() -> datetime:
    return datetime.now(TZ)


def fmt_now() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S %z")


def current_week(today: date) -> WeekRange:
    start = today - timedelta(days=today.weekday())
    return WeekRange(start=start, end=start + timedelta(days=6))


def previous_week(today: date) -> WeekRange:
    this_week = current_week(today)
    start = this_week.start - timedelta(days=7)
    return WeekRange(start=start, end=start + timedelta(days=6))


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise RefreshError(f"日期必须是 YYYY-MM-DD: {value}") from exc


def ensure_fitness_root() -> None:
    if sys.platform != "darwin":
        raise RefreshError("Mac-local Xunji refresh requires macOS and SynFit.app.")
    if not resolve_workspace(FITNESS_ROOT).state_file.is_file() or not (FITNESS_ROOT / "AGENTS.md").is_file():
        raise RefreshError(f"无效 Fitness 工作区: {FITNESS_ROOT}")
    os.chdir(FITNESS_ROOT)


def append_log(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line.rstrip() + "\n")


def atomic_write_text(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    tmp.replace(path)
    return True


def atomic_write_json(path: Path, payload: Any) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return atomic_write_text(path, text)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def preserve_created_time(path: Path) -> str:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines()[:2]:
            if line.startswith("> Created time:"):
                return line.removeprefix("> Created time:").strip()
    return now().strftime("%Y-%m-%d %H:%M")


def markdown_with_metadata(path: Path, body: str) -> str:
    created = preserve_created_time(path)
    modified = now().strftime("%Y-%m-%d %H:%M")
    return f"> Created time: {created}\n> Modified time: {modified}\n\n{body.rstrip()}\n"


def find_db_candidates() -> list[Path]:
    try:
        return [discover_xunji_database()]
    except FitnessDataBridgeError:
        return []


def localtrains_columns(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("pragma table_info(localtrains)").fetchall()
    return {str(row[1]) for row in rows}


def choose_db() -> tuple[Path, set[str]]:
    candidates = find_db_candidates()
    errors: list[str] = []
    for path in candidates:
        try:
            columns = localtrains_columns(path)
        except sqlite3.Error as exc:
            errors.append(f"{path}: {exc}")
            continue
        if REQUIRED_LOCALTRAINS_COLUMNS.issubset(columns):
            return path, columns
        missing = sorted(REQUIRED_LOCALTRAINS_COLUMNS - columns)
        errors.append(f"{path}: localtrains 缺字段 {missing}")
    detail = "; ".join(errors) if errors else "未找到 Xunji.db"
    raise RefreshError(f"Mac 本地训记数据库不可读: {detail}")


def launch_synfit() -> str:
    if not SYNFIT_APP.exists():
        raise RefreshError(f"SynFit.app 不存在: {SYNFIT_APP}")
    subprocess.run(["open", "-a", str(SYNFIT_APP)], check=True)
    return str(SYNFIT_APP)


def process_snapshot() -> list[str]:
    result = subprocess.run(
        ["ps", "-axo", "pid,comm,args"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    lines = []
    for line in result.stdout.splitlines():
        if any(token in line.lower() for token in ("trainnote", "synfit", "xunji", "训记")):
            lines.append(" ".join(line.split()))
    return lines


def wait_for_process(timeout_seconds: int) -> list[str]:
    deadline = time.time() + timeout_seconds
    last: list[str] = []
    while time.time() < deadline:
        last = process_snapshot()
        if last:
            return last
        time.sleep(2)
    return last


def int_like(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def is_completed_time(start: Any, end: Any) -> bool:
    start_value = int_like(start)
    end_value = int_like(end)
    return bool(start_value and end_value and start_value > 0 and end_value > 0)


def query_recent_pending(db_path: Path, start: date, end: date) -> list[dict[str, Any]]:
    today = now().date()
    query_end = end
    if query_end < start:
        return []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, datestr, title, start, end, sync_type, delflag,
                   length(coalesce(movement, '')) as movement_len
            from localtrains
            where datestr between ? and ?
            order by datestr, id
            """,
            (start.isoformat(), query_end.isoformat()),
        ).fetchall()
    pending: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        completed_row = is_completed_time(row_dict.get("start"), row_dict.get("end"))
        if row_dict.get("sync_type") != "done":
            pending.append(row_dict)
    return pending


def sync_signature(db_path: Path, week: WeekRange) -> str:
    """Hash every row read by this week's facts, including future plans/deletions."""
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "select id, datestr, title, start, end, movement, note, sync_type, version, delflag "
            "from localtrains where datestr between ? and ? order by datestr, id",
            (week.start.isoformat(), week.end.isoformat())).fetchall()
    return facts_hash({"rows": rows})


def wait_for_db_state(
    db_path: Path,
    week: WeekRange,
    timeout_seconds: int,
    *,
    settle_seconds: int = 15,
) -> tuple[bool, list[dict[str, Any]]]:
    deadline = time.time() + timeout_seconds
    last_pending: list[dict[str, Any]] = []
    stable_since: float | None = None
    last_signature: str | None = None
    while time.time() < deadline:
        try:
            last_pending = query_recent_pending(db_path, week.start, week.end)
            signature = sync_signature(db_path, week)
        except sqlite3.Error:
            stable_since = None
            last_signature = None
            time.sleep(3)
            continue
        if last_pending:
            stable_since = None
        elif signature != last_signature:
            stable_since = time.time()
        elif stable_since is not None and time.time() - stable_since >= settle_seconds:
            return True, []
        last_signature = signature
        time.sleep(min(3, max(1, settle_seconds)))
    return False, last_pending


def planning_eligibility(
    *,
    launched: bool,
    processes: list[str],
    sync_wait: int,
    sync_completed: bool,
) -> tuple[bool, str]:
    if not launched:
        return False, "SynFit.app was not launched in this run."
    if not processes:
        return False, "SynFit.app process was not detected after launch."
    if sync_wait <= 0:
        return False, "Sync wait must be greater than zero for planning input."
    if not sync_completed:
        return (
            False,
            "One or more completed rows did not reach sync_type=done during the bounded sync wait.",
        )
    return (
        True,
        "SynFit.app launched, its process was detected, and every completed row "
        "in the requested window reached sync_type=done.",
    )


def parse_json_text(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def note_text(note: Any) -> str:
    parsed = parse_json_text(note, "")
    if isinstance(parsed, dict):
        return str(parsed.get("text") or "").strip()
    if isinstance(parsed, str):
        return parsed.strip()
    return ""


def number_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def normalize_difficulty(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().lower()
    if text in DIFFICULTY_LABELS:
        return "normal" if text == "medium" else text
    reverse = {"简单": "easy", "一般": "normal", "困难": "hard"}
    return reverse.get(str(value).strip(), text)


def is_done_set(set_obj: dict[str, Any], completed_row: bool) -> bool:
    if "done" in set_obj:
        return bool(set_obj.get("done"))
    return completed_row


def simplify_set(
    set_obj: dict[str, Any], completed_row: bool, *, single_side: bool
) -> dict[str, Any]:
    local_left_weight = set_obj.get("left_weight")
    if local_left_weight in (None, ""):
        local_left_weight = set_obj.get("leftWeight")
    return {
        "set_type": set_obj.get("setType") or "",
        "weight": set_obj.get("weight"),
        # Mac-local Xunji rows use left_weight; API/watch payloads may expose
        # the same value as leftWeight.  Preserve both inputs as one fact.
        "left_weight": local_left_weight,
        "reps": set_obj.get("reps"),
        "time": set_obj.get("time"),
        "done": is_done_set(set_obj, completed_row),
        "dropset": set_obj.get("dropset"),
        "unit": set_obj.get("unit") or "",
        "self_weight": bool(set_obj.get("selfWeight")),
        "single_side": single_side,
    }


def analyze_action(action: dict[str, Any], completed_row: bool,
                   capabilities: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    sets = action.get("sets") if isinstance(action.get("sets"), list) else []
    single_side = bool(action.get("singleSide"))
    simplified_sets = [
        simplify_set(item, completed_row, single_side=single_side)
        for item in sets
        if isinstance(item, dict)
    ]
    exetype = action.get("exetype")
    raw_exetype = exetype
    normalization_basis = "source"
    capability = (capabilities or {}).get(str(action.get("key")))
    # Only a verified personal mapping may fill an absent recording type.
    # Never reclassify unloaded warm-ups, or override an explicit source type.
    has_load = any(number_value(s.get("weight")) is not None or
                   number_value(s.get("left_weight")) is not None for s in simplified_sets)
    if not exetype and has_load and capability and capability.get("label") == action.get("label"):
        mapped = capability.get("write_semantics", {}).get("exetype")
        if mapped in STRENGTH_EXETYPES:
            exetype = mapped
            normalization_basis = "personal_action_capabilities"
    done_sets = [item for item in simplified_sets if item["done"]]
    heat_sets = [item for item in done_sets if item.get("set_type") == "热"]
    counted_sets = 0
    counted_side_sets = 0
    counted_reps = 0
    counted_side_reps = 0
    counted_tonnage = 0.0
    drop_segments = 0
    drop_reps = 0
    drop_tonnage = 0.0
    statistics_issues = []
    max_weight: float | None = None

    if exetype in STRENGTH_EXETYPES:
        for item in done_sets:
            if item.get("set_type") == "热":
                continue
            reps = number_value(item.get("reps"))
            weight = number_value(item.get("weight"))
            if reps is None:
                continue
            counted_sets += 1
            counted_reps += int(reps)
            counted_side_sets += 2 if single_side else 1
            counted_side_reps += int(reps) * (2 if single_side else 1)
            if exetype == "weight" and weight is not None:
                counted_tonnage += weight * reps
                max_weight = weight if max_weight is None else max(max_weight, weight)
            left_weight = number_value(item.get("left_weight"))
            if exetype == "weight" and single_side and left_weight is not None:
                counted_tonnage += left_weight * reps
                max_weight = (
                    left_weight
                    if max_weight is None
                    else max(max_weight, left_weight)
                )


    # Drop segments contribute reps/load, never additional main set rows.
    for set_index, item in enumerate(simplified_sets):
        drops = item.get("dropset")
        if not drops or not item["done"] or item.get("set_type") == "热":
            continue
        if not isinstance(drops, list):
            statistics_issues.append({"set_index": set_index, "reason": "unsupported_dropset_shape"})
            continue
        for segment_index, segment in enumerate(drops):
            issue = {"set_index": set_index, "segment_index": segment_index}
            if not isinstance(segment, dict) or segment.get("dropset"):
                statistics_issues.append({**issue, "reason": "unsupported_nested_dropset"})
                continue
            if segment.get("done") is False:
                continue
            reps = number_value(segment.get("reps"))
            weight = number_value(segment.get("weight"))
            left = number_value(segment.get("left_weight", segment.get("leftWeight")))
            if (exetype not in STRENGTH_EXETYPES or reps is None or reps <= 0 or not reps.is_integer()
                    or (exetype == "weight" and (weight is None or weight < 0))
                    or (single_side and exetype == "weight" and (left is None or left < 0))
                    or segment.get("unit", item.get("unit")) not in (None, "", "kg")):
                statistics_issues.append({**issue, "reason": "incomplete_dropset_semantics"})
                continue
            drop_segments += 1
            drop_reps += int(reps)
            counted_reps += int(reps)
            counted_side_reps += int(reps) * (2 if single_side else 1)
            if exetype == "weight":
                load = weight + (left if single_side else 0)
                drop_tonnage += load * reps
                counted_tonnage += load * reps
                max_weight = max([v for v in (max_weight, weight, left if single_side else None) if v is not None])

    return {
        "key": action.get("key"),
        "label": action.get("label") or "",
        "type": action.get("type") or "",
        "exetype": raw_exetype or "",
        "normalized_exetype": exetype or "",
        "normalization_basis": normalization_basis,
        "unclassified_loaded_sets": sum(
            1 for s in done_sets if s.get("set_type") != "热"
            and (number_value(s.get("weight")) is not None or number_value(s.get("left_weight")) is not None)
        ) if exetype not in STRENGTH_EXETYPES else 0,
        "difficulty": normalize_difficulty(action.get("difficulty")),
        "difficulty_label": DIFFICULTY_LABELS.get(normalize_difficulty(action.get("difficulty")), ""),
        "note": action.get("note") or "",
        "single_side": single_side,
        "set_count": len(simplified_sets),
        "done_sets": len(done_sets),
        "heat_sets": len(heat_sets),
        "undone_sets": max(0, len(simplified_sets) - len(done_sets)),
        "counted_sets": counted_sets,
        "counted_side_sets": counted_side_sets,
        "counted_reps": counted_reps,
        "counted_side_reps": counted_side_reps,
        "counted_tonnage": round(counted_tonnage),
        "drop_segments": drop_segments,
        "drop_reps": drop_reps,
        "drop_tonnage": round(drop_tonnage),
        "statistics_issues": statistics_issues,
        "max_weight": max_weight,
        "sets": simplified_sets,
    }


def classify_record(title: str, actions: list[dict[str, Any]], completed_row: bool) -> tuple[bool, bool, bool]:
    is_rest = "休息" in title or (not actions and not completed_row)
    has_strength = any(action.get("normalized_exetype", action.get("exetype")) in STRENGTH_EXETYPES for action in actions)
    is_cardio = any(word in title for word in CARDIO_WORDS) and not has_strength
    is_strength = has_strength
    return is_rest, is_cardio, is_strength


def query_week_records(db_path: Path, week: WeekRange,
                       capabilities: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, datestr, title, start, end, movement, note, sync_type, version, delflag
            from localtrains
            where datestr between ? and ?
            order by datestr, id
            """,
            (week.start.isoformat(), week.end.isoformat()),
        ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        movement = parse_json_text(row_dict.get("movement"), [])
        if not isinstance(movement, list):
            movement = []
        completed_row = is_completed_time(row_dict.get("start"), row_dict.get("end"))
        actions = [analyze_action(item, completed_row, capabilities) for item in movement if isinstance(item, dict)]
        title = str(row_dict.get("title") or "")
        is_rest, is_cardio, is_strength = classify_record(title, actions, completed_row)
        parts: Counter[str] = Counter()
        for action in actions:
            part = action.get("type") or "未标注"
            if action.get("counted_sets", 0):
                parts[part] += int(action["counted_sets"])

        records.append(
            {
                "id": row_dict.get("id"),
                "delflag": row_dict.get("delflag") or 0,
                "source_content_sha256": facts_hash({"movement": movement, "note": row_dict.get("note")}),
                "source_actions": movement,
                "datestr": row_dict.get("datestr"),
                "title": title,
                "start": row_dict.get("start"),
                "end": row_dict.get("end"),
                "note_text": note_text(row_dict.get("note")),
                "experience_text": note_text(row_dict.get("note")),
                "sync_type": row_dict.get("sync_type"),
                "version": row_dict.get("version"),
                "is_rest": is_rest,
                "is_completed": completed_row,
                "is_cardio": is_cardio,
                "is_strength": is_strength,
                "sets": sum(int(action["counted_sets"]) for action in actions),
                "side_sets": sum(int(action["counted_side_sets"]) for action in actions),
                "heat_sets": sum(int(action["heat_sets"]) for action in actions),
                "reps": sum(int(action["counted_reps"]) for action in actions),
                "side_reps": sum(int(action["counted_side_reps"]) for action in actions),
                "tonnage": sum(int(action["counted_tonnage"]) for action in actions),
                "parts": dict(parts),
                "actions": actions,
            }
        )
    return records


def build_summary(records: list[dict[str, Any]], week: WeekRange) -> dict[str, Any]:
    from .reconciliation import deleted, duplicate_groups
    source_records = records
    duplicates = duplicate_groups(records)
    excluded = {str(i) for group in duplicates for i in group["source_ids"][1:]}
    records = [r for r in records if not deleted(r) and str(r.get("id")) not in excluded]
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_day[str(record["datestr"])].append(record)

    daily = []
    for offset in range(7):
        day = (week.start + timedelta(days=offset)).isoformat()
        day_records = by_day.get(day, [])
        daily.append(
            {
                "date": day,
                "records": len(day_records),
                "completed": sum(1 for item in day_records if item["is_completed"]),
                "strength": sum(1 for item in day_records if item["is_strength"] and item["is_completed"]),
                "cardio": sum(1 for item in day_records if item["is_cardio"] and item["is_completed"]),
                "rest_or_plan": sum(1 for item in day_records if item["is_rest"] or not item["is_completed"]),
                "sets": sum(int(item["sets"]) for item in day_records if item["is_completed"]),
                "side_sets": sum(int(item["side_sets"]) for item in day_records if item["is_completed"]),
                "heat_sets": sum(int(item["heat_sets"]) for item in day_records if item["is_completed"]),
                "reps": sum(int(item["reps"]) for item in day_records if item["is_completed"]),
                "side_reps": sum(int(item["side_reps"]) for item in day_records if item["is_completed"]),
                "tonnage": sum(int(item["tonnage"]) for item in day_records if item["is_completed"]),
                "titles": [item["title"] for item in day_records],
                "notes": [item["note_text"] for item in day_records if item.get("note_text")],
            }
        )

    action_counter: Counter[str] = Counter()
    part_counter: Counter[str] = Counter()
    difficulty_counter: Counter[str] = Counter()
    difficulty_actions: list[dict[str, Any]] = []
    for record in records:
        if not record["is_completed"]:
            continue
        for action in record["actions"]:
            if action.get("counted_sets", 0):
                action_counter[str(action["label"])] += int(action["counted_sets"])
            difficulty = str(action.get("difficulty") or "")
            if difficulty:
                difficulty_counter[difficulty] += 1
                difficulty_actions.append(
                    {
                        "date": record["datestr"],
                        "title": record["title"],
                        "label": action.get("label") or "",
                        "difficulty": difficulty,
                        "difficulty_label": action.get("difficulty_label")
                        or DIFFICULTY_LABELS.get(difficulty, difficulty),
                        "done_sets": action.get("done_sets", 0),
                        "counted_sets": action.get("counted_sets", 0),
                        "max_weight": action.get("max_weight"),
                        "note": action.get("note") or "",
                    }
                )
        for part, count in record["parts"].items():
            part_counter[str(part)] += int(count)

    completed = [item for item in records if item["is_completed"]]
    unresolved = [
        {"record_id": r.get("id"), "date": r["datestr"], "action_key": a.get("key"),
         "label": a.get("label"), "sets": a["unclassified_loaded_sets"]}
        for r in completed for a in r["actions"] if a.get("unclassified_loaded_sets", 0)
    ]
    issues = [{"record_id": r.get("id"), "action_key": a.get("key"), **issue}
              for r in completed for a in r["actions"] for issue in a.get("statistics_issues", [])]
    return {
        "statistics_complete": not unresolved and not issues,
        "statistics_issues": issues,
        "source_record_count": len(source_records),
        "deleted_record_count": sum(1 for r in source_records if deleted(r)),
        "duplicate_completed_records": duplicates,
        "drop_segments": sum(a.get("drop_segments", 0) for r in completed for a in r["actions"]),
        "unclassified_loaded_actions": unresolved,
        "unclassified_loaded_sets": sum(a["sets"] for a in unresolved),
        "week_start": week.start.isoformat(),
        "week_end": week.end.isoformat(),
        "record_count": len(records),
        "completed_records": len(completed),
        "strength_records": sum(1 for item in completed if item["is_strength"]),
        "cardio_records": sum(1 for item in completed if item["is_cardio"]),
        "rest_or_plan_records": sum(1 for item in records if item["is_rest"] or not item["is_completed"]),
        "sets": sum(int(item["sets"]) for item in completed),
        "side_sets": sum(int(item["side_sets"]) for item in completed),
        "heat_sets": sum(int(item["heat_sets"]) for item in completed),
        "reps": sum(int(item["reps"]) for item in completed),
        "side_reps": sum(int(item["side_reps"]) for item in completed),
        "tonnage": sum(int(item["tonnage"]) for item in completed),
        "daily": daily,
        "top_actions_by_sets": action_counter.most_common(20),
        "parts_by_sets": part_counter.most_common(),
        "difficulty_counts": [
            {
                "difficulty": key,
                "label": DIFFICULTY_LABELS.get(key, key),
                "count": count,
            }
            for key, count in difficulty_counter.most_common()
        ],
        "difficulty_actions": difficulty_actions,
        "notes": [
            {"date": item["datestr"], "title": item["title"], "note": item["note_text"]}
            for item in completed
            if item.get("note_text")
        ],
        "experience_feedback": [
            {
                "record_id": item.get("id"),
                "record_version": item.get("version"),
                "date": item["datestr"],
                "title": item["title"],
                "text": item["experience_text"],
            }
            for item in completed
            if item.get("experience_text")
        ],
        "action_feedback": [
            {
                "record_id": item.get("id"),
                "record_version": item.get("version"),
                "action_index": action_index,
                "date": item["datestr"],
                "title": item["title"],
                "action_key": action.get("key"),
                "label": action.get("label") or "",
                "text": action.get("note") or "",
            }
            for item in completed
            for action_index, action in enumerate(item["actions"])
            if action.get("note")
        ],
    }


def facts_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_markdown_summary(path: Path, payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    sync = payload["sync"]
    planning_eligible = payload["planning_eligible"]
    lines = [
        "# Mac 学员训练事实包",
        "",
        "## 数据源",
        "",
        f"- 来源：{payload['source']}",
        f"- 生成时间：{payload['generated_at']}",
        f"- 覆盖日期：{summary['week_start']} 至 {summary['week_end']}",
        f"- SynFit.app：{'已启动' if sync['launched'] else '未启动'}；进程检查：{'有进程' if sync['processes'] else '未检测到进程'}",
        f"- 本地同步状态：{'已通过轮询' if sync['completed'] else '未完全确认'}",
        f"- 计划输入资格：{'可用于复盘和计划' if planning_eligible else '不可用于复盘和计划，仅限测试或诊断'}",
        f"- 资格判定原因：{payload['planning_eligibility_reason']}",
        f"- API 降级：未使用",
        "",
        "## 本周事实摘要",
        "",
        f"- 统计完整性：{'完整' if summary.get('statistics_complete', False) else '不完整；不得把汇总当作全部训练量'}；未分类负重完成组：{summary.get('unclassified_loaded_sets', '未知')}。",
        f"- 训练记录：{summary['record_count']} 条；已完成：{summary['completed_records']} 条。",
        f"- 力量记录：{summary['strength_records']} 条；"
        f"有氧记录：{summary['cardio_records']} 条；"
        f"休息或未完成计划记录：{summary['rest_or_plan_records']} 条。",
        f"- 力量合计：训记记录组 {summary['sets']} 组，"
        f"左右展开后侧组 {summary['side_sets']} 组，"
        f"热身组 {summary['heat_sets']} 组，正式次数 {summary['reps']} 次，"
        f"估算总吨位 {summary['tonnage']} kg。",
        "",
        "## 逐日事实",
        "",
        "| 日期 | 记录 | 已完成 | 力量 | 有氧 | 休息/未完成 | 正式组 | 热身组 | 标题 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for day in summary["daily"]:
        titles = "；".join(day["titles"]) if day["titles"] else "-"
        lines.append(
            f"| {day['date']} | {day['records']} | {day['completed']} | {day['strength']} | "
            f"{day['cardio']} | {day['rest_or_plan']} | {day['sets']} | {day['heat_sets']} | {titles} |"
        )
    lines.extend(["", "## 动作组数前 20", ""])
    if summary["top_actions_by_sets"]:
        lines.extend(["| 动作 | 组数 |", "|---|---:|"])
        for label, count in summary["top_actions_by_sets"]:
            lines.append(f"| {label} | {count} |")
    else:
        lines.append("无已完成力量动作。")
    lines.extend(["", "## 动作主观难度", ""])
    if summary.get("difficulty_actions"):
        counts = "；".join(
            f"{item['label']} {item['count']}" for item in summary.get("difficulty_counts", [])
        )
        lines.append(f"- 汇总：{counts}")
        lines.extend(["", "| 日期 | 动作 | 标记 | 完成组 | 最高重量 |", "|---|---|---|---:|---:|"])
        for item in summary["difficulty_actions"]:
            max_weight = item["max_weight"] if item["max_weight"] is not None else "-"
            lines.append(
                f"| {item['date']} | {item['label']} | {item['difficulty_label']} "
                f"`{item['difficulty']}` | {item['done_sets']} | {max_weight} |"
            )
    else:
        lines.append("无动作级主观难度标记。")
    lines.extend(["", "## 训练心得事实", ""])
    if summary["experience_feedback"]:
        for item in summary["experience_feedback"]:
            text = str(item["text"]).replace("\n", "；")
            lines.append(f"- {item['date']} {item['title']}：{text}")
    else:
        lines.append("无训练心得。")
    lines.extend(["", "## 动作备注事实", ""])
    if summary["action_feedback"]:
        for item in summary["action_feedback"]:
            text = str(item["text"]).replace("\n", "；")
            lines.append(f"- {item['date']} {item['title']} / {item['label']}：{text}")
    else:
        lines.append("无动作备注。")
    if sync["pending_rows"]:
        lines.extend(["", "## 同步未确认记录", ""])
        lines.extend(["| 日期 | 标题 | start | end | sync_type |", "|---|---|---:|---:|---|"])
        for item in sync["pending_rows"]:
            lines.append(
                f"| {item.get('datestr')} | {item.get('title') or ''} | {item.get('start')} | "
                f"{item.get('end')} | {item.get('sync_type')} |"
            )
    lines.extend(
        [
            "",
            "## 给教练的输入边界",
            "",
            "本文件只记录 Mac 本地可读事实，不判断计划差异、不解释疲劳原因、不生成下一轮安排。",
        ]
    )
    return markdown_with_metadata(path, "\n".join(lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mac 学员事实采集：启动 SynFit，同步后读取本地 Xunji.db，生成给教练的低 token 事实包。",
    )
    parser.add_argument(
        "--workspace",
        help="Fitness workspace root. Defaults to FITNESS_WORKSPACE or discovery from the current directory.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--week-start", help="周一日期，格式 YYYY-MM-DD。默认当前周。")
    group.add_argument("--previous-week", action="store_true", help="采集上一个完整自然周。")
    parser.add_argument("--sync-wait", type=int, default=90, help="等待本地库同步的秒数，默认 90。")
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=15,
        help="同步字段完成后要求数据库内容保持稳定的秒数，默认 15。",
    )
    parser.add_argument("--process-wait", type=int, default=45, help="等待 SynFit 进程出现的秒数，默认 45。")
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="不启动 SynFit，仅用于脚本结构测试或诊断；输出不可用于复盘和计划，也不覆盖正式事实包。",
    )
    parser.add_argument("--print-json", action="store_true", help="把运行摘要输出为 JSON。")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    ensure_fitness_root()
    today = now().date()
    if args.week_start:
        start = parse_date(args.week_start)
        week = WeekRange(start=start, end=start + timedelta(days=6))
    elif args.previous_week:
        week = previous_week(today)
    else:
        week = current_week(today)

    log_lines = [f"[{fmt_now()}] mac student facts refresh start week={week.key}"]
    if not SYNC_DOC.exists():
        raise RefreshError(f"缺少本地导出说明: {SYNC_DOC}")

    db_path, columns = choose_db()
    launched = False
    if not args.no_launch:
        launch_synfit()
        launched = True
    processes = wait_for_process(args.process_wait) if not args.no_launch else process_snapshot()
    if args.settle_seconds < 0:
        raise RefreshError("--settle-seconds 不得为负数")
    sync_completed, pending_rows = wait_for_db_state(
        db_path,
        week,
        args.sync_wait,
        settle_seconds=args.settle_seconds,
    )
    from .session_bridge import load_action_capabilities
    capabilities = load_action_capabilities(resolve_workspace(FITNESS_ROOT))
    records = query_week_records(db_path, week, capabilities)
    summary = build_summary(records, week)
    planning_eligible, planning_eligibility_reason = planning_eligibility(
        launched=launched,
        processes=processes,
        sync_wait=args.sync_wait,
        sync_completed=sync_completed,
    )
    source = (
        "Mac local SQLite localtrains after launching SynFit.app"
        if launched
        else "Mac local SQLite localtrains without launching SynFit.app; test or diagnostic only"
    )

    payload = {
        "facts_schema_version": 5,
        "normalization_capabilities_sha256": facts_hash(capabilities),
        "statistics_complete": summary["statistics_complete"],
        "source": source,
        "generated_at": fmt_now(),
        "planning_eligible": planning_eligible,
        "planning_eligibility_reason": planning_eligibility_reason,
        "fitness_root": str(FITNESS_ROOT),
        "db_path": str(db_path),
        "schema": {"localtrains_columns": sorted(columns)},
        "week": {"start": week.start.isoformat(), "end": week.end.isoformat(), "key": week.key},
        "sync": {
            "synfit_app": str(SYNFIT_APP),
            "launched": launched,
            "processes": processes,
            "completed": sync_completed,
            "pending_rows": pending_rows,
        },
        "summary": summary,
        "records": records,
        "api_fallback": {"used": False, "reason": "Mac local SQLite was readable; API is not a primary data source."},
    }
    digest = facts_hash(
        {
            "facts_schema_version": payload["facts_schema_version"],
            "summary": summary,
            "records": records,
            "planning_eligible": planning_eligible,
            "planning_eligibility_reason": planning_eligibility_reason,
            "sync": {
                "completed": sync_completed,
                "pending_rows": pending_rows,
                "launched": launched,
            },
        }
    )

    suffix = "" if planning_eligible else "_test_only"
    raw_path = RAW_CACHE_DIR / f"week_{week.key}{suffix}.json"
    facts_path = CACHE_DIR / f"facts_{week.key}{suffix}.json"
    summary_path = CACHE_DIR / f"summary_{week.key}{suffix}.md"
    latest_path = CACHE_DIR / f"latest_summary{suffix}.md"
    state_path = CACHE_DIR / f"state{suffix}.json"
    state = read_json(state_path)
    output_paths = [raw_path, facts_path, summary_path, latest_path]
    facts_unchanged = (
        state.get("last_week_key") == week.key
        and state.get("last_digest") == digest
        and all(path.exists() for path in output_paths)
    )

    changed_files = []
    if not facts_unchanged:
        # Keep content-addressed evidence before replacing the latest weekly view.
        for path in (raw_path, facts_path):
            if path.exists():
                snapshot = path.parent / "history" / (file_sha256(path) + ".json")
                if not snapshot.exists():
                    atomic_write_text(snapshot, path.read_text(encoding="utf-8"))
        for path, writer_payload in ((raw_path, payload), (facts_path, payload)):
            if atomic_write_json(path, writer_payload):
                changed_files.append(str(path.relative_to(FITNESS_ROOT)))
        md_text = build_markdown_summary(summary_path, payload)
        if atomic_write_text(summary_path, md_text):
            changed_files.append(str(summary_path.relative_to(FITNESS_ROOT)))
        latest_text = build_markdown_summary(latest_path, payload)
        if atomic_write_text(latest_path, latest_text):
            changed_files.append(str(latest_path.relative_to(FITNESS_ROOT)))

        output_sha256 = {
            str(path.relative_to(FITNESS_ROOT)): file_sha256(path)
            for path in output_paths
            if path.exists()
        }

        state.update(
            {
                "last_week_key": week.key,
                "last_digest": digest,
                "last_generated_at": payload["generated_at"],
                "last_source": payload["source"],
                "last_planning_eligible": planning_eligible,
                "last_changed_files": changed_files,
                "last_output_sha256": output_sha256,
            }
        )
        if atomic_write_json(state_path, state):
            changed_files.append(str(state_path.relative_to(FITNESS_ROOT)))

    output_sha256 = {
        str(path.relative_to(FITNESS_ROOT)): file_sha256(path)
        for path in output_paths
        if path.exists()
    }
    status = "test_only" if not planning_eligible else ("changed" if changed_files else "unchanged")
    log_lines.extend(
        [
            f"source\tMac local SQLite localtrains; SynFit.app launched={launched}",
            (
                f"planning\teligible={planning_eligible}\tmode={'formal' if planning_eligible else 'test_only'}"
                f"\treason={planning_eligibility_reason}"
            ),
            f"db\t{db_path}",
            f"sync\tcompleted={sync_completed}\tpending_rows={len(pending_rows)}\tprocesses={len(processes)}",
            (
                f"week\t{week.start.isoformat()}..{week.end.isoformat()}"
                f"\trecords={summary['record_count']}\tcompleted={summary['completed_records']}"
                f"\tstrength={summary['strength_records']}\tcardio={summary['cardio_records']}"
                f"\tsets={summary['sets']}\ttoken_role=student_facts_only"
            ),
            f"api_fallback\tused=false\treason=mac_local_sqlite_readable",
            f"outputs\tstatus={status}\tfiles={','.join(changed_files) if changed_files else '-'}",
            f"[{fmt_now()}] mac student facts refresh end status=0",
        ]
    )
    append_log(log_lines)
    return {
        "status": status,
        "week": week.key,
        "changed_files": changed_files,
        "records": summary["record_count"],
        "completed_records": summary["completed_records"],
        "generated_at": payload["generated_at"],
        "synfit_launched": launched,
        "planning_eligible": planning_eligible,
        "planning_eligibility_reason": planning_eligibility_reason,
        "sync_completed": sync_completed,
        "pending_rows": len(pending_rows),
        "facts_path": str(facts_path),
        "summary_path": str(summary_path),
        "output_sha256": output_sha256,
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        configure_workspace(args.workspace)
        result = run(args)
    except Exception as exc:
        append_log(
            [
                f"[{fmt_now()}] mac student facts refresh failed",
                f"source\tMac local SQLite localtrains",
                f"api_fallback\tused=false\treason={exc}",
                f"error\t{exc}",
            ]
        )
        print(f"weekly_mac_student_refresh.py: {exc}", file=sys.stderr)
        return 1
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "mac student facts refresh: "
            f"{result['status']} week={result['week']} records={result['records']} "
            f"completed={result['completed_records']} sync_completed={result['sync_completed']} "
            f"planning_eligible={result['planning_eligible']}"
        )
        print(f"summary: {result['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
