"""SynFit launch and bounded synchronization polling."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from .errors import WriteGateError
from .sqlite_executor import SQLitePlanExecutor


SYNFIT_APP = Path("/Applications/SynFit.app")


def launch_synfit() -> None:
    if sys.platform != "darwin" or not SYNFIT_APP.exists():
        raise WriteGateError("SynFit is unavailable on this device")
    subprocess.run(["open", "-a", str(SYNFIT_APP)], check=True)


def wait_for_sync(
    executor: SQLitePlanExecutor,
    row_ids: list[int],
    *,
    timeout_seconds: int,
    poll_seconds: int = 5,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout_seconds, 0)
    latest = executor.sync_status(row_ids)
    while latest["pending_ids"] and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        latest = executor.sync_status(row_ids)
    return latest
