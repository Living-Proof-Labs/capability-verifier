# SPDX-License-Identifier: Apache-2.0
"""Resolved pin inputs on the scenario plan step.

The step is the workflow's own shell. A jq stub stands in for the verifier
runtime, which does not install jq. Empty inputs keep the lock. A set input
replaces that field after a strict check, and a different lock value fails.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_walk_hallway_workflow import _JQ_STUB, _step, _step_head

ROOT = Path(__file__).parents[1]
VERIFIER_SHA = "a" * 40
OTHER_SHA = "b" * 40
EVAL_SHA = "e" * 40
OTHER_EVAL = "f" * 40
VERIFIER_DIGEST = "ab" * 32
REPORTER_DIGEST = "cd" * 32
OTHER_DIGEST = "ef" * 32
SHA_REQUIREMENT = "a 40-character lowercase hexadecimal SHA"
VERIFIER_IMAGE_REQUIREMENT = (
    "ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex>"
)
REPORTER_IMAGE_REQUIREMENT = (
    "ghcr.io/living-proof-labs/livingproof-capability-report image, tag <semver>, digest sha256:<64 hex>"
)
PIN_ENV = (
    "RESOLVED_VERIFIER_COMMIT",
    "RESOLVED_VERIFIER_IMAGE",
    "RESOLVED_REPORTER_IMAGE",
    "RESOLVED_EVALUATOR_COMMIT",
)


def _ref(name, version, digest):
    return f"ghcr.io/living-proof-labs/{name}:{version}@sha256:{digest}"


VERIFIER_IMAGE = _ref("capability-verifier-hallway", "2.11.4", VERIFIER_DIGEST)
REPORTER_IMAGE = _ref("livingproof-capability-report", "3.15.4", REPORTER_DIGEST)
OTHER_VERIFIER_IMAGE = _ref("capability-verifier-hallway", "2.11.5", VERIFIER_DIGEST)
OTHER_REPORTER_IMAGE = _ref("livingproof-capability-report", "3.15.5", REPORTER_DIGEST)


def _document(
    *,
    verifier_commit=VERIFIER_SHA,
    verifier_image="ghcr.io/living-proof-labs/capability-verifier-hallway",
    verifier_digest="sha256:" + VERIFIER_DIGEST,
    verifier_tag="v2.11.4",
    reporter_image="ghcr.io/living-proof-labs/livingproof-capability-report",
    reporter_digest="sha256:" + REPORTER_DIGEST,
    reporter_tag="v3.15.4",
    evaluator=EVAL_SHA,
    include_reporter=True,
    include_verifier_image=True,
):
    verifier = {
        "repository": "Living-Proof-Labs/capability-verifier",
        "tag": verifier_tag,
    }
    if verifier_commit is not None:
        verifier["commit"] = verifier_commit
    if include_verifier_image:
        verifier["image"] = verifier_image
        verifier["digest"] = verifier_digest
    releases = {"verifier": verifier}
    if include_reporter:
        releases["reporter"] = {
            "image": reporter_image,
            "tag": reporter_tag,
            "digest": reporter_digest,
        }
    body = {
        "configurations": [{"name": "stock"}],
        "releases": releases,
        "scenario": "walk-hallway@0.1.0",
        "world": {"id": "hallway-open"},
    }
    if evaluator is not None:
        body["evaluators"] = {
            "livingproof-evaluator-scenario": {
                "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
                "tag": "v1.5.16",
                "commit": evaluator,
            }
        }
    return body


def _write(robot, body, caller=None):
    robot.mkdir(parents=True, exist_ok=True)
    (robot / "verification.lock.json").write_text(json.dumps(body))
    if caller is not None:
        (robot / "caller-verification.lock.json").write_text(json.dumps(caller))


def _run(robot, source, pins=None, *, jq_text=None, use_stub=True):
    output = robot / "github-output.txt"
    if output.exists():
        output.unlink()
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output)
    env["ONLY"] = ""
    env["EVIDENCE_SOURCE"] = source
    env.pop("PYTHONPATH", None)
    for name in PIN_ENV:
        env.pop(name, None)
    if pins:
        env.update(pins)
    if use_stub:
        bindir = robot / "bin"
        bindir.mkdir(exist_ok=True)
        jq = bindir / "jq"
        jq.write_text(_JQ_STUB if jq_text is None else jq_text)
        jq.chmod(0o755)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    done = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", _step("Refs from verification.lock.json")],
        check=False,
        cwd=robot,
        env=env,
        capture_output=True,
        text=True,
    )
    text = output.read_text() if output.is_file() else ""
    parsed = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    return done, parsed


def _sources(outputs):
    return {
        "verifier-commit": outputs["verifier-commit-source"],
        "verifier-image": outputs["verifier-image-source"],
        "reporter-image": outputs["reporter-image-source"],
        "evaluator-commit": outputs["evaluator-commit-source"],
    }


def test_resolved_pin_inputs_are_optional_and_forwarded():
    import yaml

    scenario = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
    wrapper = yaml.safe_load((ROOT / ".github/workflows/walk-hallway.yml").read_text())
    names = ("verifier-commit", "verifier-image", "reporter-image", "evaluator-commit")
    call = scenario[True]["workflow_call"]
    wrapped = wrapper[True]["workflow_call"]
    for name in names:
        spec = call["inputs"][name]
        assert spec["type"] == "string"
        assert spec["default"] == ""
        assert "required" not in spec
        assert wrapped["inputs"][name] == spec
        forwarded = wrapper["jobs"]["scenario"]["with"][name]
        assert forwarded == f"${{{{ inputs.{name} }}}}"
    sources = (
        "verifier-commit-source",
        "verifier-image-source",
        "reporter-image-source",
        "evaluator-commit-source",
    )
    plan = scenario["jobs"]["plan"]["outputs"]
    for name in sources:
        assert plan[name] == f"${{{{ steps.lock.outputs.{name} }}}}"
    assert set(sources).isdisjoint(call["outputs"])
    assert set(sources).isdisjoint(wrapped["outputs"])


def test_resolved_pins_reach_the_step_through_env():
    head = _step_head("Refs from verification.lock.json")
    script = _step("Refs from verification.lock.json")
    expected = {
        "RESOLVED_VERIFIER_COMMIT": "verifier-commit",
        "RESOLVED_VERIFIER_IMAGE": "verifier-image",
        "RESOLVED_REPORTER_IMAGE": "reporter-image",
        "RESOLVED_EVALUATOR_COMMIT": "evaluator-commit",
    }
    assert "${{" not in script
    for env_name, input_name in expected.items():
        assert f"inputs.{input_name}" not in script
        assert f"{env_name}: ${{{{ inputs.{input_name} }}}}" in head
        assert f"${{{env_name}:-}}" in script


@pytest.mark.parametrize(
    "pins",
    [
        None,
        {
            "RESOLVED_VERIFIER_COMMIT": "",
            "RESOLVED_VERIFIER_IMAGE": "",
            "RESOLVED_REPORTER_IMAGE": "",
            "RESOLVED_EVALUATOR_COMMIT": "",
        },
    ],
    ids=["omitted", "empty"],
)
def test_empty_inputs_keep_the_lock_and_record_lock(tmp_path, pins):
    robot = tmp_path / "robot"
    _write(robot, _document())
    done, outputs = _run(robot, "robot", pins)
    assert done.returncode == 0, done.stderr
    assert outputs["verifier-commit"] == VERIFIER_SHA
    assert outputs["verifier-image"] == VERIFIER_IMAGE
    assert outputs["reporter-image"] == REPORTER_IMAGE
    assert outputs["evaluator-commit"] == EVAL_SHA
    assert _sources(outputs) == {
        "verifier-commit": "lock",
        "verifier-image": "lock",
        "reporter-image": "lock",
        "evaluator-commit": "lock",
    }
    assert "pin source verifier-commit=lock" in done.stdout
    assert "pin source evaluator-commit=lock" in done.stdout


def test_empty_inputs_keep_a_lock_image_outside_the_resolved_form(tmp_path):
    """Today's locks are not re-checked when the caller passes no resolved ref."""
    robot = tmp_path / "robot"
    _write(
        robot,
        _document(
            verifier_image="ghcr.io/example/hallway",
            verifier_digest="sha256:" + "1" * 64,
            verifier_tag="v1.16.1",
            reporter_image="ghcr.io/example/reporter",
            reporter_digest="sha256:" + "2" * 64,
            reporter_tag="v3.3.1",
        ),
    )
    done, outputs = _run(robot, "robot")
    assert done.returncode == 0, done.stderr
    assert outputs["verifier-image"] == "ghcr.io/example/hallway:1.16.1@sha256:" + "1" * 64
    assert outputs["reporter-image"] == "ghcr.io/example/reporter:3.3.1@sha256:" + "2" * 64
    assert outputs["verifier-image-source"] == "lock"
    assert outputs["reporter-image-source"] == "lock"


