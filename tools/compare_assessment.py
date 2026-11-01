"""Compare a bundle 1.1.0 assessment with a capability report.json computed from the same evidence.

Exit 0 when every per-result decision (status, reason, failures, assessor types) and every
run summary (k, n, required_passes, wilson95, grade, result ids, failures, ...) matches.
"""

import argparse
import json
import sys
from pathlib import Path

from capability_verifier import evaluate


def differences(bundle: dict, report: dict) -> list[str]:
    a = bundle["assessment"]
    out = []
    mine = a["decisions"]
    theirs = (
        [
            {k: d.get(k, [] if k in ("failures", "assessor_types") else None) for k in mine[0]}
            for d in report["decisions"]
        ]
        if mine
        else []
    )
    if mine != theirs:
        out.append("decisions differ")
    runs = [evaluate.as_reporter_summary(r) for r in a["runs"]]
    if runs != report.get("summaries", []):
        out.append(f"summaries differ: {runs} != {report.get('summaries')}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bundle", type=Path)
    p.add_argument("report", type=Path)
    args = p.parse_args()
    diff = differences(json.loads(args.bundle.read_text()), json.loads(args.report.read_text()))
    print("\n".join(diff) or "assessment matches report")
    sys.exit(1 if diff else 0)
