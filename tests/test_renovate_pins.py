# SPDX-License-Identifier: Apache-2.0
"""Renovate package rules skip two intentional pins and leave ordinary updates enabled.

The runtime image has no Node, so this does not execute Renovate. It checks the
rules' match fields against the files and package names Renovate extracts.
Renovate rewrites a uv git source's packageName to the GitHub repository while
depName stays the project name, so the fixture rule matches both.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FIXTURE_FILE = "tests/fixtures/repin/pyproject.toml"
FIXTURE_LOCK = "tests/fixtures/repin/verification.lock.json"
FIXTURE_DEP = "livingproof-sim-mujoco"
OTHER_FIXTURE_FILE = "tests/fixtures/thin-robot/pyproject.toml"
REPORTER_FILE = ".github/workflows/verify.yml"
REPORTER_IMAGE = "ghcr.io/living-proof-labs/livingproof-capability-report"
LEGACY_TAG = "1.6.0"
RUNTIME_FILE = "pyproject.toml"
RUNTIME_PACKAGE = "pyyaml"
SAME_FILE_PACKAGE = "actions/checkout"
RUNTIME_REPORTER = "livingproof-capability-report"
PRESET = re.compile(
    r"github>Living-Proof-Labs/collaboration-guidelines:renovate-preset#v[0-9]+\.[0-9]+\.[0-9]+"
)
GIT_SOURCE = re.compile(r'livingproof-sim-mujoco = \{ git = "https://github.com/([^"]+?)\.git"')


def config():
    return json.loads((ROOT / "renovate.json").read_text())


def disabled_rules():
    rules = [rule for rule in config()["packageRules"] if rule.get("enabled") is False]
    assert len(rules) == len(config()["packageRules"]) == 2
    for rule in rules:
        assert rule["matchFileNames"]
        assert rule["matchPackageNames"]
    return rules


def rule_for(file_name):
    found = [rule for rule in disabled_rules() if rule["matchFileNames"] == [file_name]]
    assert len(found) == 1
    return found[0]


def names(rule):
    packages = set(rule["matchPackageNames"])
    packages.update(rule.get("matchDepNames", []))
    return packages


def test_disabled_rules_name_only_the_two_intentional_pins():
    # Permit reviewed release upgrades, but never floating branches or other presets.
    presets = config()["extends"]
    assert len(presets) == 1
    assert PRESET.fullmatch(presets[0])
    assert "renovate-config" in config()["enabledManagers"]
    fixture = rule_for(FIXTURE_FILE)
    reporter = rule_for(REPORTER_FILE)
    source = GIT_SOURCE.search((ROOT / FIXTURE_FILE).read_text())
    assert source is not None
    assert fixture["matchDepNames"] == [FIXTURE_DEP]
    assert fixture["matchPackageNames"] == [source.group(1)]
    assert reporter["matchPackageNames"] == [REPORTER_IMAGE]
    assert "matchDepNames" not in reporter
    assert "matchCurrentValue" not in fixture
    assert "matchCurrentValue" not in reporter


def test_fixture_pin_is_the_repin_file_and_the_thin_robot_pin_stays_enabled():
    rule = rule_for(FIXTURE_FILE)
    fixture = (ROOT / FIXTURE_FILE).read_text()
    lock = json.loads((ROOT / FIXTURE_LOCK).read_text())
    runner = lock["releases"]["runner"]
    assert runner["repository"] == rule["matchPackageNames"][0]
    assert f'tag = "{runner["tag"]}"' in fixture
    assert f"{FIXTURE_DEP}[torch]" in fixture
    assert '"mujoco==' in fixture
    assert "mujoco" not in names(rule)

    other = (ROOT / OTHER_FIXTURE_FILE).read_text()
    assert f"{FIXTURE_DEP} =" in other
    assert all(OTHER_FIXTURE_FILE not in rule["matchFileNames"] for rule in disabled_rules())


def test_legacy_reporter_image_is_only_the_verify_workflow_pin():
    rule = rule_for(REPORTER_FILE)
    workflow = (ROOT / REPORTER_FILE).read_text()
    legacy = f"image: {REPORTER_IMAGE}:{LEGACY_TAG}"
    assert legacy in workflow
    assert REPORTER_IMAGE in names(rule)
    assert SAME_FILE_PACKAGE not in names(rule)
    assert "uses: actions/checkout@" in workflow

    tagged = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for line in path.read_text().splitlines():
            if f"{REPORTER_IMAGE}:" in line:
                tagged.append((path.relative_to(ROOT).as_posix(), line.strip()))
    assert tagged == [(REPORTER_FILE, legacy)]


def test_runtime_dependencies_are_not_disabled():
    runtime = (ROOT / RUNTIME_FILE).read_text()
    assert f'"{RUNTIME_PACKAGE}>=' in runtime
    assert "livingproof-capability-report @ " in runtime
    disabled = set()
    for rule in disabled_rules():
        disabled.update(names(rule))
    assert RUNTIME_PACKAGE not in disabled
    assert RUNTIME_REPORTER not in disabled
    assert all(RUNTIME_FILE not in rule["matchFileNames"] for rule in disabled_rules())
