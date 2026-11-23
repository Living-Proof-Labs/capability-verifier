# SPDX-License-Identifier: Apache-2.0
"""http(s) addresses in a refusal keep '_' '&' and '~' (#230, Vera's N1 on PR 223).

L6 backslash-escapes those characters so an evaluator's message stays plain text. Doing that
inside an address makes a GFM auto-link point somewhere else (the href gains ``%5C``) and the
visible address shows the backslashes. The address is left as written in ``report.md``, shown
verbatim in ``report.txt``, and HTML-escaped once in ``report.html`` so ``&`` is ``&amp;``.
Link text and every other run of text stay escaped.
"""

from __future__ import annotations

import html
from pathlib import Path

import pytest

from capability_verifier.collect import place_integrity_lines

# Real addresses. Each one has '_', '&', '~', a query string and a fragment.
URLS = [
    "https://example.com/a_b?c=1&d=~x#frag_ment",
    (
        "https://github.com/Living-Proof-Labs/capability-verifier/issues/230"
        "?q=run_integrity&source=~locks#n1_review"
    ),
    (
        "http://docs.livingprooflabs.com/capability-verifier/locks/"
        "?topic=run_integrity&ref=~main#section_2"
    ),
]

PAGE = "<main class='wrap'><p class='stored-status'>Stored overall status: PASS</p></main>\n"


def _formats(tmp_path, line, *, verdict=True):
    (tmp_path / "integrity-lines.txt").write_text(line + "\n", encoding="utf-8")
    if verdict:
        (tmp_path / "report.md").write_text("x: overall PASS.\n\n| a |\n", encoding="utf-8")
        (tmp_path / "report.txt").write_text("x: overall PASS.\n", encoding="utf-8")
    else:
        (tmp_path / "report.md").write_text("no verdict here\n", encoding="utf-8")
        (tmp_path / "report.txt").write_text("no verdict here\n", encoding="utf-8")
    (tmp_path / "report.html").write_text(PAGE, encoding="utf-8")
    place_integrity_lines(Path(tmp_path))
    return (
        (tmp_path / "report.md").read_text(encoding="utf-8"),
        (tmp_path / "report.txt").read_text(encoding="utf-8"),
        (tmp_path / "report.html").read_text(encoding="utf-8"),
    )


def _md_txt(text, *, verdict, markdown):
    lines = text.splitlines()
    if not verdict:
        return lines[-1]
    # Markdown puts a blank line between the verdict and the integrity line. Text does not.
    return lines[2] if markdown else lines[1]


def _html(page):
    marker = "<p class='stored-status integrity-status'>"
    start = page.index(marker) + len(marker)
    inner = page[start : page.index("</p>", start)]
    assert "<" not in inner
    return inner


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("verdict", [True, False])
def test_an_http_address_keeps_underscore_ampersand_and_tilde(url, verdict, tmp_path):
    raw = f"Run Integrity: REFUSED: see {url}"
    md, txt, page = _formats(tmp_path, raw, verdict=verdict)
    # The markdown token is what an autolinker copies into both the href and the visible text.
    assert _md_txt(md, verdict=verdict, markdown=True) == raw
    assert _md_txt(txt, verdict=verdict, markdown=False) == raw
    shown = _html(page)
    assert shown == html.escape(raw, quote=False)
    assert shown.count("&amp;") == url.count("&")
    assert "&amp;amp;" not in shown
    assert "\\" not in shown
    for char in ("_", "&", "~"):
        assert "\\" + char not in _md_txt(md, verdict=verdict, markdown=True)


def test_the_href_and_the_auto_linked_text_both_keep_the_address(tmp_path):
    url = URLS[1]
    raw = (
        f"Run Integrity: REFUSED: see {url} and [click]({url}) "
        "_it_ ~~s~~ &lt;b&gt; <script>alert(1)</script>"
    )
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    md_line = _md_txt(md, verdict=True, markdown=True)
    # Bare address (auto-linked text) and the address in parentheses (the link target).
    assert md_line.count(url) == 2
    assert f"]({url})" in md_line
    assert "\\[click\\]" in md_line and "[click](" not in md_line
    assert "\\_it\\_" in md_line
    assert "\\~\\~s\\~\\~" in md_line
    assert "\\&lt;b\\&gt;" in md_line
    assert "\\<script\\>alert(1)\\</script\\>" in md_line
    assert "<script>" not in md_line
    shown = _html(page)
    assert shown == html.escape(raw, quote=False)
    assert shown.count(html.escape(url, quote=False)) == 2
    assert "&amp;amp;" not in shown and "\\" not in shown
    assert "<script>" not in page and "&lt;script&gt;" in shown


def test_markup_stuck_to_an_address_stays_escaped_and_the_address_does_not(tmp_path):
    url = URLS[0]
    raw = f"Run Integrity: REFUSED: see {url}<script>alert(1)</script> `code` | cell"
    md, _, page = _formats(tmp_path, raw)
    assert _md_txt(md, verdict=True, markdown=True) == (
        f"Run Integrity: REFUSED: see {url}\\<script\\>alert(1)\\</script\\> \\`code\\` \\| cell"
    )
    assert _html(page) == html.escape(raw, quote=False)
    assert "<script>" not in page


def test_a_non_http_address_is_still_escaped_in_markdown(tmp_path):
    raw = "Run Integrity: REFUSED: file:///tmp/a_b and ftp://files.example/a_b?c=1&d=~x"
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    assert _md_txt(md, verdict=True, markdown=True) == (
        "Run Integrity: REFUSED: file:///tmp/a\\_b and ftp://files.example/a\\_b?c=1\\&d=\\~x"
    )
    assert _html(page) == html.escape(raw, quote=False)
    assert "\\" not in _html(page)


def test_an_address_that_will_not_auto_link_is_still_escaped(tmp_path):
    raw = "Run Integrity: REFUSED: note:https://example.com/a_b_c?x=1&y=~z#frag_ment"
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    assert _md_txt(md, verdict=True, markdown=True) == (
        "Run Integrity: REFUSED: note:https://example.com/a\\_b\\_c?x=1\\&y=\\~z#frag\\_ment"
    )
    assert _html(page) == html.escape(raw, quote=False)
