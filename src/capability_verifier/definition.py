#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a declarative verification locally through the same steps as the reusable workflow.

A verification project (for example ``livingproof-verification``) keeps only a definition,
its reviewed lock and a thin workflow call. This module owns the rest:

    python3 -m capability_verifier.definition lock DEFINITION.toml [--check] [--workflow FILE]
    python3 -m capability_verifier.definition run DEFINITION.toml --out DIR
    livingproof capability verify lock|run ...   (the same, through the livingproof plugin)

``lock`` resolves the definition's robot ref to a commit and records that commit, the sha256
of the robot's ``verification.lock.json`` and the releases it names (runner, verifier,
reporter image digest, specs) for review. ``--check`` re-resolves nothing: it fails when the
definition changed since locking, when the robot lock at the locked commit no longer has the
recorded sha256, or when ``--workflow`` does not call ``walk-hallway.yml`` at the locked
verifier commit with the locked robot repository and commit.

``run`` checks the lock, checks out the robot at the locked commit and the verifier at the
commit the robot lock names, then executes the named ``run:`` scripts of that verifier
commit's ``walk-hallway.yml`` (plan gate, setup, evidence, catalog, assessment, render) in
the same directories CI uses. No step logic is copied here, so local and CI runs share one
definition of each step. Rendering uses the locked reporter image by digest (``docker``),
or the reporter source at the locked commit (``--reporter source``).
Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

FORMAT = "livingproof-verification-lock/0.1.0"
DEFINITION_FORMAT = "livingproof-verification-definition/0.1.0"
PROCEDURES = {"walk-hallway": ".github/workflows/walk-hallway.yml"}
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
GITHUB = os.environ.get("LIVINGPROOF_GIT_BASE", "https://github.com")


