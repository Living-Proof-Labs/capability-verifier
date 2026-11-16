# SPDX-License-Identifier: Apache-2.0
"""Compare a lock's evaluator pin with the verifier that installs it.

The lock names a repository, a release tag and a commit. The verifier checkout
requests that package in ``pyproject.toml``. ``uv.lock`` resolves the request to
a commit. Both must match the lock, and the tag must resolve to that commit.

``fetch_release_tag`` gives the evaluator checkout its release identity (#125).
actions/checkout fetches the locked commit shallow and without tags, so hatch-vcs
builds it as ``0.1.dev1``. The locked tag is fetched into that checkout only when the
checkout is the locked commit and the tag names that same commit. hatch-vcs then
reads the tag, and the installed package reports the release version.
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


RELEASE_TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], check=False, capture_output=True, text=True
    )


def fetch_release_tag(checkout: Path, tag: str, commit: str) -> str:
    """Fetch the locked ``tag`` into the evaluator ``checkout``; return its version.

    Refused, with nothing fetched, when ``tag`` is not ``vX.Y.Z`` or the checkout is
    not at ``commit``. Refused when the fetched tag names another commit (the tag is
    then deleted again, so the build cannot read it).
    """
    match = RELEASE_TAG.fullmatch(tag or "")
    if match is None:
        raise SystemExit(f"{LABEL}: {tag!r} is not a release tag (vX.Y.Z)")
    head = _git(checkout, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != commit:
        raise SystemExit(
            f"{LABEL}: the evaluator checkout is at {head.stdout.strip() or 'no commit'}, "
            f"not the locked commit {commit}"
        )
    ref = f"refs/tags/{tag}"
    fetched = _git(checkout, "fetch", "-q", "--no-tags", "--depth=1", "origin", f"+{ref}:{ref}")
    if fetched.returncode != 0:
        raise SystemExit(
            f"{LABEL}: cannot fetch tag {tag} into the evaluator checkout: {fetched.stderr.strip()}"
        )
    peeled = _git(checkout, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").stdout.strip()
    if peeled != commit:
        _git(checkout, "tag", "-d", tag)
        raise SystemExit(
            f"{LABEL}: tag {tag} is {peeled or 'missing'}, not the locked commit {commit}"
        )
    return match["version"]


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
    parser.add_argument(
        "--checkout",
        type=Path,
        help="evaluator checkout at the locked commit: fetch the locked tag into it",
    )
    args = parser.parse_args(argv)
    require_evaluator_pin(args.repository, args.tag, args.commit, args.verifier)
    if args.checkout is not None:
        print(fetch_release_tag(args.checkout, args.tag, args.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
