#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a declarative verification locally through the same steps as the reusable workflow.

A verification project (for example ``livingproof-verification``) keeps only a definition,
its reviewed lock and a thin workflow call. This module owns the rest:

    python3 -m capability_verifier.definition lock DEFINITION.toml [--check] [--workflow FILE]
    python3 -m capability_verifier.definition run DEFINITION.toml --out DIR
    livingproof capability verify lock|run ...   (the same, through the livingproof plugin)

``lock`` resolves the definition's robot ref to a commit and writes
``livingproof-verification-lock/0.1.0``: that commit, the sha256 of the robot's
``verification.lock.json``, and the releases that file names. ``--check`` accepts ``0.1.0``
and ``livingproof-verification-lock/0.2.0``. It re-resolves nothing. It fails when the
definition changed since locking, or when ``--workflow`` does not call ``walk-hallway.yml``
at the locked verifier commit with the locked robot repository, commit and configurations.
The job for a definition is the one whose id equals the definition id. Pairing is by job id,
so one workflow can hold several definitions that share a robot repository. If that job is
absent, the check fails. Any other procedure-calling job whose id is not a definition is
refused.

``0.1.0`` still fails when the robot ``verification.lock.json`` at the locked commit has a
different sha256 or different releases. ``0.2.0`` owns releases (specs, runner, verifier,
reporter digest), plan, world, configuration sha256s and the
``livingproof-evaluator-scenario`` package pin. At the locked commit it compares each
configuration file's sha256, the ``[tool.livingproof]`` adapter string in ``pyproject.toml``,
and the configuration names in ``robot.json``. It does not read a robot
``verification.lock.json``.

``run`` checks the lock, checks out the robot at the locked commit and the verifier at the
commit the lock names, then executes the named ``run:`` scripts of that verifier
commit's ``walk-hallway.yml`` in the same directories CI uses: the plan job's checks (refs
from that lock, every ref a released tag, media and bundle capability), setup, robot
adapter, profiles valid, catalog, evidence, assessment and render. A ``0.2.0`` lock
controls those steps even when the robot commit has ``verification.lock.json``: ``run``
writes the evidence document from it, logs ``EVIDENCE_SOURCE: caller``, records the
replaced robot file's sha256, and points ``repin.py --check`` at that lock. When the
robot commit has no lock, ``run`` logs that instead. A ``0.1.0`` lock leaves a robot
file untouched. The robot's own test suite (``uv run pytest tests``) is CI-only. No step logic is
copied here, so local and CI runs share one definition of each step. Rendering uses the
locked reporter image by digest (``docker``),
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

from capability_verifier import evidence_lock
from capability_verifier.repin import DIGEST, TAG

FORMAT = "livingproof-verification-lock/0.1.0"
FORMAT_V2 = evidence_lock.FORMAT_V2
FILE_SHA = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
SCENARIO_ID = re.compile(r"[a-z0-9-]+@\d+\.\d+\.\d+")
V1_SHAPE = {
    "definition": ("sha256",),
    "procedure": (),
    "robot": ("repository", "commit", "configurations", "lock"),
    "releases": ("verifier",),
}
V2_SHAPE = {
    "definition": ("sha256",),
    "procedure": (),
    "robot": ("repository", "commit", "configurations", "adapter"),
    "releases": ("specs", "runner", "verifier", "reporter"),
    "scenario": (),
    "plan": ("command_speed",),
    "world": ("id", "version"),
    "configurations": (),
    "evaluators": ("livingproof-evaluator-scenario",),
}
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


def _for_definition(name: str, definition_id: str | None) -> bool:
    """Whether this procedure job is the definition's call: its id equals the definition id."""
    return definition_id is not None and name == definition_id


def _for_other(name: str, others: set[str]) -> bool:
    """Whether this procedure job's id is another known definition.

    That definition's own check covers the call. A shared robot repository does not.
    """
    return name in others


def other_definitions(definition: Path) -> set[str]:
    """Ids of the other definitions beside ``definition``. A file that is not a valid
    definition is left out, so a procedure call for it is refused rather than trusted."""
    found = set()
    for path in sorted(definition.parent.glob("*.toml")):
        if path.resolve() == definition.resolve():
            continue
        try:
            d = load_definition(path)
        except DefinitionError:
            continue
        found.add(d["id"])
    return found


