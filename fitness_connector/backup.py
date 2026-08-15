"""Operation-bound Xunji backup state machine."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ValidationError, WriteGateError
from .io import atomic_write_json, load_json, sha256_file
from .layout import WorkspaceLayout


BACKUP_SCHEMA_VERSION = "2.0"


def snapshot_sqlite_database(source: Path, destination: Path) -> None:
    """Create one standalone SQLite snapshot, including committed WAL pages."""

    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise ValidationError(f"Backup source is not a file: {source}")
    if destination.exists():
        raise WriteGateError(f"Backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        ) as live, closing(sqlite3.connect(destination)) as snapshot:
            live.backup(snapshot)
            snapshot.execute("pragma journal_mode=delete")
            snapshot.commit()
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise WriteGateError(f"Consistent SQLite snapshot failed: {exc}") from exc


def sqlite_snapshot_sha256(source: Path, *, temporary_directory: Path) -> str:
    """Hash the logical contents of a transient consistent SQLite snapshot."""

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".verify-Xunji-",
        suffix=".db",
        dir=temporary_directory,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    try:
        snapshot_sqlite_database(source, temporary_path)
        return sqlite_logical_sha256(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def sqlite_logical_sha256(database: Path) -> str:
    """Return a deterministic content hash independent of SQLite file layout."""

    digest = hashlib.sha256()
    with closing(
        sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    ) as connection:
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def discover_xunji_database() -> Path:
    explicit = os.environ.get("XUNJI_DB_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file():
            return path
        raise ValidationError(f"XUNJI_DB_PATH is not a file: {path}")
    if sys.platform != "darwin":
        raise ValidationError("Xunji.db discovery is available only on macOS")
    root = Path.home() / "Library" / "Containers"
    matches = sorted(root.glob("*/Data/Library/LocalDatabase/Xunji.db"))
    if len(matches) != 1:
        raise ValidationError(f"Expected one discovered Xunji.db, found {len(matches)}")
    return matches[0].resolve()


def _now() -> datetime:
    try:
        local_timezone = ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        local_timezone = timezone(timedelta(hours=8))
    return datetime.now(local_timezone)


def _operation_id() -> str:
    return _now().strftime("%Y%m%d_%H%M%S")


class BackupManager:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout
        self.root = layout.backups_dir
        self.status_path = self.root / "status.json"

    def status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {
                "backup_schema_version": BACKUP_SCHEMA_VERSION,
                "pending": None,
                "confirmed": None,
                "abandoned": [],
            }
        data = load_json(self.status_path)
        if not isinstance(data, dict) or data.get("backup_schema_version") != BACKUP_SCHEMA_VERSION:
            raise ValidationError(f"Unsupported backup status: {self.status_path}")
        if not isinstance(data.get("abandoned", []), list):
            raise ValidationError("backup status abandoned must be an array")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.status_path, data)

    def before(self, *, label: str, source: Path | None = None) -> str:
        state = self.status()
        if state.get("pending") is not None:
            raise WriteGateError("A backup operation is already pending")
        if not label.strip():
            raise ValidationError("Backup label must not be empty")
        database = source.resolve() if source else discover_xunji_database()
        if not database.is_file():
            raise ValidationError(f"Backup source is not a file: {database}")
        operation_id = _operation_id()
        snapshot_dir = self.root / "snapshots" / operation_id
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        before_path = snapshot_dir / "before-Xunji.db"
        snapshot_sqlite_database(database, before_path)
        state["pending"] = {
            "operation_id": operation_id,
            "label": label,
            "stage": "before",
            "created_at": _now().isoformat(timespec="seconds"),
            "source_kind": "runtime_discovered",
            "before_path": str(before_path.relative_to(self.layout.root).as_posix()),
            "before_sha256": sha256_file(before_path),
        }
        self._save(state)
        return operation_id

    def require_pending(self, operation_id: str, *, stage: str | None = None) -> dict[str, Any]:
        pending = self.status().get("pending")
        if not isinstance(pending, dict) or pending.get("operation_id") != operation_id:
            raise WriteGateError("Backup operation ID does not match the pending operation")
        if stage is not None and pending.get("stage") != stage:
            raise WriteGateError(
                f"Backup operation stage is {pending.get('stage')}, expected {stage}"
            )
        return pending

    def pending_before_matches_source(
        self, *, operation_id: str, source: Path | None = None
    ) -> bool:
        pending = self.require_pending(operation_id, stage="before")
        database = source.resolve() if source else discover_xunji_database()
        snapshot_dir = self.root / "snapshots" / operation_id
        before_value = pending.get("before_path")
        if not isinstance(before_value, str):
            return False
        before_path = (self.layout.root / before_value).resolve()
        if (
            not before_path.is_relative_to(self.layout.root.resolve())
            or not before_path.is_file()
            or pending.get("before_sha256") != sha256_file(before_path)
        ):
            return False
        current_hash = sqlite_snapshot_sha256(
            database,
            temporary_directory=snapshot_dir,
        )
        return sqlite_logical_sha256(before_path) == current_hash

    def after(self, *, operation_id: str, source: Path | None = None) -> dict[str, Any]:
        state = self.status()
        pending = self.require_pending(operation_id, stage="before")
        database = source.resolve() if source else discover_xunji_database()
        snapshot_dir = self.root / "snapshots" / operation_id
        after_path = snapshot_dir / "after-Xunji.db"
        snapshot_sqlite_database(database, after_path)
        pending.update(
            {
                "stage": "after",
                "after_at": _now().isoformat(timespec="seconds"),
                "after_path": str(after_path.relative_to(self.layout.root).as_posix()),
                "after_sha256": sha256_file(after_path),
            }
        )
        state["pending"] = pending
        self._save(state)
        return pending

    def confirm(self, *, operation_id: str) -> dict[str, Any]:
        state = self.status()
        pending = self.require_pending(operation_id, stage="after")
        confirmed = dict(pending)
        confirmed["stage"] = "confirmed"
        confirmed["confirmed_at"] = _now().isoformat(timespec="seconds")
        state["confirmed"] = confirmed
        state["pending"] = None
        self._save(state)
        return confirmed

    def abandon(self, *, operation_id: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValidationError("Abandon requires a reason")
        state = self.status()
        pending = self.require_pending(operation_id)
        abandoned = dict(pending)
        abandoned.update(
            {
                "stage": "abandoned",
                "abandoned_at": _now().isoformat(timespec="seconds"),
                "reason": reason,
            }
        )
        state.setdefault("abandoned", []).append(abandoned)
        state["pending"] = None
        self._save(state)
        return abandoned

    def cleanup(self, *, targets: list[Path], confirmation: str) -> list[Path]:
        if confirmation != "DELETE-LISTED-BACKUP-TARGETS":
            raise WriteGateError("Cleanup requires the exact confirmation phrase")
        protected = {self.status_path.resolve()}
        root = self.root.resolve()
        resolved: list[Path] = []
        for target in targets:
            candidate = target.expanduser().resolve()
            if not candidate.is_relative_to(root) or candidate in protected:
                raise WriteGateError(f"Refusing cleanup target: {target}")
            if not candidate.exists():
                raise ValidationError(f"Cleanup target does not exist: {target}")
            resolved.append(candidate)
        for candidate in resolved:
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
        return resolved
