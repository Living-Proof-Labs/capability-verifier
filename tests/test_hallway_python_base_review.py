# SPDX-License-Identifier: Apache-2.0
"""The hallway stage's python base is a digest-pinned official multi-arch index.

Proposed by Vera in the capability-verifier#107 review. Offline: the hallway stage pins
python:<X.Y>-slim-bookworm by a sha256 digest. LIVINGPROOF_NETWORK=1: that digest is an OCI
index in Docker Hub's library/python with linux/amd64 and linux/arm64 images, and both report
the tag's Python X.Y. It does not freeze the digest or the patch version, so Renovate digest
bumps keep passing.
"""

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FROM = re.compile(
    r"^FROM python:(?P<tag>(?P<minor>3\.\d+)-slim-bookworm)@(?P<digest>sha256:[0-9a-f]{64}) AS hallway$",
    re.MULTILINE,
)
REGISTRY = "https://registry-1.docker.io/v2/library/python"
INDEX = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"


def hallway_base():
    match = FROM.search((ROOT / "Dockerfile").read_text())
    assert match, (
        "the hallway stage must be FROM python:<X.Y>-slim-bookworm@sha256:<64 hex> AS hallway"
    )
    return match


def get(url, token, accept=None):
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=60
    ) as response:
        return json.load(response)


def test_the_hallway_python_base_is_digest_pinned():
    hallway_base()


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_the_pinned_digest_is_an_official_amd64_and_arm64_index():
    match = hallway_base()
    auth = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull"
    with urllib.request.urlopen(auth, timeout=60) as response:
        token = json.load(response)["token"]
    index = get(f"{REGISTRY}/manifests/{match['digest']}", token, INDEX)
    platforms = {
        m["platform"]["architecture"]: m["digest"]
        for m in index["manifests"]
        if m.get("platform", {}).get("os") == "linux"
    }
    assert {"amd64", "arm64"} <= platforms.keys()
    for architecture in ("amd64", "arm64"):
        manifest = get(f"{REGISTRY}/manifests/{platforms[architecture]}", token, MANIFEST)
        config = get(f"{REGISTRY}/blobs/{manifest['config']['digest']}", token)
        assert config["architecture"] == architecture
        version = dict(e.split("=", 1) for e in config["config"]["Env"])["PYTHON_VERSION"]
        assert version.startswith(match["minor"] + "."), (architecture, version, match["tag"])
