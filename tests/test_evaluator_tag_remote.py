# SPDX-License-Identifier: Apache-2.0
"""The declared evaluator tag resolves to the commit uv.lock records.

The pin tests already check that pyproject.toml, uv.lock and the installed
package name the same vX.Y.Z release. This adds the network check from the #96
review: `git ls-remote` for that tag returns the lock's commit, so the hash was
not typed in by hand. Set LIVINGPROOF_NETWORK=1 to run it. Proposed by Vera in
the capability-verifier#96 review.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

REPO = "https://github.com/Living-Proof-Labs/livingproof-evaluator-scenario.git"
PIN = re.compile(
    r"livingproof-evaluator-scenario @ git\+https://github.com/Living-Proof-Labs/"
    r"livingproof-evaluator-scenario\.git@(?P<tag>v\d+\.\d+\.\d+)"
)
LOCK = re.compile(
    r"livingproof-evaluator-scenario\.git\?rev=(?P<tag>v\d+\.\d+\.\d+)#(?P<commit>[0-9a-f]{40})"
)

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVINGPROOF_NETWORK") != "1",
    reason="set LIVINGPROOF_NETWORK=1 to resolve the published evaluator tag",
)


def test_the_declared_tag_resolves_to_the_lock_commit():
    pin = PIN.search((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = LOCK.search((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert pin and lock, "declared pin and uv.lock must name a vX.Y.Z evaluator tag"
    assert pin.group("tag") == lock.group("tag")
    remote = subprocess.run(
        ["git", "ls-remote", REPO, f"refs/tags/{pin.group('tag')}"],
        check=True,
        capture_output=True,
        text=True,
    )
    line = remote.stdout.strip().splitlines()[0]
    sha, _, ref = line.partition("\t")
    assert ref == f"refs/tags/{pin.group('tag')}"
    assert sha == lock.group("commit")
