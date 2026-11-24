# SPDX-License-Identifier: Apache-2.0
"""The run-integrity result is shown next to the verdict in every rendered format (#219).

Runs the report job's own steps (collect, then render) with reporter 3.6.0 on a real bundle
1.1.0 export. For each state (completed, failed, refused by the evaluator or the job, not run) the
line must sit with the verdict in report.md, report.txt and report.html, so no format shows a
PASS without the integrity state. Before this, report.html showed no integrity line at all, and
report.md and report.txt put it after the footer.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest
from test_collect_integrity import _integrity
from test_report_integrity_not_run_render import _job
from test_walk_hallway_workflow import _isolated_collector, _reporter_3_6_0, _step

REPORTS = ("report.md", "report.txt", "report.html")
REFUSAL = "livingproof-evaluator-run: media is refused: media.json has no trials; no result written"

# (integrity document, no-result note, refusal note, job result, the line the reports must show)
STATES = {
    "pass": (
        _integrity("livingproof-run-integrity", ok=True),
        None,
        None,
        "success",
        "Run Integrity: PASS",
    ),
    "failed": (
        _integrity("livingproof-run-integrity", ok=False),
        None,
        None,
        "success",
        "Run Integrity: FAILED",
    ),
    "refused": (None, None, REFUSAL, "failure", f"Run Integrity: REFUSED: {REFUSAL}"),
    "not-run": (None, None, None, "cancelled", "Run Integrity: NOT RUN (cancelled)"),
    "not-run-job-failed": (None, None, None, "failure", "Run Integrity: NOT RUN (failure)"),
}


def _reports(tmp_path, monkeypatch, state):
    integrity, note, refusal, result, _ = STATES[state]
    job = _job(tmp_path, monkeypatch, integrity=integrity, note=note, refusal=refusal)
    env = dict(os.environ)
    env["PATH"] = f"{_reporter_3_6_0(tmp_path)}{os.pathsep}{env['PATH']}"
    env["RUN_INTEGRITY_RESULT"] = result
    env["EVALUATOR_RESULTS_PATTERN"] = "evaluator-results-*-playground"
    for name in ("Append each evaluator's result documents", "Validate, then render"):
        subprocess.run(
            ["bash", "-e", "-c", _isolated_collector(_step(name), job)],
            cwd=job,
            env=env,
            check=True,
        )
    texts = [(job / "reports" / n).read_text(encoding="utf-8") for n in REPORTS]
    return (*texts, job)


def _verdict_index(lines):
    return next(i for i, line in enumerate(lines) if ": overall " in line)


@pytest.mark.parametrize("state", STATES)
def test_report_md_puts_the_integrity_line_with_the_verdict(state, tmp_path, monkeypatch):
    md, _, _, _ = _reports(tmp_path, monkeypatch, state)
    expected = STATES[state][4]
    lines = md.splitlines()
    verdict = _verdict_index(lines)
    where = next(i for i, line in enumerate(lines) if line.startswith(expected))
    assert verdict < where <= verdict + 3, "the integrity line is not next to the verdict"
    assert where < next(i for i, line in enumerate(lines) if line.startswith("|"))
    assert sum(line.startswith("Run Integrity") for line in lines) == 1


@pytest.mark.parametrize("state", STATES)
def test_report_txt_puts_the_integrity_line_right_after_the_verdict(state, tmp_path, monkeypatch):
    _, txt, _, _ = _reports(tmp_path, monkeypatch, state)
    lines = txt.splitlines()
    verdict = _verdict_index(lines)
    assert lines[verdict + 1].startswith(STATES[state][4])
    assert sum(line.startswith("Run Integrity") for line in lines) == 1


@pytest.mark.parametrize("state", STATES)
def test_report_html_shows_the_integrity_line_beside_the_stored_status(
    state, tmp_path, monkeypatch
):
    _, _, html, _ = _reports(tmp_path, monkeypatch, state)
    status = re.search(r"<p class='stored-status'>Stored overall status: [A-Z_ ]+</p>", html)
    assert status, "the reporter's verdict paragraph moved"
    after = html[status.end() :]
    match = re.match(r"<p class='stored-status integrity-status'>([^<]*)</p>", after)
    assert match, "no integrity line directly after the verdict in report.html"
    assert match.group(1).startswith(STATES[state][4].replace("&", "&amp;"))
    # Every stored-status paragraph (the summary card and the run card) carries the line.
    assert html.count("integrity-status'>") == html.count("<p class='stored-status'>")


def test_the_sums_cover_the_edited_reports(tmp_path, monkeypatch):
    _, _, _, job = _reports(tmp_path, monkeypatch, "refused")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=job / "reports", check=True)


def test_without_the_job_result_or_a_document_the_reports_have_no_integrity_line(
    tmp_path, monkeypatch
):
    job = _job(tmp_path, monkeypatch, integrity=None, note=None)
    env = dict(os.environ)
    env["PATH"] = f"{_reporter_3_6_0(tmp_path)}{os.pathsep}{env['PATH']}"
    env["EVALUATOR_RESULTS_PATTERN"] = "evaluator-results-*-playground"
    env.pop("RUN_INTEGRITY_RESULT", None)
    for name in ("Append each evaluator's result documents", "Validate, then render"):
        subprocess.run(
            ["bash", "-e", "-c", _isolated_collector(_step(name), job)],
            cwd=job,
            env=env,
            check=True,
        )
    reports = job / "reports"
    assert "Run Integrity" not in (reports / "report.md").read_text(encoding="utf-8")
    assert "Run Integrity" not in (reports / "report.txt").read_text(encoding="utf-8")
    assert "integrity-status" not in (reports / "report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("state", ["not-run", "not-run-job-failed"])
def test_a_check_that_never_ran_is_not_called_refused_in_any_format(state, tmp_path, monkeypatch):
    for text in _reports(tmp_path, monkeypatch, state)[:3]:
        assert "REFUSED" not in text and "NOT RUN" in text


def test_a_refusal_is_not_called_not_run_in_any_format(tmp_path, monkeypatch):
    for text in _reports(tmp_path, monkeypatch, "refused")[:3]:
        assert "REFUSED" in text and "NOT RUN" not in text
