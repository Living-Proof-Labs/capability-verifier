# SPDX-License-Identifier: Apache-2.0
"""Write the scenario evaluator's result files for one evidence manifest.

Thresholds come from ``hallway.thresholds``. A scenario graded on its world
(walk-corner) also passes that world and its parameter file, from
``hallway.grading_inputs``. This does not edit the bundle.
The report job appends the files this writes.

    python tools/run_scenario_evaluator.py evidence/manifest.json \\
        evaluator-results/livingproof-scenario
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from livingproof_evaluator_scenario.result import write_run

from capability_verifier.hallway import grading_inputs, thresholds, unmet_scenario_supported


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2, 3):
        print(
            "usage: run_scenario_evaluator.py MANIFEST OUT_DIR [STARTED_AT]",
            file=sys.stderr,
        )
        return 2
    manifest = Path(args[0])
    started = args[2] if len(args) == 3 else _now()
    document = json.loads(manifest.read_text(encoding="utf-8"))
    trials = document.get("trials") if isinstance(document, dict) else None
    results = trials.get("results") if isinstance(trials, dict) else None
    if unmet_scenario_supported(document) is not None and results == []:
        # No trial ran, so there is no evaluator result. A note keeps the artifact upload.
        destination = Path(args[1])
        destination.mkdir(parents=True, exist_ok=True)
        note = destination / "UNSUPPORTED.txt"
        note.write_text("no trial: precondition scenario_supported unmet\n", encoding="utf-8")
        print(note)
        return 0
    world, parameters = grading_inputs(document)
    written = write_run(
        manifest,
        thresholds(document),
        Path(args[1]),
        started_at=started,
        completed_at=started,
        world=world,
        parameters=parameters,
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
