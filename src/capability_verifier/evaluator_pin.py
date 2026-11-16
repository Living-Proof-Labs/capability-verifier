# SPDX-License-Identifier: Apache-2.0
"""Compare a lock's evaluator pin with the verifier that installs it.

The lock names a repository, a release tag and a commit. The verifier checkout
requests that package in ``pyproject.toml``. ``uv.lock`` resolves the request to
a commit. Both must match the lock, and the tag must resolve to that commit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from capability_verifier.repin import resolve as resolve_tag

LABEL = "evaluators.livingproof-evaluator-scenario"
PACKAGE = "livingproof-evaluator-scenario"
REQUESTED = re.compile(
    r"^livingproof-evaluator-scenario\s*@\s*git\+https://github\.com/"
    r"(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?@(?P<tag>v\d+\.\d+\.\d+)\s*$"
)
RESOLVED = re.compile(
    r"^https://github\.com/(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"\?rev=(?P<tag>v\d+\.\d+\.\d+)#(?P<commit>[0-9a-f]{40})$"
)


def resolve_evaluator_tag(repository: str, tag: str) -> str:
    """The commit ``tag`` names. An annotated tag is peeled, as ``repin.resolve`` does."""
    return resolve_tag(repository, tag)


def _requested(verifier: Path) -> dict | None:
    path = verifier / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        dependencies = tomllib.loads(path.read_text())["project"]["dependencies"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return None
    if not isinstance(dependencies, list):
        return None
    for item in dependencies:
        if not isinstance(item, str):
            continue
        match = REQUESTED.fullmatch(item.strip())
        if match:
            return match.groupdict()
    return None


def _resolved(verifier: Path) -> dict | None:
    path = verifier / "uv.lock"
    if not path.is_file():
        return None
    try:
        packages = tomllib.loads(path.read_text())["package"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return None
    if not isinstance(packages, list):
        return None
    for item in packages:
        if not isinstance(item, dict) or item.get("name") != PACKAGE:
            continue
        source = item.get("source")
        git = source.get("git") if isinstance(source, dict) else None
        if not isinstance(git, str):
            return None
        match = RESOLVED.fullmatch(git)
        return match.groupdict() if match else None
    return None


def evaluator_pin_problems(
    repository: str, tag: str, commit: str, verifier: Path, resolver=None
) -> list[str]:
    """Problems when ``verifier`` does not install the locked evaluator pin."""
    problems = []
    if not tag:
        problems.append(f"{LABEL}: tag is required")
    requested = _requested(verifier)
    if requested is None:
        problems.append(f"{LABEL}: the verifier pyproject.toml does not request {PACKAGE}")
    elif (requested["repository"], requested["tag"]) != (repository, tag):
        problems.append(
            f"{LABEL}: the verifier requests {requested['repository']}@{requested['tag']}, "
            f"not the locked {repository}@{tag}"
        )
    resolved = _resolved(verifier)
    if resolved is None:
        problems.append(f"{LABEL}: the verifier uv.lock does not resolve {PACKAGE}")
    elif (resolved["repository"], resolved["tag"], resolved["commit"]) != (
        repository,
        tag,
        commit,
    ):
        problems.append(
            f"{LABEL}: the verifier resolves {resolved['repository']}@{resolved['tag']}"
            f"#{resolved['commit']}, not the locked {repository}@{tag}#{commit}"
        )
    if tag:
        lookup = resolver or resolve_evaluator_tag
        try:
            resolved_commit = lookup(repository, tag)
        except (SystemExit, subprocess.SubprocessError, OSError) as exc:
            problems.append(f"{LABEL}: cannot resolve tag {tag} of {repository}: {exc}")
        else:
            if resolved_commit != commit:
                problems.append(
                    f"{LABEL}: tag {tag} is {resolved_commit}, not the locked commit {commit}"
                )
    return problems


def require_evaluator_pin(
    repository: str, tag: str, commit: str, verifier: Path, resolver=None
) -> None:
    problems = evaluator_pin_problems(repository, tag, commit, verifier, resolver)
    if problems:
        raise SystemExit("\n".join(problems))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", default="", help="release tag; empty is refused")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    args = parser.parse_args(argv)
    require_evaluator_pin(args.repository, args.tag, args.commit, args.verifier)
    return 0


if __name__ == "__main__":
    sys.exit(main())
