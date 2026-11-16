# SPDX-License-Identifier: Apache-2.0
"""Renovate package rules skip two intentional pins and leave ordinary updates enabled.

The runtime image has no Node, so this does not execute Renovate. It checks the
rules' match fields against the files and package names Renovate extracts.
Renovate rewrites a uv git source's packageName to the GitHub repository while
depName stays the project name, so the fixture rule matches both.

Runtime dependencies stay enabled. The shared preset's rangeStrategy pin records
them as exact ``==`` versions, and pyproject.toml, the repin fixture, and
uv.lock name the same release.
"""

import json
import re
import tomllib
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
EXACT_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*\])?"
    r"==(?P<version>\d+(?:\.\d+)+)$"
)
FIXTURE_UNPINNED = "livingproof-sim-mujoco[torch]"


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


def exact_pins(dependencies):
    """Map each ``name==version`` dependency to that version. Extras stay on the name."""
    found = {}
    for item in dependencies:
        if not isinstance(item, str):
            continue
        match = EXACT_PIN.fullmatch(item.strip())
        if match is None:
            continue
        name = match.group("name")
        assert name not in found, name
        found[name] = match.group("version")
    return found


def locked_versions(lock):
    found = {}
    for item in lock["package"]:
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        assert found.get(name, version) == version, name
        found[name] = version
    return found


def test_runtime_dependencies_are_not_disabled():
    """Renovate still versions runtime dependencies, now as exact pins.

    ``rangeStrategy: pin`` replaces a ``>=`` range with ``==``. The dependency
    stays out of the disabled rules, and the project, the repin fixture, and
    uv.lock agree on the version. A bare name or a range does not pass.
    """
    project = tomllib.loads((ROOT / RUNTIME_FILE).read_text(encoding="utf-8"))
    fixture = tomllib.loads((ROOT / FIXTURE_FILE).read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    for item in project["project"]["dependencies"]:
        if not isinstance(item, str) or " @ " in item:
            continue
        assert EXACT_PIN.fullmatch(item.strip()), item

    runtime = exact_pins(project["project"]["dependencies"])
    assert RUNTIME_PACKAGE in runtime
    version = runtime[RUNTIME_PACKAGE]
    fixture_dependencies = fixture["project"]["dependencies"]
    assert exact_pins(fixture_dependencies)[RUNTIME_PACKAGE] == version
    unpinned = [
        item
        for item in fixture_dependencies
        if isinstance(item, str) and EXACT_PIN.fullmatch(item.strip()) is None
    ]
    assert unpinned == [FIXTURE_UNPINNED]

    resolved = locked_versions(lock)
    assert RUNTIME_PACKAGE in resolved
    assert resolved[RUNTIME_PACKAGE] == version
    for name, pin in exact_pins(fixture_dependencies).items():
        if name in resolved:
            assert pin == resolved[name], name

    root_package = next(item for item in lock["package"] if item["name"] == "capability-verifier")
    assert {"name": RUNTIME_PACKAGE} in root_package["dependencies"]
    declared = [
        item
        for item in root_package["metadata"]["requires-dist"]
        if item["name"] == RUNTIME_PACKAGE
    ]
    assert len(declared) == 1
    assert declared[0]["specifier"] == f"=={version}"

    dev = project["dependency-groups"]["dev"]
    assert any(isinstance(item, str) and item.startswith(f"{RUNTIME_REPORTER} @ ") for item in dev)
    disabled = set()
    for rule in disabled_rules():
        disabled.update(names(rule))
    assert RUNTIME_PACKAGE not in disabled
    assert RUNTIME_REPORTER not in disabled
    assert all(RUNTIME_FILE not in rule["matchFileNames"] for rule in disabled_rules())
