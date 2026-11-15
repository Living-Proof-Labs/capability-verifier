# SPDX-License-Identifier: Apache-2.0
"""isinstance failures raise TypeError, except non-object evidence.

TRY004 in ruff 0.17 wants TypeError when a type check fails. collect() and
structured_failures() were changed to match. core.collect() still raises
ValueError for null and [], because test_unreadable_evidence_fails_closed
requires that. Proposed by Vera in the capability-verifier#77 review.
"""

from __future__ import annotations

import json

import pytest

from capability_verifier.collect import collect
from capability_verifier.core import collect as collect_evidence
from capability_verifier.evaluate import structured_failures


def test_collect_raises_type_error_when_the_bundle_is_not_an_object(tmp_path):
    with pytest.raises(TypeError, match="bundle is missing results"):
        collect(["not", "a", "mapping"], tmp_path)


def test_collect_raises_type_error_when_evaluator_results_is_not_a_list(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "trial.json").write_text(
        json.dumps(
            {
                "format": "livingproof-evaluator-result/1.1.0",
                "scenario": {"trial_id": "t1", "steps": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = {"results": [{"id": "t1", "evaluator_results": {"not": "a list"}}]}
    with pytest.raises(TypeError, match="evaluator_results is not a list"):
        collect(bundle, results)


def test_structured_failures_raises_type_error_when_failures_is_not_a_list():
    assessment = {
        "id": "x",
        "provenance": {"details": {"failures": {"not": "a list"}}},
    }
    with pytest.raises(TypeError, match="Invalid structured failures"):
        structured_failures(assessment)


@pytest.mark.parametrize("payload", ["null", "[]"])
def test_non_object_evidence_still_raises_value_error(tmp_path, payload):
    path = tmp_path / "evidence.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="Evidence must be an object"):
        collect_evidence(path)