def workflow_problems(
    lock: dict,
    workflow: Path,
    definition_id: str | None = None,
    others: set[str] | None = None,
) -> list[str]:
    """Compare the parsed ``uses:`` and ``with:`` of the job whose id is this definition's, in
    ``workflow`` and in every other workflow file beside it.

    Pairing is by job id. Repository, configurations, ``uses`` and ref are checked on that
    job. Comments, other keys and other jobs never satisfy the check: ``workflow`` must contain
    a job with this definition's id, and that job, under any file name, owner, path or ref,
    must call the procedure exactly as locked. A procedure-calling job whose id is another
    definition is left to that definition. Any other procedure-calling job is refused.
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
            if runs_procedure(job, lock["procedure"], root) and _for_definition(name, definition_id)
        }
        known = others or set()
        for name, job in jobs.items():
            if (
                name not in calls
                and runs_procedure(job, lock["procedure"], root)
                and not _for_other(name, known)
            ):
                problems.append(
                    f"{path}: job {name}: calls the procedure, but job id {name!r} is no known "
                    "definition; every procedure-calling job's id must be a definition"
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


def _relative_robot_path(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    return not Path(path).is_absolute() and ".." not in Path(path).parts


def _pin_problems(label: str, ref: object, *, require_digest: bool = False) -> list[str]:
    """Tag, commit and digest problems. The patterns match ``repin.py``."""
    if not isinstance(ref, dict):
        return [f"{label} must be an object"]
    problems = []
    repository = ref.get("repository")
    if not isinstance(repository, str) or not REPO.fullmatch(repository):
        problems.append(f"{label}.repository must be OWNER/NAME")
    tag = ref.get("tag")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        problems.append(f"{label}.tag must be a vX.Y.Z release tag (got {tag!r})")
    commit = ref.get("commit")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        problems.append(f"{label}.commit must be a full commit sha")
    if require_digest or "image" in ref or "digest" in ref:
        digest = ref.get("digest")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            problems.append(f"{label}.digest must be a sha256 digest")
    if require_digest and not isinstance(ref.get("image"), str):
        problems.append(f"{label}.image is required")
    return problems


def _v2_details(lock: dict) -> list[str]:
    """Shape problems for a 0.2.0 lock whose required keys are already present."""
    problems = []
    scenario = lock.get("scenario")
    if not isinstance(scenario, str) or not SCENARIO_ID.fullmatch(scenario):
        problems.append(
            f"scenario must be name@version (for example walk-hallway@0.1.0), got {scenario!r}"
        )
    robot = lock["robot"]
    adapter = robot.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        problems.append("robot.adapter must be the [tool.livingproof] adapter string")
    selected = robot.get("configurations")
    if (
        not isinstance(selected, list)
        or not selected
        or not all(isinstance(name, str) for name in selected)
    ):
        problems.append("robot.configurations must be a non-empty list of names")
        selected = []
    entries = lock.get("configurations")
    names: list[str] = []
    if not isinstance(entries, list) or not entries:
        problems.append("configurations must be a non-empty list")
        entries = []
    for index, entry in enumerate(entries):
        where = f"configurations[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{where}.name must be a string")
        else:
            names.append(name)
        version = entry.get("version")
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            problems.append(f"{where}.version must be X.Y.Z")
        if not _relative_robot_path(entry.get("path")):
            problems.append(f"{where}.path must stay inside the robot repository")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not FILE_SHA.fullmatch(digest):
            problems.append(f"{where}.sha256 must be 64 hex characters")
    if len(names) != len(set(names)):
        problems.append("configuration names must be unique")
    missing = [name for name in selected if name not in names]
    if missing:
        problems.append(f"robot.configurations {missing} are not in configurations")
    plan = lock.get("plan")
    speed = plan.get("command_speed") if isinstance(plan, dict) else None
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        problems.append("plan.command_speed must be a number")
    world = lock.get("world")
    if isinstance(world, dict):
        for key in ("id", "version"):
            if not isinstance(world.get(key), str) or not world[key]:
                problems.append(f"world.{key} must be a string")
        declared = world.get("sha256")
        if declared is not None and (
            not isinstance(declared, str) or not FILE_SHA.fullmatch(declared)
        ):
            problems.append("world.sha256 must be 64 hex characters")
    releases = lock.get("releases") if isinstance(lock.get("releases"), dict) else {}
    for name in ("specs", "runner", "verifier"):
        if name in releases:
            problems.extend(_pin_problems(f"releases.{name}", releases[name]))
    if "reporter" in releases:
        problems.extend(
            _pin_problems("releases.reporter", releases["reporter"], require_digest=True)
        )
    evaluators = lock.get("evaluators") if isinstance(lock.get("evaluators"), dict) else {}
    problems.extend(
        _pin_problems(
            "evaluators.livingproof-evaluator-scenario",
            evaluators.get("livingproof-evaluator-scenario"),
        )
    )
    return problems


def read_lock(path: Path) -> dict:
    """The lock at ``path``, with a message (not a traceback) when it is malformed.

    ``0.1.0`` records the robot ``verification.lock.json`` digest and the releases copied
    out of that file. ``0.2.0`` owns releases, plan, world, configuration sha256s and the
    evaluator pin. ``0.1.0`` remains accepted until the robots no longer publish a lock.
    """
    try:
        lock = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise DefinitionError(f"{path}: not valid JSON ({e}); re-run lock and review") from None
    if not isinstance(lock, dict):
        raise DefinitionError(f"{path}: must be a JSON object; re-run lock and review")
    problems = []
    version = lock.get("format")
    if version not in (FORMAT, FORMAT_V2):
        problems.append(
            "format is not livingproof-verification-lock/0.1.0 or "
            "livingproof-verification-lock/0.2.0"
        )
    shape = V2_SHAPE if version == FORMAT_V2 else V1_SHAPE
    for key, inner in shape.items():
        if key not in lock:
            problems.append(f"missing {key}")
        elif inner and not (isinstance(lock[key], dict) and all(k in lock[key] for k in inner)):
            problems.append(f"{key} must have {', '.join(inner)}")
    if not problems:
        if lock["procedure"] not in PROCEDURES:
            problems.append(f"procedure must be one of {sorted(PROCEDURES)}")
        if version == FORMAT:
            if not (
                isinstance(lock["robot"]["lock"], dict)
                and {"path", "sha256"} <= set(lock["robot"]["lock"])
            ):
                problems.append("robot.lock must have path, sha256")
            verifier = lock["releases"]["verifier"]
            if not (
                isinstance(verifier, dict) and {"repository", "tag", "commit"} <= set(verifier)
            ):
                problems.append("releases.verifier must have repository, tag, commit")
        else:
            problems.extend(_v2_details(lock))
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
    elif repository_ok and lock["format"] == FORMAT_V2:
        # 0.2.0 owns releases. Compare the robot tree, not a robot verification.lock.json.
        problems.extend(pinned_robot_problems(lock, work / "robot"))
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


def called_workflow(text: str) -> str | None:
    """Relative path when ``text`` is a one-job wrapper around a same-repository workflow.

    ``./`` is the caller's repository. ``$/`` is the repository and revision of the
    workflow that contains the call, which is what a caller in another repository
    resolves. A ``@ref`` is not allowed on ``$/``. Both forms name a file in this
    checkout.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or len(jobs) != 1:
        return None
    job = next(iter(jobs.values()))
    if not isinstance(job, dict) or job.get("steps"):
        return None
    uses = job.get("uses")
    if isinstance(uses, str) and uses.startswith("$/"):
        path = uses[2:]
        if "@" in path:
            return None
        return path
    if isinstance(uses, str) and uses.startswith("./"):
        return uses[2:]
    return None


