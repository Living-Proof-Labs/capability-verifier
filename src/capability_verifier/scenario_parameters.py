# SPDX-License-Identifier: Apache-2.0
"""Resolve scenario limits with livingproof_scenario_parameters v2.2.0.

``reference_errors`` runs first. ``resolve`` runs only when that list is empty.
The numbers are the spec-rounded limits: half-even on the binary value, to the
decimals the parameter file names. Callers pass those numbers to the evaluator.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from livingproof_scenario_parameters import ScenarioParameterError, reference_errors, resolve

# Frozen evidence that does not name a scenario still uses this parameter file.
FROZEN_EVIDENCE = {
    "livingproof-hallway-evidence/1.0.0": "walk-hallway@0.1.0",
    "livingproof-hallway-evidence/2.0.0": "walk-hallway@0.1.0",
}
# The frozen scenario: graded on the frozen straight route, with no world document.
FROZEN_SCENARIO = "walk-hallway"
_CACHE: dict[str, dict] = {}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def scenario_identity(manifest: dict) -> tuple[str, str] | None:
    """``(id, revision)`` from the plan, the lock, or a frozen evidence format."""
    texts: list[str] = []
    plan = manifest.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("scenario"), str):
        texts.append(plan["scenario"])
    lock = manifest.get("lock")
    content = lock.get("content") if isinstance(lock, dict) else None
    if isinstance(content, dict) and isinstance(content.get("scenario"), str):
        texts.append(content["scenario"])
    if not texts:
        frozen = FROZEN_EVIDENCE.get(manifest.get("format"))
        if frozen:
            texts.append(frozen)
    if not texts or "@" not in texts[0]:
        return None
    name, revision = texts[0].split("@", 1)
    if not name or not revision:
        return None
    return name, revision


def parameters_path(scenario_id: str, root: Path | None = None) -> Path:
    base = repo_root() if root is None else root
    return base / ".specs-hallway" / "specs" / "scenarios" / scenario_id / "parameters.json"


def load_parameters(manifest: dict | None = None, root: Path | None = None) -> dict:
    """The scenario parameter file for ``manifest``, or walk-hallway when it names none."""
    scenario, revision = ("walk-hallway", "0.1.0")
    if manifest is not None:
        identity = scenario_identity(manifest)
        if identity is not None:
            scenario, revision = identity
    path = parameters_path(scenario, root)
    key = str(path)
    if key not in _CACHE:
        if not path.is_file():
            raise ScenarioParameterError(
                f"scenario parameters for {scenario} are not in the catalog checkout "
                f"({path}). Run: python tools/fetch_hallway_specs.py"
            )
        _CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    document = _CACHE[key]
    named = document.get("scenario") if isinstance(document.get("scenario"), dict) else {}
    if (named.get("id"), named.get("revision")) != (scenario, revision):
        raise ScenarioParameterError(
            f"parameter file names {named.get('id')}@{named.get('revision')}, "
            f"not {scenario}@{revision}"
        )
    return document


def uses_world(manifest: dict) -> bool:
    """True when the scenario's route and places come from its world document.

    walk-hallway (and evidence that names no scenario) keeps the frozen straight route,
    so its grading and stored bytes are unchanged.
    """
    identity = scenario_identity(manifest)
    return identity is not None and identity[0] != FROZEN_SCENARIO


def check_names(manifest: dict | None = None, root: Path | None = None) -> tuple[str, ...]:
    """The scenario's checks, in its parameter file's order."""
    return tuple(c["evaluator"] for c in load_parameters(manifest, root)["checks"])


def command_speed(manifest: dict, root: Path | None = None):
    """The speed the evidence recorded, else the parameter file's (walk-corner has no plan speed)."""
    plan = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
    if "command_speed" in plan:
        return plan["command_speed"]
    return load_parameters(manifest, root)["parameters"]["command_speed"]["value"]


def worlds_root(root: Path | None = None) -> Path:
    return (repo_root() if root is None else root) / ".worlds"


def world_document(world: dict, sha256: str | None = None, root: Path | None = None) -> dict:
    """``world.json`` for ``world`` (id and version) from the locked livingproof-worlds release.

    ``sha256`` is the world digest the run lock recorded. Different bytes are refused:
    the run was not on the world this verifier grades with.
    """
    path = (
        worlds_root(root)
        / "worlds"
        / str(world.get("id"))
        / str(world.get("version"))
        / "world.json"
    )
    if not path.is_file():
        raise ScenarioParameterError(
            f"world {world.get('id')} {world.get('version')} is not in the worlds checkout "
            f"({path}). Run: python tools/fetch_hallway_specs.py"
        )
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ScenarioParameterError(
            f"world {world.get('id')} {world.get('version')} in the run lock has sha256 "
            f"{sha256}, not the locked worlds release's {digest}"
        )
    return json.loads(raw)


def raw_inputs(manifest: dict) -> dict:
    """Quantities the evidence already carries. Route length is not recomputed."""
    robot = manifest["robot"]["profile"]
    configuration = manifest["configuration"]["profile"]
    world = manifest["world"]
    return {
        "route_length": world["route_length"],
        "clear_width": world["clear_width"],
        "robot_width": robot["dimensions"]["width"],
        "robot_class": robot.get("class"),
        "fall_base_height": configuration["locomotion"]["fall_base_height"],
    }


def acceptance(manifest: dict, root: Path | None = None) -> dict:
    """``resolve`` after ``reference_errors``. A reference problem does not resolve.

    ``plan.command_speed`` is the speed the evidence recorded. The parameter file's
    expression and rounding are applied to that speed, which is 0.5 for walk-hallway.
    Evidence whose plan has no speed (walk-corner) uses the parameter file's value.
    """
    document = copy.deepcopy(load_parameters(manifest, root))
    world = document.get("world") if isinstance(document.get("world"), dict) else {}
    got = manifest.get("world") if isinstance(manifest.get("world"), dict) else {}
    if "id" in got and (got.get("id"), got.get("version")) != (
        world.get("id"),
        world.get("content_version"),
    ):
        raise ScenarioParameterError(
            f"parameter file is for {world.get('id')} {world.get('content_version')}, "
            f"not {got.get('id')} {got.get('version')}"
        )
    document["parameters"]["command_speed"]["value"] = command_speed(manifest, root)
    problems = reference_errors(document)
    if problems:
        raise ScenarioParameterError("; ".join(problems))
    return resolve(document, raw_inputs(manifest))


def evaluator_thresholds(resolved: dict) -> dict:
    """The four numbers the released evaluator reads.

    ``exit_segment`` is passed as ``half_width``. The values are the spec-rounded
    limits from ``resolve``.
    """
    limits = resolved["limits"]
    return {
        "time_limit": limits["time_limit"],
        "lateral_limit": limits["lateral_limit"],
        "half_width": limits["exit_segment"],
        "fall_height": limits["fall_height"],
    }


def clearance_table(manifest: dict | None = None, root: Path | None = None) -> dict:
    """Wall clearance by robot class, from the scenario parameter file."""
    table = load_parameters(manifest, root)["parameters"]["wall_clearance"]["values"]
    if not isinstance(table, dict):
        raise ScenarioParameterError("parameter file wall_clearance values are missing")
    return table
