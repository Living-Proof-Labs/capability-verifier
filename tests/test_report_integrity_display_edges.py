# SPDX-License-Identifier: Apache-2.0
"""Edges of the integrity display (#219) that no other test pins.

The evaluator's refusal message is escaped in report.html. The line also has to
survive a reporter that words its verdict differently.
"""

from __future__ import annotations

from pathlib import Path

from capability_verifier.collect import integrity_report_lines, place_integrity_lines

LINE = "Run Integrity: NOT RUN (cancelled): it never ran"
HTML = "<main class='wrap'><p class='stored-status'>Stored overall status: PASS</p></main>"


def _dir(tmp_path, *, md="", txt="", html=HTML, lines=LINE):
    (tmp_path / "integrity-lines.txt").write_text(lines + "\n", encoding="utf-8")
    (tmp_path / "report.md").write_text(md, encoding="utf-8")
    (tmp_path / "report.txt").write_text(txt, encoding="utf-8")
    (tmp_path / "report.html").write_text(html, encoding="utf-8")
    return Path(tmp_path)


def test_a_hostile_refusal_message_is_escaped_in_html_and_never_raw(tmp_path):
    hostile = "<script>alert(\"x\")</script> & <img src=x onerror='y'>"
    out = _dir(tmp_path, lines=f"Run Integrity: REFUSED: {hostile}")
    place_integrity_lines(out)
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html and "&lt;img" in html


def test_md_and_txt_fall_back_to_the_end_when_the_verdict_is_worded_differently(tmp_path):
    out = _dir(tmp_path, md="# Report\n\nThe grade is PASS\n", txt="Report\nThe grade is PASS\n")
    place_integrity_lines(out)
    assert (out / "report.md").read_text(encoding="utf-8").splitlines()[-2:] == ["", LINE]
    assert (out / "report.txt").read_text(encoding="utf-8").splitlines()[-1] == LINE


def test_html_falls_back_to_the_top_of_the_page_when_there_is_no_stored_status(tmp_path):
    out = _dir(tmp_path, html="<main class='wrap'><h1>Report</h1></main>")
    place_integrity_lines(out)
    html = (out / "report.html").read_text(encoding="utf-8")
    assert html.startswith("<main class='wrap'><p class='stored-status integrity-status'>")
    assert html.count("integrity-status") == 1


def test_md_puts_a_blank_line_between_the_verdict_and_the_integrity_line(tmp_path):
    out = _dir(tmp_path, md="x: overall PASS.\n\n| a |\n|---|\n")
    place_integrity_lines(out)
    assert (out / "report.md").read_text(encoding="utf-8").splitlines()[:3] == [
        "x: overall PASS.",
        "",
        LINE,
    ]


def test_an_inconclusive_run_is_shown_as_inconclusive_not_failed():
    document = {
        "evaluator": {"id": "e", "kind": "integrity", "display_name": "Run Integrity"},
        "integrity": {"run": {"test_system_status": "INCONCLUSIVE", "success": True}},
    }
    (line,) = integrity_report_lines({"assessment": {"integrity_results": {"e": document}}})
    assert line.endswith(": INCONCLUSIVE") and "FAILED" not in line
