# SPDX-License-Identifier: Apache-2.0
"""Append evaluator result documents onto a bundle.

Each file whose ``format`` is ``livingproof-evaluator-result/1.*`` is appended
whole to the trial named by ``scenario.trial_id``. Documents are not merged
with each other. The report groups them by ``evaluator.id``.

A result whose ``evaluator.kind`` is ``integrity`` is run-level: it is stored
on the assessment, keyed by ``evaluator.id``. More than one integrity
evaluator per run is kept. A failed integrity result marks each assessment
run ``INCONCLUSIVE`` and never changes the robot grade.

A result whose ``evaluator.kind`` is ``advisory`` is stored the same way, at
``assessment.advisory_results[<evaluator.id>]``. The value is that result
document. Advisory results never grade, and they never change
``integrity_results``, the integrity lines, or any run conclusion. The loader
also stores ``assessment.advisory_provenance[<id>]`` for each advisory record,
and ``assessment.advisory_not_run[<id>]`` when that record failed or was
refused. A bundle with no advisory evaluator has none of those three keys.

A declared Scenario line runs in the trial job, away from this collection. The loader
writes ``<evaluator id>.scenario-provenance.json`` beside the scenario results it
uploads, and the collector stores that record at
``assessment.scenario_provenance[<evaluator id>]``: ``line``, ``repo``, ``ref``,
``commit``, ``descriptor_sha256``, ``package_name`` and ``installed_version``. The trial
and media jobs write the same record, which is stored once; two different records for
one evaluator fail the collection. The record does not grade. A bundle with no declared
Scenario line has no ``scenario_provenance``.

An advisory result may cite ``advisory.details_file``. The file at that
relative path is copied into the report output, and its SHA-256 has to match
the citation. A missing file, different bytes, a substituted path, or a path
outside the contract fails the collection. The manifest is left unchanged
when that happens.

Collecting result files never invents a missing-integrity record. The loader
stores ``assessment.integrity_not_run`` when a declared integrity evaluator
fails or is refused. That record marks each assessment run ``INCONCLUSIVE``
and the report shows a Run Integrity line. With no integrity result and no
such record, the report has no Run Integrity card.

A result that cites ``thresholds.json`` keeps that input reference. The file
at the cited path is copied into the report output, and its SHA-256 has to
match the citation. A missing file, different bytes, or a different path
fails the collection. The manifest is left unchanged when that happens.

The command prints, per evaluator, how many documents it appended and how many it
skipped because their trial is not in this bundle (another configuration). A
bundle with trials that ends up with no appended document fails, naming the
artifact pattern in ``EVALUATOR_RESULTS_PATTERN``; a bundle with no trials (a
"not supported" run) has nothing to append.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

RESULT_FORMAT = re.compile(r"^livingproof-evaluator-result/1\.\d+\.\d+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_PROVENANCE_FORMAT = "livingproof-scenario-provenance/1.0.0"
_SCENARIO_PROVENANCE_FAMILY = "livingproof-scenario-provenance/"
SCENARIO_PROVENANCE_SUFFIX = ".scenario-provenance.json"
_EVALUATOR_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_TAG = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SCENARIO_PROVENANCE_FIELDS = (
    "line",
    "repo",
    "ref",
    "commit",
    "descriptor_sha256",
    "package_name",
    "installed_version",
)
# Characters the Reporter rejects in an advisory not-run reason.
_BAD_REASON = re.compile(r"[<>\x00-\x1f\x7f-\x9f\u2028\u2029]")
_REASON_LIMIT = 2000
# Control, bidi and markup characters refused in scenario provenance free text.
_BAD_PROVENANCE_TEXT = re.compile(
    r"[<>\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2028\u2029\u2066-\u2069]"
)
_PROVENANCE_TEXT_LIMIT = 2000
_BIDI_CHARS = frozenset(
    chr(c) for c in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
)
_THRESHOLDS_NAME = "thresholds.json"
# Contract rule for advisory.details_file.uri. The schema states it; the
# collector applies it again because a result file is not re-validated here.
_DETAILS_URI = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.(?:json|txt)$")
_DETAILS_URI_MAX = 256
_MEDIA_PREFIX = "media/"  # where the scenario media job cites its thresholds.json
PROBLEMS_FILE = "evaluator-result-problems.txt"
INTEGRITY_FILE = "integrity-lines.txt"


class ThresholdCitationError(ValueError):
    """The cited thresholds.json is missing, altered, or a different path."""


class ThresholdMissingError(ThresholdCitationError):
    """The cited thresholds.json is not among the results (``relative`` is the cited path)."""

    def __init__(self, message: str, relative: str):
        super().__init__(message)
        self.relative = relative


class DetailsCitationError(ValueError):
    """The cited advisory details file is missing, altered, or a different path."""


class AdvisoryResultError(ValueError):
    """An advisory result breaks a rule the schema cannot express."""


class ScenarioProvenanceError(ValueError):
    """A scenario provenance record is malformed, or two records for one id differ."""


def collect(
    bundle: dict,
    directory: Path,
    report_dir: Path | None = None,
    counts: dict | None = None,
) -> dict:
    """Append this bundle's result documents. ``bundle`` is updated.

    A document whose ``scenario.trial_id`` is not one of this bundle's results
    belongs to another configuration. It is left untouched. Documents that do
    match are appended whole and are not merged with each other.

    An integrity result has no trial id. It is kept on
    ``assessment.integrity_results``, keyed by ``evaluator.id``.

    An advisory result is kept on ``assessment.advisory_results``, keyed by
    ``evaluator.id``. It does not grade. A cited ``advisory.details_file`` is
    checked against the file at that path, relative to the result file, and
    copied into ``report_dir`` when that directory is set.

    Each appended document that cites ``thresholds.json`` is checked against
    the file at that URI, relative to the result file. When ``report_dir`` is
    set, the matching bytes are copied there under the cited relative path.

    A directory with no integrity result does not store ``integrity_not_run``.
    The loader records a missing declared evaluator separately.

    A ``<id>.scenario-provenance.json`` file is stored at
    ``assessment.scenario_provenance[<id>]`` and does not grade.

    ``counts``, when given, is filled with ``{evaluator id: {"appended": n,
    "skipped": m}}``.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("results"), list):
        raise TypeError("bundle is missing results")
    directory = Path(directory)
    found = []
    problems: list[str] = []
    unusable: list[tuple[str, str, str]] = []  # (evaluator, file, reason)
    scenario_provenance: dict[str, dict] = {}
    if directory.is_dir():
        for path in sorted(directory.rglob("*.json")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:  # truncated, invalid JSON or not UTF-8
                _note(unusable, problems, path, directory, None, f"not valid JSON ({exc})")
                continue
            fmt = document.get("format") if isinstance(document, dict) else None
            if isinstance(fmt, str) and fmt.startswith(_SCENARIO_PROVENANCE_FAMILY):
                if fmt != SCENARIO_PROVENANCE_FORMAT:
                    _note(
                        unusable,
                        problems,
                        path,
                        directory,
                        document.get("evaluator"),
                        f"unrecognised scenario provenance format {fmt!r}",
                    )
                    continue
                _take_scenario_provenance(path, document, scenario_provenance)
                continue
            if (
                isinstance(document, dict)
                and isinstance(document.get("format"), str)
                and RESULT_FORMAT.fullmatch(document["format"])
            ):
                found.append((path, document))
    by_id = {}
    for result in bundle["results"]:
        if isinstance(result, dict) and isinstance(result.get("id"), str):
            by_id[result["id"]] = result
    matched = []
    integrity_docs: list[dict] = []
    advisory_docs: list[dict] = []
    trial_appended = 0
    exports: dict[str, bytes] = {}
    for path, document in found:
        evaluator = document.get("evaluator")
        name = evaluator.get("id") if isinstance(evaluator, dict) else None
        tally = (counts if counts is not None else {}).setdefault(
            name if isinstance(name, str) and name else "(no evaluator id)",
            {"appended": 0, "skipped": 0},
        )
        if _is_integrity(document):
            tally["appended"] += 1
            integrity_docs.append(document)
            continue
        if _is_advisory(document):
            problem = repeated_advisory_index(document)
            if problem:
                raise AdvisoryResultError(problem)
            _take_details_file(path, document, directory, exports)
            tally["appended"] += 1
            advisory_docs.append(document)
            continue
        scenario = document.get("scenario")
        trial_id = scenario.get("trial_id") if isinstance(scenario, dict) else None
        result = by_id.get(trial_id)
        if result is None:
            tally["skipped"] += 1
            continue
        try:
            _take_thresholds(path, document, directory, exports)
        except ThresholdMissingError as exc:
            # Only the media job's results (cited under media/) are tolerated: the document is
            # left off the report and the problem is listed. Any other missing citation fails.
            if not exc.relative.startswith(_MEDIA_PREFIX):
                raise
            _note(unusable, problems, path, directory, evaluator, str(exc))
            continue
        tally["appended"] += 1
        trial_appended += 1
        matched.append((trial_id, result, document))
    for trial_id, result, document in matched:
        entries = result.get("evaluator_results")
        if entries is None:
            entries = []
            result["evaluator_results"] = entries
        if not isinstance(entries, list):
            raise TypeError(f"result {trial_id!r} evaluator_results is not a list")
        entries.append(document)
    _attach_integrity(bundle, integrity_docs)
    _attach_advisory(bundle, advisory_docs)
    for evaluator, provenance in scenario_provenance.items():
        _attach_scenario_provenance(bundle, evaluator, provenance)
    if report_dir is not None:
        report = Path(report_dir)
        _write_thresholds(report, exports)
        _write_integrity_lines(report, bundle)
    # Used by main() so a run-level result cannot hide a missing trial result.
    if counts is not None:
        counts["_trial_appended"] = trial_appended
        counts["_problems"] = problems
        counts["_unusable"] = unusable
    return bundle


def _note(unusable, problems, path, directory, evaluator, reason) -> None:
    """Record a result left off the report: which file, which evaluator (if known), and why."""
    where = path.relative_to(directory).as_posix()
    if isinstance(evaluator, dict) and isinstance(evaluator.get("id"), str):
        label = f"{evaluator['id']} {evaluator.get('version') or '(no version)'}"
    else:
        label = "unknown"
    unusable.append((label, where, reason))
    problems.append(f"{where} [evaluator {label}]: {reason}")


def _citations(document: dict) -> list[dict]:
    inputs = document.get("inputs")
    if not isinstance(inputs, list):
        return []
    cited = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri")
        if item.get("role") == "thresholds" or _file_name(uri) == _THRESHOLDS_NAME:
            cited.append(item)
    return cited


def _file_name(uri) -> str | None:
    if not isinstance(uri, str) or not uri or "\\" in uri or "://" in uri or uri.startswith("/"):
        return None
    parts = PurePosixPath(uri).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    return parts[-1]


def _take_thresholds(result_path: Path, document: dict, results_root: Path, exports: dict) -> None:
    for item in _citations(document):
        relative, data = _cited_file(result_path, item, results_root)
        previous = exports.get(relative)
        if previous is not None and previous != data:
            raise ThresholdCitationError(
                f"substituted: {relative} is cited with two different sha256 values"
            )
        exports[relative] = data


def _cited_file(result_path: Path, item: dict, results_root: Path) -> tuple[str, bytes]:
    uri = item.get("uri")
    where = result_path.name
    if not isinstance(uri, str) or not uri or "\\" in uri or "://" in uri or uri.startswith("/"):
        raise ThresholdCitationError(f"substituted: {where} cites {uri!r}, not thresholds.json")
    parts = PurePosixPath(uri).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or parts[-1] != _THRESHOLDS_NAME:
        raise ThresholdCitationError(
            f"substituted: {where} cites {uri!r}, a different path than thresholds.json"
        )
    relative = "/".join(parts)
    candidate = result_path.parent.joinpath(*parts)
    try:
        candidate.relative_to(results_root)
    except ValueError:
        raise ThresholdCitationError(
            f"substituted: {where} cites {relative}, outside the results directory"
        ) from None
    if _symlink_at_or_below(candidate, results_root):
        raise ThresholdCitationError(
            f"substituted: {where} cites {relative}, which is a different path"
        )
    got = candidate.relative_to(result_path.parent).as_posix()
    if got != relative:
        raise ThresholdCitationError(
            f"substituted: {where} cites {relative}, but the file is {got}"
        )
    if not candidate.is_file():
        raise ThresholdMissingError(
            f"missing: {where} cites {relative}, and that file is not in the results", relative
        )
    data = candidate.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    cited = item.get("sha256")
    if not isinstance(cited, str) or _SHA256.fullmatch(cited) is None or digest != cited:
        raise ThresholdCitationError(
            f"altered: {relative} sha256 is {digest}, not the cited {cited!r}"
        )
    return relative, data


def _details_citation(document: dict) -> dict | None:
    payload = document.get("advisory")
    if not isinstance(payload, dict) or "details_file" not in payload:
        return None
    item = payload.get("details_file")
    if not isinstance(item, dict):
        raise DetailsCitationError("substituted: advisory.details_file is not an object")
    return item


def _take_details_file(
    result_path: Path, document: dict, results_root: Path, exports: dict
) -> None:
    item = _details_citation(document)
    if item is None:
        return
    relative, data = _cited_details(result_path, item, results_root)
    previous = exports.get(relative)
    if previous is not None and previous != data:
        raise DetailsCitationError(
            f"substituted: {relative} is cited with two different sha256 values"
        )
    exports[relative] = data


def _cited_details(result_path: Path, item: dict, results_root: Path) -> tuple[str, bytes]:
    uri = item.get("uri")
    where = result_path.name
    if (
        not isinstance(uri, str)
        or len(uri) > _DETAILS_URI_MAX
        or _DETAILS_URI.fullmatch(uri) is None
    ):
        raise DetailsCitationError(f"substituted: {where} cites {uri!r}, outside the contract")
    relative = uri
    parts = PurePosixPath(uri).parts
    candidate = result_path.parent.joinpath(*parts)
    try:
        candidate.relative_to(results_root)
    except ValueError:
        raise DetailsCitationError(
            f"substituted: {where} cites {relative}, outside the results directory"
        ) from None
    if _symlink_at_or_below(candidate, results_root):
        raise DetailsCitationError(
            f"substituted: {where} cites {relative}, which is a different path"
        )
    got = candidate.relative_to(result_path.parent).as_posix()
    if got != relative:
        raise DetailsCitationError(f"substituted: {where} cites {relative}, but the file is {got}")
    if not candidate.is_file():
        raise DetailsCitationError(
            f"missing: {where} cites {relative}, and that file is not in the results"
        )
    data = candidate.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    cited = item.get("sha256")
    if not isinstance(cited, str) or _SHA256.fullmatch(cited) is None or digest != cited:
        raise DetailsCitationError(
            f"altered: {relative} sha256 is {digest}, not the cited {cited!r}"
        )
    return relative, data


def _symlink_at_or_below(path: Path, root: Path) -> bool:
    """True when ``path`` or a directory under ``root`` is a symlink.

    Ancestors of ``root`` are not checked. A symlink at ``/tmp`` must not make
    a regular file inside a results directory look substituted.
    """
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == root:
            return False
        parent = current.parent
        if parent == current:
            return True
        current = parent


def _write_thresholds(report_dir: Path, exports: dict[str, bytes]) -> None:
    planned = []
    for relative, data in exports.items():
        dest = report_dir.joinpath(*PurePosixPath(relative).parts)
        if _symlink_at_or_below(dest, report_dir):
            raise ThresholdCitationError(
                f"substituted: {relative} would replace a different path in the report"
            )
        if dest.exists() and (not dest.is_file() or dest.read_bytes() != data):
            raise ThresholdCitationError(
                f"substituted: {relative} is already a different file in the report"
            )
        planned.append((dest, data))
    for dest, data in planned:
        if dest.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _is_integrity(document: dict) -> bool:
    evaluator = document.get("evaluator")
    return isinstance(evaluator, dict) and evaluator.get("kind") == "integrity"


def _is_advisory(document: dict) -> bool:
    evaluator = document.get("evaluator")
    return isinstance(evaluator, dict) and evaluator.get("kind") == "advisory"


def _whole_index(value: object) -> int | None:
    """The integer a trial index counts as. A whole-number float is that integer.

    JSON Schema's integer type accepts ``1.0``. ``json.loads`` parses that token
    as a float, so ``1`` and ``1.0`` have to compare as the same index.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def repeated_advisory_index(document: dict) -> str | None:
    """An error when an advisory result repeats a trial index, else None.

    The schema's ``uniqueItems`` rejects an identical entry. It cannot require
    the index values to be unique when the hashes differ, so the loader does.
    """
    if not _is_advisory(document):
        return None
    payload = document.get("advisory")
    if not isinstance(payload, dict):
        return None
    trials = payload.get("trials")
    if not isinstance(trials, list):
        return None
    seen: dict[int, int] = {}
    for position, trial in enumerate(trials):
        if not isinstance(trial, dict):
            continue
        index = _whole_index(trial.get("index"))
        if index is None:
            continue
        previous = seen.get(index)
        if previous is None:
            seen[index] = position
            continue
        return f"advisory/trials/{position}/index: repeats advisory/trials/{previous}/index"
    return None


_GRADED_STATUSES = frozenset({"COMPLETED", "ERROR"})


def _integrity_failed(document: dict) -> bool:
    """True only when the integrity run was judged and failed.

    A 1.2.0 INCONCLUSIVE run (no check applied) and any run status this
    collector does not recognise are not graded, so neither is a failure
    (livingproof-evaluator#38: a 1.x minor may add run statuses).
    """
    payload = document.get("integrity") if isinstance(document.get("integrity"), dict) else {}
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    status = run.get("test_system_status")
    if isinstance(status, str) and status not in _GRADED_STATUSES:
        return False
    if run.get("success") is False or run.get("conclusion") == "INCONCLUSIVE":
        return True
    return isinstance(status, str) and status != "COMPLETED"


def _attach_integrity(bundle: dict, documents: list[dict]) -> None:
    if not documents:
        return
    assessment = bundle.get("assessment")
    if not isinstance(assessment, dict):
        assessment = {}
        bundle["assessment"] = assessment
    stored = assessment.get("integrity_results")
    if stored is None:
        stored = {}
        assessment["integrity_results"] = stored
    if not isinstance(stored, dict):
        raise TypeError("assessment integrity_results is not a mapping")
    failed = False
    for document in documents:
        evaluator = document.get("evaluator") if isinstance(document.get("evaluator"), dict) else {}
        name = evaluator.get("id")
        key = name if isinstance(name, str) and name else "(no evaluator id)"
        stored[key] = document
        if _integrity_failed(document):
            failed = True
    if not failed:
        return
    runs = assessment.get("runs") if isinstance(assessment.get("runs"), list) else []
    for run in runs:
        if isinstance(run, dict):
            run["conclusion"] = "INCONCLUSIVE"


def _attach_advisory(bundle: dict, documents: list[dict]) -> None:
    """Store advisory results. They do not grade and they do not touch integrity.

    Each document is stored at ``assessment.advisory_results[<evaluator.id>]``,
    the same one-document-per-id shape as ``integrity_results``. Nothing is
    written when ``documents`` is empty, so a bundle with no advisory result
    has no ``advisory_results`` key.
    """
    if not documents:
        return
    assessment = bundle.get("assessment")
    if not isinstance(assessment, dict):
        assessment = {}
        bundle["assessment"] = assessment
    stored = assessment.get("advisory_results")
    if stored is None:
        stored = {}
        assessment["advisory_results"] = stored
    if not isinstance(stored, dict):
        raise TypeError("assessment advisory_results is not a mapping")
    for document in documents:
        problem = repeated_advisory_index(document)
        if problem:
            raise AdvisoryResultError(problem)
        evaluator = document.get("evaluator") if isinstance(document.get("evaluator"), dict) else {}
        name = evaluator.get("id")
        key = name if isinstance(name, str) and name else "(no evaluator id)"
        stored[key] = document
        _drop_advisory_id(bundle, "advisory_not_run", key)


def _advisory_reason(note: str | None) -> str:
    """Plain text the Reporter will accept, or ``no result`` when nothing remains.

    ``<``, ``>``, and control characters, including newlines, are replaced.
    The result is one line of at most 2000 characters.
    """
    text = "" if note is None else str(note)
    text = _BAD_REASON.sub(" ", text).strip()
    if len(text) > _REASON_LIMIT:
        text = text[:_REASON_LIMIT].rstrip()
    return text or "no result"


def _drop_advisory_id(bundle: dict, field: str, key: str) -> None:
    """Remove ``key`` from one advisory map, and the map when it is left empty."""
    assessment = bundle.get("assessment")
    if not isinstance(assessment, dict):
        return
    stored = assessment.get(field)
    if not isinstance(stored, dict) or key not in stored:
        return
    del stored[key]
    if not stored:
        del assessment[field]


def _advisory_key(evaluator: str) -> str:
    if isinstance(evaluator, str) and evaluator.strip():
        return evaluator
    return "(no evaluator id)"


def _advisory_map(bundle: dict, field: str) -> dict:
    assessment = bundle.get("assessment")
    if not isinstance(assessment, dict):
        assessment = {}
        bundle["assessment"] = assessment
    stored = assessment.get(field)
    if stored is None:
        stored = {}
        assessment[field] = stored
    if not isinstance(stored, dict):
        raise TypeError(f"assessment {field} is not a mapping")
    return stored


def _attach_advisory_provenance(bundle: dict, evaluator: str, provenance: dict) -> None:
    """Store one advisory record's provenance. It does not grade."""
    _advisory_map(bundle, "advisory_provenance")[_advisory_key(evaluator)] = provenance


def scenario_provenance_record(record: object, where: str) -> dict:
    """The checked record: exactly the seven fields, each well formed."""
    if not isinstance(record, dict):
        raise ScenarioProvenanceError(f"{where}: provenance is not an object")
    extra = sorted(set(record) - set(_SCENARIO_PROVENANCE_FIELDS))
    if extra:
        raise ScenarioProvenanceError(f"{where}: unexpected field {extra[0]!r}")
    missing = [name for name in _SCENARIO_PROVENANCE_FIELDS if name not in record]
    if missing:
        raise ScenarioProvenanceError(f"{where}: missing field {missing[0]!r}")

    def text(name: str) -> str:
        value = record[name]
        if not isinstance(value, str) or not value.strip():
            raise ScenarioProvenanceError(f"{where}: {name} is not a non-empty string")
        return value

    def classify(char: str) -> str:
        if char in "<>":
            return "markup"
        if char in _BIDI_CHARS:
            return "bidi"
        return "control"

    for name in ("line", "package_name", "installed_version"):
        value = text(name)
        bad = _BAD_PROVENANCE_TEXT.search(value)
        if bad is not None:
            kind = classify(bad.group(0))
            raise ScenarioProvenanceError(f"{where}: {name} contains a {kind} character")
        if len(value) > _PROVENANCE_TEXT_LIMIT:
            raise ScenarioProvenanceError(
                f"{where}: {name} is longer than {_PROVENANCE_TEXT_LIMIT} characters"
            )
    if _REPO.fullmatch(text("repo")) is None:
        raise ScenarioProvenanceError(f"{where}: repo {record['repo']!r} is not owner/name")
    ref = text("ref")
    if _TAG.fullmatch(ref) is None and _COMMIT.fullmatch(ref) is None:
        raise ScenarioProvenanceError(f"{where}: ref {ref!r} is not a vX.Y.Z tag or commit")
    if _COMMIT.fullmatch(text("commit")) is None:
        raise ScenarioProvenanceError(f"{where}: commit {record['commit']!r} is not 40 hex")
    if _SHA256.fullmatch(text("descriptor_sha256")) is None:
        raise ScenarioProvenanceError(f"{where}: descriptor_sha256 is not 64 lowercase hex")
    return {name: record[name] for name in _SCENARIO_PROVENANCE_FIELDS}


def _take_scenario_provenance(path: Path, document: dict, taken: dict[str, dict]) -> None:
    where = path.name
    evaluator = document.get("evaluator")
    name = evaluator.get("id") if isinstance(evaluator, dict) else None
    if not isinstance(name, str) or _EVALUATOR_ID.fullmatch(name) is None:
        raise ScenarioProvenanceError(f"{where}: evaluator id {name!r} is not valid")
    record = scenario_provenance_record(document.get("provenance"), f"{where} ({name})")
    if name in taken and taken[name] != record:
        raise ScenarioProvenanceError(
            f"{where}: two different scenario provenance records for evaluator {name}"
        )
    taken[name] = record


def _attach_scenario_provenance(bundle: dict, evaluator: str, provenance: dict) -> None:
    """Store one declared Scenario line's resolved pin. It does not grade."""
    _advisory_map(bundle, "scenario_provenance")[evaluator] = provenance


def _attach_advisory_not_run(bundle: dict, evaluator: str, outcome: str, reason: str) -> None:
    """Record a missing advisory result. Does not touch integrity or conclusions.

    The same id is removed from ``advisory_results``, so a result and a not-run
    record are never stored together.
    """
    if outcome not in {"failure", "refused"}:
        raise ValueError(f"advisory not-run outcome {outcome!r} is not failure or refused")
    key = _advisory_key(evaluator)
    _drop_advisory_id(bundle, "advisory_results", key)
    _advisory_map(bundle, "advisory_not_run")[key] = {
        "outcome": outcome,
        "reason": _advisory_reason(reason),
    }


def _sentence(text: str) -> str:
    """``text`` ended with a full stop (none added when it already ends one), so a sentence can follow."""
    text = text.rstrip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _attach_not_run(
    bundle: dict,
    job_result: str,
    reason: str,
    *,
    evaluator: str,
    refused: bool = False,
) -> None:
    """Record a missing integrity result for one declared evaluator.

    The loader calls this when that evaluator fails or is refused. Collecting
    result files does not.
    """
    assessment = bundle.get("assessment")
    if not isinstance(assessment, dict):
        assessment = {}
        bundle["assessment"] = assessment
    assessment["integrity_not_run"] = {
        "evaluator": evaluator,
        "job_result": job_result,
        "reason": reason,
    }
    if refused:
        assessment["integrity_not_run"]["refused"] = True
    runs = assessment.get("runs") if isinstance(assessment.get("runs"), list) else []
    for run in runs:
        if isinstance(run, dict):
            run["conclusion"] = "INCONCLUSIVE"


def _label(document: dict, status: str) -> str:
    """The word the report shows: PASS or FAILED, else the stored status as it is.

    Display only: the stored result keeps COMPLETED, ERROR and the rest.
    """
    if _integrity_failed(document):
        return "FAILED"
    return "PASS" if status == "COMPLETED" else status


def integrity_report_lines(bundle: dict) -> list[str]:
    """Plain report lines for each stored run-level integrity result."""
    assessment = bundle.get("assessment") if isinstance(bundle.get("assessment"), dict) else {}
    stored = assessment.get("integrity_results") if isinstance(assessment, dict) else None
    lines = []
    missing = assessment.get("integrity_not_run") if isinstance(assessment, dict) else None
    if isinstance(missing, dict):
        tail = "The run is INCONCLUSIVE; the robot grade is not backed by an integrity result."
        if missing.get("refused"):
            lines.append(f"Run Integrity: REFUSED: {_sentence(str(missing.get('reason')))} {tail}")
        else:
            lines.append(
                f"Run Integrity: NOT RUN ({missing.get('job_result')}): "
                f"{_sentence(str(missing.get('reason')))} " + tail
            )
    if not isinstance(stored, dict):
        return lines
    for key, document in stored.items():
        if not isinstance(document, dict):
            continue
        evaluator = document.get("evaluator") if isinstance(document.get("evaluator"), dict) else {}
        name = evaluator.get("display_name")
        if not isinstance(name, str) or not name.strip():
            name = key
        payload = document.get("integrity") if isinstance(document.get("integrity"), dict) else {}
        run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        status = run.get("test_system_status")
        if not isinstance(status, str) or not status:
            status = "UNKNOWN"
        lines.append(f"{name}: {_label(document, status)}")
    return lines


def _write_integrity_lines(report_dir: Path, bundle: dict) -> None:
    lines = integrity_report_lines(bundle)
    if not lines:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / INTEGRITY_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


_VERDICT = re.compile(r"[:;] overall [A-Z][A-Za-z_ ]*\.(?:\s.*)?$")
_HTML_VERDICT = re.compile(r"<p class=(['\"])stored-status\1>[^<]*</p>")
# cmark-gfm extended autolinks. www. only counts at the start of a line, after
# cmark_isspace, or after one of these. http/https/ftp are recognised wherever
# the scheme is exactly those letters.
_AUTOLINK_BEFORE = frozenset("*_~(")
_SCHEMES = ("http", "https", "ftp")
# cmark_isspace, src/cmark_ctype.c: class 1 is only 0x09 TAB, 0x0A LF, 0x0D CR
# and 0x20 space. 0x0B VT and 0x0C FF are class 0. autolink.c uses this for the
# byte before "www." and for the end of an extended autolink.
_CMARK_SPACE = frozenset("\t\n\r ")


# cmark_utf8proc_is_punctuation, src/utf8.c of cmark-gfm 0.29.0.gfm.13 (the
# library vendored by cmarkgfm 2025.10.22). ASCII uses cmark_ispunct. The rest
# is that function's explicit list, which is not Unicode general category P.
_CMARK_PUNCT_RANGES = (
    (161, 161),
    (167, 167),
    (171, 171),
    (182, 183),
    (187, 187),
    (191, 191),
    (894, 894),
    (903, 903),
    (1370, 1375),
    (1417, 1418),
    (1470, 1470),
    (1472, 1472),
    (1475, 1475),
    (1478, 1478),
    (1523, 1524),
    (1545, 1546),
    (1548, 1549),
    (1563, 1563),
    (1566, 1567),
    (1642, 1645),
    (1748, 1748),
    (1792, 1805),
    (2039, 2041),
    (2096, 2110),
    (2142, 2142),
    (2404, 2405),
    (2416, 2416),
    (2800, 2800),
    (3572, 3572),
    (3663, 3663),
    (3674, 3675),
    (3844, 3858),
    (3860, 3860),
    (3898, 3901),
    (3973, 3973),
    (4048, 4052),
    (4057, 4058),
    (4170, 4175),
    (4347, 4347),
    (4960, 4968),
    (5120, 5120),
    (5741, 5742),
    (5787, 5788),
    (5867, 5869),
    (5941, 5942),
    (6100, 6102),
    (6104, 6106),
    (6144, 6154),
    (6468, 6469),
    (6686, 6687),
    (6816, 6822),
    (6824, 6829),
    (7002, 7008),
    (7164, 7167),
    (7227, 7231),
    (7294, 7295),
    (7360, 7367),
    (7379, 7379),
    (8208, 8231),
    (8240, 8259),
    (8261, 8273),
    (8275, 8286),
    (8317, 8318),
    (8333, 8334),
    (8968, 8971),
    (9001, 9002),
    (10088, 10101),
    (10181, 10182),
    (10214, 10223),
    (10627, 10648),
    (10712, 10715),
    (10748, 10749),
    (11513, 11516),
    (11518, 11519),
    (11632, 11632),
    (11776, 11822),
    (11824, 11842),
    (12289, 12291),
    (12296, 12305),
    (12308, 12319),
    (12336, 12336),
    (12349, 12349),
    (12448, 12448),
    (12539, 12539),
    (42238, 42239),
    (42509, 42511),
    (42611, 42611),
    (42622, 42622),
    (42738, 42743),
    (43124, 43127),
    (43214, 43215),
    (43256, 43258),
    (43310, 43311),
    (43359, 43359),
    (43457, 43469),
    (43486, 43487),
    (43612, 43615),
    (43742, 43743),
    (43760, 43761),
    (44011, 44011),
    (64830, 64831),
    (65040, 65049),
    (65072, 65106),
    (65108, 65121),
    (65123, 65123),
    (65128, 65128),
    (65130, 65131),
    (65281, 65283),
    (65285, 65290),
    (65292, 65295),
    (65306, 65307),
    (65311, 65312),
    (65339, 65341),
    (65343, 65343),
    (65371, 65371),
    (65373, 65373),
    (65375, 65381),
    (65792, 65794),
    (66463, 66463),
    (66512, 66512),
    (66927, 66927),
    (67671, 67671),
    (67871, 67871),
    (67903, 67903),
    (68176, 68184),
    (68223, 68223),
    (68336, 68342),
    (68409, 68415),
    (68505, 68508),
    (69703, 69709),
    (69819, 69820),
    (69822, 69825),
    (69952, 69955),
    (70004, 70005),
    (70085, 70088),
    (70093, 70093),
    (70200, 70205),
    (70854, 70854),
    (71105, 71113),
    (71233, 71235),
    (74864, 74868),
    (92782, 92783),
    (92917, 92917),
    (92983, 92987),
    (92996, 92996),
    (113823, 113823),
)
_CMARK_PUNCT = frozenset(
    code for start, stop in _CMARK_PUNCT_RANGES for code in range(start, stop + 1)
)


def _cmark_utf8_punct(code: int) -> bool:
    """True when cmark_utf8proc_is_punctuation accepts this code point."""
    if code < 128:
        # cmark_ispunct, src/cmark_ctype.c class 2.
        return (
            0x21 <= code <= 0x2F
            or 0x3A <= code <= 0x40
            or 0x5B <= code <= 0x60
            or 0x7B <= code <= 0x7E
        )
    return code in _CMARK_PUNCT


def _cmark_utf8_space(ch: str) -> bool:
    """cmark_utf8proc_is_space, src/utf8.c: Zs plus TAB, LF, FF and CR.

    NBSP (160), U+2000-U+200A (including em space U+2003) and U+3000 are spaces.
    U+2028 and U+2029 are not. is_valid_hostchar rejects this set.
    """
    code = ord(ch)
    return code in (9, 10, 12, 13, 32, 160, 5760, 8239, 8287, 12288) or 8192 <= code <= 8202


def _is_host_char(ch: str) -> bool:
    """True when cmark-gfm's is_valid_hostchar would keep this in a host label.

    Space is cmark_utf8proc_is_space. Non-ASCII punctuation is
    cmark_utf8proc_is_punctuation's list, not Unicode category P.
    """
    if _cmark_utf8_space(ch):
        return False
    code = ord(ch)
    if code < 128:
        return ch.isalnum()
    return not _cmark_utf8_punct(code)


def _check_domain(text: str, *, allow_short: bool) -> int:
    """0 when cmark-gfm's check_domain rejects the host, and non-zero when it accepts.

    cmark walks UTF-8 bytes and returns a byte length. ``_url_span`` and ``_www_span`` use
    only the zero test, then scan the rest of the address by code point, so this returns a
    code-point index solely as that accept/reject flag. A non-ASCII first character makes
    byte 1 a continuation byte and the C loop stops at once (length 1, or 0 for ``www``).
    A later non-ASCII character is stopped the same way: its lead byte may be a host
    character, and the continuation byte never is. A backslash is skipped and the next
    character is examined, so ``x\\_y`` counts the underscore.
    """
    if not text:
        return 0
    size = len(text)
    i = 1
    uscore1 = 0
    uscore2 = 0
    dots = 0
    if ord(text[0]) > 127:
        # cmark scans bytes: index 1 is a UTF-8 continuation byte, so the loop stops at once.
        return 1 if allow_short else 0
    while i < size - 1:
        ch = text[i]
        if ch == "\\" and i < size - 2:
            # cmark: skip the backslash, then examine the escaped character.
            i += 1
            ch = text[i]
        if ord(ch) > 127:
            # The lead byte may be a host character, but the continuation byte never is.
            break
        if ch == "_":
            uscore2 += 1
        elif ch == ".":
            uscore1 = uscore2
            uscore2 = 0
            dots += 1
        elif ch != "-" and not _is_host_char(ch):
            break
        i += 1
    if (uscore1 or uscore2) and dots <= 10:
        return 0
    if allow_short:
        return i
    return i if dots else 0


def _autolink_end(text: str, start: int) -> int:
    end = start
    size = len(text)
    while end < size and text[end] not in _CMARK_SPACE and text[end] != "<":
        end += 1
    return end


def _autolink_delim(text: str) -> int:
    """Exclusive end of the link cmark-gfm keeps. The rest is trailing punctuation.

    Same rules as cmark's ``autolink_delim``: trim ``?!.,:*_~'"``, drop an unbalanced
    ``)``, and drop a trailing ``&name;``. Those characters are not part of the link, so
    leaving ``*``, ``_`` or ``~`` raw lets them emphasise or strike the words that follow.
    """
    link_end = len(text)
    opening = 0
    closing = 0
    for index, ch in enumerate(text):
        if ch == "<":
            link_end = index
            break
        if ch == "(":
            opening += 1
        elif ch == ")":
            closing += 1
    while link_end > 0:
        ch = text[link_end - 1]
        if ch == ")":
            if closing <= opening:
                return link_end
            closing -= 1
            link_end -= 1
        elif ch in "?!.,:*_~'\"":
            link_end -= 1
        elif ch == ";":
            new_end = link_end - 2
            while new_end > 0 and text[new_end].isascii() and text[new_end].isalpha():
                new_end -= 1
            if new_end < link_end - 2 and text[new_end] == "&":
                link_end = new_end
            else:
                link_end -= 1
        else:
            return link_end
    return link_end


_TAIL_MARKS = frozenset("*_~")


def _tail_can_mark(text: str) -> bool:
    return any(ch in _TAIL_MARKS for ch in text)


def _span_can_be_angle_autolink(span: str) -> bool:
    """A raw ``<...>`` is safe only for a span that cannot itself open or close a tag."""
    if not span:
        return False
    for ch in span:
        if ch in "<>" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127:
            return False
    return True


def _scheme_address(span: str) -> bool:
    return span.lower().startswith(("http://", "https://", "ftp://"))


def _escape_trimmed_tail(text: str, *, keep_ampersand: bool) -> str:
    """Escape a trimmed tail. ``*``, ``_`` and ``~`` become ``\\*``, ``\\_`` and ``\\~``.

    A trailing ``&name;`` is left with a raw ``&`` when the tail is not shielded: ``\\&`` would
    be swallowed into the href, and cmark then decodes the entity to text. After an angle
    autolink the backslash is outside the link, so ``&`` is escaped normally.
    """
    escaped = _md_escape_text(text)
    if keep_ampersand:
        return escaped.replace("\\&", "&")
    return escaped


def _shield_address(kept: str, tail: str) -> str:
    """Keep a trimmed ``*``, ``_`` or ``~`` from emphasising the words after the address.

    A scheme address is wrapped in a CommonMark angle autolink, ``<https://e.com/a>``, and only
    when the tail needs it. ``www.`` is not a scheme, so it cannot be an angle autolink: the dot
    is escaped and the rest of the span is escaped, which leaves the characters visible and
    unlinked. A span that contains whitespace, ``<``, ``>`` or a control character is not
    wrapped; the scheme's slashes are escaped instead, so no raw bracket is written.
    """
    if kept.startswith("www."):
        return "www\\." + _md_escape_text(kept[4:] + tail)
    if _scheme_address(kept) and _span_can_be_angle_autolink(kept):
        assert _span_can_be_angle_autolink(kept)
        # cmark decodes entities inside <...>, so '&' is written '&amp;' and the
        # href and the visible address stay the original characters.
        return (
            "<"
            + kept.replace("&", "&amp;")
            + ">"
            + _escape_trimmed_tail(tail, keep_ampersand=False)
        )
    if _scheme_address(kept):
        scheme = kept.lower().find("://")
        return kept[:scheme] + ":\\/\\/" + _md_escape_text(kept[scheme + 3 :] + tail)
    return _md_escape_text(kept + tail)


def _url_span(line: str, index: int) -> tuple[int, int] | None:
    if not line.startswith("://", index):
        return None
    scheme = index
    while scheme > 0 and line[scheme - 1].isascii() and line[scheme - 1].isalpha():
        scheme -= 1
    name = line[scheme:index].lower()
    if name not in _SCHEMES:
        return None
    host = index + 3
    if host >= len(line) or not _is_host_char(line[host]):
        return None
    if _check_domain(line[host:], allow_short=True) == 0:
        return None
    end = _autolink_end(line, host)
    # A CommonMark angle autolink is '<scheme:...>'. Its closing '>' is a delimiter, not part of
    # the address. Leaving it in the span makes the extended autolink swallow it once '<' is '&lt;'.
    if scheme and line[scheme - 1] == "<":
        close = line.find(">", host, end)
        if close != -1:
            end = close
    if end <= scheme:
        return None
    return scheme, end


def _www_span(line: str, index: int) -> tuple[int, int] | None:
    if not line.startswith("www.", index):
        return None
    if index and line[index - 1] not in _AUTOLINK_BEFORE and line[index - 1] not in _CMARK_SPACE:
        return None
    if _check_domain(line[index:], allow_short=False) == 0:
        return None
    end = _autolink_end(line, index)
    if end <= index:
        return None
    return index, end


def _autolink_spans(line: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(line):
        found = _www_span(line, index) or _url_span(line, index)
        if found is None:
            index += 1
            continue
        spans.append(found)
        index = found[1]
    return spans


def _md_escape(line: str) -> str:
    """Backslash-escape what Markdown would act on, so an evaluator's message stays plain text.

    Only the span cmark-gfm will actually auto-link is copied through, after the same
    trailing-punctuation trim cmark applies. When that trimmed tail contains ``*``, ``_`` or
    ``~``, a scheme address is written as an angle autolink (``<https://e.com/a>\\~``) so the
    href stays the address and the mark is a backslash escape, not emphasis. The angle brackets
    are the only raw ``<`` and ``>`` this function writes. An ``https://`` run whose host is
    not a real link is escaped with everything else, and a ``<`` in the message is ``&lt;``.
    """
    parts: list[str] = []
    cursor = 0
    for start, end in _autolink_spans(line):
        kept_end = start + _autolink_delim(line[start:end])
        kept = line[start:kept_end]
        tail = line[kept_end:end]
        parts.append(_md_escape_text(line[cursor:start]))
        if _tail_can_mark(tail):
            parts.append(_shield_address(kept, tail))
        else:
            parts.append(kept)
            parts.append(_escape_trimmed_tail(tail, keep_ampersand=True))
        cursor = end
    parts.append(_md_escape_text(line[cursor:]))
    return "".join(parts)


def _md_escape_text(text: str) -> str:
    pieces: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            pieces.append(re.sub(r"([\\`*_~&\[\]|])", r"\\\1", "".join(pending)))
            pending.clear()

    for ch in text:
        if ch == "<":
            flush()
            pieces.append("&lt;")
        elif ch == ">":
            flush()
            pieces.append("&gt;")
        else:
            pending.append(ch)
    flush()
    # A scheme or www. left in escaped text is still an autolink, and it swallows the
    # backslash in front of a following mark (autolink.c url_match / www_match).
    escaped = "".join(pieces)
    return escaped.replace("://", ":\\/\\/").replace("www.", "www\\.")


def place_integrity_lines(report_dir: Path) -> bool:
    """Put the integrity lines next to the verdict in report.md, report.txt and report.html.

    The reporter does not know the integrity result, so the lines the collector wrote are placed
    after rendering: in Markdown and text right after each ``... overall <GRADE>.`` line, in HTML
    right after each stored-status paragraph, so no format shows a verdict without the state.
    Where the verdict is not found (a reporter that words it differently), the lines go at the
    end instead, and the first format never loses them. Returns False when there are no lines.
    """
    source = report_dir / INTEGRITY_FILE
    if not source.is_file():
        return False
    lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return False
    for name, blank in (("report.md", True), ("report.txt", False)):
        path = report_dir / name
        if not path.is_file():
            continue
        out: list[str] = []
        placed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            out.append(line)
            if _VERDICT.search(line) and not line.startswith(("|", "<")):
                for extra in lines:
                    shown = _md_escape(extra) if blank else extra
                    out += ["", shown] if blank else [shown]
                placed = True
        if not placed:
            out += ["", *(_md_escape(x) if blank else x for x in lines)]
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    page = report_dir / "report.html"
    if page.is_file():
        text = page.read_text(encoding="utf-8")
        shown = "".join(
            f"<p class='stored-status integrity-status'>{html.escape(line, quote=False)}</p>"
            for line in lines
        )
        text, count = _HTML_VERDICT.subn(lambda found: found.group(0) + shown, text)
        if not count:
            text = text.replace("<main class='wrap'>", "<main class='wrap'>" + shown, 1)
        page.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--place-integrity":
        place_integrity_lines(Path(args[1]))
        return 0
    if len(args) != 3:
        print(
            "usage: collect_evaluator_results.py BUNDLE_MANIFEST RESULTS_DIR REPORT_DIR\n"
            "       collect_evaluator_results.py --place-integrity REPORT_DIR",
            file=sys.stderr,
        )
        return 2
    manifest = Path(args[0])
    bundle = json.loads(manifest.read_text(encoding="utf-8"))
    counts: dict = {}
    try:
        collect(
            bundle,
            Path(args[1]),
            Path(args[2]),
            counts,
        )
    except (
        ThresholdCitationError,
        DetailsCitationError,
        AdvisoryResultError,
        ScenarioProvenanceError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    trial_appended = counts.pop("_trial_appended", 0)
    problems = counts.pop("_problems", [])
    for label, where, reason in counts.pop("_unusable", []):
        print(f"evaluator {label}: skipped (unusable result: {where}: {reason})")
    for line in problems:
        print(f"evaluator result not collected: {line}", file=sys.stderr)
    if problems:
        problems_path = Path(args[2]) / PROBLEMS_FILE
        problems_path.parent.mkdir(parents=True, exist_ok=True)  # reports/ may not exist yet
        problems_path.write_text("\n".join(problems) + "\n", encoding="utf-8")
    for name in sorted(counts):
        tally = counts[name]
        print(
            f"evaluator {name}: appended {tally['appended']}, "
            f"skipped {tally['skipped']} (trial not in this bundle)"
        )
    trials = sum(1 for result in bundle["results"] if isinstance(result, dict))
    if trials == 0:
        print("no trials in this bundle, so no evaluator result to append")
    elif trial_appended == 0:
        pattern = os.environ.get("EVALUATOR_RESULTS_PATTERN") or "(EVALUATOR_RESULTS_PATTERN unset)"
        print(
            f"no evaluator result was appended to this bundle's {trials} trial(s). "
            f"Expected result documents for these trials in {args[1]}, downloaded from "
            f"artifacts matching {pattern}. Check the artifact name and prefix, and that "
            "each result's scenario.trial_id is one of this bundle's result ids.",
            file=sys.stderr,
        )
        return 1
    manifest.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
