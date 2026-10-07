from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fitness_data_bridge.errors import ValidationError
from fitness_data_bridge.session_bridge import _xunji_movements
from fitness_data_bridge.sqlite_executor import SQLitePlanExecutor


class RestTimerTests(unittest.TestCase):
    def project(self, settings):
        movement = {"action_key": "test_press", "section": "main", "settings": settings,
                    "sets": [{"set_role": "effective", "reps": 6, "load": 30, "unit": "kg"}]}
        caps = {"test_press": {"label": "Test press", "availability": {"planning_eligible": True},
                               "write_semantics": {"exetype": "weight"}}}
        before = deepcopy(movement)
        result = _xunji_movements({"movements": [movement]}, caps)[0]
        self.assertEqual(before, movement)
        return result

    def test_per_movement_seconds_and_separate_warmup(self):
        for seconds in (90, 150, 180):
            with self.subTest(seconds=seconds):
                m = self.project({"rest_seconds": seconds, "warmup_rest_seconds": 60})
                self.assertEqual((seconds, 60), (m["restTime"], m["warn_restTime"]))
                self.assertEqual("", m["note"])
                self.assertNotIn("restTime", m["sets"][0])

    def test_omitted_timers_and_explicit_zero(self):
        m = self.project({})
        self.assertNotIn("restTime", m)
        self.assertNotIn("warn_restTime", m)
        self.assertEqual(0, self.project({"rest_seconds": 0})["restTime"])
        self.assertNotIn("warn_restTime", self.project({"rest_seconds": 90}))

    def test_invalid_values_rejected_before_writes(self):
        for field in ("rest_seconds", "warmup_rest_seconds"):
            for value in (True, False, -1, 1.5, "90", None):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    self.project({field: value})

    def test_sqlite_round_trip_and_timer_tamper_detection(self):
        movement = self.project({"rest_seconds": 150, "warmup_rest_seconds": 60})
        identity = {"date": "2026-10-08", "title": "Test release"}
        artifact = {"replacement_window": {"start": identity["date"], "end": identity["date"]},
                    "database_delete_scope": [identity],
                    "app_records": [{**identity, "movements": [movement], "note": ""}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.db"
            with sqlite3.connect(path) as c:
                c.execute("create table localtrains (id integer, openid text, datestr text, end integer, "
                          "start integer, movement text, title text, templateid text, note text, watch integer, "
                          "version integer, sync_type text, delflag integer)")
            executor = SQLitePlanExecutor(path, id_factory=lambda: 100)
            executor.write(artifact)
            result = executor.readback(artifact)
            self.assertEqual("DB read back", result["status"])
            self.assertEqual([{"action_key": "test_press", "restTime": 150, "warn_restTime": 60}],
                             result["rows"][0]["movement_rest_settings"])
            with sqlite3.connect(path) as c:
                stored = json.loads(c.execute("select movement from localtrains").fetchone()[0])
                stored[0]["restTime"] = 90
                c.execute("update localtrains set movement=?", (json.dumps(stored, ensure_ascii=False,
                                                                          separators=(",", ":")),))
            self.assertEqual("DB readback mismatch", executor.readback(artifact)["status"])


if __name__ == "__main__":
    unittest.main()
