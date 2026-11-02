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
verifier commit with the locked robot repository and commit. Only the calls for this
definition are checked (job id equal to the definition id, or ``with.repository`` equal to the
locked robot repository, compared case-insensitively without ``.git``), so one workflow can
hold several robots. A call that belongs to no definition beside this one (an unknown
repository or an expression) is refused.

``run`` checks the lock, checks out the robot at the locked commit and the verifier at the
commit the robot lock names, then executes the named ``run:`` scripts of that verifier
commit's ``walk-hallway.yml`` in the same directories CI uses: the plan job's checks (refs
from the robot lock, every ref a released tag, media and bundle capability), setup, robot
adapter, profiles valid, catalog, evidence, assessment and render. The robot's own test suite
(``uv run pytest tests``) is CI-only. No step logic is copied here, so local and CI runs share one
definition of each step. Rendering uses the locked reporter image by digest (``docker``),
or the reporter source at the locked commit (``--reporter source``).
The workflow call is checked by parsing its YAML (PyYAML), never by matching text.
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

import yaml

FORMAT = "livingproof-verification-lock/0.1.0"
DEFINITION_FORMAT = "livingproof-verification-definition/0.1.0"
PROCEDURES = {"walk-hallway": ".github/workflows/walk-hallway.yml"}
# A procedure is recognised by its interface, not its file name: a reusable-workflow call that
# passes these inputs, or a local workflow that declares these workflow_call inputs and outputs,
# runs the procedure under whatever name it has. tests/test_definition.py keeps these equal to
# walk-hallway.yml's own workflow_call interface.
SIGNATURES = {
    "walk-hallway": {
        "inputs": frozenset({"repository", "ref", "configurations"}),
        "outputs": frozenset({"configurations", "report-artifacts"}),
    }
}
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
GITHUB = os.environ.get("LIVINGPROOF_GIT_BASE", "https://github.com")


class DefinitionError(SystemExit):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_definition(path: Path) -> dict:
    if not path.is_file():
        raise DefinitionError(f"{path}: no such definition")
    try:
        d = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise DefinitionError(f"{path}: not valid TOML ({e})") from None
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


def _load_jobs(workflow: Path) -> tuple[dict | None, str | None]:
    try:
        doc = yaml.safe_load(workflow.read_text())
    except (OSError, yaml.YAMLError) as e:
        return None, f"{workflow}: cannot be read as YAML ({e})"
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return None, f"{workflow}: has no jobs"
    return jobs, None


def _workflow_files(workflow: Path) -> list[Path]:
    """The given workflow and every other workflow beside it (``.github/workflows``)."""
    others = sorted(
        p for p in workflow.parent.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )
    return [workflow, *(p for p in others if p.resolve() != workflow.resolve())]


def _declares_procedure(path: Path, procedure: str) -> bool:
    """Whether the local workflow at ``path`` has the procedure's workflow_call interface."""
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return False
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    call = on.get("workflow_call") if isinstance(on, dict) else None
    if not isinstance(call, dict):
        return False
    sig = SIGNATURES[procedure]
    return sig["inputs"] <= set(call.get("inputs") or {}) and sig["outputs"] <= set(
        call.get("outputs") or {}
    )


def runs_procedure(job, procedure: str, root: Path | None = None) -> bool:
    """Whether a job runs the procedure: it calls the procedure's file from anywhere, calls a
    reusable workflow with the procedure's inputs (a renamed copy, another owner, path or ref),
    or calls a local workflow under ``root`` that declares the procedure's interface."""
    if not isinstance(job, dict):
        return False
    uses = job.get("uses")
    if calls_procedure(uses, procedure):
        return True
    if not isinstance(uses, str) or not uses.split("@", 1)[0].strip().endswith((".yml", ".yaml")):
        return False
    with_ = job.get("with") if isinstance(job.get("with"), dict) else {}
    if SIGNATURES[procedure]["inputs"] <= set(with_):
        return True
    local = uses.strip()
    if root is not None and local.startswith("./"):
        return _declares_procedure(root / local[2:], procedure)
    return False


def normal_repository(value) -> str | None:
    """How GitHub resolves ``OWNER/REPO``: case-insensitive, with an optional ``.git``."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value[:-4] if value.endswith(".git") else value


def _for_definition(name: str, job: dict, lock: dict, definition_id: str | None) -> bool:
    """Whether a job that runs the procedure is this definition's call: its job id is the
    definition id, or it passes the locked robot repository however it is spelled (case,
    ``.git``). A call that names no repository cannot be another robot's, so it is checked."""
    if definition_id is not None and name == definition_id:
        return True
    w = job.get("with") if isinstance(job.get("with"), dict) else {}
    if "repository" not in w:
        return True
    return normal_repository(w["repository"]) == normal_repository(lock["robot"]["repository"])


