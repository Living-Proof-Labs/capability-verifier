# SPDX-License-Identifier: Apache-2.0
"""Write the scenario evaluator's result files for one evidence manifest.

Thresholds come from ``hallway.thresholds``. This does not edit the bundle.
The report job appends the files this writes.

    python tools/run_scenario_evaluator.py evidence/manifest.json \\
        evaluator-results/livingproof-scenario
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from capability_verifier.hallway import thresholds
from livingproof_evaluator_scenario.result import write_run


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    written = write_run(
        manifest,
        thresholds(document),
        Path(args[1]),
        started_at=started,
        completed_at=started,
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
