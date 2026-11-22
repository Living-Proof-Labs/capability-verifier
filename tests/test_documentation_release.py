# SPDX-License-Identifier: Apache-2.0
"""Exercise the exact released documentation guards without dispatch or credentials."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from docs import release_identity

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
TAG = "v2.11.4"


def jobs(name):
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())["jobs"]


def step(workflow, job, name):
    return next(s for s in jobs(workflow)[job]["steps"] if s.get("name") == name)


def test_previews_and_release_assets_are_not_pages_input():
    main = jobs("verify")
    docs = main["documentation"]["steps"]
    assert not any("upload-pages-artifact" in s.get("uses", "") for s in docs)
    assert any(s.get("run") == "task docs:archive:check" for s in docs)
    assert "documentation" in main["release"]["needs"]
    assert "hallway-image-verify" in main["release-reports"]["needs"]
    stable = main["documentation-release"]
    assert stable["needs"] == ["release", "release-reports"]
    assert stable["permissions"] == {"contents": "read"}
    assert "needs.release-reports.result == 'success'" in stable["if"]
    assert "needs.release.outputs.commit == github.sha" in stable["if"]
    assert main["documentation-deploy"]["needs"] == ["release", "documentation-release"]
    replay = jobs("documentation-replay")
    assert {s["uses"] for s in replay.values()} == {
        main["release"]["uses"].replace("semantic-release.yml", "semantic-release-candidate.yml"),
        stable["uses"],
        main["documentation-deploy"]["uses"],
    }
    assert replay["manual"]["with"]["commit"] == "${{ needs.version.outputs.commit }}"
    assert all(s.get("permissions", {}).get("contents") == "read" for s in replay.values())
    build = jobs("documentation-release")["build"]["steps"]
    tasks = [s.get("run") for s in build]
    assert tasks.index("task verify") < tasks.index("task docs") < tasks.index("task test:docs")
    deployment = jobs("documentation-deploy")["deploy"]
    assert deployment["concurrency"] == {
        "group": "capability-verifier-pages",
        "cancel-in-progress": False,
    }
    deploy = deployment["steps"]
    assert not any("actions/checkout" in s.get("uses", "") for s in deploy)
    assert any("actions/download-artifact" in s.get("uses", "") for s in deploy)
    assert deploy[-2]["name"] == "Check current main inside deployment lock"


@pytest.fixture
def source(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    config = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
    git(*config, "commit", "--allow-empty", "-qm", "First fixture")
    previous = git("rev-parse", "HEAD")
    git(*config, "commit", "--allow-empty", "-qm", "Second fixture")
    return git, git("rev-parse", "HEAD"), previous


@pytest.mark.parametrize(
    "mode",
    [
        "exact",
        "wrong-tag-source",
        "wrong-version",
        "missing",
        "api",
        "draft",
        "mutable",
        "prerelease",
    ],
)
def test_published_release_guard_blocks_before_any_documentation_build(tmp_path, source, mode):
    git, commit, previous = source
    if mode != "missing":
        git("tag", TAG, previous if mode == "wrong-tag-source" else commit)
    release = {
        "tag_name": TAG,
        "published_at": "fixture",
        "draft": mode == "draft",
        "immutable": mode != "mutable",
        "prerelease": mode == "prerelease",
    }
    recorder = tmp_path / "gh"
    recorder.write_text(
        "#!/bin/bash\nexit 1\n"
        if mode == "api"
        else "#!/bin/bash\n"
        + f'test "$1" = api && test "$2" = "repos/example/verifier/releases/tags/{TAG}"\n'
        + f"printf '%s\\n' '{json.dumps(release)}'\n"
    )
    recorder.chmod(0o755)
    guard = step(
        "documentation-release",
        "build",
        "Require the immutable release and exact source tag before building",
    )
    result = subprocess.run(
        ["bash", "-c", guard["run"]],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "example/verifier",
            "GITHUB_SHA": commit,
            "EXPECTED_COMMIT": commit,
            "DOCS_RELEASE_TAG": TAG,
            "LIVINGPROOF_VERIFIER_VERSION": "2.11.3" if mode == "wrong-version" else TAG[1:],
        },
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (mode == "exact"), result.stderr


def test_documentation_identity_keeps_development_and_requires_green_exact_stable(monkeypatch):
    monkeypatch.delenv("DOCS_RELEASE_TAG", raising=False)
    assert release_identity({"exitstatus": 0}) == {}
    monkeypatch.setenv("DOCS_RELEASE_TAG", TAG)
    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", TAG[1:])
    monkeypatch.setenv("DOCS_RELEASE_RUN_ID", "12345")
    monkeypatch.setenv("DOCS_RELEASE_RUN_ATTEMPT", "2")
    assert release_identity({"exitstatus": 0}) == {
        "release_tag": TAG,
        "release_version": TAG[1:],
        "release_run_id": "12345",
        "release_run_attempt": "2",
    }
    with pytest.raises(ValueError, match="must match"):
        release_identity({"exitstatus": 1})


@pytest.mark.parametrize("mode", ["exact", "wrong-attempt", "wrong-revision", "changed-bytes"])
def test_deploy_reads_the_exact_sealed_tar_without_running_repository_code(tmp_path, mode):
    producer = tmp_path / "verified-capability-verifier"
    producer.mkdir()
    commit = "a" * 40
    record = json.dumps(
        {
            "release_version": TAG[1:],
            "release_tag": TAG,
            "commit": "b" * 40 if mode == "wrong-revision" else commit,
            "exitstatus": 0,
            "release_run_id": "12345",
            "release_run_attempt": "3" if mode == "wrong-attempt" else "2",
            "junit_sha256": "f" * 64,
        }
    ).encode()
    archive = producer / "artifact.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("./build.json")
        info.size = len(record)
        tar.addfile(info, io.BytesIO(record))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if mode == "changed-bytes":
        archive.write_bytes(archive.read_bytes() + b"changed")
    guard = step(
        "documentation-deploy",
        "deploy",
        "Use the exact sealed bytes and validate their build identity",
    )
    result = subprocess.run(
        ["bash", "-c", guard["run"]],
        env={
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "out"),
            "GITHUB_RUN_ID": "12345",
            "RELEASE_TAG": TAG,
            "VERSION": TAG[1:],
            "SOURCE": commit,
            "PLAN_ATTEMPT": "2",
            "DIGEST": checksum,
        },
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (mode == "exact"), result.stderr
    if mode == "exact":
        assert (tmp_path / "artifact.tar").read_bytes() == archive.read_bytes()
