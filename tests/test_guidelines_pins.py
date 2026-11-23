# SPDX-License-Identifier: Apache-2.0
"""collaboration-guidelines bumps move every consumer pin together and keep the release interface.

Proposed by Vera in the capability-verifier#72 review (guidelines bump checklist items 2, 3 and 6).
Offline: the shared release interface is recorded here and checked against the workflow call. When a
guidelines release changes `semantic-release.yml` inputs or outputs, update SHARED_RELEASE_* in the
same PR, after reading the diff. LIVINGPROOF_NETWORK=1 also compares them with the pinned tag.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/verify.yml"
REPO = "Living-Proof-Labs/collaboration-guidelines"
TAG = r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
USES = re.compile(rf"^{REPO}/\.github/workflows/(?P<file>[\w.-]+\.ya?ml)@(?P<ref>.+)$")
PRESET = re.compile(rf"^github>{REPO}:renovate-preset#(?P<ref>.+)$")
# The stable writer interface of the pinned shared release.
SHARED_RELEASE_INPUTS = {"dry-run", "draft-release", "expected-version", "expected-commit"}
SHARED_RELEASE_OUTPUTS = {
    "draft-created",
    "published",
    "version",
    "git-tag",
    "commit",
    "release-url",
}


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def guideline_calls(doc=None):
    calls = {}
    for name, job in (doc or workflow())["jobs"].items():
        match = USES.match(str(job.get("uses", "")))
        if match:
            calls[name] = match
    return calls


def preset_refs(config=None):
    config = config or json.loads((ROOT / "renovate.json").read_text())
    return [m.group("ref") for m in map(PRESET.match, config.get("extends", [])) if m]


def pinned_versions(doc=None, config=None):
    return {m.group("ref") for m in guideline_calls(doc).values()} | set(preset_refs(config))


def test_every_guidelines_pin_is_a_full_release_tag():
    refs = pinned_versions()
    assert refs, "expected the release workflow call and the Renovate preset"
    for ref in refs:
        assert re.fullmatch(TAG, ref), f"{ref} is not a full vX.Y.Z release tag"


def test_workflow_call_and_renovate_preset_name_one_guidelines_release():
    # A partial bump (one pin moved, the other left behind) fails here.
    assert len(pinned_versions()) == 1, sorted(pinned_versions())


def test_mismatched_pins_are_reported():
    doc = workflow()
    doc["jobs"]["release"]["uses"] = f"{REPO}/.github/workflows/semantic-release.yml@v2.2.0"
    assert len(pinned_versions(doc, {"extends": [f"github>{REPO}:renovate-preset#v9.2.0"]})) == 2


def test_release_call_uses_only_the_shared_interface():
    doc = workflow()
    calls = guideline_calls(doc)
    assert set(calls) == {"release"}, sorted(calls)
    job = doc["jobs"]["release"]
    assert calls["release"].group("file") == "semantic-release.yml"
    assert set(job.get("with", {})) <= SHARED_RELEASE_INPUTS
    assert job["with"]["draft-release"] is True
    assert job["permissions"] == {"contents": "write"}
    used = set(re.findall(r"needs\.release\.outputs\.([\w-]+)", WORKFLOW.read_text()))
    assert used, "release-reports reads the shared outputs"
    assert used <= SHARED_RELEASE_OUTPUTS, sorted(used - SHARED_RELEASE_OUTPUTS)


def test_an_unknown_release_output_is_reported():
    text = WORKFLOW.read_text().replace(
        "needs.release.outputs.git-tag", "needs.release.outputs.tag", 1
    )
    used = set(re.findall(r"needs\.release\.outputs\.([\w-]+)", text))
    assert used - SHARED_RELEASE_OUTPUTS == {"tag"}


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_recorded_interface_matches_the_pinned_shared_workflow():
    (ref,) = pinned_versions()
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/.github/workflows/semantic-release.yml"
    with urllib.request.urlopen(url, timeout=30) as response:
        shared = yaml.safe_load(response.read())
    call = shared[True]["workflow_call"]  # YAML 1.1 reads the `on` key as True
    assert set(call["inputs"]) == SHARED_RELEASE_INPUTS
    assert set(call["outputs"]) == SHARED_RELEASE_OUTPUTS
