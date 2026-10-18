from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from fitness_data_bridge.api_facts import main, normalize_api_week, retained_full_response
from fitness_data_bridge.xunji_open_api import TRAINING_QUERY_FULL, request_digest
from fitness_data_bridge.errors import ValidationError
from fitness_data_bridge.facts_refresh import analyze_action
from fitness_data_bridge.facts_review import review_payload


class ApiFactsTests(unittest.TestCase):
    def test_retained_request_attests_full_mode_for_both_cache_formats(self):
        response = {"res": {"trains": []}}
        request = {"schema_version": "train_open_api_v2", "datestr": "2026-01-05", "include_full_data": True}
        legacy = {**request, "endpoint": "/api_trains_for_llm_v2", "response": response}
        shared = {"schema_version": 1, "endpoint": "training.query_full", "request": request,
                  "request_digest": request_digest(TRAINING_QUERY_FULL, request), "response": response}
        for envelope in (legacy, shared):
            self.assertEqual(response, retained_full_response(envelope))
        shared["request"]["datestr"] = "2026-01-06"
        with self.assertRaises(ValidationError):
            retained_full_response(shared)

    def test_summary_or_bare_response_cannot_claim_full_data(self):
        for envelope in ({"res": {"trains": []}},
                         {"endpoint": "/api_trains_for_llm_v2", "schema_version": "train_open_api_v2",
                          "datestr": "2026-01-05", "include_full_data": False, "response": {"res": {"trains": []}}}):
            with self.assertRaises(ValidationError):
                retained_full_response(envelope)

    def setUp(self):
        self.registry = {"by_key": {"benchpress": "杠铃卧推", "hammercurl": "锤式弯举"}, "aliases": {}, "ambiguous_labels": {}}
        self.caps = {"actions": [{"key": k, "label": v, "write_semantics": {"exetype": "weight"}} for k,v in self.registry["by_key"].items()]}

    def normalize(self, movements, **kwargs):
        registry = json.dumps(self.registry, ensure_ascii=False).encode()
        caps = json.dumps(self.caps, ensure_ascii=False).encode()
        payload = {"res": {"trains": [{"localid": 42, "datestr": "2026-01-05", "start": 1000, "end": 2000,
            "title": "Session", "movements": movements, "heartRate": {"avg": 120}}]}}
        self.original = deepcopy(payload)
        result = normalize_api_week(payload, week_start="2026-01-05", registry_data=registry,
            registry_sha256=kwargs.get("registry_sha256", hashlib.sha256(registry).hexdigest()),
            capabilities_data=caps, capabilities_sha256=hashlib.sha256(caps).hexdigest())
        self.assertEqual(self.original, payload)
        return result

    def test_actual_measurements_and_drop_segments_survive_review(self):
        raw = {"name": "杠铃卧推", "singleSide": False, "restTime": 150, "warn_restTime": 60, "sets": [
            {"done": True, "weight": "60", "reps": "10", "unit": "kg", "rpe": "8.5", "restSeconds": 90,
             "metrics": {"avgHeartRate": 120}, "heartRate": {"values": [120]},
             "dropSets": [{"weight": "40", "reps": "5", "unit": "kg"}]},
            {"done": False, "weight": "60", "reps": "10"}]}
        result = self.normalize([raw])
        action = result["records"][0]["actions"][0]
        self.assertEqual((1, 15, 800), (action["counted_sets"], action["counted_reps"], action["counted_tonnage"]))
        self.assertEqual((150, 60, "8.5", 90), (action["rest_time"], action["warmup_rest_time"], action["sets"][0]["rpe"], action["sets"][0]["rest_seconds"]))
        reviewed = review_payload(result, {a["key"]:a for a in self.caps["actions"]})
        self.assertEqual(action, reviewed["records"][0]["actions"][0])
        self.assertFalse(result["planning_eligible"])
        self.assertEqual(1, reviewed["summary"]["measurement_coverage"]["actual_rpe_sets"])

    def test_superset_children_count_actual_sets_and_retain_round_positions(self):
        result = self.normalize([{"name": "推拉超级组", "sets": [
            {"done": True, "items": [
                {"name": "杠铃卧推", "singleSide": False, "set": {"weight": "60", "reps": "5", "unit": "kg"}},
                {"name": "锤式弯举", "singleSide": True, "set": {"weight": "10", "leftWeight": "12", "reps": "8", "unit": "kg"}}]},
            {"done": False, "items": [
                {"name": "杠铃卧推", "singleSide": False, "done": True, "set": {"weight": "60", "reps": "5", "unit": "kg"}},
                {"name": "锤式弯举", "singleSide": True, "done": False, "set": {"weight": "10", "leftWeight": "12", "reps": "8", "unit": "kg"}}]}]}])
        record = result["records"][0]
        self.assertEqual((3, 4, 776), (record["sets"], record["side_sets"], record["tonnage"]))
        self.assertEqual({"movement_index": 0, "round_index": 1, "child_index": 0}, record["actions"][0]["sets"][1]["source_location"])
        reviewed = review_payload(result, {a["key"]:a for a in self.caps["actions"]})
        self.assertEqual(record["actions"], reviewed["records"][0]["actions"])

    def test_missing_laterality_is_incomplete_without_double_counting(self):
        result = self.normalize([{"name": "锤式弯举", "sets": [{"done": True, "weight": "10", "leftWeight": "12", "reps": "8"}]}])
        self.assertFalse(result["statistics_complete"])
        self.assertEqual(1, result["summary"]["unclassified_loaded_sets"])

    def test_unknown_and_ambiguous_names_do_not_invent_keys(self):
        self.registry["aliases"] = {"hammercurl": ["杠铃卧推"]}
        for name in ("未识别动作", "杠铃卧推"):
            result = self.normalize([{"name": name, "sets": [{"done": True, "weight": "10", "reps": "8"}]}])
            self.assertIsNone(result["records"][0]["actions"][0]["key"])
            self.assertFalse(result["statistics_complete"])

    def test_shared_key_variants_remain_incomplete(self):
        self.registry["ambiguous_labels"] = {"benchpress": ["杠铃卧推", "另一器械"]}
        result = self.normalize([{"name": "杠铃卧推", "sets": [{"done": True, "weight": "10", "reps": "8"}]}])
        self.assertIsNone(result["records"][0]["actions"][0]["key"])
        self.assertFalse(result["statistics_complete"])

    def test_cli_uses_bound_owners_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, planner = root / "workspace", root / "planner"
            resource = planner / "resources" / "profiles" / "test"
            resource.mkdir(parents=True)
            (workspace / "状态").mkdir(parents=True)
            registry = json.dumps(self.registry, ensure_ascii=False).encode()
            caps = json.dumps(self.caps, ensure_ascii=False).encode()
            (resource / "registry.json").write_bytes(registry)
            (workspace / "caps.json").write_bytes(caps)
            bundle = json.dumps({"owners": {"official_action_registry": {
                "path": "plugin://fitness-planner/profiles/test/registry.json",
                "sha256": hashlib.sha256(registry).hexdigest()}}}).encode()
            (resource / "bundle.json").write_bytes(bundle)
            (workspace / "index.json").write_text(json.dumps({"action_capabilities": {
                "path": "caps.json", "sha256": hashlib.sha256(caps).hexdigest()}}), encoding="utf-8")
            state = ("reference_bundle_path: plugin://fitness-planner/profiles/test/bundle.json\n"
                     f"reference_bundle_sha256: {hashlib.sha256(bundle).hexdigest()}\n"
                     "personal_data_path: index.json\n")
            (workspace / "状态" / "state.yml").write_text(state, encoding="utf-8")
            envelope = {"endpoint": "/api_trains_for_llm_v2", "schema_version": "train_open_api_v2",
                        "datestr": "2026-01-05", "include_full_data": True, "response": {"res": {"trains": []}}}
            (workspace / "source.json").write_text(json.dumps(envelope), encoding="utf-8")
            arguments = ["--workspace", str(workspace), "--planner-root", str(planner),
                         "--api-file", "source.json", "--week-start", "2026-01-05", "--output", "review.json"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(arguments))
            before = (workspace / "review.json").read_bytes()
            self.assertFalse(json.loads(before)["planning_eligible"])
            with self.assertRaises(FileExistsError):
                main(arguments)
            self.assertEqual(before, (workspace / "review.json").read_bytes())
            self.assertEqual(state, (workspace / "状态" / "state.yml").read_text(encoding="utf-8"))
            (resource / "registry.json").write_bytes(registry + b" ")
            with self.assertRaises(ValidationError):
                main(arguments[:-1] + ["other.json"])
            self.assertFalse((workspace / "other.json").exists())

    def test_owner_hash_mismatch_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.normalize([], registry_sha256="0"*64)

    def test_native_parser_flags_api_shapes_instead_of_silently_claiming_completeness(self):
        result = analyze_action({"key": "benchpress", "exetype": "weight", "sets": [
            {"done": True, "weight": "60", "reps": "10", "dropSets": [{"weight": "40", "reps": "5"}]}]}, True)
        self.assertTrue(result["statistics_issues"])

    def test_old_native_snapshot_can_recover_only_measurements_from_source(self):
        raw = {"key": "benchpress", "label": "杠铃卧推", "exetype": "weight", "restTime": 150,
            "sets": [{"done": True, "weight": "60", "reps": "10", "rpe": "8.5", "restSeconds": 90}]}
        action = analyze_action(raw, True)
        for key in ("rest_time", "warmup_rest_time", "metrics", "heart_rate"):
            action.pop(key, None)
        for key in ("rpe", "rest_seconds", "heart_rate", "metrics"):
            action["sets"][0].pop(key, None)
        payload = {"planning_eligible": True, "week": {"start":"2026-01-05", "end":"2026-01-11"}, "records":[{
            "id":42,"title":"Session","datestr":"2026-01-05","is_completed":True,"note_text":"","experience_text":"",
            "actions":[action],"source_actions":[raw]}]}
        before = deepcopy(payload)
        viewed = review_payload(payload,{})["records"][0]["actions"][0]
        self.assertEqual((150,"8.5",90), (viewed["rest_time"],viewed["sets"][0]["rpe"],viewed["sets"][0]["rest_seconds"]))
        self.assertEqual(before,payload)


if __name__ == "__main__":
    unittest.main()
