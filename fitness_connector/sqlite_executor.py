"""Scoped Xunji localtrains dry-run, write, and read-back logic."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable

from .errors import ValidationError, WriteGateError


REQUIRED_COLUMNS = {
    "id",
    "openid",
    "datestr",
    "end",
    "start",
    "movement",
    "title",
    "templateid",
    "note",
    "watch",
    "version",
    "sync_type",
    "delflag",
}


def note_payload(text: str) -> str:
    return json.dumps(
        {"text": text, "calorie": 0},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def movement_payload(movements: list[dict[str, Any]]) -> str:
    return json.dumps(movements, ensure_ascii=False, separators=(",", ":"))


def _int_like(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_completed_row(row: sqlite3.Row) -> bool:
    start = _int_like(row["start"])
    end = _int_like(row["end"])
    return bool(start and end and start > 0 and end > 0)


class SQLitePlanExecutor:
    def __init__(
        self,
        database: Path,
        *,
        id_factory: Callable[[], int] | None = None,
    ):
        self.database = database
        self.id_factory = id_factory or (lambda: int(time.time() * 1000) * 1000)

    def connect(self, *, read_only: bool) -> sqlite3.Connection:
        if not self.database.is_file():
            raise ValidationError(f"Xunji.db not found: {self.database}")
        if read_only:
            uri = f"file:{self.database.resolve()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
        else:
            connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        self.validate_schema(connection)
        return connection

    @staticmethod
    def validate_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute("pragma table_info(localtrains)").fetchall()
        columns = {str(row[1]) for row in rows}
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValidationError(
                f"localtrains missing columns: {', '.join(missing)}"
            )

    @staticmethod
    def _window(artifact: dict[str, Any]) -> tuple[str, str]:
        window = artifact["replacement_window"]
        return str(window["start"]), str(window["end"])

    @staticmethod
    def _delete_scope(artifact: dict[str, Any]) -> list[dict[str, str]]:
        return list(artifact["database_delete_scope"])

    @staticmethod
    def _readback_scope(artifact: dict[str, Any]) -> list[dict[str, str]]:
        return list(
            artifact.get(
                "database_readback_scope", artifact["database_delete_scope"]
            )
        )

    @staticmethod
    def _rows_for_scope(
        connection: sqlite3.Connection, scope: list[dict[str, str]]
    ) -> list[sqlite3.Row]:
        if not scope:
            return []
        clauses = " or ".join("(datestr = ? and title = ?)" for _ in scope)
        parameters = [
            value
            for identity in scope
            for value in (identity["date"], identity["title"])
        ]
        return connection.execute(
            f"""
            select id, datestr, title, start, end, sync_type, delflag,
                   version, movement
              from localtrains
             where coalesce(delflag, 0) = 0
               and ({clauses})
             order by datestr, id
            """,
            parameters,
        ).fetchall()

    def active_rows(
        self, connection: sqlite3.Connection, artifact: dict[str, Any]
    ) -> list[sqlite3.Row]:
        return self._rows_for_scope(connection, self._readback_scope(artifact))

    def replacement_rows(
        self, connection: sqlite3.Connection, artifact: dict[str, Any]
    ) -> list[sqlite3.Row]:
        return self._rows_for_scope(connection, self._delete_scope(artifact))

    def window_active_rows(
        self, connection: sqlite3.Connection, artifact: dict[str, Any]
    ) -> list[sqlite3.Row]:
        start, end = self._window(artifact)
        return connection.execute(
            """
            select id, datestr, title, start, end, sync_type, delflag,
                   version, movement
              from localtrains
             where datestr >= ? and datestr <= ?
               and coalesce(delflag, 0) = 0
             order by datestr, id
            """,
            (start, end),
        ).fetchall()

    @staticmethod
    def _expected_signatures(
        artifact: dict[str, Any],
    ) -> Counter[tuple[str, str, str]]:
        return Counter(
            (
                str(record["date"]),
                str(record["title"]),
                movement_payload(record["movements"]),
            )
            for record in artifact["app_records"]
        )

    def _readback_from_connection(
        self, connection: sqlite3.Connection, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        targeted = self.active_rows(connection, artifact)
        window = self.window_active_rows(connection, artifact)
        expected = self._expected_signatures(artifact)
        actual = Counter(
            (str(row["datestr"]), str(row["title"]), str(row["movement"]))
            for row in targeted
        )
        targeted_ids = {int(row["id"]) for row in targeted}
        matches = actual == expected
        return {
            "status": "DB read back" if matches else "DB readback mismatch",
            "active_rows": len(targeted),
            "out_of_scope_active_rows": sum(
                int(row["id"]) not in targeted_ids for row in window
            ),
            "active_not_sync": sum(
                row["sync_type"] != "done" for row in targeted
            ),
            "rows": [
                {
                    "id": row["id"],
                    "date": row["datestr"],
                    "title": row["title"],
                    "sync_type": row["sync_type"],
                    "delflag": row["delflag"],
                }
                for row in targeted
            ],
        }

    def dry_run(self, artifact: dict[str, Any]) -> dict[str, Any]:
        with closing(self.connect(read_only=True)) as connection:
            with connection:
                rows = self.replacement_rows(connection, artifact)
                window_rows = self.window_active_rows(connection, artifact)
        completed = [row for row in rows if is_completed_row(row)]
        replaceable = [row for row in rows if not is_completed_row(row)]
        targeted_ids = {int(row["id"]) for row in rows}
        return {
            "status": "dry-run passed" if not completed else "dry-run blocked",
            "database": str(self.database),
            "replacement_window": artifact["replacement_window"],
            "delete_scope": artifact["database_delete_scope"],
            "replaceable_rows": [
                {"id": row["id"], "date": row["datestr"], "title": row["title"]}
                for row in replaceable
            ],
            "completed_rows": [
                {"id": row["id"], "date": row["datestr"], "title": row["title"]}
                for row in completed
            ],
            "out_of_scope_active_rows": sum(
                int(row["id"]) not in targeted_ids for row in window_rows
            ),
            "insert_count": len(artifact["app_records"]),
        }

    def write(self, artifact: dict[str, Any]) -> dict[str, Any]:
        connection = self.connect(read_only=False)
        try:
            connection.execute("begin immediate")
            rows = self.replacement_rows(connection, artifact)
            completed = [row for row in rows if is_completed_row(row)]
            if completed:
                detail = ", ".join(
                    f"{row['datestr']}:{row['id']}:{row['title']}"
                    for row in completed
                )
                raise WriteGateError(
                    f"Completed rows in database delete scope: {detail}"
                )
            old_ids = [int(row["id"]) for row in rows]
            if old_ids:
                connection.executemany(
                    """
                    update localtrains
                       set delflag = 1,
                           sync_type = 'not_sync',
                           version = coalesce(version, 0) + 1
                     where id = ? and coalesce(delflag, 0) = 0
                    """,
                    [(row_id,) for row_id in old_ids],
                )
            base_id = self.id_factory()
            inserted_ids: list[int] = []
            for offset, record in enumerate(artifact["app_records"], start=1):
                row_id = base_id + offset
                inserted_ids.append(row_id)
                connection.execute(
                    """
                    insert into localtrains (
                        id, openid, datestr, end, start, movement, title,
                        templateid, note, watch, version, sync_type, delflag
                    ) values (?, '', ?, '-1', '-1', ?, ?, null, ?, -1, 1, 'not_sync', 0)
                    """,
                    (
                        row_id,
                        record["date"],
                        movement_payload(record["movements"]),
                        record["title"],
                        note_payload(record.get("note", "")),
                    ),
                )
            readback = self._readback_from_connection(connection, artifact)
            if readback["status"] != "DB read back":
                raise WriteGateError(
                    "Database read-back did not match the artifact"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "status": "DB written",
            "soft_deleted_ids": old_ids,
            "inserted_ids": inserted_ids,
            "readback": self.readback(artifact),
        }

    def readback(self, artifact: dict[str, Any]) -> dict[str, Any]:
        with closing(self.connect(read_only=True)) as connection:
            with connection:
                return self._readback_from_connection(connection, artifact)

    def sync_status(self, row_ids: list[int]) -> dict[str, Any]:
        if not row_ids:
            return {"status": "sync_type done", "pending_ids": []}
        placeholders = ",".join("?" for _ in row_ids)
        with closing(self.connect(read_only=True)) as connection:
            with connection:
                rows = connection.execute(
                    f"select id, sync_type from localtrains where id in ({placeholders})",
                    row_ids,
                ).fetchall()
        pending = [int(row["id"]) for row in rows if row["sync_type"] != "done"]
        return {
            "status": "sync_type done" if not pending else "App sync pending",
            "pending_ids": pending,
        }