def test_matching_inputs_are_used_and_record_input(tmp_path):
    robot = tmp_path / "robot"
    _write(robot, _document())
    done, outputs = _run(
        robot,
        "robot",
        {
            "RESOLVED_VERIFIER_COMMIT": VERIFIER_SHA,
            "RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE,
            "RESOLVED_REPORTER_IMAGE": REPORTER_IMAGE,
            "RESOLVED_EVALUATOR_COMMIT": EVAL_SHA,
        },
    )
    assert done.returncode == 0, done.stderr
    assert outputs["verifier-commit"] == VERIFIER_SHA
    assert outputs["verifier-image"] == VERIFIER_IMAGE
    assert outputs["reporter-image"] == REPORTER_IMAGE
    assert outputs["evaluator-commit"] == EVAL_SHA
    assert set(_sources(outputs).values()) == {"input"}


def test_inputs_fill_absent_lock_fields(tmp_path):
    robot = tmp_path / "robot"
    _write(
        robot,
        _document(
            verifier_commit=None,
            include_verifier_image=False,
            include_reporter=False,
            evaluator=None,
        ),
    )
    done, outputs = _run(
        robot,
        "robot",
        {
            "RESOLVED_VERIFIER_COMMIT": OTHER_SHA,
            "RESOLVED_VERIFIER_IMAGE": VERIFIER_IMAGE,
            "RESOLVED_REPORTER_IMAGE": REPORTER_IMAGE,
            "RESOLVED_EVALUATOR_COMMIT": OTHER_EVAL,
        },
    )
    assert done.returncode == 0, done.stderr
    assert outputs["verifier-commit"] == OTHER_SHA
    assert outputs["verifier-image"] == VERIFIER_IMAGE
    assert outputs["reporter-image"] == REPORTER_IMAGE
    assert outputs["evaluator-commit"] == OTHER_EVAL
    assert set(_sources(outputs).values()) == {"input"}


