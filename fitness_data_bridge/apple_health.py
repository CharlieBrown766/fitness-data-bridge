#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from io import TextIOWrapper
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import FitnessDataBridgeError
from .layout import resolve_workspace


try:
    TZ = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import overlapping Apple Health merged JSON snapshots or a legacy "
            "Simple Health Export CSV zip and generate health-metric summaries."
        )
    )
    parser.add_argument(
        "--workspace",
        help="Fitness workspace root. Defaults to FITNESS_WORKSPACE or discovery from the current directory.",
    )
    parser.add_argument(
        "export_path",
        nargs="?",
        help=(
            "A health-merged-*.json or legacy HealthAll_*.zip path. If omitted, "
            "aggregate every merged JSON snapshot, falling back to the newest legacy zip."
        ),
    )
    parser.add_argument("--start", help="Start date, YYYY-MM-DD. Defaults to earliest key metric date.")
    parser.add_argument("--end", help="End date, YYYY-MM-DD. Defaults to latest complete key metric date.")
    parser.add_argument(
        "--keep-current-day",
        action="store_true",
        help="Keep a trailing partial day when the export includes one.",
    )
    parser.add_argument(
        "--move-source",
        action="store_true",
        help="Move the source export into raw/. The default copies and preserves the input.",
    )
    return parser.parse_args()


def ensure_dirs(*directories: Path) -> None:
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def find_export_sources(
    explicit_path: str | None,
    *,
    raw_dir: Path,
    merged_dir: Path,
) -> list[Path]:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() not in {".json", ".zip"}:
            raise ValueError(f"Unsupported Apple Health export: {path}")
        return [path]

    merged_candidates = sorted(
        merged_dir.glob("health-merged-*.json"),
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )
    if merged_candidates:
        return merged_candidates

    candidates = list(raw_dir.glob("HealthAll_*.zip"))
    if not candidates:
        raise FileNotFoundError(
            "No health-merged-*.json found in 数据/体况/apple-health/merged/ "
            "and no HealthAll_*.zip found in 数据/体况/apple-health/raw/."
        )
    return [max(candidates, key=lambda p: p.stat().st_mtime)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_source(path: Path, raw_dir: Path, *, move_source: bool) -> Path:
    dest = raw_dir / path.name
    if path.resolve() == dest.resolve():
        return dest
    if dest.exists():
        if file_sha256(path) == file_sha256(dest):
            if move_source:
                path.unlink()
            return dest
        stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
        dest = raw_dir / f"{path.stem}_{stamp}{path.suffix}"
    if move_source:
        shutil.move(str(path), str(dest))
    else:
        shutil.copy2(path, dest)
    return dest


def read_csv_from_zip(zf: zipfile.ZipFile, member: str | None) -> list[dict[str, str]]:
    if not member:
        return []
    with zf.open(member) as raw:
        text = TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        first = text.readline()
        if not first.startswith("sep="):
            text.seek(0)
        return list(csv.DictReader(text))


def find_member(names: list[str], prefix: str) -> str | None:
    matches = [name for name in names if name.startswith(prefix)]
    return matches[0] if matches else None


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = str(value).strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(TZ)
    except ValueError:
        pass
    try:
        return datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S %z").astimezone(TZ)
    except ValueError:
        return None


def parse_float(value: object | None) -> float:
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"[-+]?\d*\.?\d+", str(value))
        return float(match.group()) if match else math.nan


MERGED_SCHEMA_VERSION = "apple-health-fitness-merged-v1"
MERGED_DEDUPE_FIELDS = ("type", "startDate", "endDate", "value", "unit", "source")
SLEEP_VALUE_NAMES = {
    "0": "inBed",
    "1": "asleepUnspecified",
    "2": "awake",
    "3": "asleepCore",
    "4": "asleepDeep",
    "5": "asleepREM",
    "6": "asleepUnspecified",
}


