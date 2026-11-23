# SPDX-License-Identifier: Apache-2.0
"""http(s) addresses in a refusal keep '_' '&' and '~' only when GFM auto-links them (#230).

Vera's review of PR 233: an ``https://`` run that GitHub will not auto-link must stay escaped
(emphasis, strike-through, entities), and ``<`` must be written ``&lt;`` so a tag stuck to an
address cannot become raw HTML in ``report.md``. ``report.txt`` is verbatim. ``report.html``
HTML-escapes the original line once.
"""

from __future__ import annotations

import html
import os
import random
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest
from cmarkgfm.cmark import Options, markdown_to_html_with_extensions

from capability_verifier.collect import _md_escape, place_integrity_lines

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
ROOT = Path(__file__).parents[1]


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


def _render(markdown: str) -> str:
    # Unsafe, and without the tag filter, so a leaked <script> or <img> is visible.
    return markdown_to_html_with_extensions(
        markdown,
        options=Options.CMARK_OPT_UNSAFE,
        extensions=["autolink", "strikethrough"],
    )


class _Rendered(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {key: value or "" for key, value in attrs}))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _rendered(markdown: str) -> _Rendered:
    parsed = _Rendered()
    parsed.feed(_render(markdown))
    return parsed


def _assert_no_injected_html(parsed: _Rendered) -> None:
    for tag, attrs in parsed.tags:
        assert tag in {"p", "a"}, tag
        if tag == "a":
            href = attrs["href"]
            assert href.lower().startswith(("http://", "https://", "ftp://"))
            assert "javascript:" not in href.lower()


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("verdict", [True, False])
def test_an_http_address_keeps_underscore_ampersand_and_tilde(url, verdict, tmp_path):
    raw = f"Run Integrity: REFUSED: see {url}"
    md, txt, page = _formats(tmp_path, raw, verdict=verdict)
    line = _md_txt(md, verdict=verdict, markdown=True)
    # The markdown token is what an autolinker copies into both the href and the visible text.
    assert line == raw
    assert _md_txt(txt, verdict=verdict, markdown=False) == raw
    shown = _html(page)
    assert shown == html.escape(raw, quote=False)
    assert shown.count("&amp;") == url.count("&")
    assert "&amp;amp;" not in shown
    assert "\\" not in shown
    rendered = _rendered(line)
    _assert_no_injected_html(rendered)
    hrefs = [attrs["href"] for tag, attrs in rendered.tags if tag == "a"]
    assert hrefs == [url]
    assert url in "".join(rendered.text)
    assert "&amp;amp;" not in _render(line)


def test_the_href_and_the_auto_linked_text_both_keep_the_address(tmp_path):
    url = URLS[1]
    raw = (
        f"Run Integrity: REFUSED: see {url} and [click]({url}) "
        "_it_ ~~s~~ &lt;b&gt; <script>alert(1)</script>"
    )
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    md_line = _md_txt(md, verdict=True, markdown=True)
    assert md_line.count(url) == 2
    assert f"]({url})" in md_line
    assert "\\[click\\]" in md_line and "[click](" not in md_line
    assert "\\_it\\_" in md_line
    assert "\\~\\~s\\~\\~" in md_line
    assert "\\&lt;b\\&gt;" in md_line
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in md_line
    assert "<script>" not in md_line
    shown = _html(page)
    assert shown == html.escape(raw, quote=False)
    assert shown.count(html.escape(url, quote=False)) == 2
    assert "&amp;amp;" not in shown and "\\" not in shown
    assert "<script>" not in page and "&lt;script&gt;" in shown
    rendered = _rendered(md_line)
    _assert_no_injected_html(rendered)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [url, url]
    visible = "".join(rendered.text)
    assert "_it_" in visible and "~~s~~" in visible and "&lt;b&gt;" in visible
    assert all(tag != "script" for tag, _ in rendered.tags)


@pytest.mark.parametrize(
    "message",
    [
        "https://*bold*",
        "https://_it_",
        "https://~~x~~",
        "https://&lt;b&gt;",
        "https://&#x3C;script&#x3E;",
    ],
)
def test_an_https_run_that_gfm_will_not_auto_link_stays_plain(message, tmp_path):
    raw = f"Run Integrity: REFUSED: {message}"
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    line = _md_txt(md, verdict=True, markdown=True)
    assert line != raw
    rendered = _rendered(line)
    assert [tag for tag, _ in rendered.tags] == ["p"]
    assert "".join(rendered.text).strip() == raw
    assert _html(page) == html.escape(raw, quote=False)


@pytest.mark.parametrize(
    "message",
    [
        "https://e.com/<a href=https://evil.example/>Run Integrity: PASS</a>",
        "https://e.com/<h1 id=x>FAKE VERDICT</h1 >",
        "see https://example.com/a<img src=https://evil.example/p.png>",
        'https://example.com/"><img src=x onerror=alert(1)>',
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<b>bold</b>",
        "<https://example.com/a_b?c=1&d=~x>",
    ],
)
def test_a_tag_in_a_refusal_does_not_render_as_an_html_element(message, tmp_path):
    raw = f"Run Integrity: REFUSED: {message}"
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    line = _md_txt(md, verdict=True, markdown=True)
    assert "<" not in line
    rendered = _rendered(line)
    _assert_no_injected_html(rendered)
    assert _html(page) == html.escape(raw, quote=False)
    assert "&amp;amp;" not in _html(page)


