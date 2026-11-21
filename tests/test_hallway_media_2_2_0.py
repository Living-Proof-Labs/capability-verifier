# SPDX-License-Identifier: Apache-2.0
"""livingproof-hallway-media/2.2.0 is accepted, with each trial's robot_mcap (capability-verifier#212).

sim-mujoco v3.9.0 (media.py, FORMAT and ``_run_trial``) writes, per trial: ``mcap`` (the
simulator's ground truth), ``robot_mcap`` (what the robot adapter reported, ``mcap/trial-NN.robot.mcap``)
and ``binding``, each as ``{path, bytes, sha256}``. 2.2.0 is 2.1.0 plus ``robot_mcap``. The file contents
here are placeholders, as in the 2.1.0 tests: the workflow step checks the entries and that the
files are in ``media/mcap/``; hashing them is the evaluator's job (``media-file-mismatch``).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from test_hallway_evidence_2 import FIXTURES, _media_script

from capability_verifier import hallway

V2_2 = "livingproof-hallway-media/2.2.0"


def _doc(edit=None):
    doc = json.loads((FIXTURES / "media-2.1.0.json").read_text())
    doc["format"] = V2_2
    for trial in doc["trials"]:
        n = f"{trial['index']:02d}"
        trial["robot_mcap"] = {
            "path": f"mcap/trial-{n}.robot.mcap",
            "bytes": 1,
            "sha256": "4" * 64,
        }
    if edit:
        edit(doc)
    return doc


def _run(tmp_path, *, edit=None, drop=None, doc=None):
    doc = doc or _doc(edit)
    media = tmp_path / "media"
    (media / "mcap").mkdir(parents=True)
    (media / "media.json").write_text(json.dumps(doc))
    for trial in doc["trials"]:
        for kind in ("mcap", "binding", "robot_mcap"):
            entry = trial.get(kind)
            path = entry.get("path") if isinstance(entry, dict) else None
            if isinstance(path, str) and path != drop and path.startswith("mcap/"):
                (media / path).write_text("{}")
    return subprocess.run(
        [sys.executable, "-c", _media_script()],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_the_workflow_accepts_media_2_2_0_and_reads_index(tmp_path):
    done = _run(tmp_path)
    assert done.returncode == 0, done.stderr
    assert "pass-seed=1" in done.stdout and "fail-seed=3" in done.stdout
    assert "video/trial-01-pass.mp4" in done.stdout


def test_hallway_py_accepts_media_2_2_0_with_the_index_key():
    assert V2_2 in hallway.MEDIA_FORMATS
    assert hallway.media_trial_key(_doc()) == "index"


@pytest.mark.parametrize("version", ["2.3.0", "3.0.0", "2.2.1", "2.10.0"])
def test_unknown_media_versions_are_still_refused(version, tmp_path):
    doc = _doc()
    doc["format"] = f"livingproof-hallway-media/{version}"
    done = _run(tmp_path, doc=doc)
    assert done.returncode != 0 and f"/{version}" in done.stderr
    assert "Traceback" not in done.stderr
    with pytest.raises(hallway.ManifestError, match=version):
        hallway.media_trial_key(doc)


def test_the_refusal_names_2_2_0_among_the_formats_it_reads(tmp_path):
    doc = _doc()
    doc["format"] = "livingproof-hallway-media/9.0.0"
    done = _run(tmp_path, doc=doc)
    assert V2_2 in done.stderr


def test_media_2_2_0_stops_when_a_trial_has_no_robot_mcap_entry(tmp_path):
    done = _run(tmp_path, edit=lambda doc: doc["trials"][1].pop("robot_mcap"))
    assert done.returncode != 0
    assert "robot_mcap" in done.stderr and "Traceback" not in done.stderr


def test_media_2_2_0_stops_when_a_robot_file_is_missing_from_media(tmp_path):
    done = _run(tmp_path, drop="mcap/trial-02.robot.mcap")
    assert done.returncode != 0
    assert "mcap/trial-02.robot.mcap" in done.stderr and "Traceback" not in done.stderr


@pytest.mark.parametrize(
    "path",
    ["mcap/trial-01.mcap", "../trial-01.robot.mcap", "video/trial-01.robot.mcap", "mcap/x.bin", ""],
    ids=["the-sim-file", "dotdot", "outside-mcap", "wrong-suffix", "empty"],
)
def test_media_2_2_0_refuses_a_robot_path_that_is_not_a_robot_file_in_mcap(path, tmp_path):
    def edit(doc):
        doc["trials"][0]["robot_mcap"]["path"] = path

    done = _run(tmp_path, edit=edit)
    assert done.returncode != 0 and "robot_mcap" in done.stderr
    assert "Traceback" not in done.stderr


def test_media_2_2_0_still_needs_the_binding(tmp_path):
    done = _run(tmp_path, edit=lambda doc: doc["trials"][1].pop("binding"))
    assert done.returncode != 0 and "binding" in done.stderr


def test_media_2_1_0_needs_no_robot_mcap(tmp_path):
    doc = _doc()
    doc["format"] = "livingproof-hallway-media/2.1.0"
    for trial in doc["trials"]:
        trial.pop("robot_mcap")
    done = _run(tmp_path, doc=doc)
    assert done.returncode == 0, done.stderr