def test_one_input_leaves_the_other_fields_on_the_lock(tmp_path):
    robot = tmp_path / "robot"
    _write(robot, _document(verifier_commit=None))
    done, outputs = _run(robot, "robot", {"RESOLVED_VERIFIER_COMMIT": OTHER_SHA})
    assert done.returncode == 0, done.stderr
    assert outputs["verifier-commit"] == OTHER_SHA
    assert outputs["verifier-commit-source"] == "input"
    assert outputs["verifier-image"] == VERIFIER_IMAGE
    assert outputs["reporter-image"] == REPORTER_IMAGE
    assert outputs["evaluator-commit"] == EVAL_SHA
    assert outputs["verifier-image-source"] == "lock"
    assert outputs["reporter-image-source"] == "lock"
    assert outputs["evaluator-commit-source"] == "lock"


@pytest.mark.parametrize(
    ("env_name", "given", "label", "field", "locked"),
    [
        (
            "RESOLVED_VERIFIER_COMMIT",
            OTHER_SHA,
            "verifier-commit",
            "releases.verifier.commit",
            VERIFIER_SHA,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            OTHER_VERIFIER_IMAGE,
            "verifier-image",
            "releases.verifier image ref",
            VERIFIER_IMAGE,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            OTHER_REPORTER_IMAGE,
            "reporter-image",
            "releases.reporter image ref",
            REPORTER_IMAGE,
        ),
        (
            "RESOLVED_EVALUATOR_COMMIT",
            OTHER_EVAL,
            "evaluator-commit",
            "evaluators.livingproof-evaluator-scenario.commit",
            EVAL_SHA,
        ),
    ],
)
def test_a_mismatch_fails_and_names_both_values(tmp_path, env_name, given, label, field, locked):
    robot = tmp_path / "robot"
    _write(robot, _document())
    done, outputs = _run(robot, "robot", {env_name: given})
    assert done.returncode != 0
    assert outputs == {}
    assert "pin source" not in done.stdout
    assert f"{label} {given} does not match {field}" in done.stderr
    assert locked in done.stderr
    assert "the verification lock is stale" in done.stderr
    assert "must be" not in done.stderr


@pytest.mark.parametrize(
    "given",
    [
        "A" * 40,
        "a" * 39,
        "a" * 41,
        "g" * 40,
        "not-a-sha",
        " " + "a" * 40,
        "a" * 40 + "\n",
    ],
)
def test_an_invalid_commit_is_rejected(tmp_path, given):
    robot = tmp_path / "robot"
    _write(robot, _document(verifier_commit=None))
    done, outputs = _run(robot, "robot", {"RESOLVED_VERIFIER_COMMIT": given})
    assert done.returncode != 0
    assert outputs == {}
    assert f"verifier-commit must be {SHA_REQUIREMENT}" in done.stderr
    assert "the verification lock is stale" not in done.stderr
    assert "pin source" not in done.stdout


