# SPDX-License-Identifier: Apache-2.0
"""Every sha256 a bundle declares resolves (capability-verifier#86).

The evidence folders here are real runs: livingproof-verification run 43402628666 (the
run in #86, verifier v1.18.5), the walk-corner runs from robot-capability-specs#40 and the
G1 repeat-review runs (one with a genuine disagreement). Each hash is resolved with the
released livingproof-sim-mujoco v3.2.5 ``evidence.unresolved_hashes`` and
``trajectory_sha256_of`` (sim#74), never a second copy of the formula.
"""

import json
import shutil
from pathlib import Path

import pytest
from livingproof_sim_mujoco import evidence

from capability_verifier import bundle_hashes

FIX = Path(__file__).parent / "fixtures"
RUN = FIX / "lpv-run-43402628666"
DEFINITIONS = (
    "booster-t1-stock",
    "unitree-g1-playground",
    "unitree-g1-stock",
    "unitree-go1-playground",
)
BUNDLES = [
    *(RUN / name / "evidence" for name in DEFINITIONS),
    *(FIX / "corner" / name for name in ("g1-stock", "go1-playground", "t1-stock", "unsupported")),
    *(FIX / "hallway" / "review-g1" / name for name in ("agree", "disagree", "disagree-fail")),
    FIX / "hallway" / "evidence-2.0.0",
]


def _ids(path: Path) -> str:
    return str(path.relative_to(FIX))


@pytest.mark.parametrize("folder", BUNDLES, ids=_ids)
def test_every_declared_hash_in_a_real_bundle_resolves(folder):
    assert evidence.declared_hashes(folder), "no hashes found: wrong folder"
    assert bundle_hashes.unresolved(folder) == []


@pytest.mark.parametrize("name", DEFINITIONS)
def test_every_hash_in_the_verification_report_resolves(name):
    report = RUN / name / "verification.json"
    assert bundle_hashes.report_hashes(report), "no hashes found in the report"
    assert bundle_hashes.unresolved_report(report, RUN / name / "evidence") == []


def test_the_quoted_trajectory_hashes_recompute_from_the_published_trials():
    """The two hashes #86 says had no file behind them."""
    expected = {
        "unitree-go1-playground": ("8856b0a8", "2f2b"),
        "booster-t1-stock": ("61418b3b", "8bc2"),
    }
    for name, (head, tail) in expected.items():
        folder = RUN / name / "evidence"
        manifest = json.loads((folder / "manifest.json").read_text())
        for trial in manifest["trials"]["results"]:
            got = evidence.trajectory_sha256_of(folder / trial["path"])
            assert got == trial["trajectory_sha256"]
            assert got.startswith(head) and got.endswith(tail)


def _copy(tmp_path: Path, folder: Path) -> Path:
    out = tmp_path / folder.name
    shutil.copytree(folder, out)
    return out


def _edit(path: Path, change) -> None:
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document, indent=2) + "\n")


GO1 = RUN / "unitree-go1-playground" / "evidence"


def test_an_edited_trace_is_reported(tmp_path):
    out = _copy(tmp_path, GO1)
    trial = out / "trials" / "trial-02.json"
    _edit(trial, lambda obs: obs["trace"]["samples"].pop())
    problems = bundle_hashes.unresolved(out)
    assert any("trials.results[1].trajectory_sha256" in p for p in problems), problems
    assert any("trials.results[1].sha256" in p for p in problems), problems


def test_a_missing_trial_file_is_reported(tmp_path):
    out = _copy(tmp_path, GO1)
    (out / "trials" / "trial-03.json").unlink()
    problems = bundle_hashes.unresolved(out)
    assert any("trials.results[2].sha256" in p and "not a file" in p for p in problems)


def test_an_undocumented_hash_field_is_reported(tmp_path):
    out = _copy(tmp_path, GO1)
    _edit(out / "manifest.json", lambda m: m.update(mcap_sha256="0" * 64))
    problems = bundle_hashes.unresolved(out)
    undocumented = (
        "manifest.json mcap_sha256 is not documented: it names no file in this folder and no "
        "recompute. Add it to evidence.DECLARED_HASHES with what it covers."
    )
    assert problems == [undocumented]


def test_a_finding_hash_that_is_not_a_trial_hash_is_reported(tmp_path):
    out = _copy(tmp_path, FIX / "hallway" / "review-g1" / "disagree")

    def change(manifest):
        manifest["trials"]["reproducibility"]["finding"]["trajectory_sha256"][0] = "f" * 64

    _edit(out / "manifest.json", change)
    [problem] = bundle_hashes.unresolved(out)
    assert problem.startswith("manifest.json trials.reproducibility.finding.trajectory_sha256")


def test_the_verifier_lock_fields_are_documented_not_silently_skipped():
    """Without the verifier's catalog the released sim function flags these lock fields."""
    flagged = evidence.unresolved_hashes(GO1)
    undocumented = (
        "manifest.json lock.content.overridden_robot_lock.sha256 is not documented: it names "
        "no file in this folder and no recompute. Add it to evidence.DECLARED_HASHES with "
        "what it covers."
    )
    assert flagged == [undocumented]
    assert bundle_hashes.unresolved(GO1) == []


def test_an_altered_report_evidence_hash_is_reported(tmp_path):
    name = "unitree-go1-playground"
    report = tmp_path / "verification.json"
    shutil.copy(RUN / name / "verification.json", report)
    _edit(report, lambda r: r["trials"][1]["evidence"].update(sha256="0" * 64))
    problems = bundle_hashes.unresolved_report(report, RUN / name / "evidence")
    assert problems == [
        "verification.json trials[1].evidence.sha256 is "
        + "0" * 64
        + " but the file trials/trial-02.json gives "
        + json.loads((RUN / name / "verification.json").read_text())["trials"][1]["evidence"][
            "sha256"
        ]
        + "."
    ]


def test_report_repeat_rows_must_copy_the_evidence_records(tmp_path):
    manifest = json.loads((FIX / "hallway/review-g1/disagree/manifest.json").read_text())
    rows = [
        {key: r[key] for key in ("index", "sha256", "trajectory_sha256", "outcome")}
        for r in manifest["trials"]["results"]
    ]
    report = tmp_path / "verification.json"
    report.write_text(json.dumps({"repeats": {"differing": rows}}))
    folder = FIX / "hallway/review-g1/disagree"
    assert bundle_hashes.unresolved_report(report, folder) == []
    rows[0]["trajectory_sha256"] = "e" * 64
    report.write_text(json.dumps({"repeats": {"differing": rows}}))
    [problem] = bundle_hashes.unresolved_report(report, folder)
    assert problem.startswith("verification.json repeats.differing[0].trajectory_sha256")
