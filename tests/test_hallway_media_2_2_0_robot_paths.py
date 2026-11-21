# SPDX-License-Identifier: Apache-2.0
"""Each robot_mcap path guard in the media 2.2.0 step is needed on its own (capability-verifier#212).

The refusal cases in test_hallway_media_2_2_0.py name files that do not exist, so the
``isfile`` check refuses them all: dropping the ``mcap/`` prefix, ``..`` or realpath guard
passed every test. Here the named file exists, so only one guard can refuse it. Proposed by Vera
in the capability-verifier#213 review.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from test_hallway_evidence_2 import _media_script
from test_hallway_media_2_2_0 import _run


def _with_robot_path(tmp_path, path, before=None):
    """A media folder that passes as written, then trial 1's robot_mcap path set to ``path``."""
    work = tmp_path / "work"
    work.mkdir()
    assert _run(work).returncode == 0
    media = work / "media"
    if before:
        before(media)
    doc = json.loads((media / "media.json").read_text())
    doc["trials"][0]["robot_mcap"]["path"] = path
    (media / "media.json").write_text(json.dumps(doc))
    done = subprocess.run(
        [sys.executable, "-c", _media_script()],
        check=False,
        cwd=work,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0 and "robot_mcap" in done.stderr
    assert "Traceback" not in done.stderr


def test_a_robot_path_with_dotdot_is_refused_even_when_it_resolves_into_mcap(tmp_path):
    # Exists, starts with mcap/, and resolves inside media/mcap: only the '..' guard refuses it.
    _with_robot_path(tmp_path, "mcap/../mcap/trial-01.robot.mcap")


def test_a_robot_path_not_spelled_from_mcap_is_refused_even_when_it_is_the_file(tmp_path):
    # The robot file itself, but not written as mcap/...: only the prefix guard refuses it.
    _with_robot_path(tmp_path, "./mcap/trial-01.robot.mcap")


def test_a_robot_file_that_links_out_of_mcap_is_refused(tmp_path):
    # A symlink in mcap/ to a file outside media/mcap: only the realpath guard refuses it.
    outside = tmp_path / "outside.robot.mcap"
    outside.write_text("{}")

    def link(media):
        os.symlink(outside, media / "mcap" / "escape.robot.mcap")

    _with_robot_path(tmp_path, "mcap/escape.robot.mcap", before=link)
