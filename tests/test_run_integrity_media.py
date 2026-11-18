# SPDX-License-Identifier: Apache-2.0
"""The run-integrity job gets the media artifact and passes it to the evaluator (#156).

The evaluator's `--media DIR` arrived in livingproof-evaluator-run v1.4.0 (`recording` 1.0.0).
The job downloads the media artifact only when the `media` job succeeded, and passes the
directory only when the pinned evaluator accepts `--media`. Without media, or with an
evaluator that predates the flag, the job runs as it always did and stays green.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from capability_verifier import definition

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/scenario.yml").read_text()
RUN_INTEGRITY_REPO = "https://github.com/Living-Proof-Labs/livingproof-evaluator-run.git"
EVIDENCE = ROOT / "tests/fixtures/lpv-run-43402628666/unitree-g1-stock/evidence"

# Stands in for `uv`: sync does nothing, run drops uv's options and runs python.
UV = """#!/bin/sh
if [ "$1" = sync ]; then exit 0; fi
shift
while [ "$1" != python ]; do
  case "$1" in --directory) shift ;; esac
  shift
done
shift
exec python3 "$@"
"""

# A stand-in evaluator: writes the arguments it was given into the result, and prints a help
# text with or without --media.
STUB = """import json, os, sys
if "--help" in sys.argv:
    print("usage: livingproof_evaluator_run [-h] --out OUT" + (" [--media MEDIA]" if os.environ["STUB_MEDIA"] == "1" else "") + " source")
    raise SystemExit(0)
out = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump({"argv": sys.argv[1:]}, open(out, "w"))
"""


def _jobs():
    return yaml.safe_load(WORKFLOW)["jobs"]


def test_run_integrity_waits_for_the_media_job_and_runs_whatever_it_did():
    job = _jobs()["run-integrity"]
    assert set(job["needs"]) == {"plan", "evidence", "media"}
    # `!cancelled()` runs the job after a skipped or failed need: media off, not media-capable,
    # media failed, or evidence failed so that verify and media never ran.
    assert job["if"] == "${{ !cancelled() }}"
    assert "run-integrity" in _jobs()["report"]["needs"]


def test_the_media_artifact_is_downloaded_only_when_the_media_job_succeeded():
    steps = _jobs()["run-integrity"]["steps"]
    download = next(s for s in steps if s.get("with", {}).get("path") == "media")
    assert "download-artifact" in download["uses"]
    assert download["if"] == "${{ needs.media.result == 'success' }}"
    assert (
        download["with"]["name"] == "${{ inputs.artifact-prefix }}mcap-${{ matrix.configuration }}"
    )
    names = [s.get("name") for s in steps]
    evaluate = next(i for i, n in enumerate(names) if n == "Run Integrity, one result for the run")
    assert steps.index(download) < evaluate


def test_the_download_matches_what_the_media_job_uploads():
    media = _jobs()["media"]
    upload = next(s for s in media["steps"] if s.get("id") == "mcap")
    download = next(
        s for s in _jobs()["run-integrity"]["steps"] if s.get("with", {}).get("path") == "media"
    )
    assert upload["with"]["name"] == download["with"]["name"]
    # media/mcap/ and media/media.json upload with their common root `media/` stripped, so the
    # download holds media.json and mcap/ directly: the layout --media DIR expects.
    assert upload["with"]["path"].split() == ["media/mcap/", "media/media.json"]


def _setup(tmp_path, *, media, stub=None):
    work = tmp_path / "work"
    (work / "evidence").mkdir(parents=True)
    (work / "evidence" / "specs-files.json").write_text("{}")
    if stub is not None:
        pkg = work / "run-integrity" / "src" / "livingproof_evaluator_run"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(STUB)
        (work / "evidence" / "manifest.json").write_text("{}")
    if media:
        (work / "media" / "mcap").mkdir(parents=True)
        (work / "media" / "media.json").write_text(
            json.dumps({"format": "livingproof-hallway-media/2.0.0", "trials": []})
        )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(UV)
    (bindir / "uv").chmod(0o755)
    return work, bindir


def _run(work, bindir, extra_env):
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(work),
        **extra_env,
    }
    script = definition.step(WORKFLOW, "Run Integrity, one result")
    done = subprocess.run(
        ["sh", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True, check=False
    )
    out = work / "evaluator-results" / "livingproof-run-integrity" / "result.json"
    return done, (json.loads(out.read_text()) if out.exists() else None)


def test_media_is_passed_when_present_and_the_evaluator_takes_it(tmp_path):
    work, bindir = _setup(tmp_path, media=True, stub=True)
    done, result = _run(
        work, bindir, {"STUB_MEDIA": "1", "PYTHONPATH": str(work / "run-integrity" / "src")}
    )
    assert done.returncode == 0, done.stderr
    argv = result["argv"]
    assert argv[argv.index("--media") + 1] == str(work / "media")
    assert argv[0].endswith("evidence/manifest.json")


def test_no_media_directory_means_no_argument(tmp_path):
    # The media job was disabled, skipped, or failed: the download step did not run.
    work, bindir = _setup(tmp_path, media=False, stub=True)
    done, result = _run(
        work, bindir, {"STUB_MEDIA": "1", "PYTHONPATH": str(work / "run-integrity" / "src")}
    )
    assert done.returncode == 0, done.stderr
    assert "--media" not in result["argv"]


def test_an_evaluator_that_predates_the_flag_is_not_given_it(tmp_path):
    # v1.0.0 (the default pin) to v1.3.1 do not know --media; passing it would fail the job.
    work, bindir = _setup(tmp_path, media=True, stub=True)
    done, result = _run(
        work, bindir, {"STUB_MEDIA": "0", "PYTHONPATH": str(work / "run-integrity" / "src")}
    )
    assert done.returncode == 0, done.stderr
    assert "--media" not in result["argv"]
    assert "does not take --media" in done.stdout + done.stderr


def _real(tmp_path, tag, *, media):
    checkout = tmp_path / "run-integrity"
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, RUN_INTEGRITY_REPO, str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        pytest.fail(f"could not clone {RUN_INTEGRITY_REPO}@{tag}: {clone.stderr}")
    sync = subprocess.run(
        ["uv", "sync", "--locked", "--directory", str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sync.returncode == 0, sync.stderr
    work = tmp_path / "work"
    shutil.copytree(EVIDENCE, work / "evidence")
    (work / "evidence" / "specs-files.json").write_text("{}")
    (work / "run-integrity").symlink_to(checkout)
    if media:
        (work / "media" / "mcap").mkdir(parents=True)
        (work / "media" / "media.json").write_text(
            json.dumps({"format": "livingproof-hallway-media/2.0.0", "trials": []})
        )
    env = {"GITHUB_WORKSPACE": str(work), "PYTHONPATH": str(checkout / "src")}
    return _run_real(work, env)


def _run_real(work, extra_env):
    env = {**os.environ, **extra_env}
    script = definition.step(WORKFLOW, "Run Integrity, one result")
    done = subprocess.run(
        ["sh", "-e", "-c", script], cwd=work, env=env, capture_output=True, text=True, check=False
    )
    out = work / "evaluator-results" / "livingproof-run-integrity" / "result.json"
    return done, (json.loads(out.read_text()) if out.exists() else None)


def _recording(result):
    return next((c for c in result["integrity"]["checks"] if c["id"] == "recording"), None)


@pytest.mark.parametrize("media", [False, True])
def test_v1_4_0_reports_recording_not_applicable_without_a_gap_limit(tmp_path, media):
    # No manifest or lock here declares plan.recording.max_gap_seconds, and the evaluator has
    # no default: with media or without, the recording check is not applicable, not failed.
    done, result = _real(tmp_path, "v1.4.0", media=media)
    assert done.returncode == 0, done.stderr
    recording = _recording(result)
    assert recording is not None
    assert recording["applicable"] is False
    assert recording["success"] is True
    assert recording["findings"] == []


def test_v1_0_0_with_media_present_runs_as_before(tmp_path):
    done, result = _real(tmp_path, "v1.0.0", media=True)
    assert done.returncode == 0, done.stderr
    assert result["integrity"]["run"]["success"] is True
    assert _recording(result) is None


REAL_MEDIA = ROOT / "tests/fixtures/run-integrity-media"
GO1_EVIDENCE = ROOT / "tests/fixtures/lpv-run-43402628666/unitree-go1-playground/evidence"


def _real_media_job(tmp_path, *, gap):
    """The job step on real Go1 evidence and its real recordings, with the real v1.4.0."""
    checkout = tmp_path / "run-integrity"
    clone = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "v1.4.0", RUN_INTEGRITY_REPO, str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        pytest.fail(f"could not clone {RUN_INTEGRITY_REPO}@v1.4.0: {clone.stderr}")
    sync = subprocess.run(
        ["uv", "sync", "--locked", "--directory", str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sync.returncode == 0, sync.stderr
    work = tmp_path / "work"
    shutil.copytree(GO1_EVIDENCE, work / "evidence")
    (work / "evidence" / "specs-files.json").write_text("{}")
    manifest = json.loads((work / "evidence" / "manifest.json").read_text())
    if gap is not None:
        # A test value in a test manifest. The code and the contract have no default.
        manifest["plan"]["recording"] = {"max_gap_seconds": gap}
    (work / "evidence" / "manifest.json").write_text(json.dumps(manifest))
    shutil.copytree(REAL_MEDIA, work / "media")
    for n in (2, 3):
        shutil.copy(work / "media/mcap/trial-01.mcap", work / f"media/mcap/trial-0{n}.mcap")
    (work / "run-integrity").symlink_to(checkout)
    return _run_real(work, {"GITHUB_WORKSPACE": str(work), "PYTHONPATH": str(checkout / "src")})


def test_the_real_recordings_are_judged_when_the_run_declares_a_limit(tmp_path):
    done, result = _real_media_job(tmp_path, gap=0.05)
    assert done.returncode == 0, done.stderr
    recording = _recording(result)
    assert recording["applicable"] is True
    assert recording["success"] is True
    assert recording["limits"] == {"max_gap_seconds": 0.05}
    assert recording["percentage"] == 100


def test_the_real_recordings_are_not_judged_without_a_declared_limit(tmp_path):
    # No limit is approved and none is defaulted: media is passed, and the check says so.
    done, result = _real_media_job(tmp_path, gap=None)
    assert done.returncode == 0, done.stderr
    recording = _recording(result)
    assert recording["applicable"] is False
    assert recording["success"] is True
