# SPDX-License-Identifier: Apache-2.0
"""Edges of the integrity line's message handling that PR 221 left to chance (#219).

Mutation testing 4e219b6: each of these changes survived every integrity test and is killed here:
a lookalike inner directory with a suffix (``livingproof-run-integrity-extra``) taken as the
run-integrity artifact, a cap that returns 503 characters, a cap missing on the no-result reason,
the end-of-file fallback leaving the message unescaped in report.md, a message line not stripped,
and a backslash in the message left unescaped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_report_integrity_message_text import MEDIA, _collected, _refused

from capability_verifier.collect import place_integrity_lines


@pytest.mark.parametrize(
    "folder",
    [
        "livingproof-run-integrity-extra",
        "livingproof-run-integrity2",
        "x-livingproof-run-integrity",
    ],
)
def test_a_lookalike_inner_directory_is_not_the_run_integrity_artifact(folder, tmp_path):
    refused = _collected(tmp_path / "a", folder, "refused.txt", MEDIA + "\n")
    assert not refused["assessment"]["integrity_not_run"].get("refused")
    unrelated = _collected(tmp_path / "b", folder, "no-result.txt", "spoofed reason\n")
    assert "spoofed" not in unrelated["assessment"]["integrity_not_run"]["reason"]


@pytest.mark.parametrize(("size", "expected"), [(499, 499), (500, 500), (501, 500), (5000, 500)])
def test_the_refusal_message_is_at_most_500_characters(size, expected, tmp_path):
    bundle = _refused(tmp_path, "x" * size + "\n")
    reason = bundle["assessment"]["integrity_not_run"]["reason"]
    assert len(reason) == expected
    assert reason.endswith("...") == (size > 500)


def test_the_no_result_reason_is_capped_too(tmp_path):
    bundle = _collected(
        tmp_path,
        "evaluator-results-livingproof-run-integrity-stock",
        "no-result.txt",
        "y" * 3000 + "\n",
    )
    reason = bundle["assessment"]["integrity_not_run"]["reason"]
    assert len(reason) == 500 and reason.endswith("...")


def test_the_message_line_is_stripped_of_surrounding_whitespace(tmp_path):
    bundle = _refused(tmp_path, "   the refusal   \nsecond line\n")
    assert bundle["assessment"]["integrity_not_run"]["reason"] == "the refusal"


def test_the_end_of_file_fallback_escapes_the_message_in_md_but_not_in_txt(tmp_path):
    hostile = "Run Integrity: REFUSED: [x](http://evil.example) `c` | <b>b</b> *i* C:\\dir"
    (tmp_path / "integrity-lines.txt").write_text(hostile + "\n", encoding="utf-8")
    # No verdict line the placement rule can match: the lines fall back to the end of the file.
    (tmp_path / "report.md").write_text("no verdict here\n", encoding="utf-8")
    (tmp_path / "report.txt").write_text("no verdict here\n", encoding="utf-8")
    place_integrity_lines(Path(tmp_path))
    md = (tmp_path / "report.md").read_text("utf-8").splitlines()[-1]
    txt = (tmp_path / "report.txt").read_text("utf-8").splitlines()[-1]
    assert md == (
        "Run Integrity: REFUSED: \\[x\\](http://evil.example) \\`c\\` \\| \\<b\\>b\\</b\\> "
        "\\*i\\* C:\\\\dir"
    )
    assert txt == hostile


def test_a_backslash_in_the_message_is_escaped_in_md(tmp_path):
    (tmp_path / "integrity-lines.txt").write_text(
        "Run Integrity: REFUSED: a\\b\n", encoding="utf-8"
    )
    (tmp_path / "report.md").write_text("x: overall PASS.\n\n| a |\n", encoding="utf-8")
    (tmp_path / "report.txt").write_text("x: overall PASS.\n", encoding="utf-8")
    place_integrity_lines(Path(tmp_path))
    assert (tmp_path / "report.md").read_text("utf-8").splitlines()[2] == (
        "Run Integrity: REFUSED: a\\\\b"
    )