def test_an_angle_autolink_keeps_the_address_and_is_not_a_tag(tmp_path):
    url = "https://example.com/a_b?c=1&d=~x#frag_ment"
    raw = f"Run Integrity: REFUSED: <{url}>"
    md, _, page = _formats(tmp_path, raw)
    line = _md_txt(md, verdict=True, markdown=True)
    assert line == f"Run Integrity: REFUSED: &lt;{url}&gt;"
    rendered = _rendered(line)
    _assert_no_injected_html(rendered)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [url]
    assert _html(page) == html.escape(raw, quote=False)


@pytest.mark.parametrize(
    ("message", "href"),
    [
        ("HTTPS://example.com/a_b?c=1&d=~x#frag", "HTTPS://example.com/a_b?c=1&d=~x#frag"),
        ("Https://example.com/a_b?c=1&d=~x#frag", "Https://example.com/a_b?c=1&d=~x#frag"),
        ("www.example.com/a_b?c=1&d=~x#frag", "http://www.example.com/a_b?c=1&d=~x#frag"),
        ("ftp://files.example/a_b?c=1&d=~x#frag", "ftp://files.example/a_b?c=1&d=~x#frag"),
    ],
)
def test_uppercase_schemes_www_and_ftp_autolink_with_the_address_intact(message, href, tmp_path):
    md, txt, _ = _formats(tmp_path, f"Run Integrity: REFUSED: see {message}")
    assert message in _md_txt(md, verdict=True, markdown=True)
    assert message in _md_txt(txt, verdict=True, markdown=False)
    rendered = _rendered(_md_txt(md, verdict=True, markdown=True))
    _assert_no_injected_html(rendered)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [href]


def test_a_non_http_address_is_still_escaped_in_markdown(tmp_path):
    raw = "Run Integrity: REFUSED: file:///tmp/a_b"
    md, txt, page = _formats(tmp_path, raw)
    assert _md_txt(txt, verdict=True, markdown=False) == raw
    # file:// is not a scheme we link, so :// is broken and cannot be re-autolinked.
    assert (
        _md_txt(md, verdict=True, markdown=True) == "Run Integrity: REFUSED: file:\\/\\//tmp/a\\_b"
    )
    assert _html(page) == html.escape(raw, quote=False)
    assert "\\" not in _html(page)
    rendered = _rendered(_md_txt(md, verdict=True, markdown=True))
    assert [tag for tag, _ in rendered.tags] == ["p"]
    assert "".join(rendered.text).strip() == raw


def test_a_www_address_after_an_asterisk_still_autolinks(tmp_path):
    md, _, _ = _formats(tmp_path, "Run Integrity: REFUSED: *www.example.com/a_b")
    line = _md_txt(md, verdict=True, markdown=True)
    rendered = _rendered(line)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [
        "http://www.example.com/a_b"
    ]
    assert "\\_" not in line.split("www", 1)[1]


def test_a_www_address_after_a_tab_still_autolinks(tmp_path):
    md, _, _ = _formats(tmp_path, "Run Integrity: REFUSED: see\twww.example.com/a_b")
    line = _md_txt(md, verdict=True, markdown=True)
    rendered = _rendered(line)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [
        "http://www.example.com/a_b"
    ]


def test_backslash_backtick_brackets_and_pipe_stay_inside_the_link(tmp_path):
    address = "https://example.com/a\\b`c[d]|e"
    md, _, _ = _formats(tmp_path, f"Run Integrity: REFUSED: {address}")
    line = _md_txt(md, verdict=True, markdown=True)
    assert address in line
    rendered = _rendered(line)
    _assert_no_injected_html(rendered)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [
        "https://example.com/a%5Cb%60c%5Bd%5D%7Ce"
    ]


def test_a_greater_than_inside_an_address_stays_in_the_link(tmp_path):
    md, _, _ = _formats(tmp_path, "Run Integrity: REFUSED: https://example.com/a>b")
    line = _md_txt(md, verdict=True, markdown=True)
    assert "https://example.com/a>b" in line
    rendered = _rendered(line)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [
        "https://example.com/a%3Eb"
    ]


def test_html_escapes_ampersand_once_and_does_not_escape_quotes(tmp_path):
    raw = 'Run Integrity: REFUSED: say "hi" & co <b>'
    _, _, page = _formats(tmp_path, raw)
    shown = _html(page)
    assert shown == 'Run Integrity: REFUSED: say "hi" &amp; co &lt;b&gt;'
    assert "&quot;" not in shown
    assert "&amp;amp;" not in shown


def test_brackets_after_a_non_link_do_not_become_a_labelled_link(tmp_path):
    raw = "Run Integrity: REFUSED: https://*bold* [click](https://evil.example)"
    md, _, _ = _formats(tmp_path, raw)
    line = _md_txt(md, verdict=True, markdown=True)
    assert "\\[click\\]" in line
    rendered = _rendered(line)
    _assert_no_injected_html(rendered)
    texts = "".join(rendered.text)
    assert "[click]" in texts
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == ["https://evil.example"]


@pytest.mark.parametrize(
    "message",
    [
        "see https://e.com/~. the robot PASSED https://e.com/.~ ok",
        "see https://e.com/~. x https://e.com/.~ y",
        "see https://e.com/_? hidden words https://e.com/?_ end",
        "https://e.com/_? w https://e.com/?_",
        "see https://e.com/_,_ x",
        'https://e.com/?*",*',
    ],
)
def test_trimmed_punctuation_does_not_emphasise_or_strike(message):
    rendered = _rendered(_md_escape(message))
    assert not any(tag in {"em", "strong", "del"} for tag, _ in rendered.tags)
    _assert_no_injected_html(rendered)
    assert "".join(rendered.text).strip() == message
    if "PASSED" in message:
        assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [
            "https://e.com/",
            "https://e.com/",
        ]


