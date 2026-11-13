# SPDX-License-Identifier: Apache-2.0
"""Evaluator results append per document, and a second artifact is a second mark.

Reporter v3.5.0 renders the stored playground bundle. The collector and the
reporter are unchanged between the one-evaluator and two-evaluator renders.
"""

import json
import re
import shutil
import tomllib
from pathlib import Path

import importlib.metadata

from livingproof_capability_report.assessed import report_from_bundle
from livingproof_capability_report.core import load_bundle, render
from livingproof_evaluator_scenario.result import trial_result

from capability_verifier.collect import collect

BUNDLE = Path(__file__).parent / "fixtures" / "g1-playground-bundle"
STOCK = Path(__file__).parent / "fixtures" / "g1-stock-bundle"
WHEN = "2026-11-12T05:11:41Z"
ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">'
    '<g stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6.5 7L10.5 11L17.5 4"/>'
    '<rect x="4" y="9" width="16" height="11" rx="4"/>'
    "</g>"
    '<g fill="currentColor">'
    '<circle cx="8.5" cy="14.5" r="1.5"/>'
    '<circle cx="15.5" cy="14.5" r="1.5"/>'
    "</g></svg>"
)


def _bundle(tmp_path):
    dest = tmp_path / "bundle"
    shutil.copytree(BUNDLE, dest)
    return dest


def _scenario_documents(bundle_dir):
    bundle = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    thresholds = bundle["results"][0]["provenance"]["thresholds"]
    documents = []
    for result in bundle["results"]:
        evidence = result["evidence"][0]
        observation = json.loads((bundle_dir / evidence["path"]).read_text(encoding="utf-8"))
        index = result["original_result"]["index"]
        documents.append(
            trial_result(
                observation,
                thresholds,
                result["id"],
                seed=("trial", index),
                inputs=[
                    {
                        "role": "observation",
                        "uri": evidence["path"],
                        "sha256": evidence["sha256"],
                    }
                ],
                started_at=WHEN,
                completed_at=WHEN,
            )
        )
    return documents


def _health(trial_id):
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {
            "id": "example-health",
            "version": "0.0.0",
            "kind": "scenario",
            "execution": "deterministic",
            "display_name": "Robot health",
            "publisher": "Example",
            "icon": ICON,
        },
        "inputs": [{"role": "observation", "uri": "trials/trial-01.json", "sha256": "ab" * 32}],
        "timestamps": {"started_at": WHEN, "completed_at": WHEN},
        "scenario": {
            "trial_id": trial_id,
            "steps": [{"id": "camera", "verdict": "FAIL", "summary": "Camera node crashed."}],
        },
        "descriptor": {
            "title": "Robot health",
            "short_title": "Rob health",
            "icon": ICON,
            "icon_accessible_name": "Robot health",
            "purpose": "Example second evaluator.",
            "version": "0.0.0",
            "tier": "health_finding",
            "findings": [{"text": "Camera node crashed.", "tier": "health_finding"}],
        },
    }


