import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fitness_data_bridge.io import atomic_write_json
from fitness_data_bridge.xunji_credentials import (
    CredentialError, VerifiedReadCredential, credential_account_scope, credential_status,
    import_credential, load_credential, stage_open_credential,
)
from fitness_data_bridge.xunji_key_manager import main, verify_open_read
from fitness_data_bridge.xunji_transport import HttpResponse, XunjiOpenApiError, post_json


class KeyManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"keys.json"
        self.cache = Path(self.temp.name)/"cache"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.legacy = "xjllm_" + "A"*40
        self.candidate = "synthetic-open-candidate-" + "B"*24

    def stage(self):
        import_credential(self.path, "training", self.legacy)
        stage_open_credential(self.path, self.candidate)

    def verify(self, **kwargs):
        return verify_open_read(self.path, "training.query", datestr="2026-01-05",
            cache_dir=self.cache, clock=lambda: 1000.0, **kwargs)

    def test_single_slot_merge_preserves_other_slots_and_root_metadata(self):
        atomic_write_json(self.path, {"schema_version": 1, "xunji": {"body": "xjbody_"+"C"*24}, "owner_metadata": {"keep": True}})
        import_credential(self.path, "training", self.legacy)
        document = json.loads(self.path.read_text())
        self.assertIn("body", document["xunji"])
        self.assertTrue(document["owner_metadata"]["keep"])
        self.assertEqual(self.legacy, load_credential("training", credential_file=self.path))

    def test_replace_is_explicit_and_type_error_does_not_modify_file(self):
        import_credential(self.path, "training", self.legacy)
        before = self.path.read_bytes()
        for value in ("xjllm_"+"D"*24, "xjbody_"+"E"*24):
            with self.assertRaises(CredentialError):
                import_credential(self.path, "training", value)
            self.assertEqual(before, self.path.read_bytes())

    def test_staged_unknown_format_is_not_automatically_active(self):
        self.stage()
        self.assertEqual(self.legacy, load_credential("training", credential_file=self.path))
        status = credential_status(self.path)
        self.assertTrue(status["open_api_staged"])
        self.assertEqual([], status["active_read_slots"])
        self.assertNotIn(self.candidate, json.dumps(status))
        self.assertFalse(status["live_authentication_checked"])

    def test_uncached_read_verification_and_explicit_activation(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)) as request:
            result = self.verify(activate_read=True)
        self.assertEqual("live_api", result["response_source"])
        self.assertFalse(result["write_permission_verified"])
        self.assertEqual(1, request.call_count)
        key = load_credential("training", credential_file=self.path)
        self.assertIsInstance(key, VerifiedReadCredential)
        self.assertIn("training.query", credential_status(self.path)["verified_reads"])
        with patch("fitness_data_bridge.xunji_transport.urlopen") as network:
            for url in ("https://trains.xunjiapp.cn/api_upsert_trains_for_llm_v2", "https://api.xunjiapp.cn/open/plan/query_gzip"):
                with self.assertRaises(XunjiOpenApiError):
                    post_json(url=url, body={}, credential=key)
            network.assert_not_called()

    def test_verification_without_activation_keeps_legacy_binding(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)):
            self.verify()
        self.assertEqual(self.legacy, load_credential("training", credential_file=self.path))

    def test_bad_response_or_transport_failure_does_not_grant_access(self):
        self.stage()
        before = self.path.read_bytes()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {}}, 200)):
            with self.assertRaises(XunjiOpenApiError):
                self.verify(activate_read=True)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], credential_status(self.path)["active_read_slots"])

    def test_rotation_clears_grants_preserves_legacy_and_isolates_cache(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)):
            self.verify(activate_read=True)
        previous = credential_account_scope("training", load_credential("training", credential_file=self.path))
        stage_open_credential(self.path, "synthetic-open-rotated-"+"F"*24, replace=True)
        self.assertEqual([], credential_status(self.path)["verified_reads"])
        self.assertEqual(self.legacy, load_credential("training", credential_file=self.path))
        self.assertNotEqual(previous, credential_account_scope("training", self.legacy))

    def test_tampered_key_cannot_reuse_verification(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)):
            self.verify(activate_read=True)
        document = json.loads(self.path.read_text())
        document["open_api"]["key"] = "synthetic-tampered-"+"G"*24
        atomic_write_json(self.path, document)
        with self.assertRaises(CredentialError):
            load_credential("training", credential_file=self.path)

    def test_status_checks_inactive_proofs_and_rejects_unregistered_names(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)):
            self.verify()
        document = json.loads(self.path.read_text())
        document["open_api"]["verified_reads"]["unregistered-probe"] = {}
        atomic_write_json(self.path, document)
        with self.assertRaises(CredentialError):
            credential_status(self.path)

    def test_concurrent_rotation_does_not_get_overwritten(self):
        self.stage()
        def request(**kwargs):
            stage_open_credential(self.path, "synthetic-concurrent-"+"H"*24, replace=True)
            return HttpResponse({"res": {"trains": []}}, 200)
        with patch("fitness_data_bridge.xunji_key_manager.post_json", side_effect=request):
            with self.assertRaises(CredentialError):
                self.verify(activate_read=True)
        self.assertEqual([], credential_status(self.path)["verified_reads"])

    def test_local_rate_limit_does_not_loop_or_use_cache_as_authentication(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json", return_value=HttpResponse({"res": {"trains": []}}, 200)) as request:
            self.verify()
            with self.assertRaises(XunjiOpenApiError):
                self.verify()
            self.assertEqual(1, request.call_count)

    def test_cli_reads_stdin_without_echoing_key(self):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(self.legacy)), redirect_stdout(output):
            result = main(["--credentials-file", str(self.path), "import", "--slot", "training"])
        self.assertEqual(0, result)
        self.assertNotIn(self.legacy, output.getvalue())
        self.assertEqual(["training"], json.loads(output.getvalue())["legacy_slots"])

    def test_unregistered_probe_never_reaches_network(self):
        self.stage()
        with patch("fitness_data_bridge.xunji_key_manager.post_json") as request:
            with self.assertRaises(CredentialError):
                verify_open_read(self.path, "training.upsert", cache_dir=self.cache)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