def _for_other(name: str, job: dict, others: dict[str, str]) -> bool:
    """Whether the call belongs to another known definition (by its job id or its literal
    ``OWNER/REPO``); that definition's own check covers it."""
    if name in others:
        return True
    w = job.get("with") if isinstance(job.get("with"), dict) else {}
    repository = w.get("repository")
    return (
        isinstance(repository, str)
        and bool(REPO.fullmatch(repository.strip().removesuffix(".git")))
        and normal_repository(repository) in {normal_repository(r) for r in others.values()}
    )


def other_definitions(definition: Path) -> dict[str, str]:
    """The other definitions beside ``definition``: id to robot repository. A file that is
    not a valid definition is left out, so a call for it is refused rather than trusted."""
    found = {}
    for path in sorted(definition.parent.glob("*.toml")):
        if path.resolve() == definition.resolve():
            continue
        try:
            d = load_definition(path)
        except DefinitionError:
            continue
        found[d["id"]] = d["robot"]["repository"]
    return found


def workflow_problems(
    lock: dict,
    workflow: Path,
    definition_id: str | None = None,
    others: dict[str, str] | None = None,
) -> list[str]:
    """Compare the parsed ``uses:`` and ``with:`` of every job that runs the procedure for this
    definition, in ``workflow`` and in every other workflow file beside it.

    A job is this definition's call when its job id is ``definition_id`` or it passes the
    locked robot repository; calls for other robots are left to their own locks. Comments,
    other keys and other jobs never satisfy the check: at least one job in ``workflow`` must be
    this definition's call, and every such job in any workflow file, under any file name,
    owner, path or ref, must call the procedure exactly as locked.
    """
    v = lock["releases"]["verifier"]
    target = f"{v['repository']}/{PROCEDURES[lock['procedure']]}"
    pinned = f"{target}@{v['commit']}"
    # .github/workflows/<file>: local ./ calls resolve against the repository root.
    root = workflow.parent.parent.parent if workflow.parent.name == "workflows" else None
    want_confs = lock["robot"]["configurations"]
    problems = []
    for index, path in enumerate(_workflow_files(workflow)):
        jobs, error = _load_jobs(path)
        if error:
            if index == 0:
                return [error]
            problems.append(error)
            continue
        calls = {
            name: job
            for name, job in jobs.items()
            if runs_procedure(job, lock["procedure"], root)
            and _for_definition(name, job, lock, definition_id)
        }
        for name, job in jobs.items():
            if (
                name not in calls
                and runs_procedure(job, lock["procedure"], root)
                and not _for_other(name, job, others or {})
            ):
                w = job.get("with") if isinstance(job.get("with"), dict) else {}
                problems.append(
                    f"{path}: job {name}: calls {target} for {w.get('repository')!r}, which is "
                    "no known definition's OWNER/REPO; every call must belong to a definition"
                )
        if index == 0 and not calls:
            problems.append(
                f"{workflow}: no job calls {target} for {lock['robot']['repository']}"
                + (f" (job {definition_id})" if definition_id else "")
                + f"; must call {pinned}"
            )
        for name, job in calls.items():
            where = f"{path}: job {name}"
            if not same_call(job["uses"], pinned):
                problems.append(f"{where}: must call {pinned} (calls {job['uses']})")
            w = job.get("with") if isinstance(job.get("with"), dict) else {}
            if w.get("repository") != lock["robot"]["repository"]:
                problems.append(
                    f"{where}: must pass the locked robot repository {lock['robot']['repository']}"
                )
            if w.get("ref") != lock["robot"]["commit"]:
                problems.append(
                    f"{where}: must pass the locked robot commit {lock['robot']['commit']}"
                )
            try:
                confs = (
                    json.loads(w["configurations"])
                    if isinstance(w.get("configurations"), str)
                    else None
                )
            except json.JSONDecodeError:
                confs = None
            if confs != want_confs:
                problems.append(
                    f"{where}: must pass the locked configurations '{json.dumps(want_confs)}'"
                )
    return problems


def calls_procedure(uses, procedure: str) -> bool:
    """Whether ``uses`` calls the procedure workflow from anywhere: any owner (GitHub owners are
    case-insensitive), a fork, or a local ``./.github/workflows`` copy."""
    if not isinstance(uses, str):
        return False
    path = uses.split("@", 1)[0].strip().lower()
    return path.endswith("/" + PROCEDURES[procedure].lower())


def same_call(uses: str, pinned: str) -> bool:
    """``OWNER/REPO/path@commit`` equal to the pin: owner and repo case-insensitively, the
    commit exactly."""
    target, _, ref = uses.partition("@")
    want, _, commit = pinned.partition("@")
    return target.lower() == want.lower() and ref == commit


