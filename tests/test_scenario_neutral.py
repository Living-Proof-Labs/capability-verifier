# SPDX-License-Identifier: Apache-2.0
"""Scenario-neutral verifier: tag pin, wrapper, frozen formats, stored-run replay."""

import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from capability_verifier import definition as d
from capability_verifier import evaluate, hallway
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
        "livingproof-sim-mujoco.git@v2.1.1"
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
    assert job["uses"] == "$/.github/workflows/scenario.yml"
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


def test_stored_verification_bytes_match_except_the_v1_19_0_summary():
    """Each stored verification.json is rewritten byte for byte with this commit's limits.

    Only ``summary`` may differ: v1.19.0 reports identical repeats as one outcome, and
    run 43402628666 was graded by v1.18.5.
    """
    for definition in DEFINITIONS:
        evidence = FIXTURES / definition / "evidence" / "manifest.json"
        raw = (FIXTURES / definition / "verification.json").read_text()
        stored = json.loads(raw)
        assert json.dumps(stored, indent=2) + "\n" == raw
        got = hallway.verify(evidence)
        assert json.dumps({**stored, "limits": got["limits"]}, indent=2) + "\n" == raw
        assert set(got) == set(stored)
        changed = sorted(key for key in stored if got[key] != stored[key])
        assert changed == ["summary"], (definition, changed)
        assert got["summary"]["aggregation"]["id"] == "repeated-outcome"
        assert got["summary"]["rationale"] == "3 of 3 repeats agree"


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


def test_wrapper_forwards_every_input_and_output_definition_unchanged():
    """Callers see the same types, defaults and descriptions through the wrapper."""
    wrapper = _workflow("walk-hallway.yml")[True]["workflow_call"]
    scenario = _workflow("scenario.yml")[True]["workflow_call"]
    assert wrapper["inputs"] == scenario["inputs"]
    assert list(wrapper["inputs"]) == list(scenario["inputs"])
    for name, output in wrapper["outputs"].items():
        assert output["description"] == scenario["outputs"][name]["description"]
    assert "secrets" not in wrapper and "secrets" not in scenario


def test_workflow_permissions_are_no_wider_than_contents_read_and_pr_write():
    allowed = {"contents": "read", "pull-requests": "write"}
    for name in ("walk-hallway.yml", "scenario.yml"):
        document = _workflow(name)
        assert document["permissions"] == {"contents": "read"}, name
        assert set(document[True]) == {"workflow_call"}, name
        for job_id, job in document["jobs"].items():
            granted = job.get("permissions", {})
            assert isinstance(granted, dict), (name, job_id)
            assert granted.items() <= allowed.items(), (name, job_id, granted)
            assert "secrets" not in job, (name, job_id)
    wrapper_job = _workflow("walk-hallway.yml")["jobs"]["scenario"]
    assert wrapper_job["permissions"] == allowed
    text = (ROOT / ".github" / "workflows" / "scenario.yml").read_text()
    assert "secrets." not in text
    assert "pull_request_target" not in text


def test_runner_follows_the_wrapper_to_the_scenario_workflow():
    scenario = (ROOT / ".github" / "workflows" / "scenario.yml").read_text()
    wrapper = (ROOT / ".github" / "workflows" / "walk-hallway.yml").read_text()
    assert d.called_workflow(wrapper) == ".github/workflows/scenario.yml"
    assert d.called_workflow(scenario) is None
    assert d.procedure_text(ROOT, "walk-hallway") == scenario


def test_scenario_workflow_names_sim_v2_1_1():
    """The workflow text names the pinned simulator, v2.1.1."""
    scenario = (ROOT / ".github" / "workflows" / "scenario.yml").read_text()
    wrapper = (ROOT / ".github" / "workflows" / "walk-hallway.yml").read_text()
    assert "v2.0.3" not in scenario
    assert "v2.0.3" not in wrapper
    assert scenario.count("livingproof-sim-mujoco v2.1.1") == 4
    assert wrapper.count("livingproof-sim-mujoco v2.1.1") == 1


def test_called_workflow_accepts_the_same_repository_form():
    """$/ is this workflow's repository and revision. ./ is the caller's repository."""
    same = "jobs:\n  scenario:\n    uses: $/.github/workflows/scenario.yml\n"
    assert d.called_workflow(same) == ".github/workflows/scenario.yml"
    assert d.called_workflow(same.replace("$/", "./")) == ".github/workflows/scenario.yml"
    remote = (
        "jobs:\n  scenario:\n    uses: Living-Proof-Labs/capability-verifier/"
        ".github/workflows/scenario.yml@abc\n"
    )
    assert d.called_workflow(remote) is None


def test_called_workflow_rejects_a_ref_on_the_same_repository_form():
    """$/ names a path. A ref is not part of that form."""
    pinned = "jobs:\n  scenario:\n    uses: $/x.yml@ref\n"
    assert d.called_workflow(pinned) is None


def test_catalog_step_accepts_the_new_name_and_the_old_name():
    scenario = (ROOT / ".github" / "workflows" / "scenario.yml").read_text()
    names = ("Scenario catalog", "Walk-hallway catalog")
    new = d.first_step(scenario, names)
    old = d.first_step(scenario.replace("Scenario catalog", "Walk-hallway catalog"), names)
    assert new == old
    assert "fetch_hallway_specs.py" in new
    with pytest.raises(d.DefinitionError, match="no step"):
        d.first_step(scenario.replace("Scenario catalog", "Other catalog"), names)


def test_wrapper_self_reference_names_a_callable_workflow_in_this_repository():
    """$/ takes no @ref, names a file under .github/workflows, and that file is callable."""
    uses = _workflow("walk-hallway.yml")["jobs"]["scenario"]["uses"]
    assert uses.startswith("$/") and "@" not in uses
    target = ROOT / uses[2:]
    assert target.parent == ROOT / ".github" / "workflows"
    assert target.is_file()
    assert "workflow_call" in _workflow(target.name)[True]
    assert d.called_workflow(target.read_text()) is None


def _stored_run_bundle(tmp_path, monkeypatch, definition):
    """The bundle this commit exports from a stored run 43402628666 evidence copy."""
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.18.5")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    spec = importlib.util.spec_from_file_location(
        f"verify_hallway_stored_{definition}", ROOT / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    evidence = tmp_path / "evidence"
    shutil.copytree(FIXTURES / definition / "evidence", evidence)
    tool.export(evidence / "manifest.json", tmp_path / "verified", bundle_version="1.1.0")
    bundle = json.loads((tmp_path / "verified" / "bundle" / "manifest.json").read_text())
    return bundle


@pytest.mark.parametrize(
    "change, grade",
    [
        ({}, "FAIL"),
        ({"commit": "f" * 40}, "FAIL"),
        ({"revision": "9.9.9"}, "FAIL"),
        ({"repository": "https://github.com/example/specs"}, "INCOMPATIBLE"),
        ({"tag": "v0.0.0"}, "INCOMPATIBLE"),
        ({"tag": "v9.9.9"}, "INCOMPATIBLE"),
    ],
)
def test_stored_compared_repeats_stay_one_run_for_any_contract(
    tmp_path, monkeypatch, change, grade
):
    """Stored G1 stock repeats are one run. The tag decides the grade, not the commit."""
    bundle = _stored_run_bundle(tmp_path, monkeypatch, "unitree-g1-stock")
    results = bundle["results"]
    for row in results:
        row["spec"].update(change)
    [run] = evaluate.assess_bundle(results, {row["id"]: True for row in results})["runs"]
    assert run["grade"] == grade
    assert run["rationale"] == "3 of 3 repeats agree"
