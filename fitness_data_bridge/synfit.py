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


def synfit_is_running() -> bool:
    """Return whether the SynFit app bundle already has a running process."""

    completed = subprocess.run(
        ["osascript", "-e", 'application "SynFit" is running'],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip().lower() == "true"


def launch_synfit() -> None:
    if sys.platform != "darwin" or not SYNFIT_APP.exists():
        raise WriteGateError("SynFit is unavailable on this device")
    if synfit_is_running():
        subprocess.run(
            ["osascript", "-e", 'tell application "SynFit" to quit'],
            check=True,
        )
        time.sleep(2)
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