def read_lock(path: Path) -> dict:
    """The lock at ``path``, with a message (not a traceback) when it is malformed."""
    try:
        lock = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise DefinitionError(f"{path}: not valid JSON ({e}); re-run lock and review") from None
    problems = []
    if not isinstance(lock, dict):
        raise DefinitionError(f"{path}: must be a JSON object; re-run lock and review")
    if lock.get("format") != FORMAT:
        problems.append(f"format is not {FORMAT}")
    shape = {
        "definition": ("sha256",),
        "procedure": (),
        "robot": ("repository", "commit", "configurations", "lock"),
        "releases": ("verifier",),
    }
    for key, inner in shape.items():
        if key not in lock:
            problems.append(f"missing {key}")
        elif inner and not (isinstance(lock[key], dict) and all(k in lock[key] for k in inner)):
            problems.append(f"{key} must have {', '.join(inner)}")
    if not problems:
        if lock["procedure"] not in PROCEDURES:
            problems.append(f"procedure must be one of {sorted(PROCEDURES)}")
        if not (
            isinstance(lock["robot"]["lock"], dict)
            and {"path", "sha256"} <= set(lock["robot"]["lock"])
        ):
            problems.append("robot.lock must have path, sha256")
        v = lock["releases"]["verifier"]
        if not (isinstance(v, dict) and {"repository", "tag", "commit"} <= set(v)):
            problems.append("releases.verifier must have repository, tag, commit")
    if problems:
        raise DefinitionError(f"{path}: " + "; ".join(problems) + "; re-run lock and review")
    return lock


def check(definition: Path, work: Path, workflow: Path | None = None) -> dict:
    load_definition(definition)
    path = lock_path(definition)
    if not path.exists():
        raise DefinitionError(f"{path} is missing; run `lock {definition}` and review it")
    lock = read_lock(path)
    problems = []
    if lock["definition"]["sha256"] != sha256(definition.read_bytes()):
        problems.append(f"{definition.name} changed since it was locked; re-run lock and review")
    r = lock["robot"]
    want = load_definition(definition)["robot"]
    for key in ("repository", "configurations"):
        if r[key] != want[key]:
            problems.append(
                f"{path.name}: robot.{key} {r[key]!r} differs from {definition.name} "
                f"({want[key]!r}); re-run lock and review"
            )
    repository_ok = (
        isinstance(r["repository"], str)
        and REPO.fullmatch(r["repository"])
        and r["repository"] == want["repository"]
    )
    if isinstance(r["repository"], str) and not REPO.fullmatch(r["repository"]):
        problems.append(f"{path.name}: robot.repository {r['repository']!r} is not OWNER/REPO")
    # Never fetch a repository the definition does not name: report the edit instead.
    if repository_ok and not SHA.fullmatch(r["commit"]):
        problems.append("robot.commit is not a full commit sha")
    elif repository_ok:
        raw = show(r["repository"], r["commit"], r["lock"]["path"], work / "robot")
        if sha256(raw) != r["lock"]["sha256"]:
            problems.append(f"{r['repository']}@{r['commit'][:12]} lock differs from {path.name}")
        elif json.loads(raw).get("releases") != lock["releases"]:
            problems.append(f"releases in {path.name} differ from the robot lock")
    if workflow is not None:
        problems += workflow_problems(
            lock, workflow, load_definition(definition)["id"], other_definitions(definition)
        )
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
    done = subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=env)
    if done.returncode:
        raise DefinitionError(
            f"step failed (exit {done.returncode}) in {cwd.name}: {script.splitlines()[0]}"
        )
    if outputs is None:
        return {}
    return dict(line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line)


def reporter_shim(lock: dict, mode: str, work: Path) -> Path:
    rep = lock["releases"].get("reporter")
    keys = {"repository", "commit", "image", "tag", "digest"}
    if not (isinstance(rep, dict) and keys <= set(rep)):
        raise DefinitionError(f"releases.reporter must have {', '.join(sorted(keys))}")
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
    # plan job: the configurations must be in the robot lock, every ref a released tag.
    only = json.dumps(lock["robot"]["configurations"])
    sh(step(wf, "Refs from verification.lock.json"), robot, {**gh, "ONLY": only}, work / "r.out")
    sh(step(wf, "Every ref is a released tag"), work, gh)
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
    sh(step(wf, "Robot and configuration profiles valid"), robot, {**gh, "ADAPTER": adapter})
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


def main(argv=None, prog: str = "python3 -m capability_verifier.definition") -> int:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
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
    try:
        return _main(a)
    except subprocess.CalledProcessError as e:
        cmd = " ".join(map(str, e.cmd)) if isinstance(e.cmd, list) else str(e.cmd)
        detail = (e.stderr or "").strip().splitlines()[-1:] if isinstance(e.stderr, str) else []
        raise DefinitionError(
            f"command failed (exit {e.returncode}): {cmd}" + (f": {detail[0]}" if detail else "")
        ) from None


def _main(a) -> int:
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
