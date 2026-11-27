# SPDX-License-Identifier: Apache-2.0
"""Install and run evaluators named by declaration lines.

A line is ``<owner>/<repo>@<ref>/<path>.json``. ``ref`` is a release tag
``vMAJOR.MINOR.PATCH`` or a 40-character commit. Tags are peeled. A branch
of the same name is refused. Blank lines and ``#`` comments are ignored.

    python -m capability_verifier.evaluators check --lines FILE
    python -m capability_verifier.evaluators run --lines FILE \\
        --evidence-manifest MANIFEST --out DIR [--media-dir DIR] [--bundle MANIFEST]

``check`` resolves each line and validates the descriptor. ``run`` then
installs the package from that commit and executes it. This module does not
name a particular evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.exceptions import Unretrievable
from referencing.jsonschema import DRAFT202012

from capability_verifier import collect
from capability_verifier.evaluator_contract import CONTRACT
from capability_verifier.repin import resolve as resolve_tag

LINE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9._-]+)@"
    r"(?P<ref>v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)|[0-9a-f]{40})/"
    r"(?P<path>[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.json)$"
)
_SHA = re.compile(r"[0-9a-f]{40}")
_PROVENANCE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_PROVENANCE_TAG = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_TOKEN = re.compile(r"\{(evidence_manifest|media_dir|result)\}")
DEFAULT_TIMEOUT = 30 * 60
RESULT_SCHEMA = "evaluator-result.schema.json"
# Placement knows these kinds. A descriptor kind outside the map is refused.
PLACERS = {
    "integrity": collect._attach_integrity,
    "advisory": collect._attach_advisory,
}
SUPPORTED_MAJORS = {1}
# The contract tag must still peel to this commit. A moved tag is refused.
_PINNED_CONTRACT = ("v1.10.0", "d46062a9300611e32ad76749e67a8f035489ebce")


class Refusal(Exception):
    """A line, descriptor, install, or result this loader will not accept."""


class NotFound(Exception):
    """The requested URL returned HTTP 404."""

    def __init__(self, url: str):
        self.url = url
        super().__init__(url)


class DuplicateKey(Exception):
    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


@dataclass(frozen=True)
class Declaration:
    line: str
    owner: str
    repo: str
    ref: str
    path: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def tag(self) -> str | None:
        return None if _SHA.fullmatch(self.ref) else self.ref


@dataclass
class Record:
    line: str
    repo: str
    ref: str
    commit: str
    descriptor_sha256: str
    package_name: str
    installed_version: str | None
    exit_code: int | None
    outcome: str
    note: str | None = None
    evaluator_id: str = ""
    kind: str = ""
    document: dict | None = None

    def as_dict(self) -> dict:
        return {
            "line": self.line,
            "repo": self.repo,
            "ref": self.ref,
            "commit": self.commit,
            "descriptor_sha256": self.descriptor_sha256,
            "package_name": self.package_name,
            "installed_version": self.installed_version,
            "exit_code": self.exit_code,
            "outcome": self.outcome,
        }


def parse_lines(text: str) -> list[Declaration]:
    """Declaration lines in ``text``. Blank lines and ``#`` comments are ignored."""
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.fullmatch(line)
        if match is None or any(part in {".", ".."} for part in match["path"].split("/")):
            raise Refusal(
                f"{line!r} is not a declaration line <owner>/<repo>@<vX.Y.Z-or-commit>/<path>.json"
            )
        found.append(Declaration(line=line, **match.groupdict()))
    if not found:
        raise Refusal("no declaration lines (blank lines and # comments do not count)")
    return found


