# SPDX-License-Identifier: Apache-2.0
"""livingproof-verification-refusal/0.1 document and emitter.

A pre-grade gate writes this document and exits non-zero instead of writing
``verification.json``. This module is not called from the live hallway path;
step 4 of livingproof-verification#100 is the wiring, and it waits on a
product decision.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

FORMAT = "livingproof-verification-refusal/0.1"
STATUS = "REFUSED"
CODES = frozenset({"integrity", "schema", "vocabulary"})
REFUSAL_NAME = "refusal.json"
GRADE_NAME = "verification.json"
# Non-zero: no grade is written. 2 is a refusal, not a crash.
REFUSAL_EXIT = 2


def document(
    *,
    code: str,
    role: str,
    declared: object,
    sentence: str,
    actual_digest: str | None = None,
) -> dict:
    """A ``livingproof-verification-refusal/0.1`` document."""
    if code not in CODES:
        raise ValueError(f"refusal code must be one of {sorted(CODES)}, not {code!r}")
    if not isinstance(sentence, str) or not sentence.endswith(".") or sentence.count(".") != 1:
        raise ValueError("refusal sentence must be one sentence ending with a period")
    body = {
        "format": FORMAT,
        "status": STATUS,
        "code": code,
        "role": role,
        "declared": declared,
    }
    if actual_digest is not None:
        body["actual_digest"] = actual_digest
    body["sentence"] = sentence
    return body


def emit(out: Path, refusal: dict) -> Path:
    """Write ``refusal.json`` under ``out``. Never writes ``verification.json``."""
    if refusal.get("format") != FORMAT or refusal.get("status") != STATUS:
        raise ValueError("emit requires a livingproof-verification-refusal/0.1 document")
    dest = Path(out)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / REFUSAL_NAME
    path.write_text(json.dumps(refusal, indent=2) + "\n")
    return path


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(request: dict, out: Path) -> int:
    """Run integrity, schema, then vocabulary. Stop on the first failure.

    On refusal: write ``refusal.json``, write no grade, return ``REFUSAL_EXIT``.
    On success: write nothing (the live hallway path is unchanged), return 0.
    """
    found = _first_refusal(request)
    if found is None:
        return 0
    emit(out, found)
    return REFUSAL_EXIT


def _first_refusal(request: dict) -> dict | None:
    for entry in request.get("files") or ():
        found = _integrity_refusal(entry)
        if found is not None:
            return found
    schema = request.get("schema")
    if schema:
        found = _schema_refusal(schema)
        if found is not None:
            return found
    vocabulary = request.get("vocabulary")
    if vocabulary:
        found = _vocabulary_refusal(vocabulary, request)
        if found is not None:
            return found
    return None


def _declared_digest(declared: object) -> str | None:
    if isinstance(declared, dict) and isinstance(declared.get("sha256"), str):
        return declared["sha256"]
    return None


def _integrity_refusal(entry: dict) -> dict | None:
    role = entry["role"]
    declared = entry["declared"]
    path = Path(entry["path"])
    expected = _declared_digest(declared)
    if not path.is_file():
        return document(
            code="integrity",
            role=role,
            declared=declared,
            sentence=f"The {role} file was not found.",
        )
    actual = file_digest(path)
    if expected is None or actual != expected:
        return document(
            code="integrity",
            role=role,
            declared=declared,
            actual_digest=actual,
            sentence=f"The {role} digest does not match the declared sha256.",
        )
    return None


def _schema_refusal(entry: dict) -> dict | None:
    role = entry.get("role", "parameters")
    declared = entry["declared"]
    path = Path(entry["path"])
    schema = entry.get("schema")
    if schema is None and entry.get("schema_path"):
        schema = json.loads(Path(entry["schema_path"]).read_text(encoding="utf-8"))
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return document(
            code="schema",
            role=role,
            declared=declared,
            actual_digest=actual,
            sentence=f"The {role} file is not valid JSON.",
        )
    if _schema_errors(payload, schema):
        return document(
            code="schema",
            role=role,
            declared=declared,
            actual_digest=actual,
            sentence=f"The {role} file does not match the parameter schema.",
        )
    return None


def _vocabulary_refusal(entry: dict, request: dict) -> dict | None:
    role = entry.get("role", "checks")
    declared = entry["declared"]
    supported = {str(name) for name in entry.get("supported_check_ids") or ()}
    names = [str(name) for name in entry.get("check_ids") or ()]
    actual = None
    for item in request.get("files") or ():
        if Path(item["path"]).is_file() and _declared_digest(item["declared"]) == file_digest(
            item["path"]
        ):
            actual = file_digest(item["path"])
            break
    unknown = [name for name in names if name not in supported]
    if not unknown:
        return None
    if len(unknown) == 1:
        sentence = f"The check id {unknown[0]} is not in the supported set."
    else:
        listed = " and ".join(unknown) if len(unknown) == 2 else ", ".join(unknown)
        sentence = f"The check ids {listed} are not in the supported set."
    return document(
        code="vocabulary",
        role=role,
        declared=declared,
        actual_digest=actual,
        sentence=sentence,
    )


def _schema_errors(instance, schema) -> list[str]:
    """Draft 2020-12 validation, including $ref, against the schema the caller passed."""
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(instance)]
