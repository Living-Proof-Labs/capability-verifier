# SPDX-License-Identifier: Apache-2.0
"""The pinned collaboration-guidelines preset keeps docs-brand updates off by default.

v9.4.0 added a custom manager for docs-brand.lock.json and a packageRule that
disables that dep type until a repo opts in, after installing the trusted
post-upgrade command and provenance CI check. Proposed by Vera in the
capability-verifier#98 review. Set LIVINGPROOF_NETWORK=1 to fetch the preset.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from test_guidelines_pins import REPO, pinned_versions


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_the_pinned_preset_disables_docs_brand_updates_by_default():
    (ref,) = pinned_versions()
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/renovate-preset.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        preset = json.loads(response.read().decode())
    rules = [
        rule
        for rule in preset.get("packageRules") or []
        if "docs-brand" in (rule.get("matchDepTypes") or [])
    ]
    assert len(rules) == 1, rules
    assert rules[0]["enabled"] is False
    assert rules[0]["automerge"] is False
    managers = [
        manager
        for manager in preset.get("customManagers") or []
        if manager.get("depTypeTemplate") == "docs-brand"
    ]
    assert len(managers) == 1, managers
    assert managers[0]["managerFilePatterns"] == ["/^docs-brand\\.lock\\.json$/"]
