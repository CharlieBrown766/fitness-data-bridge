"""Dry-run and explicitly authorized Fitness session or half-Block publication."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from .backup import BackupManager, discover_xunji_database
from .calendar_writer import dry_run_calendar, readback_calendar, write_calendar
from .errors import ValidationError, WriteGateError
from .io import atomic_write_json, canonical_sha256, load_json, sha256_file
from .layout import WorkspaceLayout, resolve_workspace
from .release_bridge import project_release_path
from .session_bridge import load_next_session, project_session
from .sqlite_executor import SQLitePlanExecutor
from .synfit import launch_synfit, wait_for_sync


AUTHORIZATION_KEYS = {
    "authorization_schema_version",
    "operation",
    "session_id",
    "prescription_sha256",
    "scheduled_for",
    "confirmed",
    "one_time",
    "expires_at",
}
RELEASE_AUTHORIZATION_KEYS = {
    "authorization_schema_version",
    "operation",
    "phase_id",
    "block_id",
    "release_id",
    "revision",
    "release_sha256",
    "session_ids",
    "replacement_window",
    "confirmed",
    "one_time",
    "expires_at",
}


def _authorization(
    path: str | Path, *, session: dict[str, Any], layout: WorkspaceLayout
) -> tuple[dict[str, Any], str]:
    source = Path(path).expanduser().resolve()
    value = load_json(source)
    if not isinstance(value, dict) or set(value) != AUTHORIZATION_KEYS:
        raise WriteGateError("Authorization must use the exact connector schema")
    expected = {
        "authorization_schema_version": "1.0",
        "operation": "publish_next_session",
        "session_id": session["session_id"],
        "prescription_sha256": session["prescription_sha256"],
        "scheduled_for": session["schedule"]["scheduled_for"],
        "confirmed": True,
        "one_time": True,
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise WriteGateError(f"Authorization mismatch: {key}")
    try:
        expires = datetime.fromisoformat(str(value["expires_at"]))
    except ValueError as exc:
        raise ValidationError("Authorization expires_at must be ISO-8601") from exc
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc).astimezone(expires.tzinfo):
        raise WriteGateError("Authorization has expired")
    digest = sha256_file(source)
    if layout.receipts_dir.exists():
        for receipt_path in layout.receipts_dir.glob("*.json"):
            try:
                receipt = load_json(receipt_path)
            except (OSError, ValueError):
                continue
            if isinstance(receipt, dict) and receipt.get("authorization_sha256") == digest:
                raise WriteGateError("Authorization was already consumed")
    return value, digest


def dry_run_next(
    workspace: str | Path | WorkspaceLayout | None = None,
    *,
    database: str | Path | None = None,
) -> dict[str, Any]:
    layout, session = load_next_session(workspace)
    projection = project_session(session, layout=layout)
    database_result: dict[str, Any]
    if projection["app_records"]:
        if database is None:
            database_result = {
                "status": "projection only",
                "reason": "No Xunji.db path was supplied; target Mac dry-run remains required",
            }
        else:
            database_result = SQLitePlanExecutor(Path(database).expanduser().resolve()).dry_run(projection)
    else:
        database_result = {"status": "not applicable", "insert_count": 0}
    return {
        "status": "dry-run passed"
        if database_result.get("status") != "dry-run blocked"
        else "dry-run blocked",
        "session_id": session["session_id"],
        "projection_sha256": canonical_sha256(projection),
        "projection": projection,
        "database": database_result,
        "calendar": dry_run_calendar(projection),
        "live_write_performed": False,
    }


def publish_next(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    authorization_path: str | Path,
    database: str | Path | None = None,
    sync_timeout_seconds: int = 180,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Live publication requires the target macOS device")
    layout, session = load_next_session(workspace)
    projection = project_session(session, layout=layout)
    _, authorization_sha256 = _authorization(
        authorization_path, session=session, layout=layout
    )
    database_path = (
        Path(database).expanduser().resolve() if database is not None else discover_xunji_database()
    )
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = layout.receipts_dir / f"{timestamp}-{session['session_id']}.json"
    receipt: dict[str, Any] = {
        "connector_receipt_schema_version": "1.0",
        "status": "started",
        "session_id": session["session_id"],
        "prescription_sha256": session["prescription_sha256"],
        "projection_sha256": canonical_sha256(projection),
        "authorization_sha256": authorization_sha256,
        "database_identity_map": projection.get("database_identity_map", []),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_write_json(receipt_path, receipt)
    backup: BackupManager | None = None
    operation_id: str | None = None
    database_changed = False
    try:
        if projection["app_records"]:
            executor = SQLitePlanExecutor(database_path)
            preview = executor.dry_run(projection)
            receipt["database_dry_run"] = preview
            if preview["status"] != "dry-run passed":
                raise WriteGateError("Xunji database dry-run is blocked")
            backup = BackupManager(layout)
            operation_id = backup.before(
                label=f"publish {session['session_id']}", source=database_path
            )
            if not backup.pending_before_matches_source(
                operation_id=operation_id, source=database_path
            ):
                raise WriteGateError("Backup-before no longer matches Xunji.db")
            receipt["database"] = executor.write(projection)
            database_changed = True
            launch_synfit()
            affected_ids = receipt["database"]["soft_deleted_ids"] + receipt["database"]["inserted_ids"]
            receipt["synfit"] = wait_for_sync(
                executor, affected_ids, timeout_seconds=sync_timeout_seconds
            )
            if receipt["synfit"]["pending_ids"]:
                raise WriteGateError("SynFit synchronization timed out")
            database_readback = executor.readback(projection)
            receipt["database_readback"] = database_readback
            if database_readback["status"] != "DB read back" or database_readback["active_not_sync"]:
                raise WriteGateError("Xunji database read-back failed after SynFit sync")
        receipt["calendar"] = write_calendar(projection)
        receipt["calendar_readback"] = readback_calendar(projection)
        if receipt["calendar_readback"]["status"] != "Calendar read back":
            raise WriteGateError("Apple Calendar read-back did not match")
        if backup is not None and operation_id is not None:
            receipt["backup_after"] = backup.after(
                operation_id=operation_id, source=database_path
            )
            receipt["backup_confirmed"] = backup.confirm(operation_id=operation_id)
        receipt["status"] = "succeeded"
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        return receipt
    except Exception as exc:
        receipt["status"] = "partial" if database_changed else "failed"
        receipt["error"] = str(exc)
        if backup is not None and operation_id is not None:
            try:
                pending = backup.status().get("pending")
                if isinstance(pending, dict) and pending.get("stage") == "before" and database_changed:
                    receipt["backup_after"] = backup.after(
                        operation_id=operation_id, source=database_path
                    )
                receipt["backup_abandoned"] = backup.abandon(
                    operation_id=operation_id,
                    reason="connector publication failed; snapshots retained for recovery",
                )
            except Exception as backup_error:
                receipt["backup_error"] = str(backup_error)
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        raise


def _release_authorization(
    path: str | Path,
    *,
    release: dict[str, Any],
    projection: dict[str, Any],
    layout: WorkspaceLayout,
) -> tuple[dict[str, Any], str]:
    source = Path(path).expanduser().resolve()
    value = load_json(source)
    if not isinstance(value, dict) or set(value) != RELEASE_AUTHORIZATION_KEYS:
        raise WriteGateError("Release authorization must use the exact connector schema 2.0")
    expected = {
        "authorization_schema_version": "2.0",
        "operation": "publish_half_block_release",
        "phase_id": release["phase_id"],
        "block_id": release["block_id"],
        "release_id": release["release_id"],
        "revision": release["revision"],
        "release_sha256": projection["source"]["release_sha256"],
        "session_ids": projection["source"]["session_ids"],
        "replacement_window": projection["replacement_window"],
        "confirmed": True,
        "one_time": True,
    }
    for key, item in expected.items():
        if value.get(key) != item:
            raise WriteGateError(f"Release authorization mismatch: {key}")
    try:
        expires = datetime.fromisoformat(str(value["expires_at"]))
    except ValueError as exc:
        raise ValidationError("Authorization expires_at must be ISO-8601") from exc
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc).astimezone(expires.tzinfo):
        raise WriteGateError("Authorization has expired")
    digest = sha256_file(source)
    if layout.receipts_dir.exists():
        for receipt_path in layout.receipts_dir.glob("*.json"):
            try:
                receipt = load_json(receipt_path)
            except (OSError, ValueError):
                continue
            if isinstance(receipt, dict) and receipt.get("authorization_sha256") == digest:
                raise WriteGateError("Authorization was already consumed")
    return value, digest


def dry_run_release(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
    database: str | Path | None = None,
) -> dict[str, Any]:
    _, release, projection = project_release_path(workspace, release_path)
    if projection["app_records"]:
        if database is None:
            database_result: dict[str, Any] = {
                "status": "projection only",
                "reason": "No Xunji.db path was supplied; target Mac dry-run remains required",
            }
        else:
            database_result = SQLitePlanExecutor(Path(database).expanduser().resolve()).dry_run(
                projection
            )
    else:
        database_result = {"status": "not applicable", "insert_count": 0}
    return {
        "status": "dry-run passed"
        if database_result.get("status") != "dry-run blocked"
        else "dry-run blocked",
        "release": {
            "phase_id": release["phase_id"],
            "block_id": release["block_id"],
            "release_id": release["release_id"],
            "revision": release["revision"],
            "session_count": len(release["sessions"]),
        },
        "projection_sha256": canonical_sha256(projection),
        "projection": projection,
        "database": database_result,
        "calendar": dry_run_calendar(projection),
        "live_write_performed": False,
    }


def publish_release(
    workspace: str | Path | WorkspaceLayout | None,
    *,
    release_path: str | Path,
    authorization_path: str | Path,
    database: str | Path | None = None,
    sync_timeout_seconds: int = 180,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise WriteGateError("Live publication requires the target macOS device")
    layout, release, projection = project_release_path(workspace, release_path)
    _, authorization_sha256 = _release_authorization(
        authorization_path,
        release=release,
        projection=projection,
        layout=layout,
    )
    database_path = (
        Path(database).expanduser().resolve()
        if database is not None
        else discover_xunji_database()
    )
    release_label = f"{release['release_id']}-r{release['revision']:02d}"
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    receipt_path = layout.receipts_dir / f"{timestamp}-{release_label}.json"
    receipt: dict[str, Any] = {
        "connector_receipt_schema_version": "2.0",
        "status": "started",
        "phase_id": release["phase_id"],
        "block_id": release["block_id"],
        "release_id": release["release_id"],
        "revision": release["revision"],
        "session_ids": projection["source"]["session_ids"],
        "release_sha256": projection["source"]["release_sha256"],
        "projection_sha256": canonical_sha256(projection),
        "authorization_sha256": authorization_sha256,
        "database_identity_map": projection.get("database_identity_map", []),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    atomic_write_json(receipt_path, receipt)
    backup: BackupManager | None = None
    operation_id: str | None = None
    database_changed = False
    try:
        if projection["app_records"]:
            executor = SQLitePlanExecutor(database_path)
            preview = executor.dry_run(projection)
            receipt["database_dry_run"] = preview
            if preview["status"] != "dry-run passed":
                raise WriteGateError("Xunji database dry-run is blocked")
            backup = BackupManager(layout)
            operation_id = backup.before(label=f"publish {release_label}", source=database_path)
            if not backup.pending_before_matches_source(
                operation_id=operation_id, source=database_path
            ):
                raise WriteGateError("Backup-before no longer matches Xunji.db")
            receipt["database"] = executor.write(projection)
            database_changed = True
            launch_synfit()
            affected_ids = (
                receipt["database"]["soft_deleted_ids"]
                + receipt["database"]["inserted_ids"]
            )
            receipt["synfit"] = wait_for_sync(
                executor, affected_ids, timeout_seconds=sync_timeout_seconds
            )
            if receipt["synfit"]["pending_ids"]:
                raise WriteGateError("SynFit synchronization timed out")
            receipt["database_readback"] = executor.readback(projection)
            if (
                receipt["database_readback"]["status"] != "DB read back"
                or receipt["database_readback"]["active_not_sync"]
            ):
                raise WriteGateError("Xunji database read-back failed after SynFit sync")
        receipt["calendar"] = write_calendar(projection)
        receipt["calendar_readback"] = readback_calendar(projection)
        if receipt["calendar_readback"]["status"] != "Calendar read back":
            raise WriteGateError("Apple Calendar read-back did not match")
        if backup is not None and operation_id is not None:
            receipt["backup_after"] = backup.after(
                operation_id=operation_id, source=database_path
            )
            receipt["backup_confirmed"] = backup.confirm(operation_id=operation_id)
        receipt["status"] = "succeeded"
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        return receipt
    except Exception as exc:
        receipt["status"] = "partial" if database_changed else "failed"
        receipt["error"] = str(exc)
        if backup is not None and operation_id is not None:
            try:
                pending = backup.status().get("pending")
                if isinstance(pending, dict) and pending.get("stage") == "before" and database_changed:
                    receipt["backup_after"] = backup.after(
                        operation_id=operation_id, source=database_path
                    )
                receipt["backup_abandoned"] = backup.abandon(
                    operation_id=operation_id,
                    reason="connector half-Block publication failed; snapshots retained for recovery",
                )
            except Exception as backup_error:
                receipt["backup_error"] = str(backup_error)
        receipt["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write_json(receipt_path, receipt)
        raise