@pytest.mark.parametrize(
    ("message", "href"),
    [
        ("https://e.com/a~", "https://e.com/a"),
        ("https://e.com/a*", "https://e.com/a"),
        ("https://e.com/a_", "https://e.com/a"),
        ("https://e.com/a**", "https://e.com/a"),
        ("https://e.com/a~~", "https://e.com/a"),
    ],
)
def test_a_trimmed_mark_keeps_the_exact_href_and_the_ascii_character(message, href):
    escaped = _md_escape(message)
    assert escaped.startswith("<" + href + ">")
    assert "&#126;" not in escaped and "&tilde;" not in escaped
    rendered = _rendered(escaped)
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [href]
    assert "".join(rendered.text).strip() == message
    assert not any(tag in {"em", "strong", "del"} for tag, _ in rendered.tags)


def test_a_plain_address_is_not_wrapped_in_angle_brackets():
    """Angle form is only for a tail that can emphasise. A normal address stays bare."""
    escaped = _md_escape("https://example.com/a_b?c=1&d=~x#frag_ment")
    assert not escaped.startswith("<")
    assert "<https://" not in escaped


def test_a_greater_than_inside_a_marked_address_is_not_angle_wrapped():
    """``>`` would close the angle autolink, so the span is not wrapped and is not linked."""
    message = "https://e.com/a>b~"
    escaped = _md_escape(message)
    assert "<" not in escaped
    rendered = _rendered(escaped)
    assert "".join(rendered.text).strip() == message
    assert not any(tag == "a" for tag, _ in rendered.tags)
    assert not any(tag in {"em", "strong", "del"} for tag, _ in rendered.tags)


@pytest.mark.parametrize(
    "message",
    [
        "x\u00a0www.e.com/~~PASSED~~x y",
        "x\u2003www.e.com/*PASSED*x y",
        "https://e.com/a\u00a0_b_",
    ],
)
def test_non_ascii_space_does_not_let_a_mark_become_emphasis(message):
    rendered = _rendered(_md_escape(message))
    assert "".join(rendered.text).strip() == message
    assert not any(tag in {"em", "strong", "del", "code"} for tag, _ in rendered.tags)


def test_an_embedded_scheme_after_www_is_not_relinked():
    message = "see www.e.com/a~https://e.com/b*c~ end"
    escaped = _md_escape(message)
    assert "https:\\/\\/" in escaped
    rendered = _rendered(escaped)
    assert "".join(rendered.text).strip() == message
    assert not any(tag == "a" for tag, _ in rendered.tags)


def test_an_ampersand_inside_an_angle_autolink_stays_in_the_address():
    message = "https://e.com/a&lt;b~"
    rendered = _rendered(_md_escape(message))
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [message[:-1]]
    assert "".join(rendered.text).strip() == message


def test_a_control_character_is_not_placed_inside_an_angle_autolink():
    message = "https://e.com/a\x01b~"
    escaped = _md_escape(message)
    assert "<" not in escaped
    rendered = _rendered(escaped)
    assert "".join(rendered.text).strip() == message
    assert not any(tag == "a" for tag, _ in rendered.tags)


def test_a_trailing_named_entity_is_trimmed_off_the_link():
    message = "https://e.com/a&copy;~"
    rendered = _rendered(_md_escape(message))
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [
        "https://e.com/a"
    ]
    assert "".join(rendered.text).strip() == message


def test_a_raw_ampersand_after_an_unshielded_link_decodes_the_entity():
    rendered = _rendered(_md_escape("https://e.com/&copy;"))
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [
        "https://e.com/"
    ]
    assert "".join(rendered.text).strip() == "https://e.com/\u00a9"
    assert "\\" not in "".join(rendered.text)


def test_an_ampersand_in_a_shielded_tail_stays_literal():
    message = "https://e.com/a~&copy;"
    rendered = _rendered(_md_escape(message))
    assert "".join(rendered.text).strip() == message
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [
        "https://e.com/a"
    ]


def test_a_www_address_with_a_tilde_is_shown_exactly_and_not_linked():
    message = "www.example.com/a_b~"
    escaped = _md_escape(message)
    assert escaped == "www\\.example.com/a\\_b\\~"
    rendered = _rendered(escaped)
    assert not any(tag == "a" for tag, _ in rendered.tags)
    assert "".join(rendered.text).strip() == message
    assert not any(tag in {"em", "strong", "del"} for tag, _ in rendered.tags)


@pytest.mark.parametrize(
    "message",
    [
        "https://e.com/a~<img src=x onerror=alert(1)>",
        "https://e.com/a~<script>alert(1)</script>",
        "https://e.com/a><b>no</b>",
        "https://e.com/<a href=https://evil.example/>Run Integrity: PASS</a>",
        "see https://example.com/a<img src=https://evil.example/p.png>",
    ],
)
def test_a_bracket_next_to_an_address_does_not_open_a_tag(message):
    escaped = _md_escape(message)
    # The only raw brackets are an angle autolink around a scheme address.
    without_angles = escaped
    while "<" in without_angles:
        open_at = without_angles.index("<")
        close_at = without_angles.find(">", open_at)
        assert close_at != -1
        inside = without_angles[open_at + 1 : close_at]
        assert inside.lower().startswith(("http://", "https://", "ftp://"))
        assert not any(
            ch in "<>" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in inside
        )
        without_angles = without_angles[:open_at] + without_angles[close_at + 1 :]
    assert "<" not in without_angles
    rendered = _rendered(escaped)
    _assert_no_injected_html(rendered)


