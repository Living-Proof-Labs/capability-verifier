# SPDX-License-Identifier: Apache-2.0
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.release_eligibility import SELF_PIN, eligible, git


class ReleaseEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.pin = self.root / SELF_PIN
        self.pin.parent.mkdir(parents=True)
        self.write_pin("1.0.0")
        self.commit("feat: initial release")
        self.git("tag", "v1.0.0")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def commit(self, message="chore(deps): update self-image"):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def write_pin(self, version, **extra):
        self.pin.write_text(
            json.dumps(
                {
                    "image": "ghcr.io/living-proof-labs/capability-verifier-hallway:"
                    + version
                    + "@sha256:"
                    + "a" * 64,
                    "remoteUser": "lpl",
                    **extra,
                }
            )
        )

    def test_pin_only_history_does_not_release(self):
        for version in ("1.0.1", "1.0.2"):
            self.write_pin(version)
            self.commit()
            self.assertFalse(eligible(self.root)[0])

    def test_mixed_fix_and_pin_releases(self):
        for message in ("fix: runtime behavior", "feat: new feature", "chore: maintenance"):
            with self.subTest(message=message):
                self.write_pin("1.0.1")
                (self.root / "runtime.py").write_text(message)
                self.commit(message)
                self.assertTrue(eligible(self.root)[0])

    def test_other_devcontainer_changes_release(self):
        self.write_pin("1.0.1", remoteUser="other")
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_reverted_runtime_change_still_releases(self):
        (self.root / "runtime.py").write_text("change")
        self.commit("fix: runtime")
        (self.root / "runtime.py").unlink()
        self.commit("revert: runtime")
        self.write_pin("1.0.1")
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_invalid_pin_releases(self):
        self.write_pin("latest")
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_mode_change_releases(self):
        self.write_pin("1.0.1")
        self.pin.chmod(0o755)
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_no_release_tag_preserves_policy(self):
        self.git("tag", "-d", "v1.0.0")
        self.write_pin("1.0.1")
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_empty_range_preserves_policy(self):
        self.assertTrue(eligible(self.root)[0])

    def test_shallow_history_preserves_policy(self):
        self.write_pin("1.0.1")
        self.commit()
        (self.root / ".git/shallow").write_text(self.git("rev-parse", "HEAD") + "\n")
        self.assertTrue(eligible(self.root)[0])

    def test_merge_commit_pin_only_does_not_release(self):
        original = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "renovate")
        self.write_pin("1.0.1")
        self.commit()
        self.git("checkout", "-q", original)
        self.git("merge", "--no-ff", "-qm", "chore: merge pin", "renovate")
        self.assertFalse(eligible(self.root)[0])

    def test_new_release_resets_range(self):
        (self.root / "runtime.py").write_text("fix")
        self.commit("fix: runtime")
        self.git("tag", "v1.0.1")
        self.write_pin("1.0.1")
        self.commit()
        self.assertFalse(eligible(self.root)[0])

    def test_side_branch_release_tag_preserves_policy(self):
        original = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "side")
        self.write_pin("1.0.1")
        self.commit()
        self.git("tag", "v1.0.1")
        self.git("checkout", "-q", original)
        self.git("merge", "--no-ff", "-qm", "merge side release", "side")
        self.assertTrue(eligible(self.root)[0])

    def test_history_query_failure_is_not_silently_suppressed(self):
        with (
            patch(
                "tools.release_eligibility.git", side_effect=subprocess.CalledProcessError(1, "git")
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            eligible(self.root)

    def test_unreadable_pin_preserves_policy(self):
        self.write_pin("1.0.1")
        self.commit()

        def fail_blob(root, *args):
            if args[0] == "show":
                raise subprocess.CalledProcessError(1, ["git", *args])
            return git(root, *args)

        with patch("tools.release_eligibility.git", side_effect=fail_blob):
            self.assertTrue(eligible(self.root)[0])

    def test_malformed_pin_preserves_policy(self):
        self.pin.write_text("invalid JSON")
        self.commit()
        self.assertTrue(eligible(self.root)[0])

    def test_workflow_gates_only_release(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/verify.yml").read_text()
        self.assertIn('task --silent release:classify >> "$GITHUB_OUTPUT"', workflow)
        self.assertEqual(workflow.count("needs.verify.outputs.release-eligible == 'true'"), 1)
        self.assertIn("fetch-depth: 0", workflow)