@pytest.mark.parametrize(
    ("env_name", "given", "requirement"),
    [
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace("ghcr.io", "docker.io"),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace("capability-verifier-hallway", "attacker"),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace(":2.11.4@", ":v2.11.4@"),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace(":2.11.4@", ":02.11.4@"),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace(":2.11.4@", ":2.11.4-rc.1@"),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE.replace(VERIFIER_DIGEST, VERIFIER_DIGEST.upper()),
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE[:-1],
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            VERIFIER_IMAGE + " ",
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_VERIFIER_IMAGE",
            REPORTER_IMAGE,
            VERIFIER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            VERIFIER_IMAGE,
            REPORTER_IMAGE_REQUIREMENT,
        ),
        (
            "RESOLVED_REPORTER_IMAGE",
            REPORTER_IMAGE.replace("@sha256:", "@"),
            REPORTER_IMAGE_REQUIREMENT,
        ),
    ],
)
def test_an_invalid_image_ref_is_rejected(tmp_path, env_name, given, requirement):
    label = "verifier-image" if env_name.endswith("VERIFIER_IMAGE") else "reporter-image"
    robot = tmp_path / "robot"
    _write(robot, _document(include_verifier_image=False, include_reporter=False))
    done, outputs = _run(robot, "robot", {env_name: given})
    assert done.returncode != 0, given
    assert outputs == {}
    assert f"{label} must be {requirement}" in done.stderr
    assert "the verification lock is stale" not in done.stderr


def test_the_reporter_digest_must_be_sixty_four_lowercase_hex(tmp_path):
    """A same-registry ref with a short digest is rejected before the lock is compared."""
    short = REPORTER_IMAGE[:-1]
    robot = tmp_path / "robot"
    _write(robot, _document())
    done, outputs = _run(robot, "robot", {"RESOLVED_REPORTER_IMAGE": short})
    assert done.returncode != 0
    assert outputs == {}
    assert f"reporter-image must be {REPORTER_IMAGE_REQUIREMENT}" in done.stderr


def test_evaluator_input_is_compared_to_the_selected_pin(tmp_path):
    """The caller copy is the pin when EVIDENCE_SOURCE is caller."""
    robot_body = _document(evaluator=OTHER_EVAL)
    caller_body = _document(evaluator=EVAL_SHA)
    matched = tmp_path / "matched"
    _write(matched, robot_body, caller=caller_body)
    done, outputs = _run(matched, "caller", {"RESOLVED_EVALUATOR_COMMIT": EVAL_SHA})
    assert done.returncode == 0, done.stderr
    assert outputs["evaluator-commit"] == EVAL_SHA
    assert outputs["evaluator-commit-source"] == "input"
    assert outputs["verifier-commit-source"] == "lock"

    rejected = tmp_path / "rejected"
    _write(rejected, robot_body, caller=caller_body)
    done, outputs = _run(rejected, "caller", {"RESOLVED_EVALUATOR_COMMIT": OTHER_EVAL})
    assert done.returncode != 0
    assert outputs == {}
    assert OTHER_EVAL in done.stderr
    assert EVAL_SHA in done.stderr
    assert "evaluators.livingproof-evaluator-scenario.commit" in done.stderr
    assert "the verification lock is stale" in done.stderr


def test_a_resolved_pin_does_not_run_before_the_caller_lock_refusal(tmp_path):
    robot = tmp_path / "robot"
    _write(robot, _document(), caller=_document())
    canary = "#!/bin/sh\necho jq-was-invoked >&2\nexit 99\n"
    done, outputs = _run(
        robot,
        "robot",
        {"RESOLVED_VERIFIER_COMMIT": VERIFIER_SHA},
        jq_text=canary,
    )
    assert done.returncode != 0
    assert outputs == {}
    assert "committed in the robot repository" in done.stderr
    assert "jq-was-invoked" not in done.stderr
    assert "scenario:" not in done.stdout
    assert "the verification lock is stale" not in done.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not on PATH")
def test_the_jq_stub_matches_jq_for_the_pin_fields(tmp_path):
    """The stub covers the verifier runtime. Where jq is installed, it must agree."""
    cases = {
        "present": _document(),
        "absent": _document(
            verifier_commit=None,
            include_verifier_image=False,
            include_reporter=False,
            evaluator=None,
        ),
        "other-registry": _document(
            verifier_image="ghcr.io/example/hallway",
            verifier_digest="sha256:" + "1" * 64,
            verifier_tag="v1.16.1",
            reporter_image="ghcr.io/example/reporter",
            reporter_digest="sha256:" + "2" * 64,
            reporter_tag="v3.3.1",
        ),
    }
    fields = ("verifier-commit", "verifier-image", "reporter-image", "evaluator-commit")
    for name, body in cases.items():
        stub_robot = tmp_path / f"{name}-stub"
        real_robot = tmp_path / f"{name}-real"
        _write(stub_robot, body)
        _write(real_robot, body)
        stub, stub_out = _run(stub_robot, "robot")
        real, real_out = _run(real_robot, "robot", use_stub=False)
        assert stub.returncode == 0, stub.stderr
        assert real.returncode == 0, real.stderr
        assert {field: stub_out[field] for field in fields} == {
            field: real_out[field] for field in fields
        }
