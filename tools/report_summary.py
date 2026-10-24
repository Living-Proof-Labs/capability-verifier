# SPDX-License-Identifier: Apache-2.0
"""Show capability verdicts separately from successful expectation tests."""

import html
from pathlib import Path
import xml.etree.ElementTree as ET


def cell(value):
    return html.escape(value).replace("|", "&#124;").replace("\n", " ")


def render(path):
    lines = [
        "## Fixture capability verdicts",
        "",
        "**Passing expectation tests do not mean the robot capability passed.** "
        "These are deterministic fixtures, not robot certification.",
        "",
    ]
    if not path.exists():
        return "\n".join(
            lines + ["No demo report was produced; inspect the failed build/test steps."]
        )
    lines += [
        "| Fixture expectation | Expected capability | Actual capability | Expectation test |",
        "| --- | --- | --- | --- |",
    ]
    for case in ET.parse(path).iter("testcase"):
        props = {p.attrib["name"]: p.attrib.get("value", "") for p in case.iter("property")}
        result = "PASS"
        for tag, status in [("skipped", "SKIPPED"), ("failure", "FAIL"), ("error", "ERROR")]:
            if case.find(tag) is not None:
                result = status
        values = [
            case.attrib["name"],
            props.get("expected_capability_status", "NOT RECORDED"),
            props.get("actual_capability_status", "NOT RECORDED"),
            result,
        ]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(render(Path("artifacts/demo.xml")))
