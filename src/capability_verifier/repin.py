#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fill PENDING_RELEASE refs in a robot repository's verification.lock.json from real
release tags, or check them. Owned by the capability verifier; robot repositories call it
(the reusable walk-hallway workflow runs ``--check`` from the verifier checkout).

    python3 -m capability_verifier.repin --root ROBOT_REPO --check
    python3 path/to/repin.py --root ROBOT_REPO [--dry-run] runner=v1.4.0 verifier=v1.8.0

Each tag is resolved with ``git ls-remote`` against the entry's GitHub repository
(peeled for annotated tags). A tag that does not exist is an error, never a guess. An entry
with an ``image`` (the reporter) also records ``digest``: the registry's manifest digest for
``image:X.Y.Z``, read anonymously from GHCR, so CI runs ``image:X.Y.Z@digest``. Pinning
the runner also rewrites its ``[tool.uv.sources]`` line in pyproject.toml to that tag and
reruns ``uv lock``. The workflow reads every other ref straight from the lock.

``--check`` fails while any ref is PENDING_RELEASE, while an image entry has no sha256 digest,
while the runner source in
pyproject.toml is not the locked tag, and if any dependency is path-pinned.
Stdlib only, so CI can run it before installing anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

LOCK = "verification.lock.json"
PYPROJECT = "pyproject.toml"
PENDING = "PENDING_RELEASE"
TAG = re.compile(r"v\d+\.\d+\.\d+")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MANIFESTS = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
RUNNER_DIST = "livingproof-sim-mujoco"
SOURCE = re.compile(rf"^{RUNNER_DIST}\s*=\s*\{{(?P<body>[^}}]*)\}}", re.M)


def resolve(repository: str, tag: str) -> str:
    url = f"https://github.com/{repository}.git"
    out = subprocess.run(
        ["git", "ls-remote", "--tags", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    refs = dict(reversed(line.split("\t")) for line in out.splitlines() if "\t" in line)
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit or not SHA.fullmatch(commit):
        raise SystemExit(
            f"{repository} has no tag {tag}; release it first (tags are never invented)"
        )
    return commit


def image_digest(image: str, tag: str) -> str:
    """Manifest digest of a public ghcr.io image tag (``tag`` without the leading v)."""
    host, _, name = image.partition("/")
    if host != "ghcr.io":
        raise SystemExit(f"{image}: only ghcr.io images are supported")
    with urllib.request.urlopen(
        f"https://ghcr.io/token?scope=repository:{name}:pull", timeout=60
    ) as r:
        token = json.load(r)["token"]
    req = urllib.request.Request(
        f"https://ghcr.io/v2/{name}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "Accept": MANIFESTS},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            digest = r.headers.get("Docker-Content-Digest", "")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{image}:{tag} is not published ({e.code}); release it first") from e
    if not DIGEST.fullmatch(digest):
        raise SystemExit(f"{image}:{tag}: registry returned no sha256 digest")
    return digest


def runner_source(text: str) -> str | None:
    m = SOURCE.search(text)
    return m.group("body").strip() if m else None


def check(lock: dict, pyproject: str) -> list[str]:
    problems = [
        f"releases.{name} is {PENDING} (expected {ref.get('expected', 'a release tag')})"
        for name, ref in lock["releases"].items()
        if PENDING in (ref.get("tag"), ref.get("commit"))
    ]
    for name, ref in lock["releases"].items():
        if ref.get("tag") != PENDING and not (
            TAG.fullmatch(ref.get("tag", "")) and SHA.fullmatch(ref.get("commit", ""))
        ):
            problems.append(f"releases.{name} needs a vX.Y.Z tag and a full commit")
        if (
            "image" in ref
            and ref.get("tag") != PENDING
            and not DIGEST.fullmatch(ref.get("digest", ""))
        ):
            problems.append(f"releases.{name} has an image but no sha256 digest")
    if re.search(r"^\S+\s*=\s*\{[^}]*\bpath\s*=", pyproject, re.M):
        problems.append("pyproject.toml has a path-pinned dependency")
    runner = lock["releases"]["runner"]
    body = runner_source(pyproject) or ""
    if runner["tag"] != PENDING and f'tag = "{runner["tag"]}"' not in body:
        problems.append(f"pyproject.toml {RUNNER_DIST} source is not tag {runner['tag']}: {body}")
    return problems


def repin(
    lock: dict, pyproject: str, pins: dict[str, str], resolver=resolve, digester=image_digest
):
    """Return (lock, pyproject, changes) with every named entry resolved."""
    lock = json.loads(json.dumps(lock))
    changes = []
    for name, tag in pins.items():
        if name not in lock["releases"]:
            raise SystemExit(f"unknown entry {name}; one of {', '.join(lock['releases'])}")
        if not TAG.fullmatch(tag):
            raise SystemExit(f"{name}={tag}: expected vX.Y.Z")
        ref = lock["releases"][name]
        commit = resolver(ref["repository"], tag)
        changes.append(f"releases.{name}: {ref['tag']} {ref['commit']} -> {tag} {commit}")
        ref.update(tag=tag, commit=commit)
        ref.pop("expected", None)
        if "image" in ref:
            old = ref.get("digest", PENDING)
            ref["digest"] = digester(ref["image"], tag.removeprefix("v"))
            changes.append(f"releases.{name}.digest: {old} -> {ref['digest']}")
    if "runner" in pins:
        ref = lock["releases"]["runner"]
        new = f'git = "https://github.com/{ref["repository"]}.git", tag = "{ref["tag"]}"'
        old = runner_source(pyproject)
        if old is None:
            raise SystemExit(f"pyproject.toml has no [tool.uv.sources] {RUNNER_DIST} line")
        pyproject = SOURCE.sub(f"{RUNNER_DIST} = {{ {new} }}", pyproject, count=1)
        changes.append(f"pyproject.toml {RUNNER_DIST}: {{ {old} }} -> {{ {new} }}")
        changes.append("uv.lock: regenerated by uv lock")
    return lock, pyproject, changes


def dump(lock: dict) -> str:
    """indent=2 JSON with lists of scalars kept on one line, as the lock is hand-written."""
    text = json.dumps(lock, indent=2)
    return (
        re.sub(
            r"\[\s*([^\[\]{}]*?)\s*\]",
            lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]"
            if m.group(1)
            else "[]",
            text,
        )
        + "\n"
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("pins", nargs="*", metavar="entry=vX.Y.Z")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", type=Path, default=Path("."), help="robot repository (default .)")
    ap.add_argument("--lock", default=LOCK, help=f"lock path under --root (default {LOCK})")
    a = ap.parse_args(argv)
    lock_path, pyproject_path = a.root / a.lock, a.root / PYPROJECT
    lock, pyproject = json.loads(lock_path.read_text()), pyproject_path.read_text()
    if a.check:
        problems = check(lock, pyproject)
        for p in problems:
            print(f"NOT RELEASED: {p}")
        if not problems:
            print(f"{a.lock}: every ref is a released tag")
        return 1 if problems else 0
    if not a.pins:
        ap.error("name at least one entry=vX.Y.Z, or pass --check")
    pins = dict(p.split("=", 1) for p in a.pins)
    lock, pyproject, changes = repin(lock, pyproject, pins)
    print("\n".join(changes))
    if a.dry_run:
        return 0
    lock_path.write_text(dump(lock))
    pyproject_path.write_text(pyproject)
    if "runner" in pins:
        subprocess.run(["uv", "lock"], cwd=a.root, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
