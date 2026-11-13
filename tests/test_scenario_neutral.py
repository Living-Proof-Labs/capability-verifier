# SPDX-License-Identifier: Apache-2.0
"""Scenario-neutral verifier: tag pin, wrapper, frozen formats, stored-run replay."""

import hashlib
import json
import re
from pathlib import Path

import yaml

from capability_verifier import hallway
from capability_verifier.specs_ref import SPECS_TAG, catalog_reference_ok

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "lpv-run-43402628666"
FROZEN_FORMATS = {
    "livingproof-hallway-observation/1.0.0",
    "livingproof-hallway-observation/2.0.0",
    "livingproof-hallway-evidence/1.0.0",
    "livingproof-hallway-evidence/2.0.0",
    "livingproof-hallway-media/1.0.0",
    "livingproof-hallway-media/2.0.0",
}
DEFINITIONS = (
    "unitree-g1-playground",
    "unitree-g1-stock",
    "unitree-go1-playground",
    "booster-t1-stock",
)
FORMAT_LITERAL = re.compile(r"livingproof-hallway-[a-z0-9./-]+")


def test_catalog_reference_is_the_tag_not_the_commit():
    assert catalog_reference_ok("v2.2.0", "v2.2.0", "a" * 40, "b" * 40)
    assert not catalog_reference_ok("v1.9.0", "v2.2.0", "b" * 40, "b" * 40)
    lock = json.loads((ROOT / "hallway-specs.lock.json").read_text())
    assert lock["tag"] == SPECS_TAG == "v2.2.0"
    assert lock["commit"] == "23bec516bda29be7b26555286cbf2e8217ac5c81"
    assert "specs/scenarios/walk-hallway/parameters.json" in lock["files"]
    fetch = (ROOT / "tools" / "fetch_hallway_specs.py").read_text()
    assert "does not match the locked commit" not in fetch
    assert "catalog_reference_ok" in fetch


def test_released_pins_are_tags():
    project = (ROOT / "pyproject.toml").read_text()
    assert (
        "livingproof-evaluator-scenario @ git+https://github.com/Living-Proof-Labs/"
        "livingproof-evaluator-scenario.git@v1.2.0"
    ) in project
    assert (
        "livingproof-scenario-parameters @ git+https://github.com/Living-Proof-Labs/"
        "robot-capability-specs@v2.2.0#subdirectory=python"
    ) in project
    assert (
        "livingproof-sim-mujoco @ git+https://github.com/Living-Proof-Labs/"
        "livingproof-sim-mujoco.git@v2.1.0"
    ) in project
    assert "TODO" not in project
    worlds = json.loads((ROOT / "worlds.lock.json").read_text())
    assert worlds["tag"] == "v1.2.0"
    assert worlds["repository"] == "https://github.com/Living-Proof-Labs/livingproof-worlds"
    # The sim run-manifest world digest is not a pin and is not checked here.
    assert "sha256" not in worlds


def test_walk_hallway_workflow_is_a_thin_wrapper():
    wrapper = yaml.safe_load((ROOT / ".github/workflows/walk-hallway.yml").read_text())
    scenario = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
    call = wrapper[True]["workflow_call"]
    inner = scenario[True]["workflow_call"]
    assert set(call["inputs"]) == set(inner["inputs"])
    assert set(call["outputs"]) == set(inner["outputs"])
    jobs = wrapper["jobs"]
    assert set(jobs) == {"scenario"}
    job = jobs["scenario"]
    assert job["uses"] == "./.github/workflows/scenario.yml"
    assert set(job["with"]) == set(call["inputs"])
    for name in call["inputs"]:
        assert job["with"][name] == f"${{{{ inputs.{name} }}}}"
    for name in call["outputs"]:
        assert call["outputs"][name]["value"] == f"${{{{ jobs.scenario.outputs.{name} }}}}"
    assert "steps" not in job
    text = (ROOT / ".github/workflows/scenario.yml").read_text()
    assert "scenario: $(jq -r '.scenario // empty' verification.lock.json)" in text
    assert "world: $(jq -r '.world.id // empty' verification.lock.json)" in text
    assert "TODO" not in text


def test_no_new_hallway_format_is_written():
    roots = [ROOT / "src", ROOT / "tools", ROOT / ".github" / "workflows"]
    found = set()
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".yml", ".yaml", ".sh"}:
                continue
            found.update(FORMAT_LITERAL.findall(path.read_text(encoding="utf-8")))
    assert found == FROZEN_FORMATS


def test_limits_and_replay_match_the_stored_run():
    """Assessments, limits and grades from livingproof-verification run 43402628666."""
    provenance = json.loads((FIXTURES / "PROVENANCE.json").read_text())
    assert provenance["run_id"] == 43402628666
    assert provenance["head_sha"] == "2bcdb51415a9394dec322b1e531cb8780b9d72e4"
    assert provenance["verifier"] == "v1.18.5"
    by_id = {item["id"]: item for item in provenance["definitions"]}
    for definition in DEFINITIONS:
        evidence = FIXTURES / definition / "evidence" / "manifest.json"
        stored = json.loads((FIXTURES / definition / "verification.json").read_text())
        got = hallway.verify(evidence)
        assert json.dumps(got["limits"], indent=2) == json.dumps(stored["limits"], indent=2)
        assert got["thresholds"] == stored["thresholds"]
        assert [trial["status"] for trial in got["trials"]] == [
            trial["status"] for trial in stored["trials"]
        ]
        assert [trial["assessments"] for trial in got["trials"]] == [
            trial["assessments"] for trial in stored["trials"]
        ]
        assert got["summary"]["grade"] == stored["summary"]["grade"]
        item = by_id[definition]
        assert item["grade"] == stored["summary"]["grade"]
        for copied in item["files"]:
            fixture = FIXTURES / copied["fixture_path"]
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            assert digest == copied["sha256"]
