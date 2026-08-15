"""Timezone helpers that remain deterministic without a system IANA database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def shanghai_timezone() -> tzinfo:
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


SHANGHAI_TZ = shanghai_timezone()


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)