class DefinitionError(SystemExit):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_definition(path: Path) -> dict:
    d = tomllib.loads(path.read_text())
    problems = []
    if d.get("format") != DEFINITION_FORMAT:
        problems.append(f"format must be {DEFINITION_FORMAT!r}")
    if d.get("procedure") not in PROCEDURES:
        problems.append(f"procedure must be one of {sorted(PROCEDURES)}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(d.get("id", ""))):
        problems.append("id must be lower-case letters, digits and dashes")
    robot = d.get("robot", {})
    if not REPO.fullmatch(str(robot.get("repository", ""))):
        problems.append("robot.repository must be OWNER/NAME")
    if not robot.get("ref"):
        problems.append("robot.ref is required (a branch, tag or commit)")
    confs = robot.get("configurations")
    if not (isinstance(confs, list) and confs and all(isinstance(c, str) for c in confs)):
        problems.append("robot.configurations must be a non-empty list of names")
    if problems:
        raise DefinitionError(f"{path}: " + "; ".join(problems))
    return d


def lock_path(definition: Path) -> Path:
    return definition.with_name(definition.stem + ".lock.json")


def git(*args, cwd=None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def checkout(repository: str, ref: str, dest: Path) -> str:
    """Fetch only ``ref`` of ``repository`` into ``dest`` (detached); return the commit."""
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dest)
    git("fetch", "-q", "--depth", "1", f"{GITHUB}/{repository}.git", ref, cwd=dest)
    git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
    git("clean", "-qfdx", "-e", ".venv", cwd=dest)
    return git("rev-parse", "HEAD", cwd=dest)


def show(repository: str, commit: str, path: str, work: Path) -> bytes:
    checkout(repository, commit, work)
    return (work / path).read_bytes()


def resolve(definition: Path, work: Path) -> dict:
    d = load_definition(definition)
    robot = d["robot"]
    dest = work / "robot"
    commit = checkout(robot["repository"], robot["ref"], dest)
    raw = (dest / "verification.lock.json").read_bytes()
    robot_lock = json.loads(raw)
    names = [c["name"] for c in robot_lock.get("configurations", [])]
    missing = [c for c in robot["configurations"] if c not in names]
    if missing:
        raise DefinitionError(f"{robot['repository']}@{commit[:12]} has no configuration {missing}")
    return {
        "format": FORMAT,
        "definition": {"path": definition.name, "sha256": sha256(definition.read_bytes())},
        "procedure": d["procedure"],
        "robot": {
            "repository": robot["repository"],
            "ref": robot["ref"],
            "commit": commit,
            "configurations": robot["configurations"],
            "lock": {"path": "verification.lock.json", "sha256": sha256(raw)},
        },
        "releases": robot_lock["releases"],
    }


def workflow_problems(lock: dict, workflow: Path) -> list[str]:
    text = workflow.read_text()
    v = lock["releases"]["verifier"]
    uses = f"uses: {v['repository']}/{PROCEDURES[lock['procedure']]}@{v['commit']}"
    want = {
        uses: f"call {uses.split(': ', 1)[1]}",
        f"repository: {lock['robot']['repository']}": "pass the locked robot repository",
        f"ref: {lock['robot']['commit']}": "pass the locked robot commit",
        f"configurations: '{json.dumps(lock['robot']['configurations'])}'": (
            "pass the locked configurations"
        ),
    }
    return [f"{workflow}: must {why} ({line})" for line, why in want.items() if line not in text]


def check(definition: Path, work: Path, workflow: Path | None = None) -> dict:
    path = lock_path(definition)
    if not path.exists():
        raise DefinitionError(f"{path} is missing; run `lock {definition}` and review it")
    lock = json.loads(path.read_text())
    load_definition(definition)
    problems = []
    if lock.get("format") != FORMAT:
        problems.append(f"{path.name}: format is not {FORMAT}")
    if lock["definition"]["sha256"] != sha256(definition.read_bytes()):
        problems.append(f"{definition.name} changed since it was locked; re-run lock and review")
    r = lock["robot"]
    if not SHA.fullmatch(r["commit"]):
        problems.append("robot.commit is not a full commit sha")
    else:
        raw = show(r["repository"], r["commit"], r["lock"]["path"], work / "robot")
        if sha256(raw) != r["lock"]["sha256"]:
            problems.append(f"{r['repository']}@{r['commit'][:12]} lock differs from {path.name}")
        elif json.loads(raw)["releases"] != lock["releases"]:
            problems.append(f"releases in {path.name} differ from the robot lock")
    if workflow is not None:
        problems += workflow_problems(lock, workflow)
    if problems:
        raise DefinitionError("\n".join(problems))
    return lock


def step(workflow: str, name: str) -> str:
    """The ``run: |`` script of the first step whose name starts with ``name``."""
    lines = workflow.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- name: ") and stripped[8:].startswith(name):
            indent = len(line) - len(line.lstrip()) + 2
            for j in range(i + 1, len(lines)):
                body = lines[j].strip()
                if body.startswith("- ") and len(lines[j]) - len(lines[j].lstrip()) < indent:
                    break
                if body.startswith("run: |"):
                    out = []
                    for raw in lines[j + 1 :]:
                        if raw.strip() and len(raw) - len(raw.lstrip()) <= indent:
                            break
                        out.append(raw[indent + 2 :])
                    return "\n".join(out).strip() + "\n"
                if body.startswith("run: "):
                    return body[5:] + "\n"
            break
    raise DefinitionError(f"walk-hallway.yml has no step {name!r} with a run script")


def sh(script: str, cwd: Path, env: dict, outputs: Path | None = None) -> dict:
    env = {**os.environ, **env}
    if outputs is not None:
        outputs.write_text("")
        env["GITHUB_OUTPUT"] = str(outputs)
    # CI provisions tools with `pip install --user`; locally uv provides them (see run()).
    script = "\n".join(
        line for line in script.splitlines() if not line.startswith("python3 -m pip install")
    )
    print(f"$ ({cwd.name}) " + script.splitlines()[0], file=sys.stderr, flush=True)
    subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=env, check=True)
    if outputs is None:
        return {}
    return dict(line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line)


def reporter_shim(lock: dict, mode: str, work: Path) -> Path:
    rep = lock["releases"]["reporter"]
    bindir = work / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "livingproof-capability-report"
    if mode == "docker":
        image = f"{rep['image']}:{rep['tag'].lstrip('v')}@{rep['digest']}"
        cmd = (
            f'exec docker run --rm --network none -u "$(id -u):$(id -g)" '
            f'-v "$PWD:$PWD" -w "$PWD" {shlex.quote(image)} "$@"'
        )
    else:
        src = work / "reporter"
        checkout(rep["repository"], rep["commit"], src)
        subprocess.run(["uv", "sync", "--locked", "-q"], cwd=src, check=True)
        cmd = f'exec uv run --project {shlex.quote(str(src))} --no-sync livingproof-capability-report "$@"'
    shim.write_text(f"#!/bin/sh\n{cmd}\n")
    shim.chmod(0o755)
    return bindir


