import unittest
from fitness_data_bridge.facts_refresh import analyze_action
from fitness_data_bridge.facts_review import apply_feedback_dispositions, review_payload, session_candidates


class FactCompletenessTests(unittest.TestCase):
    def action(self, **kwargs):
        return {"key": "machine", "label": "Machine", "exetype": "", "sets": [
            {"done": True, "weight": "30", "reps": "10", "unit": "kg"},
            {"done": True, "weight": "20", "reps": "5", "setType": "热"},
            {"done": False, "weight": "30", "reps": "10"}], **kwargs}

    def test_known_loaded_action_preserves_raw_type_and_normalizes(self):
        cap = {"machine": {"label": "Machine", "write_semantics": {"exetype": "weight"}}}
        result = analyze_action(self.action(), True, cap)
        self.assertEqual("", result["exetype"])
        self.assertEqual("weight", result["normalized_exetype"])
        self.assertEqual((1, 300, 0), (result["counted_sets"], result["counted_tonnage"], result["unclassified_loaded_sets"]))

    def test_unknown_or_mismatched_mapping_is_explicitly_incomplete(self):
        cap = {"machine": {"label": "Other", "write_semantics": {"exetype": "weight"}}}
        for mapping in ({}, cap):
            result = analyze_action(self.action(), True, mapping)
            self.assertEqual(0, result["counted_sets"])
            self.assertEqual(1, result["unclassified_loaded_sets"])

    def test_unloaded_warmup_not_promoted_to_strength(self):
        cap = {"machine": {"label": "Machine", "write_semantics": {"exetype": "weight"}}}
        result = analyze_action(self.action(sets=[{"done": True, "reps": "12", "weight": ""}]), True, cap)
        self.assertEqual((0, 0), (result["counted_sets"], result["unclassified_loaded_sets"]))

    def test_explicit_source_type_is_not_overridden(self):
        result = analyze_action(self.action(exetype="stretch"), True,
            {"machine": {"label": "Machine", "write_semantics": {"exetype": "weight"}}})
        self.assertEqual("stretch", result["normalized_exetype"])
        self.assertEqual(1, result["unclassified_loaded_sets"])

    def test_paired_alias_and_heat_sets(self):
        a = self.action(exetype="weight", singleSide=True,
                        sets=[{"done": True, "weight": "10", "leftWeight": "12", "reps": "8"}])
        result = analyze_action(a, True)
        self.assertEqual((1, 2, 176), (result["counted_sets"], result["counted_side_sets"], result["counted_tonnage"]))

    def test_retained_review_is_traceable_and_does_not_mutate_source(self):
        import copy
        action = analyze_action(self.action(note="Please add this machine"), True)
        record = {"id": 42, "version": 2, "datestr": "2026-09-05", "title": "Legs",
                  "is_completed": True, "sync_type": "done", "actions": [action],
                  "experience_text": "Tight today", "note_text": "Tight today"}
        payload = {"planning_eligible": True, "week": {"start": "2026-08-31", "end": "2026-09-06"}, "records": [record]}
        before = copy.deepcopy(payload)
        unresolved = review_payload(payload, {})
        self.assertFalse(unresolved["summary"]["statistics_complete"])
        self.assertEqual(1, unresolved["summary"]["unclassified_loaded_sets"])
        cap = {"machine": {"label": "Machine", "write_semantics": {"exetype": "weight"}}}
        resolved = review_payload(payload, cap)
        self.assertTrue(resolved["summary"]["statistics_complete"])
        self.assertEqual(1, resolved["summary"]["sets"])
        feedback = resolved["summary"]["action_feedback"][0]
        self.assertEqual((42, 0), (feedback["record_id"], feedback["action_index"]))
        self.assertEqual(feedback["feedback_id"], unresolved["summary"]["action_feedback"][0]["feedback_id"])
        self.assertEqual(before, payload)

    def test_dispositions_require_exact_snapshot_and_unambiguous_decision(self):
        feedback = {"feedback_id": "f", "disposition": "unreviewed"}
        weeks = [{"source_sha256": "snapshot", "summary": {"experience_feedback": [feedback], "action_feedback": []}}]
        event = {"event_id": "e", "status": "applied", "effect": {"feedback_id": "f", "source_sha256": "old"}}
        apply_feedback_dispositions(weeks, [event])
        self.assertEqual("unreviewed", feedback["disposition"])
        event["effect"]["source_sha256"] = "snapshot"
        apply_feedback_dispositions(weeks, [event])
        self.assertEqual("applied", feedback["disposition"])
        apply_feedback_dispositions(weeks, [event, event])
        self.assertEqual("conflicting_decisions_require_review", feedback["disposition"])

    def test_shifted_title_match_requires_review_and_terminal_is_immutable(self):
        sessions = [{"session_id": "leg", "prescription": {"title": "Legs"},
                     "schedule": {"state": "scheduled", "scheduled_for": "2026-09-04"}}]
        records = [{"id": 42, "title": "Legs", "datestr": "2026-09-05", "is_completed": True, "sync_type": "done"}]
        self.assertEqual("requires_receipt_identity_review", session_candidates(sessions, records)[0]["status"])
        self.assertEqual("scheduled", sessions[0]["schedule"]["state"])
        self.assertEqual("ambiguous", session_candidates(sessions, records + records)[0]["status"])
        sessions[0]["schedule"]["state"] = "completed"
        self.assertEqual([], session_candidates(sessions, records))


if __name__ == "__main__":
    unittest.main()
