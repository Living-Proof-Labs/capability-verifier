# SPDX-License-Identifier: Apache-2.0
"""scenario-media builds the evaluator at its release, as scenario-evaluator does.

actions/checkout fetches the locked commit shallow and tagless, which hatch-vcs builds as 0.1.dev1.
scenario-evaluator fetches the locked tag first (evaluator_pin --checkout) and refuses a build that
does not report the release. scenario-media installed the same pin with no such step, so its
results said evaluator version 0.1.dev1 (livingproof-evaluator-scenario#35, Low 1).
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
DOC = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
PIN_COMMAND = "python3 -m capability_verifier.evaluator_pin"


def _step(job: str, name: str) -> dict:
    return next(s for s in DOC["jobs"][job]["steps"] if s.get("name", "").startswith(name))


def _pin_lines(script: str) -> list[str]:
    start = script.index(PIN_COMMAND)
    end = script.index("--checkout evaluator)", start) + len("--checkout evaluator)")
    return [line.strip() for line in script[start:end].splitlines()]


def test_the_media_step_has_the_locked_tag_in_its_environment():
    env = _step("scenario-media", "Scenario evaluator on the recordings")["env"]
    assert env["EVALUATOR_TAG"] == "${{ needs.plan.outputs.evaluator-tag }}"
    assert env["EVALUATOR_REPOSITORY"] == "${{ needs.plan.outputs.evaluator-repository }}"
    assert env["EVALUATOR_COMMIT"] == "${{ needs.plan.outputs.evaluator-commit }}"


def test_the_media_step_fetches_the_locked_tag_before_uv_sync():
    script = _step("scenario-media", "Scenario evaluator on the recordings")["run"]
    assert PIN_COMMAND in script and "--checkout evaluator" in script
    assert script.index(PIN_COMMAND) < script.index("uv sync --locked --directory evaluator")


def test_the_media_step_runs_the_same_pin_command_as_scenario_evaluator():
    media = _step("scenario-media", "Scenario evaluator on the recordings")["run"]
    other = _step("scenario-evaluator", "Scenario evaluator, one result file per trial")["run"]
    assert _pin_lines(media) == _pin_lines(other)


def test_the_media_job_has_the_workflow_source_the_pin_command_imports_from():
    names = [s.get("name") for s in DOC["jobs"]["scenario-media"]["steps"]]
    assert "Workflow source for the evaluator pin" in names
    assert names.index("Workflow source for the evaluator pin") < names.index(
        "Scenario evaluator on the recordings"
    )


def test_the_media_step_refuses_a_build_that_does_not_report_the_release():
    script = _step("scenario-media", "Scenario evaluator on the recordings")["run"]
    assert 'm.version("livingproof-evaluator-scenario")' in script
    assert '[ "$reported" != "$release" ]' in script
    assert script.index('[ "$reported" != "$release" ]') < script.index("run_scenario_media.py")
