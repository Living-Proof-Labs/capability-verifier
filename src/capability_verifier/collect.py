# SPDX-License-Identifier: Apache-2.0
"""Append evaluator result documents onto a bundle.

Each file whose ``format`` is ``livingproof-evaluator-result/1.*`` is appended
whole to the trial named by ``scenario.trial_id``. Documents are not merged
with each other. The report groups them by ``evaluator.id``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RESULT_FORMAT = re.compile(r"^livingproof-evaluator-result/1\.\d+\.\d+$")


def collect(bundle: dict, directory: Path) -> dict:
    """Append every result document under ``directory``. ``bundle`` is updated."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("results"), list):
        raise ValueError("bundle is missing results")
    directory = Path(directory)
    documents = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*.json")):
            if not path.is_file():
                continue
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, dict) and isinstance(document.get("format"), str):
                if RESULT_FORMAT.fullmatch(document["format"]):
                    documents.append(document)
    by_id = {}
    for result in bundle["results"]:
        if isinstance(result, dict) and isinstance(result.get("id"), str):
            by_id[result["id"]] = result
    for document in documents:
        scenario = document.get("scenario")
        trial_id = scenario.get("trial_id") if isinstance(scenario, dict) else None
        result = by_id.get(trial_id)
        if result is None:
            raise ValueError(f"evaluator result trial_id {trial_id!r} is not a bundle result")
        entries = result.get("evaluator_results")
        if entries is None:
            entries = []
            result["evaluator_results"] = entries
        if not isinstance(entries, list):
            raise ValueError(f"result {trial_id!r} evaluator_results is not a list")
        entries.append(document)
    return bundle


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "usage: collect_evaluator_results.py BUNDLE_MANIFEST RESULTS_DIR",
            file=sys.stderr,
        )
        return 2
    manifest = Path(args[0])
    bundle = json.loads(manifest.read_text(encoding="utf-8"))
    collect(bundle, Path(args[1]))
    manifest.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
