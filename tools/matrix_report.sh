#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
: "${REPORTER_IMAGE:?Set an explicitly versioned installed reporter image with execution-compatibility support}"
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test -s "$root/artifacts/matrix/manifest.json"
mkdir "$root/artifacts/matrix-report"
docker run --rm --entrypoint sh \
  --mount "type=bind,src=$root/artifacts/matrix,dst=/input,readonly" \
  --mount "type=bind,src=$root/artifacts/matrix-report,dst=/output" \
  "$REPORTER_IMAGE" -ec '
    livingproof-capability-report --version
    livingproof-capability-report validate /input/manifest.json
    livingproof-capability-report grade /input/manifest.json -o /output/report.json
    livingproof-capability-report render /output/report.json --format html -o /output/report.html
    livingproof-capability-report render /output/report.json > /output/report.txt
    livingproof-capability-report grade /input/controls.json -o /output/controls.json
    python - <<"PYCODE"
import json
report = json.load(open("/output/report.json"))
expected = {
    "v1.1.1-v1.0.1-pass": "PASS", "v1.1.1-v1.1.0-slow": "PASS",
    "v1.2.0-v1.0.1-pass": "INCOMPATIBLE", "v1.2.0-v1.1.0-slow": "FAIL",
}
assert {d["result_id"]: d["status"] for d in report["decisions"]} == expected
controls = json.load(open("/output/controls.json"))
assert len(controls["decisions"]) == 4
for decision in controls["decisions"]:
    assert decision["status"] == ("FAIL" if decision["result_id"].endswith("-fail") else "INSUFFICIENT_EVIDENCE")
PYCODE
  '
