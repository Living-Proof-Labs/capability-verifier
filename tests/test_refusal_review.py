# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for livingproof-verification-refusal/0.1 (livingproof-verification#100).

A request whose file, schema and check ids are all good writes nothing and exits 0.
A missing file is an integrity refusal with no actual digest. When several gates
would fail, the first one (integrity, then schema, then vocabulary) is reported.
"""

from __future__ import annotations

import json

from test_refusal import (
    _emitter,
    _identity,
    _request,
    _sha,
    _valid_parameters,
    _write,
)


def test_a_good_request_exits_zero_and_writes_nothing(tmp_path):
    path = _write(tmp_path / "parameters.json", _valid_parameters())
    out = tmp_path / "out"
    assert _emitter().run(_request(path, _sha(path)), out) == 0
    assert not (out / "refusal.json").exists()
    assert not (out / "verification.json").exists()


def test_a_missing_file_is_an_integrity_refusal_without_a_digest(tmp_path):
    present = _write(tmp_path / "parameters.json", _valid_parameters())
    digest = _sha(present)
    request = _request(present, digest)
    missing = tmp_path / "absent.json"
    request["files"][0]["path"] = str(missing)
    out = tmp_path / "out"
    assert _emitter().run(request, out) != 0
    document = json.loads((out / "refusal.json").read_text())
    assert document["code"] == "integrity"
    assert document["declared"] == _identity(missing, digest)
    assert "actual_digest" not in document
    assert not (out / "verification.json").exists()


def test_integrity_is_reported_before_schema_and_vocabulary(tmp_path):
    path = _write(tmp_path / "parameters.json", {"format": "livingproof-scenario-parameters/0.1"})
    out = tmp_path / "out"
    request = _request(path, "0" * 64, check_ids=["hallway-hover"])
    assert _emitter().run(request, out) != 0
    assert json.loads((out / "refusal.json").read_text())["code"] == "integrity"


def test_schema_is_reported_before_vocabulary(tmp_path):
    path = _write(tmp_path / "parameters.json", {"format": "livingproof-scenario-parameters/0.1"})
    out = tmp_path / "out"
    request = _request(path, _sha(path), check_ids=["hallway-hover"])
    assert _emitter().run(request, out) != 0
    assert json.loads((out / "refusal.json").read_text())["code"] == "schema"
