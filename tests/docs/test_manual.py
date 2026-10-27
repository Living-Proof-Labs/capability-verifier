# SPDX-License-Identifier: Apache-2.0
import json
import os
import threading
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from playwright.sync_api import sync_playwright

SITE = Path(__file__).resolve().parents[2] / "artifacts/docs/html"


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.ids = [], set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])


def test_all_local_pages_assets_and_fragments():
    pages = {p: Links(p.read_text()) for p in SITE.rglob("*.html")}
    assert len(pages) >= 7
    for page, parsed in pages.items():
        for link in parsed.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            path = unquote(url.path)
            if path.startswith("/capability-verifier/"):
                target = SITE / path.removeprefix("/capability-verifier/")
            else:
                target = (page.parent / path).resolve() if path else page
            if target.is_dir():
                target /= "index.html"
            assert target.is_relative_to(SITE.resolve()), (page, link)
            assert target.is_file(), (page, link)
            if url.fragment and target in pages:
                assert unquote(url.fragment) in pages[target].ids, (page, link)


def test_version_and_real_results():
    info = json.loads((SITE / "build.json").read_text())
    results = json.loads((SITE / "feature-results.json").read_text())
    assert len(info["commit"]) == 40
    assert len(results) == info["collected"] > 5
    assert len([r for r in results if r["id"].startswith("tests.test_reference::")]) == 5
    assert {r["status"] for r in results} <= {"passed", "failed", "skipped"}
    assert "not robot safety certification" in (SITE / "index.html").read_text()


@pytest.fixture(scope="module")
def browser_page():
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            if path.startswith("/capability-verifier/"):
                path = path[len("/capability-verifier") :]
            return super().translate_path(path)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(SITE)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as engine:
        options = {"headless": True}
        if os.environ.get("DOCS_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["DOCS_BROWSER_EXECUTABLE"]
        browser = engine.chromium.launch(**options)
        page = browser.new_page()
        page.set_default_timeout(10000)
        yield page, f"http://127.0.0.1:{server.server_port}/capability-verifier/"
        browser.close()
    server.shutdown()
    thread.join()


def test_navigation_search_and_assets(browser_page):
    page, base = browser_page
    failed = []
    page.on(
        "response", lambda response: failed.append(response.url) if response.status >= 400 else None
    )
    page.goto(base)
    page.get_by_role("link", name="Scenarios and results", exact=True).first.click()
    page.wait_for_url("**/scenarios.html")
    assert page.locator("h1").inner_text().rstrip("¶") == "Scenarios and results"
    page.goto(base)
    page.locator(".md-search__button").click()
    search = page.get_by_role("combobox", name="Search")
    search.fill("provenance")
    result = page.locator("a[role=option]").first
    result.wait_for(state="visible")
    result.click()
    page.wait_for_timeout(300)
    assert not failed


def test_themes_and_mobile_navigation(browser_page):
    page, base = browser_page
    page.goto(base)
    page.locator('label[title="Switch to light mode"]').click()
    page.locator('label[title="Switch to dark mode"]').click()
    assert page.locator("body").get_attribute("data-md-color-scheme") == "slate"
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base)
    page.locator('label.md-header__button[for="__drawer"]').first.click()
    page.get_by_role("link", name="Task reference", exact=True).first.click()
    page.wait_for_url("**/tasks.html")
    assert page.locator("h1").inner_text().rstrip("¶") == "Task reference"
    page.screenshot(path=str(SITE.parent / "mobile-manual.png"), full_page=True)
