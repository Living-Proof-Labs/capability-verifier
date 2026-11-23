# SPDX-License-Identifier: Apache-2.0
"""http(s) addresses in a refusal keep '_' '&' and '~' only when GFM auto-links them (#230).

Vera's review of PR 233: an ``https://`` run that GitHub will not auto-link must stay escaped
(emphasis, strike-through, entities), and ``<`` must be written ``&lt;`` so a tag stuck to an
address cannot become raw HTML in ``report.md``. ``report.txt`` is verbatim. ``report.html``
HTML-escapes the original line once.
"""

from __future__ import annotations

import html
import random
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest
from cmarkgfm.cmark import Options, markdown_to_html_with_extensions

from capability_verifier.collect import (
    _autolink_delim,
    _autolink_spans,
    _escape_trimmed_tail,
    _md_escape,
    _md_escape_text,
    _scheme_address,
    _span_can_be_angle_autolink,
    _tail_can_mark,
    place_integrity_lines,
)

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
        assert _scheme_address(inside)
        assert _span_can_be_angle_autolink(inside)
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


# cmark_isspace, matching collect.py: tab, LF, CR, space. Not VT, FF or NBSP.
_CMARK_SPACE_FOR_ORACLE = frozenset("\t\n\r ")


def _n1_suffix(kept: str, tail: str, message: str, end: int) -> tuple[str, str, int]:
    """Href and the visible run after an unshielded address that is directly before ``<``.

    The trimmed punctuation stays in front of ``&lt;``, so it is no longer trailing and
    cmark keeps it in the href. ``autolink_delim`` then trims a final ``&gt;`` or ``&lt;``
    and that entity is decoded in the text. An ``&lt;`` left inside the link is shown as
    the characters ``&lt;``.
    """
    stop = end
    while stop < len(message) and message[stop] not in _CMARK_SPACE_FOR_ORACLE:
        stop += 1
    emitted = (
        kept + _escape_trimmed_tail(tail, keep_ampersand=True) + _md_escape_text(message[end:stop])
    )
    link_end = _autolink_delim(emitted)
    outside = html.unescape(emitted[link_end:])
    # The visible address is the original kept span plus the original tail; only the
    # run that starts at '<' is rewritten, with a trimmed entity decoded.
    tail_out = _escape_trimmed_tail(tail, keep_ampersand=True)
    rewritten = emitted[len(kept) + len(tail_out) : link_end] + outside
    return emitted[:link_end], rewritten, stop


def _expected_visible_and_hrefs(message: str) -> tuple[str, list[str]]:
    """Oracle for one line. Two narrow exceptions, both from cmark, not from a skip.

    N1: an unshielded span immediately before ``<`` (see ``_n1_suffix``).
    N3: a named entity in an unshielded trimmed tail is decoded (``&copy;`` shows ``©``).
    """
    hrefs: list[str] = []
    parts: list[str] = []
    cursor = 0
    for start, end in _autolink_spans(message):
        kept_end = start + _autolink_delim(message[start:end])
        kept = message[start:kept_end]
        tail = message[kept_end:end]
        marks = _tail_can_mark(tail)
        angle = _scheme_address(kept) and _span_can_be_angle_autolink(kept)
        parts.append(message[cursor:start])
        parts.append(kept)
        if marks:
            parts.append(tail)
            if not kept.startswith("www.") and angle:
                hrefs.append(kept)
        else:
            decoded = html.unescape(tail)
            parts.append(decoded)
            href = ("http://" + kept) if kept.startswith("www.") else kept
            if end < len(message) and message[end] == "<":
                extra, shown, stop = _n1_suffix(kept, tail, message, end)
                href = ("http://" + extra) if kept.startswith("www.") else extra
                parts.append(shown)
                cursor = stop
                hrefs.append(href)
                continue
            hrefs.append(href)
        cursor = end
    parts.append(message[cursor:])
    return "".join(parts), hrefs


def _fuzz_messages(count: int) -> list[str]:
    """Seeded corpus. The fixed exemplars are always first, so a short CI run still
    covers every class; ``CV_FUZZ_CASES`` adds more of the same mix.
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
        "https://e.com/&copy;",
        "https://e.com/&#x3C;script&#x3E;",
        "https://e.com/a\x01b~",
        "https://e.com/a\x7fb~",
        "https://e.com/a\x85b",
        "https://e.com/a~",
        "https://e.com/a*",
        "https://e.com/a_",
        "https://e.com/a**",
        "https://e.com/a~~",
        "see https://e.com/~. the robot PASSED https://e.com/.~ ok",
        "https://e.com/a_(b)",
        "https://e.com/a_(b))",
        "https://e.com/a<b>",
        "https://e.com/a><b>no</b>",
        "HTTPS://E.COM/a_b",
        "ftp://files.example/a_b~",
        "www.e.com/a_b",
        "note * _ ~ [ ] ( ) \\ ` ! # https://e.com/a",
    ]
    rng = random.Random(230)
    spaces = [" ", "\t", "\n", "\u00a0", "\u2003", "\u2028", "\u2029", "\u3000"]
    schemes = ["https://", "http://", "HTTPS://", "ftp://", "www."]
    hosts = ["e.com", "a.com", "b.com", "files.example", "E.COM"]
    marks = list("?!.,:*_~'\"()<>")
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
        space = rng.choice(spaces)
        left = (
            rng.choice(schemes)
            + rng.choice(hosts)
            + "/"
            + "".join(rng.choice(marks) for _ in range(rng.randint(0, 3)))
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


def test_fuzz_rendered_text_and_hrefs_match_the_oracle():
    count = int(__import__("os").environ.get("CV_FUZZ_CASES", "4000"))
    failures = []
    for message in _fuzz_messages(count):
        parsed = _rendered(_md_escape(message))
        tags = [tag for tag, _ in parsed.tags]
        if any(tag not in {"p", "a"} for tag in tags):
            failures.append(("element", message, tags))
            continue
        visible = "".join(parsed.text).strip("\n")
        expected, hrefs = _expected_visible_and_hrefs(message)
        got = [unquote(attrs["href"]) for tag, attrs in parsed.tags if tag == "a"]
        if visible != expected:
            failures.append(("text", message, visible, expected))
        elif got != hrefs:
            failures.append(("href", message, got, hrefs))
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
