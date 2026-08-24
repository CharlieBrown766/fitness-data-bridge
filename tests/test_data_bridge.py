from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fitness_data_bridge.apple_health import date_range, parse_dt
from fitness_data_bridge.calendar_reminders import reminder_at_values, reminder_intervals
from fitness_data_bridge.calendar_eventkit import eventkit_plan
from fitness_data_bridge.calendar_repair import repair_calendar_events
from fitness_data_bridge.calendar_writer import (
    build_calendar_alarm_update_script,
    build_calendar_readback_script,
    build_calendar_script,
    readback_calendar,
)
from fitness_data_bridge.errors import ValidationError, WriteGateError
from fitness_data_bridge.io import canonical_sha256
from fitness_data_bridge.layout import WorkspaceLayout, resolve_workspace
from fitness_data_bridge.publisher import dry_run_next
from fitness_data_bridge.publisher import _release_authorization, dry_run_release, publish_release
from fitness_data_bridge.release_bridge import project_release, validate_release
from fitness_data_bridge.session_bridge import (
    _xunji_movements,
    load_action_capabilities,
    next_scheduled_session,
    project_session,
    validate_session,
)
from fitness_data_bridge.synfit import launch_synfit
from fitness_data_bridge.sqlite_executor import SQLitePlanExecutor
from fitness_data_bridge.xunji_credentials import (
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


def half_block_release() -> dict:
    sessions = []
    sequence = 0
    for position in ("A1", "B1"):
        for slot in ("Push", "Pull", "Legs"):
            sequence += 1
            value = session(sequence)
            value["session_id"] = f"{position.lower()}-{slot.lower()}"
            value["schedule"]["scheduled_for"] = f"2026-08-{19 + sequence:02d}"
            value["prescription"] = {
                "title": f"{position} {slot}",
                "cycle_position": position,
                "slot": slot,
                "estimated_minutes": 90,
                "movements": [
                    {
                        "action_key": "benchpress",
                        "label": "杠铃卧推",
                        "section": "main",
                        "intent": "volume",
                        "settings": {},
                        "sets": [
                            {
                                "set_role": "effective",
                                "reps": 8,
                                "load": 40,
                                "unit": "kg",
                                "rir": 3,
                                "rpe": None,
                            }
                        ],
                        "load_basis": {
                            "kind": "comparable",
                            "evidence_refs": ["record:benchpress-2026-08-01"],
                            "comparison_signature": "benchpress:barbell:8reps:first",
                        },
                    }
                ],
            }
            value["prescription_sha256"] = canonical_sha256(value["prescription"])
            sessions.append(value)
    return {
        "half_block_release_schema_version": "5.1",
        "training_rule_schema_version": 5,
        "phase_id": "phase-1",
        "block_id": "block-1",
        "release_id": "first_half",
        "revision": 1,
        "status": "confirmed",
        "cycle_positions": ["A1", "B1"],
        "evidence_refs": ["facts:2026-08-16"],
        "basis": {
            "phase_path": "状态/phase.json",
            "block_path": "状态/block.json",
            "previous_release_path": None,
            "midpoint_review_path": None,
        },
        "sessions": sessions,
    }
class WorkspaceFixture:
    def __init__(self, root: Path):
        self.root = root
        capability_path = root / "用户" / "训练档案" / "动作能力.json"
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
        personal_path = root / "用户" / "训练档案" / "personal-data-v5.json"
        dump(
            personal_path,
            {
                "action_capabilities": {
                    "path": "用户/训练档案/动作能力.json",
                    "sha256": sha256(capability_path),
                }
            },
        )
        dump(
            root / "用户" / "数据接入" / "connectors.json",
            {
                "connector_registry_schema_version": "1.0",
                "selected_connector": {"plugin": "fitness-data-bridge", "version": "0.1.0"},
                "defaults": {
                    "calendar": "Workout",
                    "start_time": "20:00",
                    "reminder_times": ["09:00", "17:00"],
                },
            },
        )
        (root / "状态").mkdir(parents=True, exist_ok=True)
        (root / "状态" / "state.yml").write_text(
            "personal_data_path: 用户/训练档案/personal-data-v5.json\n"
            "session_index_path: 状态/session-index-v5.json\n",
            encoding="utf-8",
        )
        dump(root / "状态" / "session-index-v5.json", [session()])


class DataBridgeTests(unittest.TestCase):
    def test_canonical_workspace_layout_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory).resolve())
            layout = resolve_workspace(fixture.root)
            self.assertEqual(fixture.root / "状态/state.yml", layout.state_file)
            self.assertEqual(
                fixture.root / "用户/数据接入/connectors.json",
                layout.connector_registry_file,
            )
            self.assertEqual(
                fixture.root / "数据/训练/xunji/mac_student_facts",
                layout.training_facts_dir,
            )

    def test_previous_workspace_layout_remains_discoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "当前").mkdir(parents=True)
            (root / "当前/state.yml").write_text("workspace_schema_version: 4\n", encoding="utf-8")
            (root / "个人/数据源").mkdir(parents=True)
            dump(root / "个人/数据源/connectors.json", {})
            (root / "事实/训练/xunji/mac_student_facts").mkdir(parents=True)

            layout = resolve_workspace(root)

            self.assertEqual(root / "当前/state.yml", layout.state_file)
            self.assertEqual(
                root / "个人/数据源/connectors.json",
                layout.connector_registry_file,
            )
            self.assertEqual(
                root / "事实/训练/xunji/mac_student_facts",
                layout.training_facts_dir,
            )

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
            self.assertEqual(
                ["09:00", "17:00"],
                projected["calendar_events"][0]["reminder_times"],
            )
            self.assertEqual(
                "训练内容梗概（预计 60 分钟）\n"
                "主训练：\n"
                "• 杠铃卧推：1×5次 @ 40kg（余力3次）",
                projected["calendar_events"][0]["notes"],
            )

    def test_calendar_wall_clock_reminders_are_exact(self) -> None:
        event = {
            "calendar": "Workout",
            "date": "2026-08-20",
            "title": "A1 Push",
            "start_time": "20:00",
            "duration_minutes": 90,
            "reminder_times": ["09:00", "17:00"],
            "notes": "训练内容梗概（预计 90 分钟）",
        }
        self.assertEqual([-660, -180], reminder_intervals(event))
        self.assertEqual(
            ["2026-08-20 09:00", "2026-08-20 17:00"],
            reminder_at_values(event),
        )
        artifact = {
            "replacement_window": {"start": "2026-08-20", "end": "2026-08-20"},
            "calendar_events": [event],
        }
        create_script = build_calendar_script(artifact)
        repair_script = build_calendar_alarm_update_script(artifact)
        readback_script = build_calendar_readback_script(artifact)
        self.assertNotIn("trigger interval:-1800", create_script)
        self.assertIn("trigger date:my makeDate(2026, 8, 20, 9, 0)", create_script)
        self.assertIn("trigger date:my makeDate(2026, 8, 20, 17, 0)", create_script)
        self.assertNotIn("delete every display alarm", repair_script)
        self.assertIn("set description of targetEvent", repair_script)
        self.assertIn("set trigger date of (item 1 of existingAlarms)", repair_script)
        self.assertIn("make new display alarm", repair_script)
        self.assertIn("trigger date of alarmRef", readback_script)
        self.assertIn("trigger interval of alarmRef", readback_script)
        self.assertIn("((year of alarmDate) as text)", readback_script)
        self.assertIn("(((month of alarmDate) as integer) as text)", readback_script)

    def test_legacy_relative_reminder_uses_minutes_without_scaling(self) -> None:
        event = {
            "calendar": "Workout",
            "date": "2026-08-20",
            "title": "A1 Push",
            "start_time": "20:00",
            "duration_minutes": 90,
            "reminder_minutes_before": 30,
            "notes": "",
        }
        self.assertEqual([-30], reminder_intervals(event))
        self.assertEqual(["2026-08-20 19:30"], reminder_at_values(event))

    def test_eventkit_plan_carries_exact_alarms_and_readable_notes(self) -> None:
        event = {
            "calendar": "Workout",
            "date": "2026-08-20",
            "title": "A1 Push",
            "start_time": "20:00",
            "duration_minutes": 90,
            "reminder_times": ["09:00", "17:00"],
            "notes": "训练内容梗概\n• 杠铃卧推：4×5次 @ 90kg",
        }
        artifact = {
            "calendar_events": [event],
            "calendar_delete_scope": [
                {"calendar": "Workout", "date": "2026-08-20", "title": "A1 Push"}
            ],
        }
        plan = eventkit_plan(artifact)
        self.assertEqual(
            ["2026-08-20 09:00", "2026-08-20 17:00"],
            plan["calendar_events"][0]["reminder_at"],
        )
        self.assertIn("杠铃卧推", plan["calendar_events"][0]["notes"])

    def test_eventkit_readback_detects_an_inherited_default_alarm(self) -> None:
        event = {
            "calendar": "Workout",
            "date": "2026-08-20",
            "title": "A1 Push",
            "start_time": "20:00",
            "duration_minutes": 90,
            "reminder_times": ["09:00", "17:00"],
            "notes": "训练内容梗概",
        }
        artifact = {"calendar_events": [event], "calendar_delete_scope": []}
        actual = {
            "calendar": "Workout",
            "date": "2026-08-20",
            "start_time": "20:00",
            "duration_minutes": 90,
            "title": "A1 Push",
            "reminder_at": [
                "2026-08-20 09:00",
                "2026-08-20 17:00",
            ],
            "notes": "训练内容梗概",
            "default_alarm_suppressed": False,
        }
        with patch(
            "fitness_data_bridge.calendar_writer.run_eventkit",
            return_value={"status": "Calendar read back", "events": [actual]},
        ):
            self.assertEqual("Calendar mismatch", readback_calendar(artifact)["status"])

    def test_calendar_reminder_rejects_ambiguous_or_late_times(self) -> None:
        event = {
            "date": "2026-08-20",
            "start_time": "20:00",
            "reminder_times": ["20:00"],
            "reminder_minutes_before": 30,
        }
        with self.assertRaisesRegex(ValidationError, "cannot combine"):
            reminder_at_values(event)
        event.pop("reminder_minutes_before")
        with self.assertRaisesRegex(ValidationError, "must be before"):
            reminder_at_values(event)

    def test_calendar_reminder_repair_is_scoped_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            release = half_block_release()
            release_path = fixture.root / "状态" / "first-half-r01.json"
            dump(release_path, release)
            projection = project_release(
                validate_release(release), layout=WorkspaceLayout(fixture.root)
            )
            expected_events = [
                {
                    "calendar": event["calendar"],
                    "date": event["date"],
                    "start_time": event["start_time"],
                    "duration_minutes": event["duration_minutes"],
                    "title": event["title"],
                    "reminder_at": reminder_at_values(event),
                    "notes": " ".join(event["notes"].split()),
                    "default_alarm_suppressed": True,
                }
                for event in projection["calendar_events"]
            ]
            current_events = [
                {
                    **event,
                    "default_alarm_suppressed": False,
                }
                for event in expected_events
            ]
            authorization = {
                "authorization_schema_version": "1.1",
                "operation": "repair_release_calendar_event_details",
                "phase_id": release["phase_id"],
                "block_id": release["block_id"],
                "release_id": release["release_id"],
                "revision": release["revision"],
                "release_sha256": projection["source"]["release_sha256"],
                "session_ids": projection["source"]["session_ids"],
                "replacement_window": projection["replacement_window"],
                "projection_sha256": canonical_sha256(projection),
                "confirmed": True,
                "one_time": True,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
            authorization_path = fixture.root / "运行" / "calendar-repair-authorization.json"
            dump(authorization_path, authorization)
            with (
                patch("fitness_data_bridge.calendar_repair.sys.platform", "darwin"),
                patch(
                    "fitness_data_bridge.calendar_repair.readback_calendar",
                    side_effect=[
                        {
                            "status": "Calendar mismatch",
                            "event_count": len(current_events),
                            "events": current_events,
                        },
                        {
                            "status": "Calendar read back",
                            "event_count": len(expected_events),
                            "events": expected_events,
                        },
                    ],
                ) as readback,
                patch(
                    "fitness_data_bridge.calendar_repair.update_calendar_events",
                    return_value={
                        "status": "Calendar notes and alarms updated",
                        "event_count": len(expected_events),
                    },
                ) as update,
            ):
                receipt = repair_calendar_events(
                    fixture.root,
                    release_path=release_path,
                    authorization_path=authorization_path,
                )
            self.assertEqual("succeeded", receipt["status"])
            self.assertTrue(receipt["calendar_dry_run"]["reminders_match"])
            self.assertTrue(receipt["calendar_dry_run"]["notes_match"])
            self.assertFalse(receipt["calendar_dry_run"]["default_alarms_suppressed"])
            update.assert_called_once_with(projection)
            self.assertEqual(2, readback.call_count)

    def test_dynamic_warmup_uses_xunji_repetition_only_shape(self) -> None:
        prescription = {
            "movements": [
                {
                    "action_key": "27841201",
                    "label": "扩胸开合",
                    "section": "dynamic_warmup",
                    "intent": "warmup",
                    "settings": {},
                    "sets": [
                        {
                            "set_role": "warmup",
                            "reps": 12,
                            "load": None,
                            "unit": "repetitions",
                        }
                    ],
                }
            ]
        }
        capabilities = {
            "27841201": {
                "key": "27841201",
                "label": "扩胸开合",
                "availability": {"planning_eligible": True},
                "write_semantics": {
                    "exetype": "plus_weight",
                    "weight_semantics": "additional_load_kg",
                },
            }
        }
        movement = _xunji_movements(prescription, capabilities)[0]
        self.assertEqual("", movement["exetype"])
        self.assertEqual("", movement["note"])
        self.assertEqual("kg", movement["sets"][0]["unit"])
        self.assertNotIn("setType", movement["sets"][0])

    def test_cooldown_defaults_to_repetition_only_with_duration_note(self) -> None:
        prescription = {
            "movements": [
                {
                    "action_key": "stretch",
                    "section": "cooldown",
                    "settings": {"note": "低疲劳收操"},
                    "sets": [
                        {
                            "set_role": "recovery",
                            "reps": 30,
                            "load": None,
                            "unit": "seconds",
                        }
                    ],
                }
            ]
        }
        capabilities = {
            "stretch": {
                "key": "stretch",
                "label": "拉伸",
                "availability": {"planning_eligible": True},
                "write_semantics": {
                    "exetype": "stretch",
                    "weight_semantics": "duration_or_repetitions",
                },
            }
        }
        movement = _xunji_movements(prescription, capabilities)[0]
        self.assertEqual("", movement["exetype"])
        self.assertEqual("低疲劳收操；每组保持 30 秒", movement["note"])
        self.assertEqual("kg", movement["sets"][0]["unit"])
        self.assertEqual("1", movement["sets"][0]["reps"])
        self.assertEqual(0, movement["sets"][0]["time"])

    def test_synfit_is_restarted_when_already_running(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="true\n")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("fitness_data_bridge.synfit.sys.platform", "darwin"),
            patch("fitness_data_bridge.synfit.SYNFIT_APP", Path(directory)),
            patch(
                "fitness_data_bridge.synfit.subprocess.run",
                side_effect=[completed, MagicMock(), MagicMock()],
            ) as run,
            patch("fitness_data_bridge.synfit.time.sleep") as sleep,
        ):
            launch_synfit()
        self.assertEqual(3, run.call_count)
        self.assertEqual("osascript", run.call_args_list[0].args[0][0])
        self.assertIn("to quit", run.call_args_list[1].args[0][2])
        self.assertEqual("open", run.call_args_list[2].args[0][0])
        sleep.assert_called_once_with(2)

    def test_personal_capability_hash_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            path = fixture.root / "用户" / "训练档案" / "动作能力.json"
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

    def test_half_block_projects_all_sessions_as_one_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            release = half_block_release()
            projection = project_release(
                validate_release(release), layout=WorkspaceLayout(fixture.root)
            )
            self.assertEqual("2.0", projection["connector_contract_version"])
            self.assertEqual(6, len(projection["app_records"]))
            self.assertEqual(6, len(projection["calendar_events"]))
            self.assertEqual(
                [item["session_id"] for item in release["sessions"]],
                projection["source"]["session_ids"],
            )
            self.assertEqual("2026-08-20", projection["replacement_window"]["start"])
            self.assertEqual("2026-08-25", projection["replacement_window"]["end"])

    def test_half_block_rejects_silent_empty_comparable_weight(self) -> None:
        release = half_block_release()
        movement = release["sessions"][0]["prescription"]["movements"][0]
        movement["sets"][0]["load"] = None
        release["sessions"][0]["prescription_sha256"] = canonical_sha256(
            release["sessions"][0]["prescription"]
        )
        with self.assertRaisesRegex(WriteGateError, "numeric loads"):
            validate_release(release)

    def test_release_dry_run_and_authorization_bind_the_whole_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            release = half_block_release()
            release_path = fixture.root / "状态" / "first-half-r01.json"
            dump(release_path, release)
            result = dry_run_release(fixture.root, release_path=release_path)
            self.assertEqual("dry-run passed", result["status"])
            self.assertEqual(6, result["release"]["session_count"])
            authorization = {
                "authorization_schema_version": "2.0",
                "operation": "publish_half_block_release",
                "phase_id": release["phase_id"],
                "block_id": release["block_id"],
                "release_id": release["release_id"],
                "revision": release["revision"],
                "release_sha256": result["projection"]["source"]["release_sha256"],
                "session_ids": result["projection"]["source"]["session_ids"],
                "replacement_window": result["projection"]["replacement_window"],
                "confirmed": True,
                "one_time": True,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
            auth_path = fixture.root / "运行" / "authorization.json"
            dump(auth_path, authorization)
            layout = WorkspaceLayout(fixture.root)
            self.assertEqual(
                "2.0",
                _release_authorization(
                    auth_path,
                    release=release,
                    projection=result["projection"],
                    layout=layout,
                )[0]["authorization_schema_version"],
            )
            authorization["session_ids"] = authorization["session_ids"][:-1]
            dump(auth_path, authorization)
            with self.assertRaisesRegex(WriteGateError, "session_ids"):
                _release_authorization(
                    auth_path,
                    release=release,
                    projection=result["projection"],
                    layout=layout,
                )

    def test_release_publication_launches_synfit_once_for_the_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = WorkspaceFixture(Path(directory))
            release = half_block_release()
            release_path = fixture.root / "状态" / "first-half-r01.json"
            dump(release_path, release)
            preview = dry_run_release(fixture.root, release_path=release_path)
            authorization = {
                "authorization_schema_version": "2.0",
                "operation": "publish_half_block_release",
                "phase_id": release["phase_id"],
                "block_id": release["block_id"],
                "release_id": release["release_id"],
                "revision": release["revision"],
                "release_sha256": preview["projection"]["source"]["release_sha256"],
                "session_ids": preview["projection"]["source"]["session_ids"],
                "replacement_window": preview["projection"]["replacement_window"],
                "confirmed": True,
                "one_time": True,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
            auth_path = fixture.root / "运行" / "authorization.json"
            dump(auth_path, authorization)
            executor = MagicMock()
            executor.dry_run.return_value = {"status": "dry-run passed"}
            executor.write.return_value = {
                "status": "DB written",
                "soft_deleted_ids": [],
                "inserted_ids": [101, 102, 103, 104, 105, 106],
            }
            executor.readback.return_value = {
                "status": "DB read back",
                "active_not_sync": [],
            }
            backup = MagicMock()
            backup.before.return_value = "operation-1"
            backup.pending_before_matches_source.return_value = True
            backup.after.return_value = {"status": "captured"}
            backup.confirm.return_value = {"status": "confirmed"}
            with (
                patch("fitness_data_bridge.publisher.sys.platform", "darwin"),
                patch("fitness_data_bridge.publisher.SQLitePlanExecutor", return_value=executor),
                patch("fitness_data_bridge.publisher.BackupManager", return_value=backup),
                patch("fitness_data_bridge.publisher.launch_synfit") as launch,
                patch(
                    "fitness_data_bridge.publisher.wait_for_sync",
                    return_value={"status": "sync_type done", "pending_ids": []},
                ) as wait,
                patch(
                    "fitness_data_bridge.publisher.write_calendar",
                    return_value={"status": "Calendar written", "event_count": 6},
                ) as calendar_write,
                patch(
                    "fitness_data_bridge.publisher.readback_calendar",
                    return_value={"status": "Calendar read back", "event_count": 6},
                ) as calendar_read,
            ):
                receipt = publish_release(
                    fixture.root,
                    release_path=release_path,
                    authorization_path=auth_path,
                    database=fixture.root / "Xunji.db",
                )
            self.assertEqual("succeeded", receipt["status"])
            launch.assert_called_once_with()
            wait.assert_called_once()
            executor.write.assert_called_once()
            calendar_write.assert_called_once()
            calendar_read.assert_called_once()

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
