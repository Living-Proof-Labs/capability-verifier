# SPDX-License-Identifier: Apache-2.0
"""pyproject.toml, the repin fixture and uv.lock all pin the same ruff release.

Proposed by Vera in the capability-verifier#77 review.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]

PIN = "ruff==0.17.5"
LOCK = re.compile(
    r'^name = "ruff"\nversion = "(?P<version>[^"]+)"',
    re.MULTILINE,
)


def test_the_declared_ruff_pin_matches_the_lock_and_the_repin_fixture():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    fixture = (ROOT / "tests/fixtures/repin/pyproject.toml").read_text(encoding="utf-8")
    assert PIN in project
    assert PIN in fixture
    lock = LOCK.search((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert lock is not None, "uv.lock must name the ruff package"
    assert lock.group("version") == "0.17.5"
