# SPDX-License-Identifier: Apache-2.0
"""Stamp a parameters digest into a copied evidence lock for historical fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from capability_verifier.scenario_parameters import scenario_identity


def declare_parameters_digest(evidence: Path, specs_root: Path) -> None:
    """Write releases.specs.files[<parameters path>] when the lock omits it.

    Historical fixtures predate the files map. Production assess still refuses
    when no digest is declared; tests stamp the digest from the checkout they
    already use.
    """
    manifest_path = Path(evidence) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lock = manifest.setdefault("lock", {})
    if not isinstance(lock, dict):
        return
    content = lock.setdefault("content", {})
    if not isinstance(content, dict):
        return
    releases = content.setdefault("releases", {})
    if not isinstance(releases, dict):
        return
    specs = releases.setdefault("specs", {})
    if not isinstance(specs, dict):
        return
    identity = scenario_identity(manifest)
    scenario_id = identity[0] if identity else "walk-hallway"
    rel = f"specs/scenarios/{scenario_id}/parameters.json"
    param = Path(specs_root) / rel
    if not param.is_file():
        return
    files = specs.get("files")
    if not isinstance(files, dict):
        files = {}
        specs["files"] = files
    if isinstance(files.get(rel), str) or isinstance(specs.get("sha256"), str):
        return
    files[rel] = hashlib.sha256(param.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
