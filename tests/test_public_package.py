from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicPackageTests(unittest.TestCase):
    def test_public_package_excludes_development_and_private_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "build-public-package.py"),
                    "--output-directory",
                    temporary,
                    "--python",
                    sys.executable,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            release_root = Path(result["release_root"])
            plugin_root = release_root / "plugins" / "fitness-data-bridge"
            self.assertFalse((plugin_root / "tests").exists())
            self.assertFalse((plugin_root / "tools").exists())
            self.assertFalse((plugin_root / ".git").exists())
            self.assertIn("runtime contract", (plugin_root / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("PASS", result["privacy_scan"])
            self.assertEqual("shujian-component-release", result["package_type"])
            self.assertEqual("shujian-agent", result["marketplace_owner"])
            self.assertFalse(result["marketplace_generated"])
            self.assertFalse(
                (release_root / ".agents" / "plugins" / "marketplace.json").exists()
            )
            for installer in ("install.py", "install.ps1", "install.sh"):
                self.assertFalse((release_root / installer).exists())


if __name__ == "__main__":
    unittest.main()
