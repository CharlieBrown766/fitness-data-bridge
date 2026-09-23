import copy
import unittest
import tempfile
import sqlite3
import json
from pathlib import Path
from datetime import date
from contextlib import closing

from fitness_data_bridge.facts_refresh import analyze_action, query_week_records, build_summary, sync_signature, WeekRange
from fitness_data_bridge.reconciliation import reconcile, duplicate_groups
from fitness_data_bridge.action_export import extract_actions


class ExecutionTests(unittest.TestCase):
    def test_database_retains_deleted_and_summary_deduplicates_actuals_only(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder)/'synthetic.db'
            week = WeekRange(date(2026, 1, 5), date(2026, 1, 11))
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('create table localtrains(id, datestr, title, start, end, movement, note, sync_type, version, delflag)')
                movement = json.dumps([{"key": "a", "label": "A", "exetype": "weight", "sets": [
                    {"done": True, "weight": "10", "reps": "2"}]}])
                for identity, start, end, deleted in [(1,100,200,0),(2,100,200,0),(3,-1,-1,0),(4,-1,-1,1)]:
                    conn.execute('insert into localtrains values (?,?,?,?,?,?,?,?,?,?)',
                                 (identity,'2026-01-05','Test',start,end,movement,'','done',1,deleted))
                conn.commit()
            before = sync_signature(db, week)
            records = query_week_records(db, week)
            self.assertEqual(4, len(records))
            summary = build_summary(records, week)
            self.assertEqual((1,1,1,20), (summary['completed_records'],summary['rest_or_plan_records'],summary['deleted_record_count'],summary['tonnage']))
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('update localtrains set delflag=1 where id=3')
                conn.commit()
            self.assertNotEqual(before, sync_signature(db, week))

    def test_drop_reps_do_not_add_main_sets(self):
        action = {"exetype": "weight", "sets": [{"weight": "120", "reps": "2", "done": True,
                  "dropset": [{"weight": "110", "reps": "2"}]}]}
        result = analyze_action(action, True)
        self.assertEqual((1, 4, 460, 1), tuple(result[k] for k in
                         ("counted_sets", "counted_reps", "counted_tonnage", "drop_segments")))
        action["singleSide"] = True
        result = analyze_action(action, True)
        self.assertTrue(result["statistics_issues"])
        self.assertEqual(0, result["drop_segments"])

    def test_copy_delete_shift_requires_lineage_and_preserves_sources(self):
        sessions = [{"session_id": "s", "prescription": {"title": "Pull"},
                     "schedule": {"state": "scheduled", "scheduled_for": "2026-01-01"}}]
        old = {"id": 1, "datestr": "2026-01-01", "title": "Pull", "delflag": 1,
               "is_completed": False, "actions": [{"key": "row"}]}
        new = {**old, "id": 2, "datestr": "2026-01-02", "delflag": 0, "is_completed": True}
        receipt = {"status": "succeeded", "database_identity_map": [
            {"session_id": "s", "date": "2026-01-01", "title": "Pull"}],
            "database_readback": {"rows": [{"id": 1, "date": "2026-01-01", "title": "Pull"}]}}
        before = copy.deepcopy((sessions, old, new))
        result = reconcile(sessions, [old, new], [receipt])
        self.assertEqual("copy_or_extra_requires_confirmation", result["sessions"][0]["status"])
        confirmation = [{"session_id": "s", "record_id": 2, "original_text": "Moved the last class",
                         "source_sha256": "a" * 64}]
        result = reconcile(sessions, [old, new], [receipt], confirmation)
        self.assertEqual("linked", result["sessions"][0]["status"])
        self.assertEqual(2, result["sessions"][0]["selected_record_id"])
        self.assertEqual(before, (sessions, old, new))
        sessions[0]["schedule"]["state"] = "completed"
        self.assertEqual([], reconcile(sessions, [old, new], [receipt])["sessions"])

    def test_uncompleted_copies_are_not_duplicate_or_completed(self):
        row = {"id": 1, "is_completed": False, "start": -1, "end": -1,
               "source_content_sha256": "same", "datestr": "2026-01-01", "title": "Bike"}
        self.assertEqual([], duplicate_groups([row, {**row, "id": 2}]))
        row.update(is_completed=True, start="100", end="200")
        self.assertEqual([1, 2], duplicate_groups([row, {**row, "id": 2}])[0]["source_ids"])
        self.assertEqual([], duplicate_groups([row, {**row, "id": 2, "start": 300, "end": 400}]))

    def test_deleted_original_is_not_automatically_skipped(self):
        s = {"session_id": "s", "prescription": {"title": "Pull"},
             "schedule": {"state": "scheduled", "scheduled_for": "2026-01-01"}}
        result = reconcile([s], [])
        self.assertEqual("not_observed_in_supplied_window", result["sessions"][0]["status"])
        self.assertEqual("reminder_only", result["source_policy"]["calendar"])

    def test_export_variable_names_property_prefixes_and_collisions(self):
        entries = ','.join('{uri:"x",id:"'+key+'",name:"'+key+'"}' for key in
                           ['squat', 'benchpress', 'deadlift', 'legpress_machine', 'squat'])
        result = extract_actions('movementMap={};var zz=['+entries+'];sysLength:zz.length', 'test')
        self.assertEqual((5, 4), (result['entry_count'], result['unique_key_count']))
        self.assertIn('squat', result['duplicate_keys'])
        with self.assertRaises(ValueError):
            extract_actions('movementMap;sysLength:xy.length;{id:"squat"},{name:"bad"}', 'test')


if __name__ == "__main__":
    unittest.main()