def test_a_trimmed_underscore_stays_visible_and_out_of_the_href():
    rendered = _rendered(_md_escape("see https://e.com/_,_ x"))
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == ["https://e.com/"]
    assert "_,_" in "".join(rendered.text)


def test_an_underscore_in_the_last_two_labels_is_not_a_link():
    """Kills: no underscore rule, last-label only, and a dot bound of 0.

    ``a*b*c`` stays inside the run, so a scanner that accepts the host lets cmark
    emphasise ``b``. ``_secret_`` would not: the trim encodes only the final ``_``.
    """
    rendered = _rendered(_md_escape("https://a_b.com/a*b*c"))
    assert not any(tag == "em" for tag, _ in rendered.tags)
    assert "a*b*c" in "".join(rendered.text)


def test_a_domain_with_more_than_ten_dots_still_links_with_an_underscore():
    host = "a.b.c.d.e.f.g.h.i.j.k.l_m.com"
    url = f"https://{host}/x_y"
    rendered = _rendered(_md_escape(url))
    assert [attrs["href"] for tag, attrs in rendered.tags if tag == "a"] == [url]


def test_a_backslash_in_the_domain_does_not_free_the_following_emphasis():
    rendered = _rendered(_md_escape("https://a\\.b_c.com/a*b*c"))
    assert not any(tag == "em" for tag, _ in rendered.tags)


@pytest.mark.parametrize(
    "message",
    [
        "see https://x\\_y.com/*PASSED*x end",
        "see https://e.co\\_m/~~PASSED~~x end",
        "see https://a_b.\u00fc.c.d/*PASSED*x end",
        "see https://a_b.\u4f8b.c.d/~~PASSED~~x end",
        "see www.a_b.\u00fc.c.d/*PASSED*x end",
        "see https://a_b.\u00fc.c.d/`code` end",
    ],
)
def test_a_backslash_or_non_ascii_host_is_not_passed_through_raw(message):
    """cmark counts ``\\_`` and stops at a UTF-8 continuation byte, so these are not links."""
    rendered = _rendered(_md_escape(message))
    assert not any(tag in {"em", "strong", "del", "code"} for tag, _ in rendered.tags)
    assert "".join(rendered.text).strip() == message


def test_an_apostrophe_before_a_mark_is_trimmed_from_the_href():
    message = "https://e.com/a'~"
    rendered = _rendered(_md_escape(message))
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [
        "https://e.com/a"
    ]
    assert "".join(rendered.text).strip() == message


def test_a_trailing_apostrophe_is_trimmed_from_an_unshielded_link():
    message = "https://e.com/a'"
    rendered = _rendered(_md_escape(message))
    assert [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"] == [
        "https://e.com/a"
    ]
    assert "".join(rendered.text).strip() == message


def test_a_unicode_dash_cannot_start_a_host():
    """U+2014 is in cmark's U+2010–U+2027 punctuation range, not a host character."""
    message = "https://\u2014e.com/a*b*"
    rendered = _rendered(_md_escape(message))
    assert [tag for tag, _ in rendered.tags] == ["p"]
    assert "".join(rendered.text).strip() == message


def test_a_nbsp_cannot_start_a_host():
    """NBSP is cmark_utf8proc_is_space, so it is not a host character."""
    message = "https://\u00a0e.com/*PASSED*"
    rendered = _rendered(_md_escape(message))
    assert not any(tag in {"em", "strong", "del", "code"} for tag, _ in rendered.tags)
    assert "".join(rendered.text).strip() == message


def test_a_bare_www_dot_is_not_an_autolink_span():
    """The dot in ``www.`` is the last character, so cmark's strict domain check rejects it.

    Forcing ``allow_short=True`` accepts the span, but the characters need no escaping, so the
    rendered line does not change. This asserts the span itself.
    """
    from capability_verifier.collect import _www_span

    assert _www_span("www.", 0) is None


def test_non_ascii_punctuation_cannot_start_a_host():
    rendered = _rendered(_md_escape("https://。e.com/a*b*c"))
    assert not any(tag == "em" for tag, _ in rendered.tags)


def test_host_punctuation_follows_cmark_list_not_unicode_category():
    """U+166D is Unicode So, but cmark lists it. U+061D is Po and cmark does not.

    Compared with cmark_utf8proc_is_punctuation in cmark-gfm 0.29.0.gfm.13
    (cmarkgfm 2025.10.22): 155 non-ASCII characters are category P and absent
    from that list, and one listed character, U+166D, is not category P.
    """
    rejected = "https://\u166de.com/a*b*"
    rendered = _rendered(_md_escape(rejected))
    assert [tag for tag, _ in rendered.tags] == ["p"]
    assert "".join(rendered.text).strip() == rejected

    accepted = "https://\u061de.com/a*b*"
    rendered = _rendered(_md_escape(accepted))
    hrefs = [unquote(attrs["href"]) for tag, attrs in rendered.tags if tag == "a"]
    assert hrefs == ["https://\u061de.com/a*b"]
    assert "".join(rendered.text).strip() == accepted
    assert not any(tag == "em" for tag, _ in rendered.tags)


# Independent cmark-gfm extended-autolink oracle.
# Written from extensions/autolink.c (www_match, url_match, check_domain,
# autolink_delim, is_valid_hostchar) and src/utf8.c / src/cmark_ctype.c of
# cmark-gfm 0.29.0.gfm.13. It does not call collect.py. The fuzz test's only
# production call is _md_escape.

