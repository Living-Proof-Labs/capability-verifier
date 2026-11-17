# SPDX-License-Identifier: Apache-2.0
"""Assess evidence against the caller lock's declared specs and world.

The declared specs commit and world digest are the catalog for this run.
hallway-specs.lock.json and worlds.lock.json are not read. Integrity, schema,
and vocabulary failures write livingproof-verification-refusal/0.1 and no
grade (livingproof-verification#100 step 4, capability-verifier#138).
"""

from __future__ import annotations

import json
from pathlib import Path

from capability_verifier import hallway
from capability_verifier.refusal import GRADE_NAME, REFUSAL_NAME, run
from capability_verifier.scenario_parameters import checkout, scenario_identity, uses_world

# Check ids this evaluator implements: walk-hallway and walk-corner.
SUPPORTED_CHECK_IDS = (
    "hallway-exit-reached",
    "hallway-lateral-limit",
    "hallway-wall-contact",
    "hallway-fall",
    "corner-exit-reached",
    "corner-turned",
    "corner-route-limit",
    "corner-wall-contact",
    "corner-fall",
)


def refuse(
    manifest_path: Path,
    out: Path,
    *,
    specs_root: Path,
    worlds_root: Path,
) -> int:
    """Run the pre-grade gates against the declared checkout.

    On refusal: write ``refusal.json`` and return 2. On success: write nothing
    and return 0.
    """
    manifest_path = Path(manifest_path)
    specs_root = Path(specs_root)
    worlds_root = Path(worlds_root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return run(_request(manifest, specs_root, worlds_root), out)


def assess(
    manifest_path: Path,
    out: Path,
    *,
    specs_root: Path,
    worlds_root: Path,
) -> int:
    """Grade ``manifest_path`` using the declared checkout, or write a refusal.

    On refusal: write ``refusal.json``, write no ``verification.json``, return 2.
    On success: write ``verification.json`` and return 0.
    If ``out`` already holds a ``verification.json`` or ``refusal.json``, raise
    ``FileExistsError`` and write or delete nothing (Aaron's never-clobber
    decision on livingproof-verification#100).
    """
    manifest_path = Path(manifest_path)
    out = Path(out)
    specs_root = Path(specs_root)
    worlds_root = Path(worlds_root)
    if (out / GRADE_NAME).is_file() or (out / REFUSAL_NAME).is_file():
        raise FileExistsError(f"{out} already holds a verification or refusal")
    code = refuse(manifest_path, out, specs_root=specs_root, worlds_root=worlds_root)
    if code != 0:
        return code
    with checkout(specs_root, worlds_root):
        result = hallway.verify(manifest_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / GRADE_NAME).write_text(json.dumps(result, indent=2) + "\n")
    return 0


def _lock_content(manifest: dict) -> dict:
    lock = manifest.get("lock") if isinstance(manifest.get("lock"), dict) else {}
    content = lock.get("content") if isinstance(lock.get("content"), dict) else {}
    return content if isinstance(content, dict) else {}


def _specs_declared(content: dict) -> dict:
    releases = content.get("releases") if isinstance(content.get("releases"), dict) else {}
    specs = releases.get("specs") if isinstance(releases.get("specs"), dict) else {}
    return dict(specs) if isinstance(specs, dict) else {}


def _scenario_id(manifest: dict) -> str:
    identity = scenario_identity(manifest)
    if identity is None:
        return "walk-hallway"
    return identity[0]


def _parameters_digest(specs: dict, parameters_path: str) -> str | None:
    """The declared parameters digest: ``files[<path>]``, else ``sha256``."""
    files = specs.get("files") if isinstance(specs.get("files"), dict) else {}
    digest = files.get(parameters_path)
    if isinstance(digest, str):
        return digest
    digest = specs.get("sha256")
    return digest if isinstance(digest, str) else None


def _request(manifest: dict, specs_root: Path, worlds_root: Path) -> dict:
    content = _lock_content(manifest)
    specs = _specs_declared(content)
    scenario_id = _scenario_id(manifest)
    rel_path = f"specs/scenarios/{scenario_id}/parameters.json"
    param_path = specs_root / rel_path
    schema_path = specs_root / "specs" / "scenarios" / "scenario-parameters.schema.json"
    declared_parameters = {**specs, "path": rel_path}
    digest = _parameters_digest(specs, rel_path)
    if digest is not None:
        declared_parameters["sha256"] = digest
    # Always include the parameters file. A missing declared digest is an
    # integrity refusal; do not self-certify from the file's own bytes.
    files: list[dict] = [
        {
            "role": "parameters",
            "path": param_path,
            "declared": declared_parameters,
        }
    ]
    world_entry = _world_file(manifest, content, worlds_root)
    if world_entry is not None:
        files.append(world_entry)
    request: dict = {"files": files}
    if param_path.is_file():
        request["schema"] = {
            "role": "parameters",
            "path": param_path,
            "schema_path": schema_path,
            "declared": declared_parameters,
        }
        request["vocabulary"] = {
            "role": "checks",
            "declared": declared_parameters,
            "check_ids": _check_ids(param_path),
            "supported_check_ids": list(SUPPORTED_CHECK_IDS),
        }
    return request


def _world_file(manifest: dict, content: dict, worlds_root: Path) -> dict | None:
    locked = content.get("world") if isinstance(content.get("world"), dict) else {}
    observed = manifest.get("world") if isinstance(manifest.get("world"), dict) else {}
    world_id = locked.get("id") or observed.get("id")
    version = locked.get("version") or observed.get("version")
    if not world_id or not version:
        return None
    path = worlds_root / "worlds" / str(world_id) / str(version) / "world.json"
    declared = dict(locked) if locked else {"id": world_id, "version": version}
    needed = not path.is_file() and (
        uses_world(manifest) or isinstance(declared.get("sha256"), str)
    )
    if path.is_file() and isinstance(declared.get("sha256"), str) or needed:
        return {"role": "world", "path": path, "declared": declared}
    return None


def _check_ids(path: Path) -> list[str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    checks = document.get("checks") if isinstance(document, dict) else None
    if not isinstance(checks, list):
        return []
    names = []
    for item in checks:
        if isinstance(item, dict) and isinstance(item.get("evaluator"), str):
            names.append(item["evaluator"])
    return names


# Re-export so tests and tools can share the output names with this module.
__all__ = ["SUPPORTED_CHECK_IDS", "assess", "refuse"]
