# SPDX-License-Identifier: Apache-2.0
"""The locked evaluator release reports its release version (#125).

scenario.yml checks the evaluator out with actions/checkout at the locked commit: a
shallow (``--depth=1``), tagless (``--no-tags``) fetch. The evaluator's version is
``dynamic`` from hatch-vcs, so that checkout installs as ``0.1.dev1`` and every result
names evaluator-scenario 0.1.dev1, not the locked v1.3.0 (G1 corner evaluator job
133548983216).

These tests build a release repository with the evaluator's own version settings
(hatch-vcs, ``fallback_version = "0.1.dev0"``, ``local_scheme = "no-local-version"``),
check it out the way actions/checkout does, and install it the way ``uv`` builds the
checkout, so the version is the one the evaluator's ``importlib.metadata`` call reads.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from capability_verifier import evaluator_pin

TAG = "v1.3.0"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("uv") is None, reason="needs git and uv"
)

# The [build-system] and [tool.hatch.version] of livingproof-evaluator-scenario v1.3.0.
PYPROJECT = """\
[project]
name = "livingproof-evaluator-scenario"
dynamic = ["version"]
requires-python = ">=3.12"

[build-system]
requires = ["hatchling==1.27.0", "hatch-vcs==0.5.0"]
build-backend = "hatchling.build"

[tool.hatch.version]
source = "vcs"

[tool.hatch.version.raw-options]
local_scheme = "no-local-version"
fallback_version = "0.1.dev0"

[tool.hatch.build.targets.wheel]
packages = ["src/livingproof_evaluator_scenario"]
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def release(tmp_path):
    """An evaluator repository with v1.3.0 tagged on its release commit and one later commit."""
    repo = tmp_path / "origin"
    package = repo / "src" / "livingproof_evaluator_scenario"
    package.mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(repo))
    _git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (package / "__init__.py").write_text("")
    _commit(repo, "feat: first")
    (package / "result.py").write_text("DISTRIBUTION = 'livingproof-evaluator-scenario'\n")
    released = _commit(repo, "feat: release")
    _git(repo, "tag", TAG, released)
    (package / "later.py").write_text("")
    later = _commit(repo, "fix: after the release")
    return {"url": repo.as_uri(), "released": released, "later": later}


def _actions_checkout(url: str, commit: str, dest: Path) -> Path:
    """What actions/checkout does for ``ref: <commit>``: one commit, no tags."""
    _git(dest.parent, "init", "-q", str(dest))
    _git(dest, "remote", "add", "origin", url)
    _git(
        dest,
        "-c",
        "protocol.version=2",
        "fetch",
        "--no-tags",
        "--prune",
        "--no-recurse-submodules",
        "--depth=1",
        "origin",
        f"+{commit}:refs/remotes/origin/{commit}",
    )
    _git(dest, "checkout", "-q", "--force", f"refs/remotes/origin/{commit}")
    assert _git(dest, "tag", "--list") == ""
    assert _git(dest, "rev-parse", "--is-shallow-repository") == "true"
    return dest


def _installed_version(checkout: Path, venv: Path) -> str:
    """Build and install ``checkout`` as uv does, then read the installed version."""
    subprocess.run(["uv", "venv", "-q", str(venv)], check=True)
    python = venv / "bin" / "python"
    install = ["uv", "pip", "install", "-q", "--no-deps", "--python", str(python), str(checkout)]
    offline = [*install[:3], "--offline", *install[3:]]
    offline = subprocess.run(offline, check=False, capture_output=True)
    if offline.returncode != 0:  # build requirements not cached yet
        subprocess.run(install, check=True)
    return subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m; print(m.version('livingproof-evaluator-scenario'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_the_shallow_tagless_checkout_alone_installs_a_dev_version(release, tmp_path):
    """The reported bug: no tag in the checkout, so hatch-vcs gives a dev version."""
    checkout = _actions_checkout(release["url"], release["released"], tmp_path / "evaluator")
    assert _installed_version(checkout, tmp_path / "venv") == "0.1.dev1"


def test_the_locked_release_reports_its_release_version(release, tmp_path):
    """After the locked tag is fetched and checked, the same checkout installs as 1.3.0."""
    checkout = _actions_checkout(release["url"], release["released"], tmp_path / "evaluator")
    assert evaluator_pin.fetch_release_tag(checkout, TAG, release["released"]) == "1.3.0"
    assert _git(checkout, "rev-parse", "HEAD") == release["released"]
    assert _installed_version(checkout, tmp_path / "venv") == "1.3.0"


def test_a_tag_that_names_another_commit_is_rejected(release, tmp_path):
    """The lock names v1.3.0 with the later commit: the tag is not that commit."""
    checkout = _actions_checkout(release["url"], release["later"], tmp_path / "evaluator")
    with pytest.raises(SystemExit) as raised:
        evaluator_pin.fetch_release_tag(checkout, TAG, release["later"])
    message = str(raised.value)
    assert f"tag {TAG} is {release['released']}" in message
    assert f"not the locked commit {release['later']}" in message


def test_a_checkout_at_another_commit_is_rejected(release, tmp_path):
    """The checkout is not the locked commit: no tag is fetched into it."""
    checkout = _actions_checkout(release["url"], release["later"], tmp_path / "evaluator")
    with pytest.raises(SystemExit, match="checkout is at"):
        evaluator_pin.fetch_release_tag(checkout, TAG, release["released"])
    assert _git(checkout, "tag", "--list") == ""


def test_a_tag_the_repository_does_not_have_is_rejected(release, tmp_path):
    checkout = _actions_checkout(release["url"], release["released"], tmp_path / "evaluator")
    with pytest.raises(SystemExit, match="cannot fetch tag v9.9.9"):
        evaluator_pin.fetch_release_tag(checkout, "v9.9.9", release["released"])


def test_a_tag_that_is_not_a_release_version_is_rejected(release, tmp_path):
    checkout = _actions_checkout(release["url"], release["released"], tmp_path / "evaluator")
    with pytest.raises(SystemExit, match="not a release tag"):
        evaluator_pin.fetch_release_tag(checkout, "main", release["released"])