# cmark_isspace: class 1 is tab, LF, CR, space. VT and FF are class 0.
_ORACLE_SPACE = frozenset("\t\n\r ")
_ORACLE_BEFORE = frozenset("*_~(")
_ORACLE_TRIM = frozenset("?!.,:*_~'\"")
_ORACLE_MARKS = frozenset("*_~")
_ORACLE_SCHEMES = ("http", "https", "ftp")

_ORACLE_PUNCT_RANGES = (
    (161, 161),
    (167, 167),
    (171, 171),
    (182, 183),
    (187, 187),
    (191, 191),
    (894, 894),
    (903, 903),
    (1370, 1375),
    (1417, 1418),
    (1470, 1470),
    (1472, 1472),
    (1475, 1475),
    (1478, 1478),
    (1523, 1524),
    (1545, 1546),
    (1548, 1549),
    (1563, 1563),
    (1566, 1567),
    (1642, 1645),
    (1748, 1748),
    (1792, 1805),
    (2039, 2041),
    (2096, 2110),
    (2142, 2142),
    (2404, 2405),
    (2416, 2416),
    (2800, 2800),
    (3572, 3572),
    (3663, 3663),
    (3674, 3675),
    (3844, 3858),
    (3860, 3860),
    (3898, 3901),
    (3973, 3973),
    (4048, 4052),
    (4057, 4058),
    (4170, 4175),
    (4347, 4347),
    (4960, 4968),
    (5120, 5120),
    (5741, 5742),
    (5787, 5788),
    (5867, 5869),
    (5941, 5942),
    (6100, 6102),
    (6104, 6106),
    (6144, 6154),
    (6468, 6469),
    (6686, 6687),
    (6816, 6822),
    (6824, 6829),
    (7002, 7008),
    (7164, 7167),
    (7227, 7231),
    (7294, 7295),
    (7360, 7367),
    (7379, 7379),
    (8208, 8231),
    (8240, 8259),
    (8261, 8273),
    (8275, 8286),
    (8317, 8318),
    (8333, 8334),
    (8968, 8971),
    (9001, 9002),
    (10088, 10101),
    (10181, 10182),
    (10214, 10223),
    (10627, 10648),
    (10712, 10715),
    (10748, 10749),
    (11513, 11516),
    (11518, 11519),
    (11632, 11632),
    (11776, 11822),
    (11824, 11842),
    (12289, 12291),
    (12296, 12305),
    (12308, 12319),
    (12336, 12336),
    (12349, 12349),
    (12448, 12448),
    (12539, 12539),
    (42238, 42239),
    (42509, 42511),
    (42611, 42611),
    (42622, 42622),
    (42738, 42743),
    (43124, 43127),
    (43214, 43215),
    (43256, 43258),
    (43310, 43311),
    (43359, 43359),
    (43457, 43469),
    (43486, 43487),
    (43612, 43615),
    (43742, 43743),
    (43760, 43761),
    (44011, 44011),
    (64830, 64831),
    (65040, 65049),
    (65072, 65106),
    (65108, 65121),
    (65123, 65123),
    (65128, 65128),
    (65130, 65131),
    (65281, 65283),
    (65285, 65290),
    (65292, 65295),
    (65306, 65307),
    (65311, 65312),
    (65339, 65341),
    (65343, 65343),
    (65371, 65371),
    (65373, 65373),
    (65375, 65381),
    (65792, 65794),
    (66463, 66463),
    (66512, 66512),
    (66927, 66927),
    (67671, 67671),
    (67871, 67871),
    (67903, 67903),
    (68176, 68184),
    (68223, 68223),
    (68336, 68342),
    (68409, 68415),
    (68505, 68508),
    (69703, 69709),
    (69819, 69820),
    (69822, 69825),
    (69952, 69955),
    (70004, 70005),
    (70085, 70088),
    (70093, 70093),
    (70200, 70205),
    (70854, 70854),
    (71105, 71113),
    (71233, 71235),
    (74864, 74868),
    (92782, 92783),
    (92917, 92917),
    (92983, 92987),
    (92996, 92996),
    (113823, 113823),
)

_ORACLE_PUNCT = frozenset(
    code for start, stop in _ORACLE_PUNCT_RANGES for code in range(start, stop + 1)
)


def _oracle_space_utf8(ch: str) -> bool:
    """cmark_utf8proc_is_space. NBSP, em space and U+3000 are spaces. U+2028 is not."""
    code = ord(ch)
    return code in (9, 10, 12, 13, 32, 160, 5760, 8239, 8287, 12288) or 8192 <= code <= 8202


def _oracle_punct(code: int) -> bool:
    """cmark_utf8proc_is_punctuation, including cmark_ispunct for ASCII."""
    if code < 128:
        return (
            0x21 <= code <= 0x2F
            or 0x3A <= code <= 0x40
            or 0x5B <= code <= 0x60
            or 0x7B <= code <= 0x7E
        )
    return code in _ORACLE_PUNCT


def _oracle_host_char(ch: str) -> bool:
    """is_valid_hostchar: not space and not punctuation."""
    code = ord(ch)
    if _oracle_space_utf8(ch):
        return False
    return not _oracle_punct(code)


