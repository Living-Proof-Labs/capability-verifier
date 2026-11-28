# SPDX-License-Identifier: Apache-2.0
"""How the wrapper media step decides there is no world document (capability-verifier#277).

With no lock pin the step asks ``uses_world`` and writes NO-MEDIA-RESULTS.txt only on a
clean "no". Any other outcome (an unreadable manifest, an interpreter that cannot import the
package) is not a "no" and must not be reported as one: skipping a world-graded scenario
silently is the failure this change exists to remove.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from test_scenario_loader import _run_step, _script

WORLD = '{"plan": {"scenario": "walk-corner@0.1.0"}}\n'
WORLD_FROM_LOCK = '{"lock": {"content": {"scenario": "walk-corner@0.1.0"}}}\n'
WALK_HALLWAY = '{"plan": {"scenario": "walk-hallway@0.1.0"}}\n'
HALLWAY_EVIDENCE = '{"format": "livingproof-hallway-evidence/2.0.0"}\n'
FAIL_OPEN = (
    "capability-verifier#278 head d163da7: the step runs `if python3 -c '...uses_world...'; then "
    "fail; fi`, so an exception (exit 1) is read as 'no world document' and the job exits 0 with "
    "a note. Remove this marker when the step tells 'not world' from 'could not tell'."
)


def _media(tmp_path: Path, manifest: str, name: str = "media"):
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    return _run_step(script, "wrapper", tmp_path / name, evaluator_commit="", manifest=manifest)


def _notes(tmp_path: Path, name: str = "media") -> list[Path]:
    return list((tmp_path / name / "evaluator-results").rglob("NO-MEDIA-RESULTS.txt"))


def test_a_world_document_named_only_in_the_lock_still_fails_without_a_pin(tmp_path):
    done, _ = _media(tmp_path, WORLD_FROM_LOCK)
    assert done.returncode != 0
    assert "evaluator-commit" in (done.stderr or "")
    assert not _notes(tmp_path)


@pytest.mark.parametrize("manifest", [WALK_HALLWAY, HALLWAY_EVIDENCE], ids=["plan", "format"])
def test_walk_hallway_evidence_without_a_pin_writes_the_note_and_exits_0(tmp_path, manifest):
    done, _ = _media(tmp_path, manifest)
    assert done.returncode == 0, done.stderr
    assert "no world document" in _notes(tmp_path)[0].read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, reason=FAIL_OPEN)
@pytest.mark.parametrize(
    "manifest", ["", '{"plan": ', "[]"], ids=["empty", "truncated-json", "not-an-object"]
)
def test_an_unreadable_evidence_manifest_is_not_reported_as_no_world_document(tmp_path, manifest):
    done, _ = _media(tmp_path, manifest)
    assert done.returncode != 0, (
        "wrapper media reported 'no world document' for an unreadable manifest"
    )
    assert not _notes(tmp_path)


@pytest.mark.xfail(strict=True, reason=FAIL_OPEN)
def test_a_world_graded_run_without_a_pin_still_fails_when_the_interpreter_cannot_import_it(
    tmp_path, monkeypatch
):
    """python3 outside the image's /opt/lpl (a runner, the runtime image) lacks the third-party package."""
    real = sys.executable
    stub = tmp_path / "python-without-scenario-parameters"
    stub.write_text(
        "#!/bin/sh\n"
        f'[ "$1" = -c ] || exec {real} "$@"\n'
        "code=$2\nshift 2\n"
        f'exec {real} -c "import sys\n'
        "sys.modules['livingproof_scenario_parameters'] = None\n"
        '$code" "$@"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(sys, "executable", str(stub))
    done, _ = _media(tmp_path, WORLD)
    assert done.returncode != 0, (
        "a world-graded scenario was skipped with a 'no world document' note"
    )
    assert not _notes(tmp_path)
