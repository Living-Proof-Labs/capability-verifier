# SPDX-License-Identifier: Apache-2.0
"""Direct checkout() coverage for #64 part 1: a reused dest is forced onto the pin.

The two tests in test_definition.py already cover the check()/run symptoms. These
exercise checkout() itself: dirty trees, a dest at another commit, untracked files,
a symlink that points outside dest, a pin that is not yet local, and a bad ref.
"""

import subprocess

import pytest

from capability_verifier import definition as d


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    base = tmp_path / "git"
    repo = base / "Org" / "robot.git"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tracked.txt").write_text("PINNED\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pin")
    pin = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-qb", "other")
    (repo / "tracked.txt").write_text("OTHER\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "other")
    other = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    monkeypatch.setattr(d, "GITHUB", f"file://{base}")
    return {"pin": pin, "other": other, "repo": "Org/robot"}


def test_checkout_restores_tracked_edits_on_the_same_commit(tmp_path, remote):
    dest = tmp_path / "dest"
    assert d.checkout(remote["repo"], remote["pin"], dest) == remote["pin"]
    (dest / "tracked.txt").write_text("DIRTY\n")
    assert d.checkout(remote["repo"], remote["pin"], dest) == remote["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]
    assert _git(dest, "status", "--porcelain").stdout == ""


def test_checkout_switches_a_dirty_tree_at_another_commit(tmp_path, remote):
    dest = tmp_path / "dest"
    assert d.checkout(remote["repo"], remote["other"], dest) == remote["other"]
    (dest / "tracked.txt").write_text("CONFLICT-DIRTY\n")
    assert d.checkout(remote["repo"], remote["pin"], dest) == remote["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]
    assert _git(dest, "status", "--porcelain").stdout == ""


def test_checkout_removes_untracked_files_and_keeps_venv(tmp_path, remote):
    dest = tmp_path / "dest"
    d.checkout(remote["repo"], remote["pin"], dest)
    (dest / "junk.txt").write_text("JUNK\n")
    (dest / ".venv").mkdir()
    (dest / ".venv" / "keep").write_text("VENV\n")
    d.checkout(remote["repo"], remote["pin"], dest)
    assert not (dest / "junk.txt").exists()
    assert (dest / ".venv" / "keep").read_text() == "VENV\n"
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]


def test_checkout_does_not_write_through_a_symlink_to_an_outside_file(tmp_path, remote):
    dest = tmp_path / "dest"
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("DO-NOT-TOUCH\n")
    d.checkout(remote["repo"], remote["pin"], dest)
    (dest / "tracked.txt").unlink()
    (dest / "tracked.txt").symlink_to(secret)
    d.checkout(remote["repo"], remote["pin"], dest)
    assert not (dest / "tracked.txt").is_symlink()
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert secret.read_text() == "DO-NOT-TOUCH\n"
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]
    assert _git(dest, "status", "--porcelain").stdout == ""


def test_checkout_does_not_delete_the_target_of_an_untracked_symlink(tmp_path, remote):
    dest = tmp_path / "dest"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "nested.txt").write_text("NESTED\n")
    d.checkout(remote["repo"], remote["pin"], dest)
    (dest / "escape").symlink_to(outside)
    d.checkout(remote["repo"], remote["pin"], dest)
    assert not (dest / "escape").exists()
    assert (outside / "nested.txt").read_text() == "NESTED\n"
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]
    assert _git(dest, "status", "--porcelain").stdout == ""


def test_checkout_fetches_a_pin_that_is_not_already_local(tmp_path, remote):
    dest = tmp_path / "dest"
    dest.mkdir()
    _git(dest, "init", "-q")
    _git(dest, "fetch", "-q", "--depth", "1", f"{d.GITHUB}/{remote['repo']}.git", remote["other"])
    _git(dest, "checkout", "-q", "--detach", "FETCH_HEAD")
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["other"]
    missing = _git(dest, "cat-file", "-t", remote["pin"], check=False)
    assert missing.returncode != 0
    assert d.checkout(remote["repo"], remote["pin"], dest) == remote["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert _git(dest, "status", "--porcelain").stdout == ""


def test_checkout_rejects_an_unknown_commit_and_leaves_the_tree(tmp_path, remote):
    dest = tmp_path / "dest"
    d.checkout(remote["repo"], remote["pin"], dest)
    unknown = "deadbeef" * 5
    with pytest.raises(subprocess.CalledProcessError) as exc:
        d.checkout(remote["repo"], unknown, dest)
    assert exc.value.returncode != 0
    assert _git(dest, "rev-parse", "HEAD").stdout.strip() == remote["pin"]
    assert (dest / "tracked.txt").read_text() == "PINNED\n"
    assert _git(dest, "status", "--porcelain").stdout == ""
