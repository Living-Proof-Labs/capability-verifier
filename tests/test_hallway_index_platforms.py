# SPDX-License-Identifier: Apache-2.0
""".github/scripts/hallway_index_platforms.py: only real platforms count, exactly amd64 + arm64."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "hallway_index_platforms.py"
FIXTURE = ROOT / "tests" / "fixtures" / "hallway" / "index-1.15.0.json"

spec = importlib.util.spec_from_file_location("hallway_index_platforms", SCRIPT)
hip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hip)


@pytest.fixture
def index():
    """The real 1.15.0 index: amd64 + arm64 + two unknown/unknown attestation manifests."""
    return json.loads(FIXTURE.read_text())


def run(payload):
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=payload, capture_output=True, text=True, check=False
    )


def test_real_1_15_0_index_passes_ignoring_attestations(index):
    assert len(index["manifests"]) == 4
    assert hip.platforms(index) == ["linux/amd64", "linux/arm64"]
    result = run(json.dumps(index))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "linux/amd64 + linux/arm64"


def test_old_unfiltered_check_would_fail(index):
    """The 1.15.0 bug: counting every entry sees unknown/unknown twice."""
    every = sorted(
        f"{m['platform']['os']}/{m['platform']['architecture']}" for m in index["manifests"]
    )
    assert every != ["linux/amd64", "linux/arm64"]


def test_attestations_are_kept_not_rejected(index):
    attestations = [m for m in index["manifests"] if hip.is_attestation(m)]
    assert len(attestations) == 2


@pytest.mark.parametrize("drop", ["amd64", "arm64"])
def test_missing_arch_fails(index, drop):
    index["manifests"] = [m for m in index["manifests"] if m["platform"]["architecture"] != drop]
    result = run(json.dumps(index))
    assert result.returncode == 1
    assert "expected exactly" in result.stderr


def test_extra_platform_fails(index):
    extra = copy.deepcopy(index["manifests"][0])
    extra["platform"] = {"os": "linux", "architecture": "arm", "variant": "v7"}
    index["manifests"].append(extra)
    assert run(json.dumps(index)).returncode == 1


def test_duplicate_platform_fails(index):
    index["manifests"].append(copy.deepcopy(index["manifests"][0]))
    assert run(json.dumps(index)).returncode == 1


def test_unannotated_unknown_is_ignored_but_annotated_attestation_too(index):
    index["manifests"][1]["annotations"] = {}
    index["manifests"][3]["platform"] = {"os": "linux", "architecture": "arm64"}
    assert hip.platforms(index) == ["linux/amd64", "linux/arm64"]


@pytest.mark.parametrize("payload", ["", "not json", "[]", '{"manifests": []}'])
def test_bad_input_fails_closed(payload):
    assert run(payload).returncode == 1
