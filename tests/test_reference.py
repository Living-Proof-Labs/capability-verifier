# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capability_verifier.core import verify

ROOT = Path(__file__).resolve().parents[1]
scenarios(str(ROOT / ".specs/specs/v1/reference.feature"))


@pytest.fixture
def context():
    return {}


@given(parsers.parse('the pause fixture "{name}"'))
def fixture(context, name):
    context["path"] = ROOT / "fixtures" / f"{name}.json"


def execute(context, omit_human=False):
    specs = json.loads((ROOT / "specs.lock.json").read_text())
    context["report"] = verify(context["path"], specs, omit_human=omit_human)


@when("the runner collects evidence and all evaluators assess it")
def run(context):
    execute(context)


@when("the human assessment is unavailable")
def missing_human(context):
    execute(context, omit_human=True)


@then(parsers.parse('the policy result is "{expected}"'))
def result(context, expected, record_property):
    report = context["report"]
    record_property("report_kind", "fixture_expectation_test_not_robot_certification")
    record_property("expected_capability_status", expected)
    record_property("actual_capability_status", report["status"])
    record_property("verification_report", json.dumps(report, sort_keys=True))
    print(json.dumps(report, sort_keys=True))
    assert report["status"] == expected


@then("the report retains assessments and version provenance")
def provenance(context):
    report = context["report"]
    assert len(report["assessments"]) == 3
    assert all(a["version"] and a["rationale"] for a in report["assessments"])
    p = report["provenance"]
    assert p["spec"]["tag"].startswith("v")
    assert p["spec"]["revision"] == "1.0.0"
    assert len(p["spec"]["commit"]) == 40
    assert len(p["evidence_sha256"]) == 64
    assert p["system"]["version"] and p["runner"]["version"] and p["policy"]["version"]
    assert p["simulator"] is None


@then("the advisory assessments both pass")
def advisory(context):
    assert [a["status"] for a in context["report"]["assessments"][1:]] == ["PASS", "PASS"]
