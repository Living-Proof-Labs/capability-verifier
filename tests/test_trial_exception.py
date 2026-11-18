# SPDX-License-Identifier: Apache-2.0
"""A sim-mujoco manifest recording a trial exception (exit 5) must be refused (#171)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from capability_verifier import bundle_hashes, declared, hallway

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sim-mujoco-115-trial-exception"


def _copy_fixture(tmp_path: Path, mutate=None) -> Path:
    dest = tmp_path / "evidence"
    shutil.copytree(FIXTURE, dest)
    manifest_path = dest / "manifest.json"
    if mutate is not None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _verify_cli(manifest: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["LIVINGPROOF_VERIFIER_VERSION"] = "1.15.2"
    env["SOURCE_COMMIT"] = "0" * 40
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "verify_hallway.py"),
            *extra,
            str(manifest),
            str(out),
        ],
        check=False,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _export_tool():
    spec = importlib.util.spec_from_file_location(
        "verify_hallway_tool_exception", ROOT / "tools" / "verify_hallway.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_trial_exception_fixture_matches_sim_mujoco_shape():
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "livingproof-hallway-evidence/2.0.0"
    assert manifest["trials"]["count"] == 2
    assert bundle_hashes.unresolved(FIXTURE) == []

    first, second = manifest["trials"]["results"]
    assert first["index"] == 1
    assert first["outcome"] == "crossed_exit"
    assert (FIXTURE / first["path"]).is_file()

    assert second["index"] == 2
    assert second["exception"] == {
        "type": "RuntimeError",
        "message": "controller step exploded",
    }
    assert isinstance(second["wall_clock_s"], (int, float))
    for forbidden in ("outcome", "path", "sha256", "trajectory_sha256"):
        assert forbidden not in second
    assert not (FIXTURE / "trials" / "trial-02.json").exists()
    assert manifest["trials"]["reproducibility"] == {
        "compared": False,
        "identical": None,
        "finding": None,
    }


def test_trial_exception_fixture_is_refused_without_writing_bundle(tmp_path, monkeypatch):
    manifest = _copy_fixture(tmp_path)
    with pytest.raises(hallway.ManifestError) as caught:
        hallway.verify(manifest)
    message = str(caught.value)
    assert "trial 2" in message
    assert "trials.results[1]" in message
    assert "exception" in message
    assert "RuntimeError: controller step exploded" in message

    monkeypatch.setenv("LIVINGPROOF_VERIFIER_VERSION", "1.15.2")
    monkeypatch.setenv("SOURCE_COMMIT", "0" * 40)
    out = tmp_path / "verified-export"
    with pytest.raises(hallway.ManifestError, match="RuntimeError: controller step exploded"):
        _export_tool().export(manifest, out, prototype=True, bundle_version="1.1.0")
    assert not out.exists()
    assert not list(tmp_path.glob(".verified-export.*"))

    cli_out = tmp_path / "verified-cli"
    completed = _verify_cli(manifest, cli_out, "--bundle-version", "1.1.0")
    assert completed.returncode == 1
    assert completed.stderr.startswith(f"invalid evidence manifest {manifest}: ")
    assert "RuntimeError: controller step exploded" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not cli_out.exists()
    assert not (cli_out / "bundle").exists()
    assert not (cli_out / "verification.json").exists()
    assert not list(tmp_path.glob(".verified-cli.*"))


def test_first_trial_exception_early_stop_is_refused(tmp_path):
    def break_first_trial(manifest):
        manifest["trials"]["results"] = [
            {
                "index": 1,
                "exception": {
                    "type": "ValueError",
                    "message": "policy init failed",
                },
                "wall_clock_s": 0.005,
            }
        ]

    manifest = _copy_fixture(tmp_path, break_first_trial)
    with pytest.raises(hallway.ManifestError) as caught:
        hallway.verify(manifest)
    message = str(caught.value)
    assert "trial 1" in message
    assert "trials.results[0]" in message
    assert "ValueError: policy init failed" in message

    out = tmp_path / "verified"
    completed = _verify_cli(manifest, out)
    assert completed.returncode == 1
    assert "ValueError: policy init failed" in completed.stderr
    assert not out.exists()


@pytest.mark.parametrize(
    ("exc_value", "expected_parts", "forbidden_substrings"),
    [
        (
            {"type": "RuntimeError", "message": "RuntimeError"},
            ("trial 2", "exception", "RuntimeError"),
            ("RuntimeError: RuntimeError", "\n"),
        ),
        (
            {"type": "CustomError", "message": "line1\nline2"},
            ("trial 2", "CustomError: line1 line2"),
            ("\n",),
        ),
        (
            "unexpected string exception",
            ("trials.results[1].exception", "exception"),
            ("\n",),
        ),
    ],
)
def test_exception_error_formatting_handles_unusual_records(
    tmp_path, exc_value, expected_parts, forbidden_substrings
):
    def set_exception(manifest):
        manifest["trials"]["results"][1]["exception"] = exc_value

    manifest = _copy_fixture(tmp_path, set_exception)
    with pytest.raises(hallway.ManifestError) as caught:
        hallway.verify(manifest)
    message = str(caught.value)
    for part in expected_parts:
        assert part in message
    for forbidden in forbidden_substrings:
        assert forbidden not in message


def test_declared_assess_refuses_trial_exception_without_writing_outputs(tmp_path):
    specs_root = ROOT / ".specs-hallway"
    worlds_root = ROOT / ".worlds"
    rel_params = "specs/scenarios/walk-hallway/parameters.json"
    params_sha = hashlib.sha256((specs_root / rel_params).read_bytes()).hexdigest()

    def add_declared_lock(manifest):
        manifest["lock"] = {
            "path": "verification.lock.json",
            "sha256": "0" * 64,
            "content": {
                "format": "livingproof-verification-lock/0.2.0",
                "scenario": "walk-hallway@0.1.0",
                "releases": {
                    "specs": {
                        "repository": "Living-Proof-Labs/robot-capability-specs",
                        "tag": "v2.3.0",
                        "commit": "57317e617014a6df97bb7fba9d92a890af8d42e5",
                        "files": {rel_params: params_sha},
                    }
                },
            },
        }

    manifest = _copy_fixture(tmp_path, add_declared_lock)
    out = tmp_path / "declared-out"
    with pytest.raises(hallway.ManifestError, match="RuntimeError: controller step exploded"):
        declared.assess(manifest, out, specs_root=specs_root, worlds_root=worlds_root)
    assert not out.exists()

    cli_out = tmp_path / "declared-cli"
    completed = _verify_cli(manifest, cli_out, "--declared", "--bundle-version", "1.1.0")
    assert completed.returncode == 1
    assert "RuntimeError: controller step exploded" in completed.stderr
    assert not cli_out.exists()
    assert not list(tmp_path.glob(".declared-cli.*"))
