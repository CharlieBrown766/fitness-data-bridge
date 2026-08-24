#!/usr/bin/env python3
"""Collect Mac-local Xunji facts for the coach without doing training analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
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
SYNC_DOC = PLUGIN_ROOT / "skills" / "fitness-ops" / "references" / "operations.md"
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
    query_end = min(end, today)
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
              and coalesce(delflag, 0) = 0
            order by datestr, id
            """,
            (start.isoformat(), query_end.isoformat()),
        ).fetchall()
    pending: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        completed_row = is_completed_time(row_dict.get("start"), row_dict.get("end"))
        if completed_row and row_dict.get("sync_type") != "done":
            pending.append(row_dict)
    return pending


def wait_for_db_state(db_path: Path, week: WeekRange, timeout_seconds: int) -> tuple[bool, list[dict[str, Any]]]:
    deadline = time.time() + timeout_seconds
    last_pending: list[dict[str, Any]] = []
    while time.time() < deadline:
        try:
            last_pending = query_recent_pending(db_path, week.start, week.end)
        except sqlite3.Error:
            time.sleep(3)
            continue
        if not last_pending:
            return True, []
        time.sleep(5)
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
        return float(str(value).replace(",", "."))
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


def simplify_set(set_obj: dict[str, Any], completed_row: bool) -> dict[str, Any]:
    return {
        "set_type": set_obj.get("setType") or "",
        "weight": set_obj.get("weight"),
        "reps": set_obj.get("reps"),
        "time": set_obj.get("time"),
        "done": is_done_set(set_obj, completed_row),
        "dropset": set_obj.get("dropset"),
    }


def analyze_action(action: dict[str, Any], completed_row: bool) -> dict[str, Any]:
    sets = action.get("sets") if isinstance(action.get("sets"), list) else []
    simplified_sets = [simplify_set(item, completed_row) for item in sets if isinstance(item, dict)]
    exetype = action.get("exetype")
    done_sets = [item for item in simplified_sets if item["done"]]
    heat_sets = [item for item in done_sets if item.get("set_type") == "热"]
    counted_sets = 0
    counted_reps = 0
    counted_tonnage = 0.0
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
            if exetype == "weight" and weight is not None:
                counted_tonnage += weight * reps
                max_weight = weight if max_weight is None else max(max_weight, weight)

    return {
        "key": action.get("key"),
        "label": action.get("label") or "",
        "type": action.get("type") or "",
        "exetype": exetype or "",
        "difficulty": normalize_difficulty(action.get("difficulty")),
        "difficulty_label": DIFFICULTY_LABELS.get(normalize_difficulty(action.get("difficulty")), ""),
        "note": action.get("note") or "",
        "set_count": len(simplified_sets),
        "done_sets": len(done_sets),
        "heat_sets": len(heat_sets),
        "undone_sets": max(0, len(simplified_sets) - len(done_sets)),
        "counted_sets": counted_sets,
        "counted_reps": counted_reps,
        "counted_tonnage": round(counted_tonnage),
        "max_weight": max_weight,
        "sets": simplified_sets,
    }


def classify_record(title: str, actions: list[dict[str, Any]], completed_row: bool) -> tuple[bool, bool, bool]:
    is_rest = "休息" in title or (not actions and not completed_row)
    has_strength = any(action.get("exetype") in STRENGTH_EXETYPES for action in actions)
    is_cardio = any(word in title for word in CARDIO_WORDS) and not has_strength
    is_strength = has_strength
    return is_rest, is_cardio, is_strength


def query_week_records(db_path: Path, week: WeekRange) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            select id, datestr, title, start, end, movement, note, sync_type, version, delflag
            from localtrains
            where datestr between ? and ?
              and coalesce(delflag, 0) = 0
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
        actions = [analyze_action(item, completed_row) for item in movement if isinstance(item, dict)]
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
                "datestr": row_dict.get("datestr"),
                "title": title,
                "start": row_dict.get("start"),
                "end": row_dict.get("end"),
                "note_text": note_text(row_dict.get("note")),
                "sync_type": row_dict.get("sync_type"),
                "version": row_dict.get("version"),
                "is_rest": is_rest,
                "is_completed": completed_row,
                "is_cardio": is_cardio,
                "is_strength": is_strength,
                "sets": sum(int(action["counted_sets"]) for action in actions),
                "heat_sets": sum(int(action["heat_sets"]) for action in actions),
                "reps": sum(int(action["counted_reps"]) for action in actions),
                "tonnage": sum(int(action["counted_tonnage"]) for action in actions),
                "parts": dict(parts),
                "actions": actions,
            }
        )
    return records


def build_summary(records: list[dict[str, Any]], week: WeekRange) -> dict[str, Any]:
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
                "heat_sets": sum(int(item["heat_sets"]) for item in day_records if item["is_completed"]),
                "reps": sum(int(item["reps"]) for item in day_records if item["is_completed"]),
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
    return {
        "week_start": week.start.isoformat(),
        "week_end": week.end.isoformat(),
        "record_count": len(records),
        "completed_records": len(completed),
        "strength_records": sum(1 for item in completed if item["is_strength"]),
        "cardio_records": sum(1 for item in completed if item["is_cardio"]),
        "rest_or_plan_records": sum(1 for item in records if item["is_rest"] or not item["is_completed"]),
        "sets": sum(int(item["sets"]) for item in completed),
        "heat_sets": sum(int(item["heat_sets"]) for item in completed),
        "reps": sum(int(item["reps"]) for item in completed),
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
    }


def facts_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        f"- 训练记录：{summary['record_count']} 条；已完成：{summary['completed_records']} 条。",
        f"- 力量记录：{summary['strength_records']} 条；"
        f"有氧记录：{summary['cardio_records']} 条；"
        f"休息或未完成计划记录：{summary['rest_or_plan_records']} 条。",
        f"- 力量合计：正式工作组 {summary['sets']} 组，"
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
    lines.extend(["", "## 备注事实", ""])
    if summary["notes"]:
        for item in summary["notes"]:
            text = str(item["note"]).replace("\n", "；")
            lines.append(f"- {item['date']} {item['title']}：{text}")
    else:
        lines.append("无训练备注。")
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
    sync_completed, pending_rows = wait_for_db_state(db_path, week, args.sync_wait)
    records = query_week_records(db_path, week)
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
        "facts_schema_version": 2,
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
        for path, writer_payload in ((raw_path, payload), (facts_path, payload)):
            if atomic_write_json(path, writer_payload):
                changed_files.append(str(path.relative_to(FITNESS_ROOT)))
        md_text = build_markdown_summary(summary_path, payload)
        if atomic_write_text(summary_path, md_text):
            changed_files.append(str(summary_path.relative_to(FITNESS_ROOT)))
        latest_text = build_markdown_summary(latest_path, payload)
        if atomic_write_text(latest_path, latest_text):
            changed_files.append(str(latest_path.relative_to(FITNESS_ROOT)))

        state.update(
            {
                "last_week_key": week.key,
                "last_digest": digest,
                "last_generated_at": payload["generated_at"],
                "last_source": payload["source"],
                "last_planning_eligible": planning_eligible,
                "last_changed_files": changed_files,
            }
        )
        if atomic_write_json(state_path, state):
            changed_files.append(str(state_path.relative_to(FITNESS_ROOT)))

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