def procedure_text(root: Path, procedure: str) -> str:
    """Run scripts for ``procedure``, following one local wrapper."""
    path = root / PROCEDURES[procedure]
    text = path.read_text()
    called = called_workflow(text)
    if called is None:
        return text
    return (root / called).read_text()


def first_step(workflow: str, names: tuple[str, ...]) -> str:
    """The first of ``names`` that has a run script.

    A lock can check out an older workflow whose catalog step is still named
    ``Walk-hallway catalog``. The current workflow names it ``Scenario catalog``.
    """
    missing = None
    for name in names:
        try:
            return step(workflow, name)
        except DefinitionError as exc:
            missing = exc
    raise missing


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
    raise DefinitionError(f"workflow has no step {name!r} with a run script")


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


def pinned_robot_problems(lock: dict, dest: Path) -> list[str]:
    """Configuration sha256s, adapter and robot.json names at the locked commit."""
    robot = lock["robot"]
    checkout(robot["repository"], robot["commit"], dest)
    problems = []
    where = f"{robot['repository']}@{robot['commit'][:12]}"
    for entry in lock["configurations"]:
        rel = entry["path"]
        file = dest / rel
        if not file.is_file():
            problems.append(f"{where}: {rel} is missing")
            continue
        got = sha256(file.read_bytes())
        if got != entry["sha256"]:
            problems.append(f"{where}: {rel} sha256 is {got}, not the locked {entry['sha256']}")
    pyproject = dest / "pyproject.toml"
    if not pyproject.is_file():
        problems.append(f"{where}: pyproject.toml is missing")
    else:
        try:
            adapter = tomllib.loads(pyproject.read_text())["tool"]["livingproof"]["adapter"]
        except (tomllib.TOMLDecodeError, KeyError, TypeError):
            problems.append(f"{where}: pyproject.toml has no [tool.livingproof] adapter string")
        else:
            if adapter != robot["adapter"]:
                problems.append(
                    f"{where}: [tool.livingproof] adapter is {adapter!r}, "
                    f"not the locked {robot['adapter']!r}"
                )
    profile = dest / "robot.json"
    if not profile.is_file():
        problems.append(f"{where}: robot.json is missing")
    else:
        try:
            listed = json.loads(profile.read_text())["configurations"]
            names = [item["name"] for item in listed]
        except (json.JSONDecodeError, KeyError, TypeError):
            problems.append(
                f"{where}: robot.json configurations must be a list of objects with name"
            )
        else:
            want = [entry["name"] for entry in lock["configurations"]]
            if names != want:
                problems.append(
                    f"{where}: robot.json configuration names are {names}, not the locked {want}"
                )
    return problems


