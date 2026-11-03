# SPDX-License-Identifier: Apache-2.0
"""The manual renders the pinned organization brand, not Zensical's stock palette."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "artifacts/docs/html"


def test_pinned_brand_is_shipped():
    pin = json.loads((ROOT / "docs-brand.lock.json").read_text())
    assert hashlib.sha256((SITE / "assets/brand.css").read_bytes()).hexdigest() == pin["sha256"]


def test_every_page_loads_brand_and_no_default_palette():
    pages = list(SITE.rglob("*.html"))
    assert len(pages) >= 7
    for page in pages:
        html = page.read_text()
        assert "assets/brand.css" in html, page
        assert 'data-md-color-primary="indigo"' not in html, page
        assert 'data-md-color-primary="custom"' in html, page
