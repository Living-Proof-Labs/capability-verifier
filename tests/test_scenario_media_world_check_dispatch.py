# SPDX-License-Identifier: Apache-2.0
"""The exit-code dispatch of the no-pin wrapper media step (capability-verifier#277).

The step reads the world check's exit status: 10 means a world document (fail loudly),
11 means none (write the note, exit 0), and anything else means "could not tell" and fails.
These tests drive the real step with a stand-in interpreter, so the status is the only
thing that varies. They also cover a failure inside ``uses_world`` itself and the
``pipefail`` shell, which the manifest-based tests do not reach.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from test_scenario_loader import _run_step, _script

WORLD = '{"plan": {"scenario": "walk-corner@0.1.0"}}\n'
NONE = '{"plan": {"scenario": "walk-hallway@0.1.0"}}\n'
COULD_NOT_TELL = "could not tell whether the evidence has a world document"
NEEDS_PIN = "needs evaluator-commit"


def _executable(path: Path, text: str) -> str:
    path.write_text("#!/bin/sh\n" + text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _run(
    tmp_path, monkeypatch, interpreter: str | None = None, manifest: str = WORLD, shell="bash"
):
    if interpreter is not None:
        monkeypatch.setattr(sys, "executable", interpreter)
    script = _script("scenario-media", "Scenario evaluator on the recordings")
    done, _ = _run_step(
        script, "wrapper", tmp_path / "media", evaluator_commit="", manifest=manifest, shell=shell
    )
    notes = list((tmp_path / "media" / "evaluator-results").rglob("NO-MEDIA-RESULTS.txt"))
    return done, notes


def test_status_10_is_a_world_document_and_fails_loudly(tmp_path, monkeypatch):
    done, notes = _run(tmp_path, monkeypatch, _executable(tmp_path / "py", "exit 10\n"), NONE)
    assert done.returncode != 0
    assert NEEDS_PIN in done.stderr
    assert not notes


def test_status_11_is_no_world_document_and_writes_the_note(tmp_path, monkeypatch):
    done, notes = _run(tmp_path, monkeypatch, _executable(tmp_path / "py", "exit 11\n"), WORLD)
    assert done.returncode == 0, done.stderr
    assert len(notes) == 1


@pytest.mark.parametrize("status", [0, 1, 2, 12, 126, 127, 255])
def test_any_other_status_fails_closed_without_the_note(tmp_path, monkeypatch, status):
    done, notes = _run(tmp_path, monkeypatch, _executable(tmp_path / "py", f"exit {status}\n"))
    assert done.returncode != 0
    assert COULD_NOT_TELL in done.stderr
    assert f"(exit {status})" in done.stderr
    assert not notes


@pytest.mark.parametrize("signal", [9, 11], ids=["sigkill", "sigsegv"])
def test_an_interpreter_killed_by_a_signal_fails_closed(tmp_path, monkeypatch, signal):
    done, notes = _run(tmp_path, monkeypatch, _executable(tmp_path / "py", f"kill -{signal} $$\n"))
    assert done.returncode != 0
    assert COULD_NOT_TELL in done.stderr
    assert not notes


def _failing_uses_world(tmp_path: Path, body: str) -> str:
    """The real interpreter, with ``uses_world`` replaced by a function that runs ``body``."""
    real = sys.executable
    return _executable(
        tmp_path / "py-uses-world",
        f'[ "$1" = -c ] || exec {real} "$@"\n'
        "code=$2\nshift 2\n"
        f'exec {real} -c "import sys, types\n'
        "m = types.ModuleType('capability_verifier.scenario_parameters')\n"
        "def uses_world(manifest):\n"
        f"    {body}\n"
        "m.uses_world = uses_world\n"
        "sys.modules['capability_verifier.scenario_parameters'] = m\n"
        '$code" "$@"\n',
    )


@pytest.mark.parametrize("body", ["raise RuntimeError('boom')", "raise KeyError('plan')"])
def test_an_error_inside_uses_world_is_could_not_tell_not_no_world_document(
    tmp_path, monkeypatch, body
):
    done, notes = _run(tmp_path, monkeypatch, _failing_uses_world(tmp_path, body))
    assert done.returncode != 0
    assert COULD_NOT_TELL in done.stderr
    assert NEEDS_PIN not in done.stderr
    assert not notes


@pytest.mark.parametrize(("manifest", "ok"), [(WORLD, False), (NONE, True)], ids=["world", "none"])
def test_the_dispatch_holds_under_set_o_pipefail(tmp_path, monkeypatch, manifest, ok):
    """The workflow's default shell is `bash -eo pipefail` where `shell: bash` is set."""
    shell = _executable(tmp_path / "bash-pipefail", 'exec bash -o pipefail "$@"\n')
    done, notes = _run(tmp_path, monkeypatch, manifest=manifest, shell=shell)
    assert (done.returncode == 0) is ok, done.stderr
    assert bool(notes) is ok
