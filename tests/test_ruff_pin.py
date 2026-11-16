# SPDX-License-Identifier: Apache-2.0
"""pyproject.toml, the repin fixture, uv.lock and the installed package agree on ruff.

Proposed by Vera in the capability-verifier#77 review. The exact version is not
frozen, so a later ruff bump still passes when those four sources stay in agreement.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUFF_REQ = re.compile(r"^ruff==(?P<version>.+)$")


def _declared_ruff(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    found = []
    for items in (data.get("dependency-groups") or {}).values():
        for req in items:
            if isinstance(req, str) and RUFF_REQ.fullmatch(req):
                found.append(RUFF_REQ.fullmatch(req).group("version"))
    assert len(found) == 1, f"{path} must declare exactly one ruff== pin, found {found}"
    return found[0]


def _locked_ruff() -> str:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [item["version"] for item in data.get("package", []) if item.get("name") == "ruff"]
    assert len(versions) == 1, f"uv.lock must name exactly one ruff package, found {versions}"
    return versions[0]


def test_pyproject_lock_and_installed_ruff_agree():
    declared = _declared_ruff(ROOT / "pyproject.toml")
    assert _declared_ruff(ROOT / "tests/fixtures/repin/pyproject.toml") == declared
    assert _locked_ruff() == declared
    assert importlib.metadata.version("ruff") == declared
