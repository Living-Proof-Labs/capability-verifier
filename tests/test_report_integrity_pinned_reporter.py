# SPDX-License-Identifier: Apache-2.0
"""The integrity line follows the verdict in the reporter the lock pins, on a real bundle (#219).

Runs the report job's collect and render steps with reporter 3.14.0 (the version livingproof-
verification's lock pins) on the real corner-left bundle of run 44948999708, which has trial counts.
There the verdict reads ``...: 1 success, 100%; overall PASS. 100% required.``, not the
``...: overall PASS.`` that a bundle without counts gets, so a placement rule written for the second
form leaves the line at the end of the file after the footer. 3.6.0 and later all word it this way.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest
from test_walk_hallway_workflow import _isolated_collector, _step

REAL = Path(__file__).parent / "fixtures" / "real-report" / "lpv-run-44948999708-corner-left"
RELEASE = (
    "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/download/v3.14.0"
)
WHEELS = {
    "livingproof_common-1.0.0-py3-none-any.whl": (
        "8b7795748b54eeb89610f3716245f45db48ac0474465a5abbf974e7075c1476c"
    ),
    "livingproof_capability_report-3.14.0-py3-none-any.whl": (
        "cab26f8baef6217353c3fd1c6f81d52be64f24dec074c33880592a3284fd7887"
    ),
}
REFUSAL = "livingproof-evaluator-run: media is refused: media.json has no trials; no result written"


def _reporter(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("reporter-3-14-0")
    venv = root / "venv"
    subprocess.run(["uv", "venv", "-q", str(venv)], check=True)
    wheels = []
    for name, digest in WHEELS.items():
        target = root / name
        with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as response:
            target.write_bytes(response.read())
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, name
        wheels.append(str(target))
    subprocess.run(
        ["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"), *wheels],
        check=True,
    )
    return venv / "bin"


@pytest.fixture(scope="module")
def reporter(tmp_path_factory):
    return _reporter(tmp_path_factory)


def _render(tmp_path, reporter, *, result_json=None, refusal=None, job="success"):
    job_dir = tmp_path / "job"
    shutil.copytree(REAL / "verified", job_dir / "verified")
    shutil.copytree(REAL / "links", job_dir / "links")
    results = job_dir / "evaluator-results"
    for name, source in (("scenario", "scenario"), ("media", "media")):
        shutil.copytree(
            REAL / "evaluator-results" / source,
            results / f"evaluator-results-livingproof-{name}-stock",
        )
    integrity = results / "evaluator-results-livingproof-run-integrity-stock"
    integrity.mkdir(parents=True)
    if result_json is not None:
        document = json.loads(
            (
                REAL / "evaluator-results/run-integrity/livingproof-run-integrity/result.json"
            ).read_text()
        )
        result_json(document)
        (integrity / "result.json").write_text(json.dumps(document))
    if refusal is not None:
        (integrity / "refused.txt").write_text(refusal + "\n")
    env = dict(os.environ)
    env["PATH"] = f"{reporter}{os.pathsep}{env['PATH']}"
    env["RUN_INTEGRITY_RESULT"] = job
    env["EVALUATOR_RESULTS_PATTERN"] = "evaluator-results-*-stock"
    for name in ("Append each evaluator's result documents", "Validate, then render"):
        subprocess.run(
            ["bash", "-e", "-c", _isolated_collector(_step(name), job_dir)],
            cwd=job_dir,
            env=env,
            check=True,
        )
    read = lambda n: (job_dir / "reports" / n).read_text(encoding="utf-8")
    return read("report.md").splitlines(), read("report.txt").splitlines(), job_dir


CASES = {
    "pass": ({"result_json": lambda d: None}, "Run Integrity: PASS"),
    "failed": (
        {
            "result_json": lambda d: d["integrity"]["run"].update(
                test_system_status="ERROR", success=False
            )
        },
        "Run Integrity: FAILED",
    ),
    "refused": ({"refusal": REFUSAL, "job": "failure"}, f"Run Integrity: REFUSED: {REFUSAL}"),
    "not-run": ({"job": "cancelled"}, "Run Integrity: NOT RUN (cancelled)"),
}


@pytest.mark.parametrize("case", CASES)
def test_the_line_is_right_after_the_verdict_in_md_and_txt_on_the_real_bundle(
    case, reporter, tmp_path
):
    kwargs, expected = CASES[case]
    md, txt, job_dir = _render(tmp_path, reporter, **kwargs)
    verdict = next(i for i, line in enumerate(md) if "; overall " in line)
    assert md[verdict + 1] == "" and md[verdict + 2].startswith(expected)
    verdict = next(i for i, line in enumerate(txt) if "; overall " in line)
    assert txt[verdict + 1].startswith(expected)
    assert sum(line.startswith("Run Integrity") for line in md + txt) == 2
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=job_dir / "reports", check=True)