def _record_dedupe_key(record: dict[str, object], bucket_type: str) -> tuple[str, ...]:
    declared = record.get("dedupe_key")
    source = declared if isinstance(declared, dict) else record
    values: list[str] = []
    for field in MERGED_DEDUPE_FIELDS:
        value = source.get(field)
        if field == "type" and value in (None, ""):
            value = bucket_type
        values.append(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return tuple(values)


def _normalize_merged_record(bucket_type: str, record: dict[str, object]) -> dict[str, object]:
    value = record.get("value")
    unit = str(record.get("unit") or "")
    if bucket_type == "sleep":
        numeric = str(value)
        if numeric.endswith(".0"):
            numeric = numeric[:-2]
        value = SLEEP_VALUE_NAMES.get(numeric, value)
    elif bucket_type == "oxygen-saturation" and unit == "%":
        numeric_value = parse_float(value)
        if math.isfinite(numeric_value):
            value = numeric_value / 100

    return {
        "type": str(record.get("type") or bucket_type),
        "startDate": record.get("startDate"),
        "endDate": record.get("endDate"),
        "value": value,
        "unit": unit,
        "sourceName": str(record.get("source") or "unknown"),
    }


def load_merged_snapshots(
    paths: list[Path],
) -> tuple[dict[str, list[dict[str, object]]], int, int, datetime | None]:
    deduped: dict[tuple[str, ...], tuple[str, dict[str, object]]] = {}
    scanned_records = 0
    latest_generated_at: datetime | None = None

    for path in sorted(paths, key=lambda item: (item.name, item.stat().st_mtime_ns)):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != MERGED_SCHEMA_VERSION:
            raise ValueError(f"Unsupported merged Apple Health schema in {path}")
        data_types = payload.get("data_types")
        if not isinstance(data_types, dict):
            raise ValueError(f"Merged Apple Health data_types must be an object in {path}")

        generated_at = parse_dt(payload.get("generated_at"))
        if generated_at and (latest_generated_at is None or generated_at > latest_generated_at):
            latest_generated_at = generated_at

        for bucket_type, bucket in data_types.items():
            if not isinstance(bucket_type, str) or not isinstance(bucket, dict):
                raise ValueError(f"Invalid merged Apple Health data type bucket in {path}")
            records = bucket.get("records")
            if not isinstance(records, list):
                raise ValueError(f"Merged Apple Health records must be an array for {bucket_type} in {path}")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(f"Merged Apple Health record must be an object for {bucket_type} in {path}")
                scanned_records += 1
                key = _record_dedupe_key(record, bucket_type)
                deduped[key] = (bucket_type, _normalize_merged_record(bucket_type, record))

    record_sets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for bucket_type, record in deduped.values():
        record_sets[bucket_type].append(record)
    for records in record_sets.values():
        records.sort(key=lambda row: (str(row.get("startDate") or ""), str(row.get("endDate") or "")))
    return dict(record_sets), scanned_records, scanned_records - len(deduped), latest_generated_at


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return ""
    if digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def date_range(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def add_source_sum_metric(
    daily: dict[date, dict[str, object]],
    rows: list[dict[str, str]],
    raw_col: str,
    estimate_col: str,
    multiplier: float = 1.0,
) -> set[date]:
    by_date_source: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    seen: set[date] = set()
    for row in rows:
        dt = parse_dt(row.get("startDate"))
        if not dt:
            continue
        value = parse_float(row.get("value")) * multiplier
        if not math.isfinite(value):
            continue
        day = dt.date()
        by_date_source[day][row.get("sourceName") or "unknown"] += value
        seen.add(day)
    for day, sources in by_date_source.items():
        values = list(sources.values())
        daily[day][raw_col] = sum(values)
        daily[day][estimate_col] = max(values) if values else math.nan
    return seen


def add_sum_metric(
    daily: dict[date, dict[str, object]],
    rows: list[dict[str, str]],
    col: str,
    multiplier: float = 1.0,
) -> set[date]:
    totals: dict[date, float] = defaultdict(float)
    seen: set[date] = set()
    for row in rows:
        dt = parse_dt(row.get("startDate"))
        if not dt:
            continue
        value = parse_float(row.get("value")) * multiplier
        if not math.isfinite(value):
            continue
        totals[dt.date()] += value
        seen.add(dt.date())
    for day, value in totals.items():
        daily[day][col] = value
    return seen


def add_mean_metric(
    daily: dict[date, dict[str, object]],
    rows: list[dict[str, str]],
    col: str,
    date_col: str = "startDate",
    multiplier: float = 1.0,
) -> set[date]:
    values: dict[date, list[float]] = defaultdict(list)
    seen: set[date] = set()
    for row in rows:
        dt = parse_dt(row.get(date_col))
        if not dt:
            continue
        value = parse_float(row.get("value")) * multiplier
        if not math.isfinite(value):
            continue
        values[dt.date()].append(value)
        seen.add(dt.date())
    for day, day_values in values.items():
        daily[day][col] = sum(day_values) / len(day_values)
    return seen


def add_last_metric(
    daily: dict[date, dict[str, object]],
    rows: list[dict[str, str]],
    col: str,
    multiplier: float = 1.0,
) -> set[date]:
    latest: dict[date, tuple[datetime, float]] = {}
    seen: set[date] = set()
    for row in rows:
        dt = parse_dt(row.get("startDate"))
        if not dt:
            continue
        value = parse_float(row.get("value")) * multiplier
        if not math.isfinite(value):
            continue
        day = dt.date()
        if day not in latest or dt > latest[day][0]:
            latest[day] = (dt, value)
        seen.add(day)
    for day, (_, value) in latest.items():
        daily[day][col] = value
    return seen


def add_heart_rate(
    daily: dict[date, dict[str, object]], rows: list[dict[str, str]]
) -> tuple[set[date], list[tuple[datetime, float]]]:
    values: dict[date, list[float]] = defaultdict(list)
    samples: list[tuple[datetime, float]] = []
    seen: set[date] = set()
    for row in rows:
        dt = parse_dt(row.get("startDate"))
        value = parse_float(row.get("value"))
        if not dt or not math.isfinite(value):
            continue
        day = dt.date()
        values[day].append(value)
        samples.append((dt, value))
        seen.add(day)
    for day, day_values in values.items():
        daily[day]["heart_avg_bpm"] = sum(day_values) / len(day_values)
        daily[day]["heart_max_bpm"] = max(day_values)
    samples.sort(key=lambda item: item[0])
    return seen, samples


def add_sleep(daily: dict[date, dict[str, object]], rows: list[dict[str, str]]) -> set[date]:
    grouped: dict[date, list[tuple[datetime, datetime, str, float]]] = defaultdict(list)
    seen: set[date] = set()
    for row in rows:
        start = parse_dt(row.get("startDate"))
        end = parse_dt(row.get("endDate"))
        if not start or not end:
            continue
        minutes = (end - start).total_seconds() / 60
        if minutes <= 0:
            continue
        day = end.date()
        grouped[day].append((start, end, row.get("value") or "", minutes))
        seen.add(day)

    for day, entries in grouped.items():
        asleep_total = sum(minutes for _, _, stage, minutes in entries if stage.startswith("asleep"))
        awake_total = sum(minutes for _, _, stage, minutes in entries if stage == "awake")
        daily[day]["sleep_total_min"] = asleep_total
        daily[day]["sleep_awake_min"] = awake_total
        daily[day]["sleep_core_min"] = sum(minutes for _, _, stage, minutes in entries if stage == "asleepCore")
        daily[day]["sleep_deep_min"] = sum(minutes for _, _, stage, minutes in entries if stage == "asleepDeep")
        daily[day]["sleep_rem_min"] = sum(minutes for _, _, stage, minutes in entries if stage == "asleepREM")
        daily[day]["sleep_start"] = min(start for start, _, _, _ in entries).strftime("%Y-%m-%d %H:%M")
        daily[day]["sleep_end"] = max(end for _, end, _, _ in entries).strftime("%Y-%m-%d %H:%M")
    return seen


def parse_workouts(
    zf: zipfile.ZipFile,
    names: list[str],
    daily: dict[date, dict[str, object]],
    heart_samples: list[tuple[datetime, float]],
) -> list[dict[str, object]]:
    workouts: list[dict[str, object]] = []
    for member in names:
        if not member.startswith("HKWorkoutActivityType"):
            continue
        for row in read_csv_from_zip(zf, member):
            start = parse_dt(row.get("startDate"))
            end = parse_dt(row.get("endDate"))
            if not start or not end:
                continue
            duration_min = parse_float(row.get("duration")) / 60
            kcal = parse_float(row.get("totalEnergyBurned"))
            distance_m = parse_float(row.get("totalDistance"))
            if (row.get("totalDistance") or "").lower().find("km") >= 0 and math.isfinite(distance_m):
                distance_m *= 1000
            segment = [value for dt, value in heart_samples if start <= dt <= end]
            workouts.append(
                {
                    "date": start.date(),
                    "start": start,
                    "activity": row.get("activityType") or member.split("_", 1)[0],
                    "duration_min": duration_min,
                    "kcal": kcal,
                    "distance_m": distance_m,
                    "avg_hr": sum(segment) / len(segment) if segment else math.nan,
                    "max_hr": max(segment) if segment else math.nan,
                    "source": row.get("sourceName") or "",
                }
            )

    by_day: dict[date, list[dict[str, object]]] = defaultdict(list)
    for workout in workouts:
        by_day[workout["date"]].append(workout)
    for day, day_workouts in by_day.items():
        daily[day]["workout_min"] = sum(
            float(workout["duration_min"])
            for workout in day_workouts
            if math.isfinite(float(workout["duration_min"]))
        )
        daily[day]["workout_count"] = len(day_workouts)
    return workouts


def populate_daily_metrics(
    record_sets: dict[str, list[dict[str, object]]],
) -> tuple[dict[date, dict[str, object]], set[date], list[tuple[datetime, float]]]:
    daily: dict[date, dict[str, object]] = defaultdict(dict)
    seen_dates: set[date] = set()

    def rows(name: str) -> list[dict[str, object]]:
        return record_sets.get(name, [])

    seen_dates |= add_source_sum_metric(daily, rows("steps"), "steps_raw_sum", "steps_est")
    seen_dates |= add_source_sum_metric(
        daily,
        rows("distance-walking-running"),
        "walkrun_km_raw_sum",
        "walkrun_km_est",
    )
    seen_dates |= add_sum_metric(daily, rows("active-energy"), "active_kcal")
    seen_dates |= add_sum_metric(daily, rows("apple-exercise-time"), "exercise_min")
    seen_dates |= add_sum_metric(daily, rows("apple-stand-time"), "stand_min")
    seen_dates |= add_sum_metric(daily, rows("distance-cycling"), "cycling_km")
    seen_dates |= add_sum_metric(daily, rows("flights-climbed"), "flights")
    seen_dates |= add_mean_metric(daily, rows("resting-heart-rate"), "rhr_bpm", "endDate")
    seen_dates |= add_mean_metric(daily, rows("hrv-sdnn"), "hrv_ms")
    seen_dates |= add_mean_metric(daily, rows("respiratory-rate"), "resp_rate_bpm")
    seen_dates |= add_mean_metric(
        daily,
        rows("oxygen-saturation"),
        "spo2_pct",
        multiplier=100,
    )
    seen_dates |= add_last_metric(daily, rows("body-mass"), "weight_kg")
    seen_dates |= add_last_metric(
        daily,
        rows("body-fat-percentage"),
        "bodyfat_pct",
        multiplier=100,
    )
    seen_dates |= add_last_metric(daily, rows("lean-body-mass"), "lean_body_mass_kg")
    seen_dates |= add_last_metric(daily, rows("vo2-max"), "vo2max")
    heart_seen, heart_samples = add_heart_rate(daily, rows("heart-rate"))
    seen_dates |= heart_seen
    seen_dates |= add_sleep(daily, rows("sleep"))
    return daily, seen_dates, heart_samples


def choose_date_window(
    args: argparse.Namespace,
    daily: dict[date, dict[str, object]],
    seen_dates: set[date],
    *,
    trailing_partial_date: date | None = None,
) -> tuple[date, date]:
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = min(seen_dates)

    if args.end:
        end = date.fromisoformat(args.end)
    else:
        end = max(seen_dates)
        if not args.keep_current_day:
            if trailing_partial_date == end and end > start:
                end = end - timedelta(days=1)
            else:
                last = daily[end]
                has_partial_signature = (
                    "steps_est" in last
                    and "active_kcal" not in last
                    and "exercise_min" not in last
                    and end > start
                )
                if has_partial_signature:
                    end = end - timedelta(days=1)
    return start, end


def mean(rows: list[dict[str, object]], col: str) -> float:
    values = [
        float(row[col])
        for row in rows
        if isinstance(row.get(col), (int, float))
        and math.isfinite(float(row[col]))
    ]
    return sum(values) / len(values) if values else math.nan


def latest_value(rows: list[dict[str, object]], col: str) -> tuple[str, float] | None:
    for row in reversed(rows):
        value = row.get(col)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return str(row["date"]), float(value)
    return None


def write_daily_csv(rows: list[dict[str, object]], output_path: Path) -> None:
    columns = [
        "date",
        "steps_est",
        "steps_raw_sum",
        "walkrun_km_est",
        "walkrun_km_raw_sum",
        "active_kcal",
        "exercise_min",
        "workout_min",
        "workout_count",
        "stand_min",
        "cycling_km",
        "flights",
        "rhr_bpm",
        "hrv_ms",
        "heart_avg_bpm",
        "heart_max_bpm",
        "resp_rate_bpm",
        "spo2_pct",
        "sleep_total_min",
        "sleep_awake_min",
        "sleep_core_min",
        "sleep_deep_min",
        "sleep_rem_min",
        "sleep_start",
        "sleep_end",
        "weight_kg",
        "bodyfat_pct",
        "lean_body_mass_kg",
        "vo2max",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            formatted = {}
            for column in columns:
                value = row.get(column, "")
                if isinstance(value, float):
                    formatted[column] = fmt_num(value)
                else:
                    formatted[column] = value
            writer.writerow(formatted)


def write_summary_md(
    rows: list[dict[str, object]],
    workouts: list[dict[str, object]],
    source_paths: list[Path],
    csv_name: str,
    output_path: Path,
) -> None:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    created = now
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines()[:2]:
            if line.startswith("> Created time: "):
                created = line.removeprefix("> Created time: ").strip()
                break
    last7 = rows[-7:]
    sleep_rows = [row for row in rows if isinstance(row.get("sleep_total_min"), (int, float))]
    workout_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for workout in workouts:
        day = workout["date"]
        if not (date.fromisoformat(rows[0]["date"]) <= day <= date.fromisoformat(rows[-1]["date"])):
            continue
        activity = str(workout["activity"])
        workout_totals[activity]["sessions"] += 1
        for col in ["duration_min", "kcal", "distance_m"]:
            value = workout.get(col)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                workout_totals[activity][col] += float(value)

    def coverage(col: str) -> int:
        return sum(
            1
            for row in rows
            if row.get(col) not in ("", None)
            and not (
                isinstance(row.get(col), float)
                and not math.isfinite(float(row[col]))
            )
        )

    apple_health_dir = output_path.parent.parent.resolve()

    def source_ref(path: Path) -> str:
        try:
            relative = path.resolve().relative_to(apple_health_dir)
            return f"../{relative.as_posix()}"
        except ValueError:
            return path.name

    if len(source_paths) == 1:
        source_lines = [f"- 数据输入：`{source_ref(source_paths[0])}`"]
    else:
        source_lines = [
            f"- 数据输入：{len(source_paths)} 个重叠合并快照。",
            f"- 最早快照：`{source_ref(source_paths[0])}`",
            f"- 最新快照：`{source_ref(source_paths[-1])}`",
            "- 快照去重键：`type + startDate + endDate + value + unit + source`。",
        ]

    lines = [
        f"> Created time: {created}",
        f"> Modified time: {now}",
        "",
        f"# 健康数据摘要：{rows[0]['date']} 至 {rows[-1]['date']}",
        "",
        *source_lines,
        f"- 日汇总 CSV：`{csv_name}`",
        "- 时区：Asia/Shanghai",
        "- 口径：步数和步行/跑步距离同时存在 iPhone 与 Apple Watch 来源，日汇总使用“按来源分别求和后取较大值”的保守估算，保留原始合计列用于排查。",
        "",
        "## 数据可用性",
        "",
        "| 指标 | 覆盖 | 说明 |",
        "|---|---:|---|",
        f"| 步数 | {coverage('steps_est')}/{len(rows)} 天 | iPhone 与 Apple Watch 可能重复，使用 `steps_est`。 |",
        f"| 主动消耗 | {coverage('active_kcal')}/{len(rows)} 天 | 可能包含 Apple Watch 与少量训记来源。 |",
        f"| 静息心率 | {coverage('rhr_bpm')}/{len(rows)} 天 | 可用于恢复趋势。 |",
        f"| HRV | {coverage('hrv_ms')}/{len(rows)} 天 | 使用 SDNN，重趋势不重单日。 |",
        f"| 睡眠 | {coverage('sleep_total_min')}/{len(rows)} 晚 | 记录太少时不作为主要排期依据。 |",
        f"| 体重 | {coverage('weight_kg')}/{len(rows)} 天 | 米家等体重来源。 |",
        f"| 体脂 | {coverage('bodyfat_pct')}/{len(rows)} 天 | 低频参考。 |",
        f"| VO2max | {coverage('vo2max')}/{len(rows)} 天 | 样本少时只观察趋势。 |",
        "",
        "## 关键指标",
        "",
        f"- 估算日均步数：{mean(rows, 'steps_est'):,.0f} 步；最近 7 天约 {mean(last7, 'steps_est'):,.0f} 步。",
        f"- 日均主动消耗：{mean(rows, 'active_kcal'):,.0f} kcal；最近 7 天约 {mean(last7, 'active_kcal'):,.0f} kcal。",
        f"- 日均锻炼时间：{mean(rows, 'exercise_min'):.1f} 分钟；最近 7 天约 {mean(last7, 'exercise_min'):.1f} 分钟。",
        f"- 静息心率均值：{mean(rows, 'rhr_bpm'):.1f} bpm；最近 7 天约 {mean(last7, 'rhr_bpm'):.1f} bpm。",
        f"- HRV 均值：{mean(rows, 'hrv_ms'):.1f} ms；最近 7 天约 {mean(last7, 'hrv_ms'):.1f} ms。",
    ]

    for label, col, suffix in [
        ("最新体重", "weight_kg", " kg"),
        ("最新体脂", "bodyfat_pct", "%"),
        ("最新去脂体重", "lean_body_mass_kg", " kg"),
    ]:
        latest = latest_value(rows, col)
        if latest:
            value = f"{latest[1]:.1f}"
            lines.append(f"- {label}：{value}{suffix}（{latest[0]}）。")

    lines.extend(["", "## 训练记录", ""])
    if workout_totals:
        total_sessions = int(sum(v["sessions"] for v in workout_totals.values()))
        total_minutes = sum(v["duration_min"] for v in workout_totals.values())
        lines.append(f"- 训练记录合计：{total_sessions} 次，约 {total_minutes:.0f} 分钟。")
        lines.extend(["", "| 类型 | 次数 | 总时长 | 估算消耗 | 距离 |", "|---|---:|---:|---:|---:|"])
        for activity, values in sorted(workout_totals.items(), key=lambda item: item[1]["duration_min"], reverse=True):
            lines.append(
                f"| {activity} | {int(values['sessions'])} | {values['duration_min']:.0f} 分钟 | "
                f"{values['kcal']:.0f} kcal | {values['distance_m'] / 1000:.1f} km |"
            )
    else:
        lines.append("- 没有读取到训练记录。")

    lines.extend(["", "## 睡眠记录", ""])
    if sleep_rows:
        lines.extend(["| 日期 | 睡眠时长 | 清醒 | 入睡-起床 |", "|---|---:|---:|---|"])
        for row in sleep_rows:
            lines.append(
                f"| {row['date']} | {float(row['sleep_total_min']) / 60:.2f} h | "
                f"{float(row.get('sleep_awake_min') or 0):.1f} min | "
                f"{row.get('sleep_start', '')} -> {row.get('sleep_end', '')} |"
            )
        if len(sleep_rows) < len(rows) * 0.5:
            lines.extend(["", "睡眠记录覆盖不足，无法从本导出稳定描述恢复趋势。"])
    else:
        lines.append("没有读取到睡眠记录。")

    vo2_rows = [row for row in rows if isinstance(row.get("vo2max"), (int, float))]
    lines.extend(["", "## VO2max", ""])
    if vo2_rows:
        for row in vo2_rows:
            lines.append(f"- {row['date']}：{float(row['vo2max']):.2f} mL/min/kg")
        if len(vo2_rows) < 4:
            lines.extend(["", "样本较少，暂不据此判断有氧能力变化。"])
    else:
        lines.append("没有读取到 VO2max 记录。")

    lines.extend(
        [
            "",
            "## 数据口径",
            "",
            "- 本摘要和日汇总 CSV 是原始 Apple Health 导出的派生事实，不包含个人训练决策。",
            "- `steps_est` 是按来源分别求和后取较大值的保守去重估计；`steps_raw_sum` 保留用于核对。",
            "- 原始导出和合并快照保留在各自数据目录，用于复核清洗口径或补充指标。",
            "",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    args = parse_args()
    try:
        layout = resolve_workspace(args.workspace)
    except FitnessDataBridgeError as exc:
        raise SystemExit(str(exc)) from exc

    apple_health_dir = layout.apple_health_dir
    raw_dir = apple_health_dir / "raw"
    merged_dir = apple_health_dir / "merged"
    scratch_dir = apple_health_dir / "parsed"
    ensure_dirs(raw_dir, merged_dir, scratch_dir, apple_health_dir)
    source_paths = [
        stage_source(
            path,
            merged_dir if path.suffix.lower() == ".json" else raw_dir,
            move_source=args.move_source,
        )
        for path in find_export_sources(
            args.export_path,
            raw_dir=raw_dir,
            merged_dir=merged_dir,
        )
    ]
    source_paths.sort(key=lambda path: (path.name, path.stat().st_mtime_ns))

    trailing_partial_date: date | None = None
    if all(path.suffix.lower() == ".json" for path in source_paths):
        record_sets, scanned_records, duplicates_removed, latest_generated_at = load_merged_snapshots(
            source_paths
        )
        daily, seen_dates, _ = populate_daily_metrics(record_sets)
        workouts: list[dict[str, object]] = []
        if latest_generated_at:
            trailing_partial_date = latest_generated_at.date()
        print(f"Merged snapshots: {len(source_paths)}")
        print(f"Merged records:   {scanned_records - duplicates_removed}")
        print(f"Duplicates:      {duplicates_removed}")
    elif len(source_paths) == 1 and source_paths[0].suffix.lower() == ".zip":
        zip_path = source_paths[0]
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

            def legacy_rows(prefix: str) -> list[dict[str, str]]:
                return read_csv_from_zip(zf, find_member(names, prefix))

            record_sets = {
                "steps": legacy_rows("HKQuantityTypeIdentifierStepCount_"),
                "distance-walking-running": legacy_rows(
                    "HKQuantityTypeIdentifierDistanceWalkingRunning_"
                ),
                "active-energy": legacy_rows("HKQuantityTypeIdentifierActiveEnergyBurned_"),
                "apple-exercise-time": legacy_rows("HKQuantityTypeIdentifierAppleExerciseTime_"),
                "apple-stand-time": legacy_rows("HKQuantityTypeIdentifierAppleStandTime_"),
                "distance-cycling": legacy_rows("HKQuantityTypeIdentifierDistanceCycling_"),
                "flights-climbed": legacy_rows("HKQuantityTypeIdentifierFlightsClimbed_"),
                "resting-heart-rate": legacy_rows("HKQuantityTypeIdentifierRestingHeartRate_"),
                "hrv-sdnn": legacy_rows("HKQuantityTypeIdentifierHeartRateVariabilitySDNN_"),
                "respiratory-rate": legacy_rows("HKQuantityTypeIdentifierRespiratoryRate_"),
                "oxygen-saturation": legacy_rows("HKQuantityTypeIdentifierOxygenSaturation_"),
                "body-mass": legacy_rows("HKQuantityTypeIdentifierBodyMass_"),
                "body-fat-percentage": legacy_rows("HKQuantityTypeIdentifierBodyFatPercentage_"),
                "lean-body-mass": legacy_rows("HKQuantityTypeIdentifierLeanBodyMass_"),
                "vo2-max": legacy_rows("HKQuantityTypeIdentifierVO2Max_"),
                "heart-rate": legacy_rows("HKQuantityTypeIdentifierHeartRate_"),
                "sleep": legacy_rows("HKCategoryTypeIdentifierSleepAnalysis_"),
            }
            daily, seen_dates, heart_samples = populate_daily_metrics(record_sets)
            workouts = parse_workouts(zf, names, daily, heart_samples)
            seen_dates |= {workout["date"] for workout in workouts}
    else:
        raise ValueError("Apple Health import cannot mix merged JSON snapshots and legacy zip exports")

    if not seen_dates:
        raise RuntimeError(f"No usable records found in {', '.join(str(path) for path in source_paths)}")

    start, end = choose_date_window(
        args,
        daily,
        seen_dates,
        trailing_partial_date=trailing_partial_date,
    )
    output_rows = []
    for day in date_range(start, end):
        row = {"date": day.isoformat()}
        row.update(daily.get(day, {}))
        output_rows.append(row)

    csv_path = scratch_dir / f"每日恢复与活动_{start.isoformat()}_to_{end.isoformat()}.csv"
    md_path = scratch_dir / f"健康数据摘要_{start.isoformat()}_to_{end.isoformat()}.md"
    write_daily_csv(output_rows, csv_path)
    write_summary_md(output_rows, workouts, source_paths, csv_path.name, md_path)

    print(f"Input latest: {source_paths[-1]}")
    print(f"Daily CSV:  {csv_path}")
    print(f"Summary:    {md_path}")


if __name__ == "__main__":
    main()
