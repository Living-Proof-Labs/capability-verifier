# SPDX-License-Identifier: Apache-2.0
"""docs-brand.lock.json names a published release whose tag, commit and brand.css bytes all agree.

Proposed by Vera in the capability-verifier#72 review (guidelines bump checklist item 4). tools/docs.py
fetches brand.css by commit and checks only its sha256, so a lock whose `version` moved without its
commit and sha256 (a version-only update) still builds. This is the Python equivalent of
`lock.mjs --check` from collaboration-guidelines#68. Offline tests use a fake GitHub; LIVINGPROOF_NETWORK=1
checks the real release (set GITHUB_TOKEN to avoid the anonymous rate limit).
"""

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = "Living-Proof-Labs/collaboration-guidelines"
PATH = "brand/docs/brand.css"
API = f"https://api.github.com/repos/{REPO}"


def lock():
    return json.loads((ROOT / "docs-brand.lock.json").read_text())


def problems(pin, get):
    """Return the provenance problems for a docs brand lock; empty means it matches the release."""
    found = []
    if sorted(pin) != ["commit", "path", "repository", "sha256", "version"]:
        return [f"expected the five lock fields, got {sorted(pin)}"]
    if pin["repository"] != REPO or pin["path"] != PATH:
        found.append("lock must name the collaboration-guidelines brand.css")
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", pin["version"]):
        found.append(f"version {pin['version']} is not a full release tag")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]) or not re.fullmatch(
        r"[0-9a-f]{64}", pin["sha256"]
    ):
        found.append("commit must be 40 and sha256 64 lowercase hex characters")
    if found:
        return found
    release = json.loads(get(f"{API}/releases/tags/{pin['version']}"))
    if (
        release.get("tag_name") != pin["version"]
        or release.get("draft")
        or release.get("prerelease")
    ):
        found.append(f"{pin['version']} is not a published stable release")
    commit = json.loads(get(f"{API}/commits/{pin['version']}"))["sha"]
    if commit != pin["commit"]:
        found.append(f"{pin['version']} is commit {commit}, lock says {pin['commit']}")
    digest = hashlib.sha256(
        get(f"https://raw.githubusercontent.com/{REPO}/{commit}/{PATH}")
    ).hexdigest()
    if digest != pin["sha256"]:
        found.append(
            f"brand.css at {pin['version']} has sha256 {digest}, lock says {pin['sha256']}"
        )
    return found


V740 = ("v7.4.0", "613e3d97bbb1f00836d88b25c380b9eafdcfb15a", b"/* brand v7.4.0 */")
V920 = ("v9.2.0", "8c8155ec9db30dbe19ce5c2a71cf10f3f71271b0", b"/* brand v9.2.0 */")


def fake_github(*releases, draft=()):
    pages = {}
    for tag, commit, css in releases:
        pages[f"{API}/releases/tags/{tag}"] = json.dumps(
            {"tag_name": tag, "draft": tag in draft, "prerelease": False}
        )
        pages[f"{API}/commits/{tag}"] = json.dumps({"sha": commit})
        pages[f"https://raw.githubusercontent.com/{REPO}/{commit}/{PATH}"] = css
    return lambda url: pages[url] if isinstance(pages[url], bytes) else pages[url].encode()


def pin_for(tag, commit, css):
    return {
        "repository": REPO,
        "version": tag,
        "commit": commit,
        "path": PATH,
        "sha256": hashlib.sha256(css).hexdigest(),
    }


def test_committed_lock_has_the_five_fields_and_spellings():
    pin = lock()
    assert sorted(pin) == ["commit", "path", "repository", "sha256", "version"]
    assert pin["repository"] == REPO and pin["path"] == PATH
    assert re.fullmatch(r"v\d+\.\d+\.\d+", pin["version"])
    assert re.fullmatch(r"[0-9a-f]{40}", pin["commit"]) and re.fullmatch(
        r"[0-9a-f]{64}", pin["sha256"]
    )


def test_a_regenerated_lock_passes():
    assert problems(pin_for(*V920), fake_github(V740, V920)) == []


def test_a_version_only_update_is_refused():
    pin = pin_for(*V740) | {"version": "v9.2.0"}
    found = problems(pin, fake_github(V740, V920))
    assert any("is commit 8c8155e" in p for p in found), found
    assert any("brand.css at v9.2.0" in p for p in found), found


def test_a_draft_release_is_refused():
    found = problems(pin_for(*V920), fake_github(V920, draft={"v9.2.0"}))
    assert found == ["v9.2.0 is not a published stable release"]


def test_a_short_tag_is_refused_before_any_request():
    def offline(url):
        raise AssertionError(f"unexpected request {url}")

    assert problems(pin_for(*V920) | {"version": "v9.2"}, offline) == [
        "version v9.2 is not a full release tag"
    ]


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_committed_lock_matches_the_real_release():
    token = os.environ.get("GITHUB_TOKEN")

    def get(url):
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"} if token else {}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    assert problems(lock(), get) == []
