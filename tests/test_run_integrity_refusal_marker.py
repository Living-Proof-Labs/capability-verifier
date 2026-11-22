# SPDX-License-Identifier: Apache-2.0
"""The run-integrity step leaves a ``refused.txt`` when the evaluator refuses (exit 2) (#219).

The report labels a refusal REFUSED and any other missing result NOT RUN. Only the step knows the
evaluator's exit status, so it keeps the evaluator's own message in the artifact. The evaluator is a
stub ``uv`` here that exits with the status under test.
"""

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest
from test_walk_hallway_workflow import _step

STEP = "Run Integrity, one result for the run"
MESSAGE = "livingproof-evaluator-run: media is refused: media.json has no trials; no result written"


def _run(tmp_path, status):
    work = tmp_path / "work"
    (work / "evidence").mkdir(parents=True)
    (work / "evidence" / "manifest.json").write_text("{}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            case " $* " in
              *" sync "*) exit 0 ;;
              *"--help"*) exit 0 ;;
            esac
            echo "{MESSAGE}" >&2
            exit {status}
            """
        )
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["GITHUB_WORKSPACE"] = str(work)
    done = subprocess.run(
        ["bash", "-e", "-c", _step(STEP)],
        cwd=work,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return done, work / "evaluator-results" / "livingproof-run-integrity"


def test_an_exit_2_leaves_the_evaluators_message_in_refused_txt_and_still_fails_the_job(tmp_path):
    done, results = _run(tmp_path, 2)
    assert done.returncode == 2
    assert (results / "refused.txt").read_text().splitlines()[0] == MESSAGE
    assert MESSAGE in done.stderr, "the message is still in the job log"
    assert not (results / "result.json").exists()


@pytest.mark.parametrize("status", [0, 1, 3])
def test_any_other_exit_leaves_no_refusal(status, tmp_path):
    done, results = _run(tmp_path, status)
    assert done.returncode == status
    assert not (results / "refused.txt").exists()
