from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fitness_connector.apple_health import date_range, parse_dt
from fitness_connector.errors import ValidationError, WriteGateError
from fitness_connector.io import canonical_sha256
from fitness_connector.layout import WorkspaceLayout
from fitness_connector.publisher import dry_run_next
from fitness_connector.session_bridge import (
    load_action_capabilities,
    next_scheduled_session,
    project_session,
    validate_session,
)
from fitness_connector.sqlite_executor import SQLitePlanExecutor
from fitness_connector.xunji_credentials import (
    CredentialError,
    load_credential,
    validate_credentials_file,
    write_credentials_file,
)


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session(sequence: int = 1, state: str = "scheduled") -> dict:
    prescription = {
        "title": "A1 Push",
        "estimated_minutes": 60,
        "movements": [
            {
                "action_key": "benchpress",
                "label": "杠铃卧推",
                "intent": "technique",
                "settings": {},
                "sets": [
                    {
                        "set_role": "effective",
                        "reps": 5,
                        "load": 40,
                        "unit": "kg",
                        "rir": 3,
                        "rpe": None,
                    }
                ],
            }
        ],
    }
    return {
        "session_schema_version": "5.0",
        "training_rule_schema_version": 5,
        "phase_id": "phase-1",
        "block_id": "block-1",
        "session_id": f"session-{sequence}",
        "sequence": sequence,
        "session_type": "strength",
        "prescription": prescription,
        "prescription_sha256": canonical_sha256(prescription),
        "schedule": {
            "state": state,
            "scheduled_for": "2026-08-20" if state == "scheduled" else None,
            "revision": 0,
            "history": [],
        },
        "execution": None,
    }


class WorkspaceFixture:
    def __init__(self, root: Path):
        self.root = root
        capability_path = root / "个人" / "训练档案" / "动作能力.json"
        dump(
            capability_path,
            {
                "actions": [
                    {
                        "key": "benchpress",
                        "label": "杠铃卧推",
                        "availability": {"planning_eligible": True},
                        "write_semantics": {
                            "exetype": "weight",
                            "weight_semantics": "external_load_kg",
                        },
                    }
                ]
            },
        )
        personal_path = root / "个人" / "训练档案" / "personal-data-v5.json"
        dump(
            personal_path,
            {
                "action_capabilities": {
                    "path": "个人/训练档案/动作能力.json",
                    "sha256": sha256(capability_path),
                }
            },
        )
        dump(
            root / "个人" / "数据源" / "connectors.json",
            {
                "connector_registry_schema_version": "1.0",
                "selected_connector": {"plugin": "fitness-connector", "version": "0.1.0"},
                "defaults": {"calendar": "Workout", "start_time": "20:00"},
            },
        )
        (root / "当前").mkdir(parents=True, exist_ok=True)
        (root / "当前" / "state.yml").write_text(
            "personal_data_path: 个人/训练档案/personal-data-v5.json\n"
            "session_index_path: 当前/session-index-v5.json\n",
            encoding="utf-8",
        )
        dump(root / "当前" / "session-index-v5.json", [session()])


class ConnectorTests(unittest.TestCase):
    def test_next_session_is_strictly_ordered(self) -> None:
        first = session(1, "confirmed")
        later = session(2)
        self.assertIsNone(next_scheduled_session([later, first]))
        first["schedule"]["state"] = "cancelled"
        self.assertEqual("session-2", next_scheduled_session([later, first])["session_id"])

    def test_session_hash_is_enforced(self) -> None:
        value = session()
        value["prescription"]["estimated_minutes"] = 61
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            validate_session(value)

    def test_projection_uses_personal_write_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            layout = WorkspaceLayout(fixture.root)
            projected = project_session(session(), layout=layout)
            movement = projected["app_records"][0]["movements"][0]
            self.assertEqual("weight", movement["exetype"])
            self.assertEqual("40", movement["sets"][0]["weight"])
            self.assertEqual("Workout", projected["calendar_events"][0]["calendar"])

    def test_personal_capability_hash_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            path = fixture.root / "个人" / "训练档案" / "动作能力.json"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "hash"):
                load_action_capabilities(WorkspaceLayout(fixture.root))

    def test_default_route_is_projection_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            result = dry_run_next(fixture.root)
            self.assertEqual("dry-run passed", result["status"])
            self.assertFalse(result["live_write_performed"])
            self.assertEqual("projection only", result["database"]["status"])

    def test_sqlite_dry_run_blocks_completed_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Xunji.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "create table localtrains (id integer, openid text, datestr text, end integer, "
                "start integer, movement text, title text, templateid text, note text, watch integer, "
                "version integer, sync_type text, delflag integer)"
            )
            connection.execute(
                "insert into localtrains values (1, '', '2026-08-20', 2, 1, '[]', "
                "'A1 Push', null, '{}', -1, 1, 'done', 0)"
            )
            connection.commit()
            connection.close()
            artifact = {
                "replacement_window": {"start": "2026-08-20", "end": "2026-08-20"},
                "database_delete_scope": [{"date": "2026-08-20", "title": "A1 Push"}],
                "app_records": [],
            }
            result = SQLitePlanExecutor(database).dry_run(artifact)
            self.assertEqual("dry-run blocked", result["status"])

    def test_credentials_remain_explicit_and_typed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            values = {
                "training": "xjllm_abc123",
                "food_records": "xjfood_abc123",
                "food_search": "0123456789abcdef0123456789abcdef",
                "body": "xjbody_abc123",
            }
            write_credentials_file(path, values)
            self.assertEqual(4, len(validate_credentials_file(path)))
            self.assertEqual(
                "xjllm_abc123",
                load_credential("training", credential_file=path, allow_environment=False),
            )
            with self.assertRaises(CredentialError):
                load_credential("body", credential_file=path.with_suffix(".txt"), allow_environment=False)

    def test_apple_health_helpers_are_timezone_safe(self) -> None:
        parsed = parse_dt("2026-08-16 07:30:00 +0800")
        self.assertIsNotNone(parsed)
        self.assertEqual(2, len(date_range(parsed.date(), parsed.date() + timedelta(days=1))))


if __name__ == "__main__":
    unittest.main()