def _oracle_decode(raw: bytes, index: int) -> int | None:
    """cmark_utf8proc_iterate. None for a continuation byte or an invalid sequence."""
    if index >= len(raw):
        return None
    first = raw[index]
    if first < 0x80:
        return first
    if first < 0xC2 or first > 0xF4:
        return None
    width = 2 if first < 0xE0 else 3 if first < 0xF0 else 4
    if index + width > len(raw):
        return None
    for offset in range(1, width):
        if raw[index + offset] & 0xC0 != 0x80:
            return None
    if width == 2:
        code = ((first & 0x1F) << 6) | (raw[index + 1] & 0x3F)
        if code < 0x80:
            return None
    elif width == 3:
        if first == 0xE0 and raw[index + 1] < 0xA0:
            return None
        if first == 0xED and raw[index + 1] >= 0xA0:
            return None
        code = ((first & 0x0F) << 12) | ((raw[index + 1] & 0x3F) << 6) | (raw[index + 2] & 0x3F)
        if code < 0x800:
            return None
    else:
        if first == 0xF0 and raw[index + 1] < 0x90:
            return None
        if first == 0xF4 and raw[index + 1] >= 0x90:
            return None
        code = (
            ((first & 0x07) << 18)
            | ((raw[index + 1] & 0x3F) << 12)
            | ((raw[index + 2] & 0x3F) << 6)
            | (raw[index + 3] & 0x3F)
        )
        if code < 0x10000 or code >= 0x110000:
            return None
    return code


def _oracle_host_byte(raw: bytes, index: int) -> bool:
    """is_valid_hostchar at a byte offset. A continuation byte is not a host character."""
    code = _oracle_decode(raw, index)
    if code is None:
        return False
    if code in (9, 10, 12, 13, 32, 160, 5760, 8239, 8287, 12288) or 8192 <= code <= 8202:
        return False
    return not _oracle_punct(code)


def _oracle_domain(text: str, *, allow_short: bool) -> int | None:
    """Code-point index where the tail scan resumes, or None when cmark rejects the host.

    check_domain walks UTF-8 bytes. The returned byte length is mapped back to a code
    point so callers can slice the Python string. None is rejection (cmark's 0). A 0
    index is real: the byte length fell inside the first character, and the scan
    resumes there. A backslash is skipped and the next byte is examined.
    """
    raw = text.encode("utf-8")
    size = len(raw)
    index = 1
    uscore1 = 0
    uscore2 = 0
    dots = 0
    while index < size - 1:
        if raw[index] == 0x5C and index < size - 2:
            index += 1
        if raw[index] == 0x5F:
            uscore2 += 1
        elif raw[index] == 0x2E:
            uscore1 = uscore2
            uscore2 = 0
            dots += 1
        elif raw[index] != 0x2D and not _oracle_host_byte(raw, index):
            break
        index += 1
    if (uscore1 or uscore2) and dots <= 10:
        return None
    if not allow_short and not dots:
        return None
    return len(raw[:index].decode("utf-8", errors="ignore"))


def _oracle_scan_end(text: str, start: int) -> int:
    """Extend until cmark_isspace or '<'."""
    end = start
    size = len(text)
    while end < size and text[end] not in _ORACLE_SPACE and text[end] != "<":
        end += 1
    return end


def _oracle_delim(text: str) -> int:
    """autolink_delim: trim ?!.,:*_~'\", a trailing &name;, and an unbalanced ')'."""
    link_end = len(text)
    opening = 0
    closing = 0
    for index, ch in enumerate(text):
        if ch == "<":
            link_end = index
            break
        if ch == "(":
            opening += 1
        elif ch == ")":
            closing += 1
    while link_end > 0:
        ch = text[link_end - 1]
        if ch == ")":
            if closing <= opening:
                return link_end
            closing -= 1
            link_end -= 1
        elif ch in _ORACLE_TRIM:
            link_end -= 1
        elif ch == ";":
            new_end = link_end - 2
            while new_end > 0 and text[new_end].isascii() and text[new_end].isalpha():
                new_end -= 1
            if new_end < link_end - 2 and text[new_end] == "&":
                link_end = new_end
            else:
                link_end -= 1
        else:
            return link_end
    return link_end


def _oracle_url(line: str, colon: int) -> tuple[int, int] | None:
    """url_match. Scheme is http, https or ftp, any case, then a valid host."""
    if not line.startswith("://", colon):
        return None
    scheme = colon
    while scheme > 0 and line[scheme - 1].isascii() and line[scheme - 1].isalpha():
        scheme -= 1
    if line[scheme:colon].lower() not in _ORACLE_SCHEMES:
        return None
    host = colon + 3
    if host >= len(line) or not _oracle_host_char(line[host]):
        return None
    domain_at = _oracle_domain(line[host:], allow_short=True)
    if domain_at is None:
        return None
    return scheme, _oracle_scan_end(line, host + domain_at)


def _oracle_www(line: str, index: int) -> tuple[int, int] | None:
    """www_match. 'www.' is lowercase and only after the start, cmark_isspace, or *_~(."""
    if not line.startswith("www.", index):
        return None
    if index and line[index - 1] not in _ORACLE_BEFORE and line[index - 1] not in _ORACLE_SPACE:
        return None
    domain_at = _oracle_domain(line[index:], allow_short=False)
    if domain_at is None:
        return None
    return index, _oracle_scan_end(line, index + domain_at)


def _oracle_links(line: str) -> list[tuple[int, int, int]]:
    """(start, kept_end, scan_end) for each extended autolink, in order."""
    found: list[tuple[int, int, int]] = []
    index = 0
    size = len(line)
    while index < size:
        span = None
        if line[index] == ":":
            span = _oracle_url(line, index)
        elif line[index] == "w":
            span = _oracle_www(line, index)
        if span is None:
            index += 1
            continue
        start, scan_end = span
        kept_rel = _oracle_delim(line[start:scan_end])
        if kept_rel == 0:
            index += 1
            continue
        found.append((start, start + kept_rel, scan_end))
        index = scan_end
    return found


