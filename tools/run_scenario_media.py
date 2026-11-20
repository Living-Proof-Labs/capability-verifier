# SPDX-License-Identifier: Apache-2.0
"""Run the scenario evaluator on a run's media: each trial's MCAP, through its binding.

The evaluator resolves every limit from the run manifest itself; this tool passes none. It passes
the world the run lock recorded (and the parameter file) because the evaluator needs the route,
and the evaluator's identity (the commit or image digest the lock pins) for the verdict.

    python tools/run_scenario_media.py evidence/manifest.json media \\
        evaluator-results/livingproof-scenario-media --identity git+https://github.com/ORG/REPO@COMMIT

Without media (the job off, or failed) or with a pinned evaluator that has no media runner, this
writes ``NO-MEDIA-RESULTS.txt`` saying so and exits 0, so the artifact upload keeps the reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from capability_verifier.hallway import grading_inputs

NOTE = "NO-MEDIA-RESULTS.txt"


def _note(out: Path, text: str) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / NOTE).write_text(text + "\n", encoding="utf-8")
    print(f"{out / NOTE}: {text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("media", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--identity", required=True)
    args = parser.parse_args(argv)
    if not (args.media / "media.json").is_file():
        return _note(args.out, "no media.json: the run has no recordings to grade")
    try:
        from livingproof_evaluator_scenario import media
    except ImportError:
        return _note(args.out, "the pinned scenario evaluator has no media runner")
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    world, parameters = grading_inputs(document)
    if world is None:
        return _note(args.out, "the scenario has no world document, so a recording has no route")
    written = media.write_media_run(
        args.manifest,
        args.media,
        args.out,
        started_at=_now(),
        completed_at=_now(),
        world=world,
        parameters=parameters,
        identity=args.identity,
    )
    for path in written:
        print(path)
    return 0


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())