def _write(root, artifact, name, documents):
    directory = root / artifact / name
    directory.mkdir(parents=True)
    for document in documents:
        trial_id = document["scenario"]["trial_id"]
        (directory / f"{trial_id}.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )


def _html(bundle_dir):
    loaded = load_bundle(bundle_dir / "manifest.json")
    report = report_from_bundle(loaded)
    return render(report, "html")


def _ring(page):
    start = page.index("<svg class='success-ring'")
    return page[start : page.index("</svg>", start)]


def test_a_second_evaluator_artifact_adds_a_mark_without_a_collector_change(tmp_path):
    bundle_dir = _bundle(tmp_path)
    scenario = _scenario_documents(bundle_dir)
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-scenario-playground",
        "livingproof-scenario",
        scenario,
    )
    bundle = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    collect(bundle, downloads)
    (bundle_dir / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    one = _html(bundle_dir)
    assert "eval-name-full'>Scenario</span>" in one
    assert "eval-name-full'>Robot health</span>" not in one
    assert "3 successes, 100%" in one
    assert "M6.5 7L10.5 11L17.5 4" in one
    ring = _ring(one)

    both_dir = _bundle(tmp_path / "both")
    both_downloads = tmp_path / "both-downloads"
    _write(
        both_downloads,
        "evaluator-results-livingproof-scenario-playground",
        "livingproof-scenario",
        scenario,
    )
    _write(
        both_downloads,
        "evaluator-results-example-health-playground",
        "example-health",
        [_health("playground-seed-01")],
    )
    both = json.loads((both_dir / "manifest.json").read_text(encoding="utf-8"))
    collect(both, both_downloads)
    first = both["results"][0]["evaluator_results"]
    assert [item["evaluator"]["id"] for item in first] == ["example-health", "livingproof-scenario"]
    assert first[0]["descriptor"]["findings"][0]["text"] == "Camera node crashed."
    assert first[1]["descriptor"]["findings"] == []
    (both_dir / "manifest.json").write_text(json.dumps(both, indent=2) + "\n", encoding="utf-8")
    two = _html(both_dir)
    assert "eval-name-full'>Scenario</span>" in two
    assert "eval-name-full'>Robot health</span>" in two
    assert "Camera node crashed." in two
    assert "3 successes, 100%" in two
    assert _ring(two) == ring


EVALUATOR_PIN = re.compile(
    r"livingproof-evaluator-scenario @ git\+https://github\.com/Living-Proof-Labs/"
    r"livingproof-evaluator-scenario\.git@(?P<tag>v\d+\.\d+\.\d+)\""
)


def test_scenario_package_pin_is_a_released_tag():
    """The declared pin is a full released vX.Y.Z tag, not a commit or branch."""
    text = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    match = EVALUATOR_PIN.search(text)
    assert match, "declared evaluator pin must be a full released vX.Y.Z tag"
    assert match.group("tag") == "v1.1.2"
    assert "9c82a0b" not in text
    assert importlib.metadata.version("livingproof-evaluator-scenario") == "1.1.2"


LOCK_SOURCE = re.compile(
    r"https://github\.com/Living-Proof-Labs/livingproof-evaluator-scenario\.git"
    r"\?rev=(?P<tag>v\d+\.\d+\.\d+)(?:#(?P<commit>[0-9a-f]{40}))?"
)


def test_scenario_pin_matches_the_lock_and_workflows_have_no_v1_0_0_fallback():
    """pyproject, uv.lock and workflows agree on the released tag; no v1.0.0 fallback."""
    root = Path(__file__).parents[1]
    pin = EVALUATOR_PIN.search((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pin, "declared evaluator pin must be a full released vX.Y.Z tag"
    tag = pin.group("tag")

    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = [
        item for item in lock["package"] if item["name"] == "livingproof-evaluator-scenario"
    ]
    assert len(packages) == 1
    package = packages[0]
    assert package["version"] == tag.removeprefix("v")
    source = LOCK_SOURCE.fullmatch(package["source"]["git"])
    assert source, package["source"]["git"]
    assert source.group("tag") == tag
    assert source.group("commit") is not None

    metadata = next(item for item in lock["package"] if item["name"] == "capability-verifier")
    declared = next(
        item
        for item in metadata["metadata"]["requires-dist"]
        if item["name"] == "livingproof-evaluator-scenario"
    )
    declared_source = LOCK_SOURCE.fullmatch(declared["git"])
    assert declared_source, declared["git"]
    assert declared_source.group("tag") == tag

    for path in (root / ".github").rglob("*"):
        if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "v1.0.0" not in text, path
        assert "livingproof-evaluator-scenario.git@" not in text, path


def test_two_configurations_keep_their_own_entries_and_grades(tmp_path):
    """A download that holds both configurations does not cross their trials."""
    play_root = tmp_path / "play"
    play_root.mkdir()
    playground = _bundle(play_root)
    stock = tmp_path / "stock"
    shutil.copytree(STOCK, stock)
    play_docs = _scenario_documents(playground)
    stock_docs = _scenario_documents(stock)
    assert play_docs[0]["evaluator"]["version"] == "1.1.2"
    assert stock_docs[0]["evaluator"]["version"] == "1.1.2"
    assert play_docs[0]["evaluator"]["display_name"] == "Scenario"
    assert play_docs[0]["descriptor"]["title"] == "Scenario"
    assert play_docs[0]["descriptor"]["short_title"] == "Scenario"
    downloads = tmp_path / "downloads"
    _write(
        downloads,
        "evaluator-results-livingproof-scenario-playground",
        "livingproof-scenario",
        play_docs,
    )
    _write(
        downloads,
        "evaluator-results-livingproof-scenario-stock",
        "livingproof-scenario",
        stock_docs,
    )
    _write(
        downloads,
        "evaluator-results-example-health-playground",
        "example-health",
        [_health("playground-seed-01")],
    )
    _write(
        downloads,
        "evaluator-results-example-health-stock",
        "example-health",
        [_health("stock-seed-01")],
    )

    def apply(bundle_dir):
        bundle = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        before = json.dumps(bundle["assessment"])
        collect(bundle, downloads)
        assert json.dumps(bundle["assessment"]) == before
        run = bundle["assessment"]["runs"][0]
        rows = []
        for result in bundle["results"]:
            for document in result.get("evaluator_results") or []:
                rows.append(
                    (result["id"], document["evaluator"]["id"], document["scenario"]["trial_id"])
                )
                assert document["scenario"]["trial_id"] == result["id"]
        (bundle_dir / "manifest.json").write_text(json.dumps(bundle), encoding="utf-8")
        return run["grade"], run["k"], run["n"], rows, _html(bundle_dir)

    grade, k, n, rows, page = apply(playground)
    assert (grade, k, n) == ("PASS", 3, 3)
    assert "<p class='count'>3 successes, 100%</p>" in page
    assert {trial for trial, _, _ in rows} == {
        "playground-seed-01",
        "playground-seed-02",
        "playground-seed-03",
    }
    assert sum(evaluator == "livingproof-scenario" for _, evaluator, _ in rows) == 3
    assert sum(evaluator == "example-health" for _, evaluator, _ in rows) == 1
    assert "stock-seed-" not in page

    grade, k, n, rows, page = apply(stock)
    assert (grade, k, n) == ("FAIL", 0, 3)
    assert "<p class='count'>0 successes, 0%</p>" in page
    assert "<p class='count'>3 successes, 100%</p>" not in page
    assert {trial for trial, _, _ in rows} == {"stock-seed-01", "stock-seed-02", "stock-seed-03"}
    assert sum(evaluator == "livingproof-scenario" for _, evaluator, _ in rows) == 3
    assert sum(evaluator == "example-health" for _, evaluator, _ in rows) == 1
