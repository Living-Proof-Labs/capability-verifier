# SPDX-License-Identifier: Apache-2.0
"""The hallway devcontainer pins a full X.Y.Z tag and that tag's multi-arch index digest.

Proposed by Vera in the capability-verifier#68 review: Renovate once pinned the linux/amd64
child digest instead of the index. LIVINGPROOF_NETWORK=1 compares the pin with GHCR.
"""

import json
import os
import re
from pathlib import Path

import pytest

from capability_verifier.repin import image_digest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/living-proof-labs/capability-verifier-hallway"
PIN = re.compile(rf"^{re.escape(IMAGE)}:(?P<tag>\d+\.\d+\.\d+)@(?P<digest>sha256:[0-9a-f]{{64}})$")


def pinned():
    image = json.loads((ROOT / ".devcontainer/hallway/devcontainer.json").read_text())["image"]
    match = PIN.match(image)
    assert match, f"{image} is not {IMAGE}:X.Y.Z@sha256:<64 hex>"
    return match


def test_the_devcontainer_pins_a_full_tag_and_digest():
    pinned()


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_the_pinned_digest_is_the_tag_index():
    match = pinned()
    assert image_digest(IMAGE, match["tag"]) == match["digest"]