def resolve_ref(declaration: Declaration, *, resolve=None, run=None) -> str:
    """The commit a declaration ref names. A 40-hex ref is used as it is."""
    resolve = resolve_tag if resolve is None else resolve
    run = subprocess.run if run is None else run
    if _SHA.fullmatch(declaration.ref):
        return declaration.ref
    try:
        commit = resolve(declaration.repository, declaration.ref)
    except (SystemExit, subprocess.SubprocessError, OSError) as exc:
        raise Refusal(
            f"{declaration.repository} tag {declaration.ref} did not resolve: {exc}"
        ) from exc
    if not _SHA.fullmatch(commit or ""):
        raise Refusal(f"{declaration.repository} tag {declaration.ref} did not peel to a commit")
    if _branch_named(declaration.repository, declaration.ref, run):
        raise Refusal(
            f"{declaration.repository} has a branch named {declaration.ref}; "
            "a declaration ref must name a tag, not a branch"
        )
    return commit


def _branch_named(repository: str, name: str, run) -> bool:
    url = f"https://github.com/{repository}.git"
    completed = run(
        ["git", "ls-remote", url, f"refs/heads/{name}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if getattr(completed, "returncode", 1) != 0:
        detail = (getattr(completed, "stderr", "") or "").strip()
        raise Refusal(f"could not list branches of {repository}: {detail or 'ls-remote failed'}")
    return bool((getattr(completed, "stdout", "") or "").strip())


def fetch_bytes(url: str) -> bytes:
    """GET ``url``. HTTP 404 raises :class:`NotFound`."""
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(url) from exc
        raise Refusal(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise Refusal(f"could not fetch {url}: {exc.reason}") from exc


def load_object(payload: str | bytes) -> dict:
    """Parse a JSON object, refusing duplicate keys at every level."""

    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise DuplicateKey(key)
            obj[key] = value
        return obj

    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeError as exc:
            raise Refusal("document is not UTF-8") from exc
    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except DuplicateKey as exc:
        raise Refusal(f"duplicate key {exc.key!r}") from exc
    except json.JSONDecodeError as exc:
        raise Refusal(f"not JSON ({exc})") from exc
    if not isinstance(value, dict):
        raise Refusal("expected a JSON object")
    return value


def accept_result_format(declared: str, produced: str) -> None:
    """The produced format equals the descriptor, and its major is supported.

    This is the only place that decides whether a result format is acceptable.
    """
    if not isinstance(declared, str) or not isinstance(produced, str) or produced != declared:
        raise Refusal(f"result format {produced!r} is not the declared {declared!r}")
    major = _format_major(produced)
    if major not in SUPPORTED_MAJORS:
        raise Refusal(f"result format major {major!r} is not supported")


def _format_major(value: str) -> int | None:
    _prefix, _sep, version = value.rpartition("/")
    head = version.split(".", 1)[0]
    if head.isdigit():
        return int(head)
    return None


def _contract() -> Declaration:
    found = parse_lines(CONTRACT)
    if len(found) != 1:
        raise Refusal("the evaluator contract must be one declaration line")
    return found[0]


def _schema_relative(path: str) -> str:
    prefix = "schema/"
    if not path.startswith(prefix):
        raise Refusal(f"{path} is not a schema path")
    return path[len(prefix) :]


def _registry(owner: str, repo: str, commit: str, fetch) -> Registry:
    """Resolve schema ``$id`` values to the pinned raw files."""
    prefix = f"https://github.com/{owner}/{repo}/schema/"
    raw_root = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/schema/"

    def retrieve(uri: str):
        if not uri.startswith(prefix):
            raise Unretrievable(f"no schema for {uri}")
        relative = uri[len(prefix) :]
        if not relative or any(part in {".", ".."} for part in relative.split("/")):
            raise Unretrievable(relative)
        try:
            payload = fetch(raw_root + relative)
        except NotFound as exc:
            raise Unretrievable(exc.url) from exc
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise Unretrievable(relative) from exc
        return Resource.from_contents(document, default_specification=DRAFT202012)

    return Registry(retrieve=retrieve)


def _read_schema(owner: str, repo: str, commit: str, relative: str, fetch) -> dict:
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/schema/{relative}"
    try:
        payload = fetch(url)
    except NotFound as exc:
        raise Refusal(f"schema {relative} was not found") from exc
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise Refusal(f"schema {relative} is not JSON") from exc
    if not isinstance(document, dict):
        raise Refusal(f"schema {relative} is not an object")
    return document


def validate_descriptor(document: dict, schema: dict, registry: Registry) -> None:
    """Refuse a descriptor the pinned schema rejects, or a kind with no placer."""
    try:
        jsonschema.Draft202012Validator(schema, registry=registry).validate(document)
    except jsonschema.ValidationError as exc:
        raise Refusal(
            f"descriptor does not match the implementation schema: {exc.message}"
        ) from exc
    kind = document.get("kind")
    if kind not in PLACERS:
        raise Refusal(f"unknown kind {kind!r}")


def _accept_result(document: dict, descriptor: dict, schema: dict, registry: Registry) -> None:
    try:
        jsonschema.Draft202012Validator(schema, registry=registry).validate(document)
    except jsonschema.ValidationError as exc:
        raise Refusal(f"result does not match the evaluator-result schema: {exc.message}") from exc
    evaluator = document.get("evaluator")
    if not isinstance(evaluator, dict):
        raise Refusal("result has no evaluator object")
    if evaluator.get("id") != descriptor["id"]:
        raise Refusal(f"result evaluator id {evaluator.get('id')!r} is not {descriptor['id']!r}")
    if evaluator.get("kind") != descriptor["kind"]:
        raise Refusal(
            f"result evaluator kind {evaluator.get('kind')!r} is not {descriptor['kind']!r}"
        )
    accept_result_format(descriptor.get("result"), document.get("format"))
    # The schema cannot require unique trial indexes, or reject 1 and 1.0 as the
    # same index. The loader refuses both. Duplicate object keys are refused by
    # load_object, which is what reads this result.
    repeated = collect.repeated_advisory_index(document)
    if repeated:
        raise Refusal(repeated)


@dataclass
class Prepared:
    declaration: Declaration
    commit: str
    digest: str
    document: dict
    registry: Registry
    result_schema: dict


def prepare(
    text: str,
    *,
    fetch=None,
    resolve=None,
    run=None,
    injected: dict[str, bytes] | None = None,
) -> list[Prepared]:
    """Parse, resolve, fetch, and validate. Nothing is installed."""
    fetch = fetch_bytes if fetch is None else fetch
    resolve = resolve_tag if resolve is None else resolve
    run = subprocess.run if run is None else run
    declarations = parse_lines(text)
    pin = _contract()
    commit = resolve_ref(pin, resolve=resolve, run=run)
    pinned_tag, pinned_commit = _PINNED_CONTRACT
    if pin.ref == pinned_tag and commit != pinned_commit:
        raise Refusal(f"{pin.repository} tag {pin.ref} is {commit}, not {pinned_commit}")
    registry = _registry(pin.owner, pin.repo, commit, fetch)
    implementation = _read_schema(pin.owner, pin.repo, commit, _schema_relative(pin.path), fetch)
    result_schema = _read_schema(pin.owner, pin.repo, commit, RESULT_SCHEMA, fetch)
    prepared = []
    for declaration in declarations:
        resolved = resolve_ref(declaration, resolve=resolve, run=run)
        raw = _descriptor_bytes(declaration, resolved, fetch, injected)
        document = load_object(raw)
        validate_descriptor(document, implementation, registry)
        prepared.append(
            Prepared(
                declaration=declaration,
                commit=resolved,
                digest=hashlib.sha256(raw).hexdigest(),
                document=document,
                registry=registry,
                result_schema=result_schema,
            )
        )
    return prepared


def _descriptor_bytes(declaration: Declaration, commit: str, fetch, injected) -> bytes:
    if injected is not None and declaration.line in injected:
        return injected[declaration.line]
    url = (
        f"https://raw.githubusercontent.com/{declaration.owner}/"
        f"{declaration.repo}/{commit}/{declaration.path}"
    )
    try:
        return fetch(url)
    except NotFound as exc:
        raise Refusal(f"{url} was not found") from exc


def install(prepared: Prepared, work: Path, run=None) -> str:
    """Create the virtual environment, install the commit, and return its version."""
    run = subprocess.run if run is None else run
    declaration = prepared.declaration
    descriptor = prepared.document
    venv = work / descriptor["id"] / ".venv"
    created = run(
        ["uv", "venv", "--python", descriptor["runtime"]["python"], str(venv)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "").strip()
        raise Refusal(f"could not create the virtual environment: {detail}")
    env = os.environ.copy()
    env.pop("SETUPTOOLS_SCM_PRETEND_VERSION", None)
    if declaration.tag:
        env["SETUPTOOLS_SCM_PRETEND_VERSION"] = declaration.ref.removeprefix("v")
    url = f"git+https://github.com/{declaration.repository}@{prepared.commit}"
    installed = run(
        ["uv", "pip", "install", "--python", str(venv), url],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    if installed.returncode != 0:
        detail = (installed.stderr or installed.stdout or "").strip()
        raise Refusal(f"could not install {declaration.repository}@{prepared.commit}: {detail}")
    return _installed_version(venv, descriptor["package"]["name"], prepared.commit)


def _installed_version(venv: Path, package: str, commit: str) -> str:
    wanted = _normalise_name(package)
    matches = []
    for dist in venv.glob("**/*.dist-info"):
        if not dist.is_dir():
            continue
        meta = _metadata(dist / "METADATA")
        if meta and _normalise_name(meta.get("Name", "")) == wanted:
            matches.append((dist, meta))
    if not matches:
        raise Refusal(f"installed distribution name is not {package}")
    dist, meta = matches[0]
    direct_path = dist / "direct_url.json"
    if not direct_path.is_file():
        raise Refusal("direct_url.json does not record the commit")
    try:
        direct = json.loads(direct_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal("direct_url.json is not readable JSON") from exc
    recorded = direct.get("vcs_info", {}).get("commit_id") if isinstance(direct, dict) else None
    if recorded != commit:
        raise Refusal(f"direct_url.json records {recorded}, not {commit}")
    version = meta.get("Version")
    if not isinstance(version, str) or not version:
        raise Refusal("the installed distribution has no version")
    return version


def _normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _metadata(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    fields: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw or raw[:1].isspace() or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        fields.setdefault(key, value.strip())
    return fields


def _given(evidence: Path, media_dir: Path | None, result: Path) -> dict[str, str | None]:
    media = None
    if media_dir is not None and (media_dir / "media.json").is_file():
        media = str(media_dir)
    return {
        "evidence_manifest": str(evidence),
        "media_dir": media,
        "result": str(result),
    }


def _expand(arguments: list, values: dict[str, str | None]) -> list[str]:
    rendered = []
    for item in arguments:
        if isinstance(item, str):
            rendered.append(_fill(item, values))
        elif isinstance(item, dict) and values.get(item.get("if")):
            rendered.extend(_fill(part, values) for part in item["then"])
    return rendered


def _fill(text: str, values: dict[str, str | None]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        value = values.get(name)
        if not value:
            raise Refusal(f"{{{name}}} was not given")
        return value

    return _TOKEN.sub(replace, text)


def _last_line(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else "the evaluator wrote no result"


def _argparse_usage(stderr: str) -> bool:
    """True when stderr is an argparse usage error (``usage:`` … ``error:``).

    A mapped refusal must not swallow that. Argparse exits 2 for a bad argument,
    and a descriptor may map 2 to refused.
    """
    text = stderr or ""
    usage = text.find("usage:")
    return usage >= 0 and "error:" in text[usage:]


def execute(
    prepared: Prepared,
    *,
    evidence: Path,
    media_dir: Path | None,
    out: Path,
    work: Path,
    timeout: float,
    run=None,
) -> Record:
    """Install and run one prepared descriptor. Returns its provenance record."""
    run = subprocess.run if run is None else run
    descriptor = prepared.document
    version = install(prepared, work, run)
    result_path = _fresh_output(out, descriptor["id"]) / "result.json"
    values = _given(evidence, media_dir, result_path)
    command = [
        str(work / descriptor["id"] / ".venv" / "bin" / "python"),
        "-m",
        descriptor["entrypoint"]["module"],
        *_expand(descriptor["arguments"], values),
    ]
    base = _record(prepared, version, None, "failure")
    try:
        completed = run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        _discard(result_path)
        note = _last_line(stderr) if stderr else f"timed out after {timeout:g} seconds"
        return _finish(base, None, "failure", note, out)
    code = completed.returncode
    stderr = completed.stderr or ""
    label = descriptor["exit_codes"].get(str(code))
    if label == "result":
        if not result_path.is_file():
            return _finish(base, code, "failure", "the result file is missing", out)
        try:
            document = load_object(result_path.read_bytes())
            _accept_result(document, descriptor, prepared.result_schema, prepared.registry)
        except Refusal as exc:
            _discard(result_path)
            return _finish(base, code, "failure", str(exc), out)
        return _finish(base, code, "result", None, out, document)
    _discard(result_path)
    if label == "refused":
        if _argparse_usage(stderr):
            return _finish(base, code, "failure", _last_line(stderr), out)
        return _finish(base, code, "refused", _last_line(stderr), out)
    return _finish(base, code, "failure", _last_line(stderr), out)


def _record(prepared: Prepared, version: str | None, code: int | None, outcome: str) -> Record:
    declaration = prepared.declaration
    return Record(
        line=declaration.line,
        repo=declaration.repository,
        ref=declaration.ref,
        commit=prepared.commit,
        descriptor_sha256=prepared.digest,
        package_name=prepared.document["package"]["name"],
        installed_version=version,
        exit_code=code,
        outcome=outcome,
        evaluator_id=prepared.document["id"],
        kind=prepared.document.get("kind")
        if isinstance(prepared.document.get("kind"), str)
        else "",
    )


def _finish(
    base: Record,
    code: int | None,
    outcome: str,
    note: str | None,
    out: Path,
    document: dict | None = None,
) -> Record:
    base.exit_code = code
    base.outcome = outcome
    base.note = note
    base.document = document
    if note:
        target = out / base.evaluator_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "note.txt").write_text(note + "\n", encoding="utf-8")
    return base


def _discard(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _fresh_output(out: Path, evaluator_id: str) -> Path:
    """Replace ``out/<id>`` with an empty directory before one evaluator runs.

    ``id`` is the descriptor's slug, one path segment. A symlink at that path
    is unlinked and not followed. A real directory is removed. The path is
    refused unless it is directly under ``out``.
    """
    if (
        not isinstance(evaluator_id, str)
        or evaluator_id in {"", ".", ".."}
        or "/" in evaluator_id
        or "\\" in evaluator_id
        or "\x00" in evaluator_id
    ):
        raise Refusal(f"{evaluator_id!r} is not a directory directly under the output")
    out.mkdir(parents=True, exist_ok=True)
    root = out.resolve()
    target = out / evaluator_id
    if target.parent.resolve() != root:
        raise Refusal(f"{evaluator_id!r} is not a directory directly under the output")
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        if target.resolve().parent != root:
            raise Refusal(f"{evaluator_id!r} is not a directory directly under the output")
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    target.mkdir()
    if target.is_symlink() or target.resolve().parent != root:
        raise Refusal(f"{evaluator_id!r} is not a directory directly under the output")
    return target


def run_lines(
    text: str,
    *,
    evidence: Path,
    out: Path,
    media_dir: Path | None = None,
    bundle: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    work: Path | None = None,
    fetch=None,
    resolve=None,
    run=None,
    injected: dict[str, bytes] | None = None,
) -> list[Record]:
    """Validate, install, and run every line. Writes provenance under ``out``."""
    fetch = fetch_bytes if fetch is None else fetch
    resolve = resolve_tag if resolve is None else resolve
    run = subprocess.run if run is None else run
    prepared = prepare(text, fetch=fetch, resolve=resolve, run=run, injected=injected)
    out.mkdir(parents=True, exist_ok=True)
    workspace = work or (out / "work")
    records = []
    for item in prepared:
        records.append(
            execute(
                item,
                evidence=evidence,
                media_dir=media_dir,
                out=out,
                work=workspace,
                timeout=timeout,
                run=run,
            )
        )
    _write_provenance(out, records)
    if bundle is not None:
        _place(bundle, records)
    return records


def _write_provenance(out: Path, records: list[Record]) -> None:
    payload = [record.as_dict() for record in records]
    (out / "evaluators-provenance.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def _advisory_provenance(record: Record) -> dict:
    """The record dict, refused when repo, ref, or commit would fail the Reporter."""
    payload = record.as_dict()
    repo, ref, commit = payload.get("repo"), payload.get("ref"), payload.get("commit")
    if not isinstance(repo, str) or _PROVENANCE_REPO.fullmatch(repo) is None:
        raise Refusal(f"advisory provenance repo {repo!r} is not owner/name")
    if not isinstance(ref, str) or (
        _PROVENANCE_TAG.fullmatch(ref) is None and _SHA.fullmatch(ref) is None
    ):
        raise Refusal(f"advisory provenance ref {ref!r} is not a tag or commit")
    if not isinstance(commit, str) or _SHA.fullmatch(commit) is None:
        raise Refusal(f"advisory provenance commit {commit!r} is not 40 lowercase hex")
    return payload


def _place(bundle_path: Path, records: list[Record]) -> None:
    bundle = load_object(bundle_path.read_text(encoding="utf-8"))
    for record in records:
        if record.kind == "advisory":
            collect._attach_advisory_provenance(
                bundle, record.evaluator_id, _advisory_provenance(record)
            )
        document = record.document
        if document is not None:
            placer = PLACERS.get(record.kind)
            if placer is None:
                raise Refusal(f"unknown kind {record.kind!r}")
            placer(bundle, [document])
            continue
        if record.outcome not in {"refused", "failure"}:
            continue
        if record.kind == "advisory":
            # note is the last non-empty stderr line the loader kept, if any.
            collect._attach_advisory_not_run(
                bundle,
                record.evaluator_id,
                record.outcome,
                record.note or "no result",
            )
            continue
        collect._attach_not_run(
            bundle,
            "failure",
            record.note or "no result",
            refused=record.outcome == "refused",
            evaluator=record.evaluator_id,
        )
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m capability_verifier.evaluators",
        description=(
            "Validate and run evaluators from declaration lines "
            "<owner>/<repo>@<vX.Y.Z-or-commit>/<path>.json."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="resolve and validate; do not install")
    check.add_argument("--lines", type=Path, required=True, help="declaration lines")
    run = commands.add_parser("run", help="validate, install, and run")
    run.add_argument("--lines", type=Path, required=True, help="declaration lines")
    run.add_argument("--evidence-manifest", type=Path, required=True)
    run.add_argument(
        "--media-dir",
        type=Path,
        help="used only when this directory contains media.json",
    )
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--bundle", type=Path, help="verified bundle manifest to attach results to")
    run.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to wait for one evaluator (default {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args(argv)
    try:
        text = args.lines.read_text(encoding="utf-8")
        if args.command == "check":
            found = prepare(text)
            print(f"{len(found)} declaration(s) validated")
            return 0
        records = run_lines(
            text,
            evidence=args.evidence_manifest,
            out=args.out,
            media_dir=args.media_dir,
            bundle=args.bundle,
            timeout=args.timeout,
        )
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    for record in records:
        print(f"{record.evaluator_id}: {record.outcome}")
    if any(record.outcome in {"failure", "refused"} for record in records):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