def _oracle_angle_safe(span: str) -> bool:
    """The escaper's angle brackets are only safe with no whitespace, brackets or controls."""
    if not span:
        return False
    for ch in span:
        if ch in "<>" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127:
            return False
    return True


def _tail_has_named_entity(tail: str) -> bool:
    """A cmark trailing entity: '&' + ASCII letters + ';'."""
    start = 0
    while True:
        amp = tail.find("&", start)
        if amp < 0:
            return False
        end = amp + 1
        while end < len(tail) and tail[end].isascii() and tail[end].isalpha():
            end += 1
        if end > amp + 1 and end < len(tail) and tail[end] == ";":
            return True
        start = amp + 1


def _oracle_expectation(message: str) -> tuple[list[str], bool, bool]:
    """Hrefs cmark keeps, plus the N1 and N3 predicates.

    A trimmed tail that contains ``*``, ``_`` or ``~`` is written as an angle
    autolink when the kept span is a safe scheme address, and is not linked
    otherwise (``www.`` has no scheme; a space, bracket or control cannot sit
    inside ``<...>``). Either way the visible characters stay the raw input.
    An unshielded span is linked, and a ``www.`` href gains cmark's ``http://``.

    N1: that unshielded span's scan is immediately followed by ``<``.
    N3: that unshielded trimmed tail contains a named entity.
    """
    hrefs: list[str] = []
    n1 = False
    n3 = False
    for start, kept_end, scan_end in _oracle_links(message):
        kept = message[start:kept_end]
        tail = message[kept_end:scan_end]
        marks = any(ch in _ORACLE_MARKS for ch in tail)
        scheme = kept.lower().startswith(("http://", "https://", "ftp://"))
        if marks:
            if scheme and _oracle_angle_safe(kept):
                hrefs.append(kept)
            continue
        # www_match always prefixes http://, including when delim trims the dot
        # off ``www.?`` and the kept text is ``www`` (href ``http://www``).
        hrefs.append(("http://" + kept) if message.startswith("www.", start) else kept)
        if scan_end < len(message) and message[scan_end] == "<":
            n1 = True
        if _tail_has_named_entity(tail):
            n3 = True
    return hrefs, n1, n3


def _fuzz_messages(count: int) -> list[str]:
    """Seeded corpus. Exemplars are first, so a short run still covers every class.

    ``<`` is not in the random mark alphabet. A small slice of lines places it
    directly after an address so N1 stays under 5% and the rest get the exact check.
    ``@`` is absent on purpose: email, ``mailto:`` and ``xmpp:`` autolinks are not
    modeled, so the corpus never asks the oracle to predict them.
    """
    exemplars = [
        "x\u00a0www.e.com/~~PASSED~~x y",
        "x\u2003www.e.com/*PASSED*x y",
        "x\u2028https://e.com/a~ y",
        "x\u2029https://e.com/a_ y",
        "x\u3000www.e.com/a*b",
        "a\thttps://e.com/a_b\nb",
        "https://a.com/https://b.com",
        "www.a.comhttps://b",
        "see www.e.com/a~https://e.com/b*c~ end",
        "https://e.com/a&lt;b~",
        "https://e.com/?a=1&amp;b=2~",
        "https://e.com/&#x3C;script&#x3E;",
        "https://e.com/a\x01b~",
        "https://e.com/a\x7fb~",
        "https://e.com/a\x85b",
        "https://e.com/a~",
        "https://e.com/a*",
        "https://e.com/a_",
        "https://e.com/a**",
        "https://e.com/a~~",
        "https://e.com/a?*",
        "https://e.com/a.*",
        "https://e.com/a,~",
        "https://e.com/a)*",
        "https://e.com/a?_",
        "https://e.com/a.~",
        "https://e.com/a,_",
        "https://e.com/a_(b)",
        "https://e.com/a_(b))",
        "https://e.com/a_(b))*",
        "ftp://files.example/a?~",
        "https://e.com/&copy;",
        "https://e.com/a?&copy;",
        "https://e.com/a.&amp;",
        "https://e.com/a,&copy;",
        "https://e.com/a)&amp;",
        "https://e.com/a?&copy; end",
        "www.e.com/a?&amp;",
        "see https://e.com/~. the robot PASSED https://e.com/.~ ok",
        "see https://e.com/_? hidden words https://e.com/?_ end",
        "https://e.com/a<b>",
        "https://e.com/a><b>no</b>",
        "HTTPS://E.COM/a_b",
        "ftp://files.example/a_b~",
        "www.e.com/a_b",
        "www.?",
        "note * _ ~ [ ] ( ) \\ ` ! # https://e.com/a",
        "see https://x\\_y.com/*PASSED*x end",
        "see https://e.co\\_m/~~PASSED~~x end",
        "see https://a_b.\u00fc.c.d/*PASSED*x end",
        "see https://a_b.\u4f8b.c.d/~~PASSED~~x end",
        "see www.a_b.\u00fc.c.d/*PASSED*x end",
        "see https://a_b.\u00fc.c.d/`code` end",
        "https://a.b.c.d.e.f.g.h.i.j.k.l_m.com/x_y",
    ]
    rng = random.Random(230)
    spaces = [" ", "\t", "\n", "\u00a0", "\u2003", "\u2028", "\u2029", "\u3000"]
    schemes = ["https://", "http://", "HTTPS://", "ftp://", "www."]
    hosts = [
        "e.com",
        "a.com",
        "b.com",
        "files.example",
        "E.COM",
        "x_y.com",
        "x\\_y.com",
        "e\\.com",
        "\u00fc.com",
        "\u4f8b.com",
        "\u00e9_f.com",
        "a\\*.com",
        "a.b.c.d.e.f.g.h.i.j.k.l_m.com",
    ]
    marks = list("?!.,:*_~'\"()")
    extras = [
        "&copy;",
        "&lt;",
        "&amp;",
        "&#60;",
        "&#x3C;",
        "\x01",
        "\x7f",
        "\x85",
        "`code`",
        "*b*",
        "[a](b)",
    ]
    words = ["PASSED", "hidden", "robot", "x", "end"]
    out = list(exemplars)
    while len(out) < count:
        roll = rng.random()
        scheme = rng.choice(schemes)
        host = rng.choice(hosts)
        if roll < 0.02:
            out.append(f"see {scheme}{host}/a{rng.choice(marks)}<b> end")
        elif roll < 0.06:
            punct = rng.choice("?.,)")
            name = rng.choice(("copy", "amp"))
            out.append(f"see {scheme}{host}/a{punct}&{name}; end")
        else:
            space = rng.choice(spaces)
            left = (
                scheme + host + "/" + "".join(rng.choice(marks) for _ in range(rng.randint(0, 3)))
            )
            right = rng.choice(schemes) + rng.choice(hosts)
            out.append(
                "see"
                + space
                + left
                + rng.choice(extras)
                + rng.choice(words)
                + right
                + rng.choice(marks)
                + " end"
            )
    return out[:count]


