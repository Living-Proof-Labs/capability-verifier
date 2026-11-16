# SPDX-License-Identifier: Apache-2.0
"""Direct evaluator_pin coverage for #64 part 2.

The definition and workflow tests already cover check() and the scenario-evaluator
step. These exercise evaluator_pin itself: a matching pin, a mismatched sha, a
missing tag, a tag that names another commit, a tag that cannot be resolved, a
repository mismatch, and this tree's pyproject.toml / uv.lock format.
"""

from pathlib import Path

import pytest

from capability_verifier import evaluator_pin

REPO = "Living-Proof-Labs/livingproof-evaluator-scenario"
TAG = "v1.2.1"
COMMIT = "c6942f1b76ff86540e98639194a66795f242729a"
OTHER = "a" * 40
ROOT = Path(__file__).parents[1]


def _requested(tag: str, *, dotted_git: bool = True) -> str:
    suffix = ".git" if dotted_git else ""
    dependency = f"livingproof-evaluator-scenario @ git+https://github.com/{REPO}{suffix}@{tag}"
    return (
        "[project]\n"
        'name = "capability-verifier"\n'
        'version = "0.0.0"\n'
        "dependencies = [\n"
        f'  "{dependency}",\n'
        "]\n"
    )


def _resolved(tag: str, commit: str, *, dotted_git: bool = True) -> str:
    suffix = ".git" if dotted_git else ""
    source = f"https://github.com/{REPO}{suffix}?rev={tag}#{commit}"
    return (
        "[[package]]\n"
        'name = "livingproof-evaluator-scenario"\n'
        f'version = "{tag.removeprefix("v")}"\n'
        f'source = {{ git = "{source}" }}\n'
    )


def _verifier(tmp_path, *, tag=TAG, commit=COMMIT, dotted_git=True) -> Path:
    dest = tmp_path / "verifier"
    dest.mkdir()
    (dest / "pyproject.toml").write_text(_requested(tag, dotted_git=dotted_git))
    (dest / "uv.lock").write_text(_resolved(tag, commit, dotted_git=dotted_git))
    return dest


def _ok(repository, tag):
    if repository == REPO and tag == TAG:
        return COMMIT
    raise SystemExit(f"{repository} has no tag {tag}; release it first")


def test_a_matching_pin_has_no_problems(tmp_path):
    assert evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, _verifier(tmp_path), _ok) == []


def test_a_mismatched_resolved_commit_is_a_problem(tmp_path):
    dest = _verifier(tmp_path, commit=OTHER)
    problems = evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, dest, _ok)
    assert any("the verifier resolves" in item and OTHER in item for item in problems), problems


def test_a_missing_tag_is_a_problem(tmp_path):
    problems = evaluator_pin.evaluator_pin_problems(REPO, "", COMMIT, _verifier(tmp_path), _ok)
    assert any("tag is required" in item for item in problems), problems


def test_a_tag_that_names_another_commit_is_a_problem(tmp_path):
    def resolve(repository, tag):
        return OTHER

    problems = evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, _verifier(tmp_path), resolve)
    assert any("not the locked commit" in item and OTHER in item for item in problems), problems


def test_an_unresolvable_tag_is_a_problem(tmp_path):
    def resolve(repository, tag):
        raise SystemExit(f"{repository} has no tag {tag}; release it first")

    problems = evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, _verifier(tmp_path), resolve)
    assert any("cannot resolve tag" in item and TAG in item for item in problems), problems


def test_a_repository_mismatch_is_a_problem(tmp_path):
    problems = evaluator_pin.evaluator_pin_problems(
        "Example/fork", TAG, COMMIT, _verifier(tmp_path), lambda repository, tag: COMMIT
    )
    assert any("the verifier requests" in item for item in problems), problems
    assert any("the verifier resolves" in item for item in problems), problems


@pytest.mark.parametrize("dotted_git", [True, False])
def test_git_suffix_is_optional_on_requested_and_resolved_urls(tmp_path, dotted_git):
    dest = _verifier(tmp_path, dotted_git=dotted_git)
    assert evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, dest, _ok) == []


def test_a_missing_pyproject_or_lock_is_a_problem(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    problems = evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, empty, _ok)
    assert any("does not request" in item for item in problems), problems
    assert any("does not resolve" in item for item in problems), problems


def test_this_verifier_tree_matches_its_evaluator_pin():
    """The regexes must accept this repository's real pyproject.toml and uv.lock."""
    problems = evaluator_pin.evaluator_pin_problems(REPO, TAG, COMMIT, ROOT, _ok)
    assert problems == []


def test_cli_accepts_a_matching_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_pin, "resolve_evaluator_tag", _ok)
    dest = _verifier(tmp_path)
    assert (
        evaluator_pin.main(
            ["--repository", REPO, "--tag", TAG, "--commit", COMMIT, "--verifier", str(dest)]
        )
        == 0
    )


def test_cli_refuses_a_mismatched_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluator_pin, "resolve_evaluator_tag", _ok)
    dest = _verifier(tmp_path)
    with pytest.raises(SystemExit, match="the verifier resolves") as raised:
        evaluator_pin.main(
            ["--repository", REPO, "--tag", TAG, "--commit", OTHER, "--verifier", str(dest)]
        )
    assert "not the locked commit" in str(raised.value)
