from __future__ import annotations

from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location("component_package_builder", ROOT / "tools/build-public-package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageValidationIntegrationTests(unittest.TestCase):
    def arguments(self, builder, output):
        return builder.argparse.Namespace(output_directory=str(output), codex_home="nonexistent-legacy-home", python=sys.executable)

    def unit_test_context(self, builder):
        if ROOT.name != "qianji-agent":
            return nullcontext()
        # A Qianji production build runs this suite. Avoid recursively invoking
        # that suite inside this package fixture, retaining every validator call.
        original = builder.run_command
        def run(command, root, label):
            return None if label == "Unit tests" else original(command, root, label)
        return patch.object(builder, "run_command", side_effect=run)

    def test_dependency_failure_creates_no_output_directory(self):
        builder = load_builder()
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "uncreated"
            with patch.object(builder, "validator_preflight", side_effect=builder.BuildError("missing build dependency")):
                with self.assertRaisesRegex(builder.BuildError, "missing build dependency"):
                    builder.build(self.arguments(builder, output))
            self.assertFalse(output.exists())

    def test_payload_validation_failure_leaves_no_final_artifacts(self):
        builder = load_builder()
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            with self.unit_test_context(builder), patch.object(builder, "run_validator", side_effect=builder.BuildError("invalid fixture payload")):
                with self.assertRaisesRegex(builder.BuildError, "invalid fixture payload"):
                    builder.build(self.arguments(builder, output))
            self.assertEqual([], list(output.iterdir()))

    def test_valid_build_records_provenance_and_zip_payload_parity(self):
        builder = load_builder()
        with TemporaryDirectory() as temporary, self.unit_test_context(builder):
            result = builder.build(self.arguments(builder, temporary))
            release = Path(result["release_root"])
            checksums = json.loads((release / "SHA256SUMS.json").read_text(encoding="utf-8-sig"))
            provenance = checksums["validation"]["validator"]
            self.assertEqual("agent-package-validation", provenance["id"])
            self.assertEqual("1.0.0", provenance["version"])
            expected_hash = hashlib.sha256((ROOT / "tools/package_validation.py").read_bytes()).hexdigest().upper()
            self.assertEqual(expected_hash, provenance["sha256"])
            self.assertEqual(provenance, result["validator"])
            expanded = {p.relative_to(release).as_posix(): p.read_bytes() for p in release.rglob("*") if p.is_file()}
            with zipfile.ZipFile(result["zip_path"]) as archive:
                names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(set(expanded), set(names))
                for name, data in expanded.items():
                    self.assertEqual(data, archive.read(name), name)
            with self.assertRaisesRegex(builder.BuildError, "already exists"):
                builder.build(self.arguments(builder, temporary))


if __name__ == "__main__":
    unittest.main()
