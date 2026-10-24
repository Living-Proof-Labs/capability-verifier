# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest

from capability_verifier.core import (
    FAIL,
    INSUFFICIENT,
    PASS,
    assessment,
    collect,
    evaluate_pause,
    evaluate_recorded,
    grade,
)

BASE = json.loads(Path("fixtures/pass.json").read_text())


@pytest.mark.parametrize("value", [None, -1, "120", True, float("nan"), float("inf")])
def test_invalid_time_is_insufficient(value):
    assert evaluate_pause({**BASE, "elapsed_ms": value})["status"] == INSUFFICIENT


@pytest.mark.parametrize("value,expected", [(0, PASS), (500, PASS), (500.01, FAIL)])
def test_time_boundary(value, expected):
    assert evaluate_pause({**BASE, "elapsed_ms": value})["status"] == expected


def test_safety_failure_wins_even_with_missing_time():
    assert evaluate_pause({"motion_inhibited": False})["status"] == FAIL


def test_wrong_state_fails():
    assert evaluate_pause({**BASE, "observed": "moving"})["status"] == FAIL


def test_runner_preserves_evidence_and_hash(tmp_path):
    p = tmp_path / "evidence.json"
    p.write_text('{"observed": "moving"}')
    result = collect(p)
    assert result["evidence"] == {"observed": "moving"}
    assert "status" not in result
    p.write_text('{"observed": "paused"}')
    assert collect(p)["sha256"] != result["sha256"]


@pytest.mark.parametrize("data", ["broken", "null", "[]"])
def test_unreadable_evidence_fails_closed(tmp_path, data):
    p = tmp_path / "evidence.json"
    p.write_text(data)
    with pytest.raises(ValueError):
        collect(p)


@pytest.mark.parametrize(
    "members",
    [
        [],
        ["pause-rules"],
        ["pause-rules", "mock-ai", "mock-ai"],
        ["pause-rules", "mock-ai", "human-fixture", "extra"],
    ],
)
def test_missing_duplicate_or_extra_evaluators_are_insufficient(members):
    assert grade([assessment(n, PASS, "fixture") for n in members]) == INSUFFICIENT


def test_invalid_recorded_assessment_is_insufficient():
    assert evaluate_recorded({}, "human-fixture")["status"] == INSUFFICIENT


def test_veto_independent_of_order():
    votes = [
        assessment("pause-rules", FAIL, "unsafe", True),
        assessment("mock-ai", PASS, "fixture"),
        assessment("human-fixture", PASS, "fixture"),
    ]
    assert grade(votes) == grade(votes[::-1]) == FAIL
