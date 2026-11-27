# SPDX-License-Identifier: Apache-2.0
"""Edges of the integrity line's message handling that PR 221 left to chance (#219).

The end-of-file fallback must escape the message in report.md, and a backslash
in the message must be escaped there too.
"""

from __future__ import annotations

from pathlib import Path

from capability_verifier.collect import place_integrity_lines


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
        "Run Integrity: REFUSED: \\[x\\](http://evil.example) \\`c\\` \\| &lt;b&gt;b&lt;/b&gt; "
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
