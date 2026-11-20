# SPDX-License-Identifier: Apache-2.0
"""Append evaluator result documents onto a bundle.

Each file whose ``format`` is ``livingproof-evaluator-result/1.*`` is appended
whole to the trial named by ``scenario.trial_id``. Documents are not merged
with each other. The report groups them by ``evaluator.id``.

A result whose ``evaluator.kind`` is ``integrity`` is run-level: it is stored
on the assessment, keyed by ``evaluator.id``. More than one integrity
evaluator per run is kept. A failed integrity result marks each assessment
run ``INCONCLUSIVE`` and never changes the robot grade.

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
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath

RESULT_FORMAT = re.compile(r"^livingproof-evaluator-result/1\.\d+\.\d+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_THRESHOLDS_NAME = "thresholds.json"
_MEDIA_PREFIX = "media/"  # where the scenario media job cites its thresholds.json
PROBLEMS_FILE = "evaluator-result-problems.txt"


class ThresholdCitationError(ValueError):
    """The cited thresholds.json is missing, altered, or a different path."""


class ThresholdMissingError(ThresholdCitationError):
    """The cited thresholds.json is not among the results (``relative`` is the cited path)."""

    def __init__(self, message: str, relative: str):
        super().__init__(message)
        self.relative = relative


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

    Each appended document that cites ``thresholds.json`` is checked against
    the file at that URI, relative to the result file. When ``report_dir`` is
    set, the matching bytes are copied there under the cited relative path.

    ``counts``, when given, is filled with ``{evaluator id: {"appended": n,
    "skipped": m}}``.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("results"), list):
        raise TypeError("bundle is missing results")
    directory = Path(directory)
    found = []
    problems: list[str] = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*.json")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:  # truncated, invalid JSON or not UTF-8
                problems.append(f"{path.relative_to(directory).as_posix()}: not valid JSON ({exc})")
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
            problems.append(f"{path.relative_to(directory).as_posix()}: {exc}")
            tally["skipped"] += 1
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
    if report_dir is not None:
        report = Path(report_dir)
        _write_thresholds(report, exports)
        _write_integrity_lines(report, bundle)
    # Used by main() so a run-level result cannot hide a missing trial result.
    if counts is not None:
        counts["_trial_appended"] = trial_appended
        counts["_problems"] = problems
    return bundle


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


def integrity_report_lines(bundle: dict) -> list[str]:
    """Plain report lines for each stored run-level integrity result."""
    assessment = bundle.get("assessment") if isinstance(bundle.get("assessment"), dict) else {}
    stored = assessment.get("integrity_results") if isinstance(assessment, dict) else None
    if not isinstance(stored, dict):
        return []
    lines = []
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
        lines.append(f"{name}: {status}")
    return lines


def _write_integrity_lines(report_dir: Path, bundle: dict) -> None:
    lines = integrity_report_lines(bundle)
    if not lines:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "integrity-lines.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print(
            "usage: collect_evaluator_results.py BUNDLE_MANIFEST RESULTS_DIR REPORT_DIR",
            file=sys.stderr,
        )
        return 2
    manifest = Path(args[0])
    bundle = json.loads(manifest.read_text(encoding="utf-8"))
    counts: dict = {}
    try:
        collect(bundle, Path(args[1]), Path(args[2]), counts)
    except ThresholdCitationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    trial_appended = counts.pop("_trial_appended", 0)
    problems = counts.pop("_problems", [])
    for line in problems:
        print(f"evaluator result not collected: {line}", file=sys.stderr)
    if problems:
        (Path(args[2]) / PROBLEMS_FILE).write_text("\n".join(problems) + "\n", encoding="utf-8")
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
