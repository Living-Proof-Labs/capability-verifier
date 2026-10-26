#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
REPORTER_IMAGE=${REPORTER_IMAGE:-ghcr.io/living-proof-labs/livingproof-capability-report:1.5.0}
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
    livingproof-capability-report render /output/report.json --format csv -o /output/report.csv
    livingproof-capability-report render /output/report.json > /output/report.txt
    livingproof-capability-report grade /input/controls.json -o /output/controls.json
    livingproof-capability-report grade /input/manifest.json --policy all-required-v2@1.0.0 -o /output/report-v2.json
    livingproof-capability-report grade /input/controls.json --policy all-required-v2@1.0.0 -o /output/controls-v2.json
    livingproof-capability-report render /output/report-v2.json --format html -o /output/report-v2.html
    livingproof-capability-report render /output/report-v2.json --format csv -o /output/report-v2.csv
    livingproof-capability-report render /output/report-v2.json > /output/report-v2.txt
    python - <<"PYCODE"
import csv
import json
from html.parser import HTMLParser
from pathlib import Path
report = json.load(open("/output/report.json"))
expected = {
    "v1.1.1-v1.0.1-pass": "PASS", "v1.1.1-v1.1.0-slow": "PASS",
    "v1.2.0-v1.0.1-pass": "INCOMPATIBLE", "v1.2.0-v1.1.0-slow": "FAIL",
}
assert {d["result_id"]: d["status"] for d in report["decisions"]} == expected
revised = json.load(open("/output/report-v2.json"))
assert {d["result_id"]: d["status"] for d in revised["decisions"]} == expected
assert len(report["groups"]) == len(revised["groups"]) == 4
controls = json.load(open("/output/controls.json"))
assert len(controls["decisions"]) == 4
for decision in controls["decisions"]:
    assert decision["status"] == ("FAIL" if decision["result_id"].endswith("-fail") else "INSUFFICIENT_EVIDENCE")
assert {d["result_id"]: d["status"] for d in json.load(open("/output/controls-v2.json"))["decisions"]} == {d["result_id"]: d["status"] for d in controls["decisions"]}

class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.row = []
        if tag == "td": self.cell = ""
    def handle_data(self, data):
        if self.cell is not None: self.cell += data
    def handle_endtag(self, tag):
        if tag == "td":
            self.row.append(self.cell)
            self.cell = None
        if tag == "tr" and self.row: self.rows.append(self.row)

for stem in ("report", "report-v2"):
    rows = list(csv.DictReader(open(f"/output/{stem}.csv")))
    assert {row["result_id"]: row["status"] for row in rows} == expected
    table = Table()
    table.feed(Path(f"/output/{stem}.html").read_text())
    assert {row[0]: row[4] for row in table.rows if row[0] in expected} == expected
    lines = Path(f"/output/{stem}.txt").read_text().splitlines()
    rows = [list(map(str.strip, line.split("|"))) for line in lines]
    assert {row[0]: row[3] for row in rows if row[0] in expected} == expected
print("Both policies: four separate decisions, four controls, matching JSON/HTML/CSV/table outcomes")
PYCODE
  '
