# SPDX-License-Identifier: Apache-2.0
"""The integrity line's message text (#219, Vera's review of PR 221).

The refusal reason is the loader's note, shown as it is: only the markdown report escapes
it. A malformed request reads differently from a media refusal because the
messages differ. The separator before "The run is INCONCLUSIVE" is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_collect_integrity import _bundle

from capability_verifier.collect import (
    _attach_not_run,
    integrity_report_lines,
    place_integrity_lines,
)

MEDIA = "example-integrity: media is refused: media.json has no trials; no result written"
MALFORMED = "example-integrity: malformed request: manifest.json is not an object"
UV = "error: No such file or directory (os error 2)"


def _refused(tmp_path, note, *, refused=True, job_result="failure"):
    bundle, _ = _bundle(tmp_path)
    _attach_not_run(
        bundle,
        job_result,
        note.strip(),
        evaluator="example-integrity",
        refused=refused,
    )
    return bundle


def test_a_malformed_request_and_a_media_refusal_show_different_reasons(tmp_path):
    (media,) = integrity_report_lines(_refused(tmp_path / "a", MEDIA + "\n"))
    (malformed,) = integrity_report_lines(_refused(tmp_path / "b", MALFORMED + "\n"))
    assert media.startswith(f"Run Integrity: REFUSED: {MEDIA}")
    assert malformed.startswith(f"Run Integrity: REFUSED: {MALFORMED}")
    assert media != malformed


def test_any_exit_2_is_refused_even_uvs_own_error_with_its_own_text(tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, UV + "\n"))
    assert line.startswith(f"Run Integrity: REFUSED: {UV}")
    assert "NOT RUN" not in line


@pytest.mark.parametrize("reason", [MEDIA, MALFORMED, "the evaluator refused the run (exit 2)"])
def test_the_refusal_line_has_a_separator_before_the_run_is_inconclusive(reason, tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, reason + "\n"))
    assert f"{reason}. The run is INCONCLUSIVE;" in line


def test_a_reason_that_ends_in_a_full_stop_is_not_given_a_second(tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, "it was refused.\n"))
    assert "it was refused. The run is INCONCLUSIVE;" in line and ".." not in line


def test_the_not_run_line_has_the_same_separator(tmp_path):
    bundle = _refused(
        tmp_path,
        "the check ended cancelled and left no result\n",
        refused=False,
        job_result="cancelled",
    )
    (line,) = integrity_report_lines(bundle)
    assert "left no result. The run is INCONCLUSIVE;" in line


def _placed(tmp_path, line):
    (tmp_path / "integrity-lines.txt").write_text(line + "\n", encoding="utf-8")
    (tmp_path / "report.md").write_text("x: overall PASS.\n\n| a |\n", encoding="utf-8")
    (tmp_path / "report.txt").write_text("x: overall PASS.\n", encoding="utf-8")
    place_integrity_lines(Path(tmp_path))
    return (tmp_path / "report.md").read_text("utf-8").splitlines()[2], (
        tmp_path / "report.txt"
    ).read_text("utf-8").splitlines()[1]


def test_markdown_in_a_refusal_message_is_escaped_in_md_and_left_alone_in_txt(tmp_path):
    hostile = "see [x](http://evil.example) `code` | <b>bold</b> *it*"
    md, txt = _placed(tmp_path, f"Run Integrity: REFUSED: {hostile}")
    assert md == (
        "Run Integrity: REFUSED: see \\[x\\](http://evil.example) \\`code\\` \\| "
        "&lt;b&gt;bold&lt;/b&gt; \\*it\\*"
    )
    assert txt == f"Run Integrity: REFUSED: {hostile}"


def test_the_label_text_itself_is_not_mangled_by_the_md_escape(tmp_path):
    md, _ = _placed(
        tmp_path, "Run Integrity: NOT RUN (cancelled): it never ran. The run is INCONCLUSIVE."
    )
    assert md.startswith("Run Integrity: NOT RUN (cancelled): it never ran.")


@pytest.mark.parametrize("char", ["_", "~", "&"])
def test_underscore_tilde_and_ampersand_are_escaped_in_the_md_message_only(char, tmp_path):
    md, txt = _placed(tmp_path, f"Run Integrity: REFUSED: a{char}b{char}c")
    assert md == f"Run Integrity: REFUSED: a\\{char}b\\{char}c"
    assert txt == f"Run Integrity: REFUSED: a{char}b{char}c"


def test_italics_strikethrough_and_entities_in_a_message_stay_plain_text_in_md(tmp_path):
    md, _ = _placed(tmp_path, "Run Integrity: REFUSED: _it_ ~~s~~ &lt;b&gt;")
    assert md == "Run Integrity: REFUSED: \\_it\\_ \\~\\~s\\~\\~ \\&lt;b\\&gt;"
