# SPDX-License-Identifier: Apache-2.0
"""Append evaluator result documents onto a bundle.

Each file whose ``format`` is ``livingproof-evaluator-result/1.*`` is appended
whole to the trial named by ``scenario.trial_id``. Documents are not merged
with each other. The report groups them by ``evaluator.id``.

A result that cites ``thresholds.json`` keeps that input reference. The file
at the cited path is copied into the report output, and its SHA-256 has to
match the citation. A missing file, different bytes, or a different path
fails the collection. The manifest is left unchanged when that happens.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

RESULT_FORMAT = re.compile(r"^livingproof-evaluator-result/1\.\d+\.\d+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_THRESHOLDS_NAME = "thresholds.json"


class ThresholdCitationError(ValueError):
    """The cited thresholds.json is missing, altered, or a different path."""


def collect(bundle: dict, directory: Path, report_dir: Path | None = None) -> dict:
    """Append this bundle's result documents. ``bundle`` is updated.

    A document whose ``scenario.trial_id`` is not one of this bundle's results
    belongs to another configuration. It is left untouched. Documents that do
    match are appended whole and are not merged with each other.

    Each appended document that cites ``thresholds.json`` is checked against
    the file at that URI, relative to the result file. When ``report_dir`` is
    set, the matching bytes are copied there under the cited relative path.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("results"), list):
        raise TypeError("bundle is missing results")
    directory = Path(directory)
    found = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*.json")):
            if not path.is_file() or path.is_symlink():
                continue
            document = json.loads(path.read_text(encoding="utf-8"))
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
    exports: dict[str, bytes] = {}
    for path, document in found:
        scenario = document.get("scenario")
        trial_id = scenario.get("trial_id") if isinstance(scenario, dict) else None
        result = by_id.get(trial_id)
        if result is None:
            continue
        _take_thresholds(path, document, directory, exports)
        matched.append((trial_id, result, document))
    for trial_id, result, document in matched:
        entries = result.get("evaluator_results")
        if entries is None:
            entries = []
            result["evaluator_results"] = entries
        if not isinstance(entries, list):
            raise TypeError(f"result {trial_id!r} evaluator_results is not a list")
        entries.append(document)
    if report_dir is not None:
        _write_thresholds(Path(report_dir), exports)
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
        raise ThresholdCitationError(
            f"missing: {where} cites {relative}, and that file is not in the results"
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
    try:
        collect(bundle, Path(args[1]), Path(args[2]))
    except ThresholdCitationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    manifest.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
