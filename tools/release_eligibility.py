# SPDX-License-Identifier: Apache-2.0
"""Suppress only the development self-image release feedback edge."""

import json
import re
import subprocess
from pathlib import Path

SELF_PIN = ".devcontainer/hallway/devcontainer.json"
VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
IMAGE = re.compile(
    r"ghcr\.io/living-proof-labs/capability-verifier-hallway:"
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)@sha256:[0-9a-f]{64}\Z"
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def self_pin_only(root: Path, before: str, after: str) -> bool:
    if git(root, "diff", "--name-only", before, after, "--") != SELF_PIN:
        return False
    for revision in (before, after):
        if not git(root, "ls-tree", revision, "--", SELF_PIN).startswith("100644 blob "):
            return False
    try:
        old = json.loads(git(root, "show", f"{before}:{SELF_PIN}"))
        new = json.loads(git(root, "show", f"{after}:{SELF_PIN}"))
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        old_image, new_image = old.pop("image", None), new.pop("image", None)
        return (
            isinstance(old_image, str)
            and isinstance(new_image, str)
            and IMAGE.fullmatch(old_image) is not None
            and IMAGE.fullmatch(new_image) is not None
            and old_image != new_image
            and old == new
        )
    except (json.JSONDecodeError, subprocess.CalledProcessError):
        return False


def eligible(root: Path) -> tuple[bool, str]:
    """Inspect every first-parent transition, not merely the final aggregate diff.

    This preserves release behavior for mixed changes and fixes subsequently reverted.
    Unknown or absent release history never suppresses a release.
    """
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        return True, "shallow history; preserve normal release policy"
    tags = [
        (tuple(map(int, match.groups())), tag)
        for tag in git(root, "tag", "--merged", "HEAD").splitlines()
        if (match := VERSION.fullmatch(tag))
    ]
    if not tags:
        return True, "no reachable stable release; preserve normal release policy"
    base = max(tags)[1]
    commits = git(root, "rev-list", "--first-parent", "--reverse", f"{base}..HEAD").splitlines()
    if not commits:
        return True, "no new commits; shared release policy decides"
    # An unusual tag on a side branch must not hide mainline changes.
    ancestors = git(root, "rev-list", "--first-parent", "HEAD").splitlines()
    if git(root, "rev-parse", f"{base}^{{commit}}") not in ancestors:
        return True, "release is not on first-parent history; preserve normal release policy"
    changed = False
    for commit in commits:
        before = git(root, "rev-parse", f"{commit}^1")
        if not git(root, "diff", "--name-only", before, commit, "--"):
            continue
        if not self_pin_only(root, before, commit):
            return True, f"substantive or other maintenance changes since {base}"
        changed = True
    if not changed:
        return True, "empty history changes; shared release policy decides"
    return False, f"only pinned hallway development self-image refreshes since {base}"


if __name__ == "__main__":
    release, reason = eligible(Path.cwd())
    print(f"eligible={str(release).lower()}")
    print(f"reason={reason}")