def prepare_evidence_inputs(lock: dict, robot: Path, caller_lock: Path) -> dict:
    """Env for the plan steps.

    A ``0.2.0`` lock is the caller's verification lock. It controls the run even when
    the robot commit has ``verification.lock.json``: write the v2.0.3 evidence document
    from it, log ``EVIDENCE_SOURCE: caller``, and record the replaced file's sha256.
    When the robot commit has no lock, log that instead. ``EVIDENCE_SOURCE=caller``
    makes ``repin.py --check`` read the caller lock. A ``0.1.0`` lock leaves a robot
    file as it is.
    """
    robot_lock = robot / evidence_lock.ROBOT_LOCK_NAME
    if lock.get("format") != FORMAT_V2:
        if robot_lock.is_file():
            return {}
        raise DefinitionError(
            "the robot commit has no verification.lock.json; pass a "
            "livingproof-verification-lock/0.2.0 as verification-lock"
        )
    digest = sha256(robot_lock.read_bytes()) if robot_lock.is_file() else None
    evidence_lock.write_evidence_document(lock, robot_lock)
    if digest is not None:
        evidence_lock.note_overridden_robot_lock(robot_lock, digest)
    for line in evidence_lock.evidence_source_lines(digest):
        print(line, file=sys.stderr)
    (robot / evidence_lock.CALLER_LOCK_NAME).write_bytes(caller_lock.read_bytes())
    return {"EVIDENCE_SOURCE": "caller"}


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
    wf = procedure_text(verifier, lock["procedure"])
    gh = {"MUJOCO_GL": "osmesa", "PYOPENGL_PLATFORM": "osmesa"}
    # plan job: the configurations must be in the lock, every ref a released tag.
    evidence_env = prepare_evidence_inputs(lock, robot, lock_path(definition))
    only = json.dumps(lock["robot"]["configurations"])
    sh(step(wf, "Refs from verification.lock.json"), robot, {**gh, "ONLY": only}, work / "r.out")
    sh(step(wf, "Every ref is a released tag"), work, {**gh, **evidence_env})
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
    sh(first_step(wf, ("Scenario catalog", "Walk-hallway catalog")), verifier, gh)
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