def _visible(parsed: _Rendered) -> str:
    return "".join(parsed.text).strip("\n")


def _hrefs(parsed: _Rendered) -> list[str]:
    return [unquote(attrs["href"]) for tag, attrs in parsed.tags if tag == "a"]


def test_fuzz_rendered_text_and_hrefs_match_the_oracle():
    """Render each line and compare it with the independent oracle.

    Exact visible text and exact hrefs, except two raw-input predicates, which
    get safety checks only. N1 is an unshielded link whose scan is immediately
    followed by ``<``. N3 is an unshielded trimmed tail that contains ``&name;``.
    """
    count = int(os.environ.get("CV_FUZZ_CASES", "4000"))
    messages = _fuzz_messages(count)
    n1_count = 0
    n3_count = 0
    excluded = 0
    failures = []
    for message in messages:
        expected, n1, n3 = _oracle_expectation(message)
        if n1:
            n1_count += 1
        if n3:
            n3_count += 1
        parsed = _rendered(_md_escape(message))
        tags = [tag for tag, _ in parsed.tags]
        if any(tag not in {"p", "a"} for tag in tags):
            failures.append(("element", message, tags))
        elif n1 or n3:
            excluded += 1
            got = _hrefs(parsed)
            if len(got) != len(expected) or any(
                not href.startswith(address) for href, address in zip(got, expected, strict=True)
            ):
                failures.append(("safety-href", message, got, expected, n1, n3))
        else:
            visible = _visible(parsed)
            got = _hrefs(parsed)
            if visible != message:
                failures.append(("text", message, visible, expected))
            elif got != expected:
                failures.append(("href", message, got, expected))
        if len(failures) >= 5:
            break
    if count >= 80:
        # The explicit exemplars make N3 real, and N1 stays a small slice.
        assert n3_count >= 8, n3_count
        assert 0 < n1_count <= count * 0.05, (n1_count, count)
    assert not failures, (failures, n1_count, n3_count, excluded)


def _host_fuzz_messages(count: int) -> list[str]:
    """Host-focused lines. ``@`` is omitted; email autolinks are not in this set."""
    rng = random.Random(2330)
    labels = [
        "a",
        "b_c",
        "x",
        "ü",
        "例",
        "é_f",
        "\\_",
        "a\\_b",
        "e\\.",
        "d",
        "com",
        "c_d",
        "\\*",
        "ö",
    ]
    schemes = ["https://", "http://", "ftp://", "www."]
    paths = ["/", "/a", ""]
    tails = ["*PASSED*x", "~~PASSED~~x", "`code`", "_i_x", " *b* y", ""]
    messages = []
    for _ in range(count):
        host = ".".join(rng.choice(labels) for _ in range(rng.randint(1, 4)))
        messages.append(
            "see " + rng.choice(schemes) + host + rng.choice(paths) + rng.choice(tails) + " end"
        )
    return messages


def test_host_fuzz_has_no_markup_and_keeps_the_text():
    """Random hosts with underscores, backslashes and non-ASCII labels.

    CI uses ``CV_HOST_FUZZ_CASES`` (default 2000). The local gate is 20000.
    """
    count = int(os.environ.get("CV_HOST_FUZZ_CASES", "2000"))
    failures = []
    for message in _host_fuzz_messages(count):
        parsed = _rendered(_md_escape(message))
        tags = [tag for tag, _ in parsed.tags]
        if any(tag not in {"p", "a"} for tag in tags):
            failures.append(("element", message, tags))
        elif _visible(parsed) != message:
            failures.append(("text", message, _visible(parsed)))
        if len(failures) >= 5:
            break
    assert not failures, failures


def test_the_inlined_collector_matches_collect_py():
    workflow = (ROOT / ".github/workflows/scenario.yml").read_text(encoding="utf-8")
    start = workflow.index("<<'COLLECT'\n") + len("<<'COLLECT'\n")
    end = workflow.index("\n          COLLECT\n", start)
    indent = "          "
    extracted = "\n".join(line.removeprefix(indent) for line in workflow[start:end].splitlines())
    source = (ROOT / "src/capability_verifier/collect.py").read_text(encoding="utf-8")
    assert extracted + "\n" == source