def tally(verification: Path) -> dict:
    statuses = [t.get("status") for t in json.loads(verification.read_text())["trials"]]
    return {s: statuses.count(s) for s in sorted(set(statuses))}


def run(definition: Path, out: Path, *, reporter: str = "docker") -> dict:
    out = out.resolve()
    work = out / ".work"
    lock = check(definition, work)
    v = lock["releases"]["verifier"]
    robot, verifier = work / "robot", work / "verifier"
    checkout(v["repository"], v["commit"], verifier)
    wf = (verifier / PROCEDURES[lock["procedure"]]).read_text()
    gh = {"MUJOCO_GL": "osmesa", "PYOPENGL_PLATFORM": "osmesa"}
    plan = sh(
        f"uv run --no-project --with packaging==26.3 -- bash -e <<'LP'\n"
        f"{step(wf, 'Media and bundle capability')}LP\n",
        robot,
        gh,
        work / "plan.out",
    )
    bundle = plan["bundle-version"]
    sh(step(wf, "Setup (locked environment"), robot, gh)
    adapter = sh(step(wf, "Robot adapter from pyproject.toml"), robot, gh, work / "a.out")["ref"]
    subprocess.run(["uv", "sync", "--locked", "-q"], cwd=verifier, check=True)
    sh(step(wf, "Walk-hallway catalog"), verifier, gh)
    bindir = reporter_shim(lock, reporter, work)
    summary = {"definition": definition.name, "bundle-version": bundle, "configurations": {}}
    for conf in lock["robot"]["configurations"]:
        env = {
            **gh,
            "CONFIGURATION": conf,
            "ADAPTER": adapter,
            "BUNDLE_VERSION": bundle,
            "LIVINGPROOF_VERIFIER_VERSION": v["tag"].lstrip("v"),
        }
        shutil.rmtree(robot / "artifacts" / "evidence" / conf, ignore_errors=True)
        sh(step(wf, "Evidence (observations only"), robot, env)
        dest = out / conf
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        shutil.copytree(robot / "artifacts" / "evidence" / conf, dest / "evidence")
        for name in ("evidence", "verified"):
            shutil.rmtree(verifier / name, ignore_errors=True)
        shutil.copytree(dest / "evidence", verifier / "evidence")
        sh(step(wf, "Assess every trial"), verifier, env)
        shutil.move(verifier / "verified", dest / "verified")
        shutil.rmtree(verifier / "evidence")
        env["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        sh(step(wf, "Validate, then render"), dest, env)
        summary["configurations"][conf] = tally(dest / "verified" / "verification.json")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="capability_verifier.definition", description=__doc__.split("\n\n")[0]
    )
    sub = ap.add_subparsers(dest="command", required=True)
    lk = sub.add_parser("lock", help="resolve the definition into its reviewed lock, or --check it")
    lk.add_argument("definition", type=Path)
    lk.add_argument("--check", action="store_true")
    lk.add_argument("--workflow", type=Path, help="also check this thin workflow call")
    lk.add_argument("--work", type=Path, default=Path("artifacts/.lock-work"))
    rn = sub.add_parser("run", help="run the locked verification locally")
    rn.add_argument("definition", type=Path)
    rn.add_argument("--out", type=Path, default=Path("artifacts/runs"))
    rn.add_argument("--reporter", choices=("docker", "source"), default="docker")
    a = ap.parse_args(argv)
    if a.command == "lock" and a.check:
        lock = check(a.definition, a.work, a.workflow)
        print(
            f"{lock_path(a.definition).name}: robot {lock['robot']['repository']}@"
            f"{lock['robot']['commit'][:12]}, verifier {lock['releases']['verifier']['tag']}: ok"
        )
    elif a.command == "lock":
        lock = resolve(a.definition, a.work)
        lock_path(a.definition).write_text(json.dumps(lock, indent=2) + "\n")
        print(f"wrote {lock_path(a.definition)}; review it before committing")
    else:
        summary = run(
            a.definition, a.out / load_definition(a.definition)["id"], reporter=a.reporter
        )
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
