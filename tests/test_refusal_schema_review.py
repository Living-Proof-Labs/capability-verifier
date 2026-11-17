# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for the livingproof-verification-refusal/0.1 schema (livingproof-verification#100).

The shipped schema is a valid draft 2020-12 schema that rejects malformed refusals,
emit() writes nothing for a document the schema rejects, a sentence is one line,
and the parameter gate applies draft 2020-12 keywords.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator, ValidationError
from test_refusal import _emitter, _request, _sha, _valid_parameters, _write


def _good() -> dict:
    return _emitter().document(
        code="integrity",
        role="parameters",
        declared={"sha256": "0" * 64},
        actual_digest="a" * 64,
        sentence="The parameters file x.y was not found.",
    )


def _without(name: str) -> dict:
    document = _good()
    del document[name]
    return document


MALFORMED = {
    "an extra field": {**_good(), "extra": 1},
    "an unknown code": {**_good(), "code": "other"},
    "a status other than REFUSED": {**_good(), "status": "FAIL"},
    "another format": {**_good(), "format": "livingproof-verification-refusal/0.2"},
    "a short actual digest": {**_good(), "actual_digest": "abc"},
    "a two-line sentence": {**_good(), "sentence": "One.\nTwo."},
    "a sentence without a period": {**_good(), "sentence": "No period"},
    "a declared identity that is not an object": {**_good(), "declared": "v2.3.0"},
    "an empty role": {**_good(), "role": ""},
    "no declared identity": _without("declared"),
    "no sentence": _without("sentence"),
}


def test_the_refusal_schema_is_draft_2020_12_and_accepts_a_good_refusal():
    schema = _emitter().document_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_good())


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_the_refusal_schema_rejects(name):
    errors = list(Draft202012Validator(_emitter().document_schema()).iter_errors(MALFORMED[name]))
    assert errors, name


def test_emit_writes_nothing_for_a_document_the_schema_rejects(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ValidationError):
        _emitter().emit(out, MALFORMED["an extra field"])
    assert not (out / "refusal.json").exists()
    assert not (out / "verification.json").exists()


def test_a_refusal_sentence_is_one_line():
    with pytest.raises(ValueError, match="one sentence"):
        _emitter().document(
            code="schema", role="parameters", declared={}, sentence="First line.\nSecond."
        )


def test_the_parameter_gate_applies_draft_2020_12_keywords(tmp_path):
    path = _write(tmp_path / "parameters.json", _valid_parameters())
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "dependentRequired": {"format": ["not_present"]},
    }
    out = tmp_path / "out"
    assert _emitter().run(_request(path, _sha(path), schema=schema), out) != 0
    assert json.loads((out / "refusal.json").read_text())["code"] == "schema"
