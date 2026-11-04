# SPDX-License-Identifier: Apache-2.0
""".github/scripts/hallway-tag-absent.sh: only a registry not-found means the tag is absent.
docker is stubbed on PATH; every case other than an explicit not-found must fail closed."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "hallway-tag-absent.sh"
REF = "ghcr.io/living-proof-labs/capability-verifier-hallway:1.15.0"
BASH = shutil.which("bash")


def run(tmp_path, docker_body=None):
    """Run the script with a PATH holding only grep and, when given, a stub docker."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "grep").symlink_to(shutil.which("grep"))
    if docker_body is not None:
        docker = bin_dir / "docker"
        docker.write_text(f"#!{BASH}\n{docker_body}\n")
        docker.chmod(0o755)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path)}
    return subprocess.run(
        [BASH, str(SCRIPT), REF], env=env, capture_output=True, text=True, check=False
    )


def stub(inspect_output, inspect_status, buildx_status=0):
    return (
        f'if [ "$1 $2" = "buildx version" ]; then exit {buildx_status}; fi\n'
        f"echo {inspect_output!r} >&2\n"
        f"exit {inspect_status}"
    )


@pytest.mark.parametrize(
    "output",
    [
        f"ERROR: {REF}: not found",
        "ERROR: failed: MANIFEST_UNKNOWN: manifest unknown",
    ],
)
def test_registry_not_found_is_absent(tmp_path, output):
    result = run(tmp_path, stub(output, 1))
    assert result.returncode == 0, result.stderr
    assert f"{REF} is absent" in result.stdout


@pytest.mark.parametrize(
    ("case", "output"),
    [
        ("command not found", "docker-credential-helper: command not found"),
        ("denied", "ERROR: denied: repository not found"),
        ("auth", "ERROR: unexpected status 401 Unauthorized"),
        ("generic", "ERROR: dial tcp: lookup ghcr.io: no such host"),
    ],
)
def test_other_errors_fail_closed(tmp_path, case, output):
    result = run(tmp_path, stub(output, 1))
    assert result.returncode == 1, case
    assert "is absent" not in result.stdout
    assert "could not confirm" in result.stderr


def test_present_tag_fails(tmp_path):
    result = run(tmp_path, stub("Name: " + REF, 0))
    assert result.returncode == 1
    assert "never retagged" in result.stderr


def test_missing_docker_fails(tmp_path):
    result = run(tmp_path, None)
    assert result.returncode == 1
    assert "docker is not installed" in result.stderr
    assert "is absent" not in result.stdout


def test_missing_buildx_fails(tmp_path):
    result = run(tmp_path, stub(f"ERROR: {REF}: not found", 1, buildx_status=1))
    assert result.returncode == 1
    assert "buildx is not available" in result.stderr


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
