# SPDX-License-Identifier: Apache-2.0
"""The specs files the verifier loaded are checked and recorded (capability-verifier#153).

``tools/fetch_hallway_specs.py --from MANIFEST --record PATH`` clones the specs commit the
evidence lock declared, checks every file in ``releases.specs.files`` against its
declared sha256, and only then writes ``livingproof-specs-files/0.1``: the sha256 of the
bytes it read and the commit the checkout resolved to. A mismatch exits non-zero and no
record is written, so a record never claims a match that did not happen.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = "specs/scenarios/walk-hallway/description.json"
PARAMETERS = "specs/scenarios/walk-hallway/parameters.json"
FILES = {
    DESCRIPTION: b'{"format": "livingproof-scenario-description/0.1"}\n',
    PARAMETERS: b'{"parameters": {}}\n',
}


def _tool():
    spec = importlib.util.spec_from_file_location(
        "fetch_hallway_specs_under_test", ROOT / "tools" / "fetch_hallway_specs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        text=True,
    ).strip()


def _specs_repo(tmp_path: Path, files: dict[str, bytes] | None = None) -> tuple[Path, str]:
    """A local git repository standing in for robot-capability-specs, and its commit."""
    repo = tmp_path / "specs.git"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    for name, data in (FILES if files is None else files).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "specs", cwd=repo)
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _manifest(tmp_path: Path, repo: Path, commit: str, declared: dict | None) -> Path:
    specs = {"repository": str(repo), "tag": "v9.9.9", "commit": commit}
    if declared is not None:
        specs["files"] = declared
    document = {
        "format": "livingproof-hallway-evidence/2.0.0",
        "run_id": "run-under-test",
        "lock": {"content": {"releases": {"specs": specs}}},
    }
    path = tmp_path / "evidence" / "manifest.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def _declared(files: dict[str, bytes] | None = None) -> dict:
    return {name: _sha(data) for name, data in (FILES if files is None else files).items()}


def _fetch(tmp_path: Path, manifest: Path, record: Path | None):
    return _tool().fetch_from_manifest(
        manifest,
        record=record,
        specs_dir=tmp_path / "checkout-specs",
        worlds_dir=tmp_path / "checkout-worlds",
    )


def test_a_matching_run_records_the_digest_of_each_file_it_read(tmp_path):
    repo, commit = _specs_repo(tmp_path)
    manifest = _manifest(tmp_path, repo, commit, _declared())
    record = tmp_path / "evidence" / "specs-files.json"
    _fetch(tmp_path, manifest, record)
    written = json.loads(record.read_text())
    assert written == {
        "format": "livingproof-specs-files/0.1",
        "run_id": "run-under-test",
        "repository": str(repo),
        "tag": "v9.9.9",
        "commit": commit,
        "files": {name: _sha(data) for name, data in FILES.items()},
    }


def test_the_record_is_the_checkout_bytes_not_the_lock_values(tmp_path):
    """The digest comes from the file that was read, so the check is not the lock vs itself."""
    repo, commit = _specs_repo(tmp_path)
    manifest = _manifest(tmp_path, repo, commit, _declared())
    record = tmp_path / "record.json"
    _fetch(tmp_path, manifest, record)
    checkout = tmp_path / "checkout-specs"
    written = json.loads(record.read_text())["files"]
    for name in FILES:
        assert written[name] == _sha((checkout / name).read_bytes())


@pytest.mark.parametrize("name", [DESCRIPTION, PARAMETERS])
def test_a_file_that_differs_from_the_lock_fails_and_writes_no_record(tmp_path, name):
    repo, commit = _specs_repo(tmp_path)
    declared = _declared()
    declared[name] = "0" * 64
    manifest = _manifest(tmp_path, repo, commit, declared)
    record = tmp_path / "record.json"
    with pytest.raises(SystemExit, match=f"differs from the lock: {name}"):
        _fetch(tmp_path, manifest, record)
    assert not record.exists()


def test_a_failed_run_removes_a_record_left_by_an_earlier_one(tmp_path):
    repo, commit = _specs_repo(tmp_path)
    record = tmp_path / "record.json"
    record.write_text("stale")
    declared = _declared()
    declared[DESCRIPTION] = "0" * 64
    manifest = _manifest(tmp_path, repo, commit, declared)
    with pytest.raises(SystemExit):
        _fetch(tmp_path, manifest, record)
    assert not record.exists()


def test_a_declared_file_missing_from_the_checkout_fails(tmp_path):
    repo, commit = _specs_repo(tmp_path)
    declared = _declared()
    declared["specs/scenarios/walk-hallway/missing.json"] = "0" * 64
    manifest = _manifest(tmp_path, repo, commit, declared)
    record = tmp_path / "record.json"
    with pytest.raises(SystemExit, match="missing.json"):
        _fetch(tmp_path, manifest, record)
    assert not record.exists()


@pytest.mark.parametrize("path", ["../outside.json", "/etc/hostname"])
def test_a_declared_path_outside_the_checkout_is_refused(tmp_path, path):
    repo, commit = _specs_repo(tmp_path)
    manifest = _manifest(tmp_path, repo, commit, {path: "0" * 64})
    record = tmp_path / "record.json"
    with pytest.raises(SystemExit, match="inside the specs checkout"):
        _fetch(tmp_path, manifest, record)
    assert not record.exists()


def test_a_lock_that_declares_no_files_records_none(tmp_path):
    repo, commit = _specs_repo(tmp_path)
    manifest = _manifest(tmp_path, repo, commit, None)
    record = tmp_path / "record.json"
    _fetch(tmp_path, manifest, record)
    assert json.loads(record.read_text())["files"] == {}


def test_without_record_the_files_are_still_checked_and_nothing_is_written(tmp_path):
    repo, commit = _specs_repo(tmp_path)
    good = _manifest(tmp_path, repo, commit, _declared())
    _fetch(tmp_path, good, None)
    assert not list(tmp_path.glob("**/specs-files.json"))
    declared = _declared()
    declared[PARAMETERS] = "0" * 64
    bad = _manifest(tmp_path, repo, commit, declared)
    with pytest.raises(SystemExit, match="differs from the lock"):
        _fetch(tmp_path, bad, None)


def test_command_line_takes_record_only_with_from():
    tool = _tool()
    with pytest.raises(SystemExit, match="usage"):
        tool.main(["--record", "x.json"])
    with pytest.raises(SystemExit, match="usage"):
        tool.main(["--from"])


def test_a_symlink_inside_the_checkout_that_points_outside_is_refused(tmp_path):
    """A path that stays lexically inside the checkout is still refused if it is a symlink out.

    The declared digest is of the outside bytes, so a check that followed the link and skipped
    the boundary would pass and write a record.
    """
    outside = tmp_path / "outside.json"
    outside.write_bytes(FILES[DESCRIPTION])
    repo = tmp_path / "specs.git"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    dest = repo / DESCRIPTION
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(outside)
    (repo / PARAMETERS).write_bytes(FILES[PARAMETERS])
    _git("add", ".", cwd=repo)
    _git("commit", "-q", "-m", "specs", cwd=repo)
    commit = _git("rev-parse", "HEAD", cwd=repo)
    manifest = _manifest(tmp_path, repo, commit, _declared())
    record = tmp_path / "record.json"
    with pytest.raises(SystemExit, match="inside the specs checkout"):
        _fetch(tmp_path, manifest, record)
    assert not record.exists()


def test_the_record_comes_from_one_read_of_each_declared_file(tmp_path, monkeypatch):
    """The record must use the bytes from the check, not a later read of the checkout."""
    import pathlib

    repo, commit = _specs_repo(tmp_path)
    manifest = _manifest(tmp_path, repo, commit, _declared())
    record = tmp_path / "record.json"
    orig = pathlib.Path.read_bytes
    reads: dict[str, int] = {}

    def once_then_tamper(self: Path) -> bytes:
        data = orig(self)
        name = self.name
        if name in {"description.json", "parameters.json"}:
            reads[name] = reads.get(name, 0) + 1
            if reads[name] > 1:
                return data + b"#tampered"
        return data

    monkeypatch.setattr(pathlib.Path, "read_bytes", once_then_tamper)
    _fetch(tmp_path, manifest, record)
    written = json.loads(record.read_text())["files"]
    for name, data in FILES.items():
        assert written[name] == _sha(data)
    assert reads["description.json"] == 1
    assert reads["parameters.json"] == 1


def test_the_recorded_digest_is_the_hash_of_the_bytes_read_not_the_lock_value():
    """A matching run cannot tell `read[name] = actual` from `read[name] = expected`.

    The record is only written when those two are equal, so Cody's behavioural test stays
    green if the implementation copies the lock. This pins the assignment in
    `_verify_specs_files`.
    """
    tree = ast.parse((ROOT / "tools" / "fetch_hallway_specs.py").read_text())
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_verify_specs_files"
    )
    body = ast.unparse(fn)
    assert "actual = hashlib.sha256(path.read_bytes()).hexdigest()" in body
    assert "read[name] = actual" in body
    assert "read[name] = expected" not in body


def _job(name: str) -> dict:
    document = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
    return document["jobs"][name]


def test_the_run_integrity_job_writes_the_record_beside_the_evidence_manifest():
    steps = _job("run-integrity")["steps"]
    index = {step.get("name"): i for i, step in enumerate(steps)}
    fetch = next(s for s in steps if "fetch_hallway_specs.py" in s.get("run", ""))
    assert "--from evidence/manifest.json --record evidence/specs-files.json" in fetch["run"], (
        fetch["run"]
    )
    evaluate = index["Run Integrity, one result for the run"]
    download = next(i for i, s in enumerate(steps) if "download-artifact" in s.get("uses", ""))
    assert download < steps.index(fetch) < evaluate
    # The record is kept with the integrity result.
    text = json.dumps(steps[evaluate])
    assert "specs-files.json" in text


def test_the_record_is_copied_beside_the_result_before_the_evaluator_runs():
    """Steps run under bash -e, so a copy placed after the evaluator is skipped when the
    evaluator fails, and the uploaded artifact then lacks the record exactly when it is
    most needed. The copy must come before the evaluator command."""
    steps = _job("run-integrity")["steps"]
    run = next(s for s in steps if s.get("name") == "Run Integrity, one result for the run")["run"]
    copy = run.index(
        "cp evidence/specs-files.json evaluator-results/livingproof-run-integrity/specs-files.json"
    )
    assert copy < run.index("python -m livingproof_evaluator_run"), run
