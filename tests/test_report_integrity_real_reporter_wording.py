# SPDX-License-Identifier: Apache-2.0
"""The integrity line sits with the verdict in report.md and report.txt as the live reporter words it (#219).

A real bundle has trial counts, and the reporter (3.6.0 through 3.15.0, checked on the corner-left
bundle of livingproof-verification run 44948999708) then writes
``... passes walk-corner-left: 1 success, 100%; overall PASS. 100% required.``: a semicolon before
"overall" and a sentence after the grade. Only a bundle without counts has ``...: overall PASS.``.
Both forms must put the line right after the verdict, not at the end of the file after the footer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capability_verifier.collect import place_integrity_lines

LINE = "Run Integrity: REFUSED: the evaluator refused the run (exit 2) and wrote no result"
VERDICTS = {
    "real-passed": "unitree-g1 · stock passes walk-corner-left: 1 success, 100%; overall PASS. 100% required.",
    "real-failed": "unitree-g1 · stock fails walk-corner-left: 1 success, 2 failures, 33%; overall FAIL. 100% required.",
    "no-counts": "unitree-g1 · stock passes walk-hallway: overall PASS.",
}


def _md(verdict: str) -> str:
    return (
        "<!-- livingproof-verification -->\n## walk-corner-left@0.2.0 · N=1\n\n"
        f"{verdict}\n\n**Expected:** The robot walks.\n\n| Robot | Grade |\n|---|---|\n"
        "| unitree-g1 · stock | **PASS** |\n\n<sub>Policy all-required-v1@1.0.0</sub>\n"
    )


def _txt(verdict: str) -> str:
    return (
        "Living Proof Labs capability report\n\nSeparate contexts (no overall grade):\n"
        f"Run unitree-g1/stock: {verdict.split(': ', 1)[1]}\nPolicy: all-required-v1@1.0.0\n"
    )


@pytest.mark.parametrize("wording", VERDICTS)
def test_the_line_follows_the_verdict_in_md_and_txt(wording, tmp_path):
    verdict = VERDICTS[wording]
    (tmp_path / "report.md").write_text(_md(verdict), encoding="utf-8")
    (tmp_path / "report.txt").write_text(_txt(verdict), encoding="utf-8")
    (tmp_path / "integrity-lines.txt").write_text(LINE + "\n", encoding="utf-8")
    assert place_integrity_lines(Path(tmp_path))
    md = (tmp_path / "report.md").read_text(encoding="utf-8").splitlines()
    here = next(i for i, line in enumerate(md) if " overall " in line and "grade" not in line)
    assert md[here + 2] == LINE, "report.md: the integrity line is not right after the verdict"
    txt = (tmp_path / "report.txt").read_text(encoding="utf-8").splitlines()
    here = next(i for i, line in enumerate(txt) if line.startswith("Run unitree-g1/stock"))
    assert txt[here + 1] == LINE, "report.txt: the integrity line is not right after the verdict"
    assert md.count(LINE) == 1 and txt.count(LINE) == 1
