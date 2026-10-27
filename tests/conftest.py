# SPDX-License-Identifier: Apache-2.0
"""Bind published verification results to the source actually tested."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from docs import source_identity  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    # Dedicated docs checks must never replace runtime verification provenance.
    if str(session.config.option.xmlpath).endswith("artifacts/results.xml"):

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

        commit = os.environ.get("SOURCE_COMMIT") or git("rev-parse", "HEAD")
        try:
            version = git("describe", "--tags", "--always", "--long", "--dirty")
        except subprocess.CalledProcessError:
            version = "development-" + commit
        context = {
            "commit": commit,
            "version": version,
            "run": os.environ.get("GITHUB_RUN_URL", "local run"),
            "collected": session.testscollected,
            "exitstatus": int(exitstatus),
            "sources": source_identity(),
        }
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts/test-context.json").write_text(json.dumps(context, indent=2) + "\n")
