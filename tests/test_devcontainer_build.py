# SPDX-License-Identifier: Apache-2.0
"""Exercise installer failure handling without requiring a Docker daemon."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DevcontainerBuildTests(unittest.TestCase):
    def run_installer(self, *, corrupt=False, download_fails=False):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertNotIn("ADD --checksum", dockerfile)
        command = dockerfile.split("    set -eu;", 1)[1].split("\nARG SOURCE_COMMIT", 1)[0]
        command = "set -eu;" + command.replace("\\\n", "\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = b'#!/bin/sh\nprintf ran > "$MARKER"\n'
            expected = hashlib.sha256(script).hexdigest()
            command = command.replace(
                "e360c4d49273347e8ceb740f98d81d0b8b753de7d1b3c3452bdae7ec54289b99",
                expected,
            ).replace("/tmp/install-task.sh", str(root / "install-task.sh"))
            (root / "source").write_bytes(script + (b"# corrupted\n" if corrupt else b""))
            download = (
                "return 22" if download_fails else f'cp "{root}/source" "{root}/install-task.sh"'
            )
            command = f'export MARKER="{root}/ran"\ncurl() {{ {download}; }}\n' + command
            result = subprocess.run(
                ["sh", "-c", command], capture_output=True, text=True, check=False
            )
            return result.returncode, (root / "ran").exists()

    def test_matching_installer_runs(self):
        self.assertEqual(self.run_installer(), (0, True))

    def test_corrupt_installer_is_never_executed(self):
        code, ran = self.run_installer(corrupt=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(ran)

    def test_failed_download_is_never_executed(self):
        code, ran = self.run_installer(download_fails=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(ran)

    def test_default_devcontainer_reuses_runtime(self):
        config = json.loads((ROOT / ".devcontainer/devcontainer.json").read_text())
        self.assertEqual(config["build"]["dockerfile"], "../Dockerfile")
        self.assertEqual(config["build"]["target"], "development")
        self.assertEqual(config["remoteUser"], "developer")
        self.assertTrue(config["updateRemoteUserUID"])
        self.assertEqual(config["containerEnv"]["LIVINGPROOF_VERIFIER_VERSION"], "dev")
        self.assertEqual(config["postCreateCommand"], "task -t Taskfile.runtime.yml setup")

    def test_release_waits_for_devcontainer(self):
        workflow = (ROOT / ".github/workflows/verify.yml").read_text()
        self.assertIn("task devcontainer:verify", workflow)
        self.assertIn(
            "needs: [verify, report, report-results, documentation, devcontainer]", workflow
        )
        taskfile = (ROOT / "Taskfile.yml").read_text()
        self.assertIn("@devcontainers/cli@0.90.0 up", taskfile)
        self.assertIn("@devcontainers/cli@0.90.0 exec", taskfile)
