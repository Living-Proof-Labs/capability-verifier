# SPDX-License-Identifier: Apache-2.0
"""The integrity line's message text (#219, Vera's review of PR 221).

The refusal reason is the evaluator's own message, shown as it is: only the markdown report escapes
it, and it is capped. A malformed request reads differently from a media refusal because the
messages differ (Rivet: every exit 2 is REFUSED, whatever the cause; no prefix is required). The
separator before "The run is INCONCLUSIVE" and the exact artifact scoping are pinned here too.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from test_collect_integrity import _bundle
from test_walk_hallway_workflow import _step

from capability_verifier.collect import collect, integrity_report_lines, place_integrity_lines

MEDIA = "livingproof-evaluator-run: media is refused: media.json has no trials; no result written"
MALFORMED = "livingproof-evaluator-run: malformed request: manifest.json is not an object"
UV = "error: No such file or directory (os error 2)"


def _collected(tmp_path, folder, name, note, *, job="failure"):
    bundle, _ = _bundle(tmp_path)
    downloads = tmp_path / "downloads"
    (downloads / folder).mkdir(parents=True)
    (downloads / folder / name).write_text(note)
    collect(bundle, downloads, integrity_job=job)
    return bundle


def _refused(tmp_path, note):
    return _collected(
        tmp_path, "evaluator-results-livingproof-run-integrity-stock", "refused.txt", note
    )


def test_a_malformed_request_and_a_media_refusal_show_different_reasons(tmp_path):
    (media,) = integrity_report_lines(_refused(tmp_path / "a", MEDIA + "\n"))
    (malformed,) = integrity_report_lines(_refused(tmp_path / "b", MALFORMED + "\n"))
    assert media.startswith(f"Run Integrity: REFUSED: {MEDIA}")
    assert malformed.startswith(f"Run Integrity: REFUSED: {MALFORMED}")
    assert media != malformed


def test_any_exit_2_is_refused_even_uvs_own_error_with_its_own_text(tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, UV + "\n"))
    assert line.startswith(f"Run Integrity: REFUSED: {UV}")
    assert "NOT RUN" not in line


@pytest.mark.parametrize("reason", [MEDIA, MALFORMED, "the evaluator refused the run (exit 2)"])
def test_the_refusal_line_has_a_separator_before_the_run_is_inconclusive(reason, tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, reason + "\n"))
    assert f"{reason}. The run is INCONCLUSIVE;" in line


def test_a_reason_that_ends_in_a_full_stop_is_not_given_a_second(tmp_path):
    (line,) = integrity_report_lines(_refused(tmp_path, "it was refused.\n"))
    assert "it was refused. The run is INCONCLUSIVE;" in line and ".." not in line


def test_the_not_run_line_has_the_same_separator(tmp_path):
    bundle, _ = _bundle(tmp_path)
    (tmp_path / "d").mkdir()
    collect(bundle, tmp_path / "d", integrity_job="cancelled")
    (line,) = integrity_report_lines(bundle)
    assert "writes none). The run is INCONCLUSIVE;" in line


def test_a_long_message_is_capped_in_the_stored_reason_and_the_line(tmp_path):
    bundle = _refused(tmp_path, "x" * 5000 + "\n")
    reason = bundle["assessment"]["integrity_not_run"]["reason"]
    assert 200 < len(reason) <= 520 and reason.endswith("...")
    (line,) = integrity_report_lines(bundle)
    assert len(line) < 800


def _placed(tmp_path, line):
    (tmp_path / "integrity-lines.txt").write_text(line + "\n", encoding="utf-8")
    (tmp_path / "report.md").write_text("x: overall PASS.\n\n| a |\n", encoding="utf-8")
    (tmp_path / "report.txt").write_text("x: overall PASS.\n", encoding="utf-8")
    place_integrity_lines(Path(tmp_path))
    return (tmp_path / "report.md").read_text("utf-8").splitlines()[2], (
        tmp_path / "report.txt"
    ).read_text("utf-8").splitlines()[1]


def test_markdown_in_a_refusal_message_is_escaped_in_md_and_left_alone_in_txt(tmp_path):
    hostile = "see [x](http://evil.example) `code` | <b>bold</b> *it*"
    md, txt = _placed(tmp_path, f"Run Integrity: REFUSED: {hostile}")
    assert md == (
        "Run Integrity: REFUSED: see \\[x\\](http://evil.example) \\`code\\` \\| "
        "\\<b\\>bold\\</b\\> \\*it\\*"
    )
    assert txt == f"Run Integrity: REFUSED: {hostile}"


def test_the_label_text_itself_is_not_mangled_by_the_md_escape(tmp_path):
    md, _ = _placed(
        tmp_path, "Run Integrity: NOT RUN (cancelled): it never ran. The run is INCONCLUSIVE."
    )
    assert md.startswith("Run Integrity: NOT RUN (cancelled): it never ran.")


@pytest.mark.parametrize(
    "folder",
    [
        "evaluator-results-livingproof-run-integrity-stock",
        "x-evaluator-results-livingproof-run-integrity-stock",
        "livingproof-run-integrity",
    ],
)
def test_the_run_integrity_artifact_is_found_by_its_name(folder, tmp_path):
    bundle = _collected(tmp_path, folder, "refused.txt", MEDIA + "\n")
    assert bundle["assessment"]["integrity_not_run"].get("refused") is True


@pytest.mark.parametrize(
    "folder",
    [
        "evaluator-results-livingproof-scenario-stock",
        "notlivingproof-run-integrity",
        "evaluator-results-livingproof-run-integrity-lookalike/extra-livingproof-run-integrity-x",
        "lookalike-livingproof-run-integrity-stock",
    ],
)
def test_a_lookalike_artifact_name_is_not_the_run_integrity_artifact(folder, tmp_path):
    bundle = _collected(tmp_path, folder, "refused.txt", MEDIA + "\n")
    assert not bundle["assessment"]["integrity_not_run"].get("refused")


def test_the_step_keeps_uvs_own_exit_2_line_as_the_refusal_text(tmp_path):
    work = tmp_path / "work"
    (work / "evidence").mkdir(parents=True)
    (work / "evidence" / "manifest.json").write_text("{}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "uv"
    stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            case " $* " in
              *" sync "*) exit 0 ;;
              *"--help"*) exit 0 ;;
            esac
            echo "{UV}" >&2
            exit 2
            """
        )
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["GITHUB_WORKSPACE"] = str(work)
    done = subprocess.run(
        ["bash", "-e", "-c", _step("Run Integrity, one result for the run")],
        cwd=work,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2
    results = work / "evaluator-results" / "livingproof-run-integrity"
    assert (results / "refused.txt").read_text() == UV + "\n"
