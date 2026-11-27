# SPDX-License-Identifier: Apache-2.0
"""Declaration lines, descriptor validation, install, and result placement."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from test_collect_integrity import _bundle, _robot_grades

from capability_verifier.collect import (
    _attach_not_run,
    advisory_report_lines,
    collect,
    integrity_report_lines,
)
from capability_verifier.evaluator_contract import CONTRACT
from capability_verifier.evaluators import (
    NotFound,
    Record,
    Refusal,
    _place,
    accept_result_format,
    install,
    main,
    parse_lines,
    prepare,
    resolve_ref,
    run_lines,
)
from capability_verifier.repin import resolve as resolve_tag

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "evaluator-schemas"
COMMIT = "a" * 40
LINE = "acme/widgets@v1.2.3/widget.json"
SHA_LINE = f"acme/widgets@{COMMIT}/widget.json"
FORBIDDEN = (
    "livingproof_evaluator_run",
    "livingproof-evaluator-run",
    "livingproof-run-integrity",
    "run-integrity",
    "run_integrity",
    "Run Integrity",
)


def _implementation():
    return json.loads((FIXTURES / "implementation.schema.json").read_text())


def _declared_result():
    """A result format the pinned schema accepts, read from that schema."""
    field = _implementation()["properties"]["result"]
    if "const" in field:
        return field["const"]
    return "livingproof-evaluator-result/1.2.0"


def _descriptor(**overrides) -> dict:
    schema = _implementation()
    document = {
        "format": schema["properties"]["format"]["const"],
        "id": "example-integrity",
        "kind": "integrity",
        "grading": "none",
        "package": {"name": "example-integrity"},
        "runtime": {"python": "3.12"},
        "entrypoint": {"module": "example_integrity"},
        "arguments": ["{evidence_manifest}", "--out", "{result}"],
        "exit_codes": {"0": "result", "2": "refused"},
        "result": _declared_result(),
    }
    document.update(overrides)
    return document


def _result(fmt: str, evaluator_id: str = "example-integrity") -> dict:
    return {
        "format": fmt,
        "evaluator": {
            "id": evaluator_id,
            "version": "1.0.0",
            "kind": "integrity",
            "execution": "deterministic",
            "display_name": "Example",
            "publisher": "Example",
            "icon": "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        },
        "inputs": [{"role": "evidence", "uri": "manifest.json", "sha256": "a" * 64}],
        "timestamps": {
            "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z",
        },
        "integrity": {
            "run": {
                "id": "run-1",
                "test_system_status": "COMPLETED",
                "success": True,
                "percentage": 100,
            },
            "trials": [
                {
                    "id": "trial-01",
                    "test_system_status": "COMPLETED",
                    "success": True,
                    "percentage": 100,
                }
            ],
            "checks": [
                {
                    "id": "completeness",
                    "version": "1.0.0",
                    "applicable": True,
                    "success": True,
                    "percentage": 100,
                    "findings": [],
                }
            ],
        },
    }


def _schema_fetch(url: str, implementation: dict | None = None) -> bytes:
    if "/schema/" not in url:
        raise NotFound(url)
    relative = url.split("/schema/", 1)[1]
    if relative == "implementation.schema.json" and implementation is not None:
        return json.dumps(implementation).encode()
    path = FIXTURES / relative
    if not path.is_file():
        raise NotFound(url)
    return path.read_bytes()


def _no_branch(command, **kwargs):
    assert command[:2] == ["git", "ls-remote"]
    assert command[-1].startswith("refs/heads/")
    return subprocess.CompletedProcess(command, 0, "", "")


def _resolve(repository, tag):
    if (repository, tag) == ("Living-Proof-Labs/livingproof-evaluator", "v1.10.0"):
        return "d46062a9300611e32ad76749e67a8f035489ebce"
    return COMMIT


@pytest.mark.parametrize(
    "line",
    [
        "acme/widgets@main/widget.json",
        "acme/widgets@latest/widget.json",
        "acme/widgets@abcdef1/widget.json",
        "acme/widgets@" + "a" * 39 + "/widget.json",
        "acme/widgets/widget.json",
        "acme/widgets@/widget.json",
        "https://github.com/acme/widgets@v1.2.3/widget.json",
        "gitlab.com/acme/widgets@v1.2.3/widget.json",
        "acme/widgets@v1.2.3/../widget.json",
        "acme/widgets@v1.2.3/foo/../../x.json",
        "acme/widgets@v1.2.3/./widget.json",
        "acme/widgets@v01.2.3/widget.json",
        "acme/widgets@v1.2/widget.json",
        "acme/widgets@V1.2.3/widget.json",
        "acme/widgets@v1.2.3/wid get.json",
        "acme/widgets@v1.2.3/widget.txt",
        "acme_widgets/repo@v1.2.3/a.json",
        "acme/widgets@v1.2.3/widget.json extra",
    ],
)
def test_a_line_outside_the_declaration_grammar_is_refused(line):
    with pytest.raises(Refusal):
        parse_lines(line)


@pytest.mark.parametrize("text", ["", "\n", "# only a comment\n\n  # still a comment\n"])
def test_input_with_no_declaration_lines_is_refused(text):
    with pytest.raises(Refusal, match="no declaration"):
        parse_lines(text)


def test_tag_and_commit_lines_are_accepted_and_comments_are_ignored():
    text = "\n# note\n\n" + LINE + "\n" + SHA_LINE + "\n"
    found = parse_lines(text)
    assert [item.ref for item in found] == ["v1.2.3", COMMIT]
    assert found[0].repository == "acme/widgets"
    assert found[0].path == "widget.json"
    nested = parse_lines("acme/widgets@v0.0.0/dir/file.json")
    assert nested[0].path == "dir/file.json"
    hyphen = parse_lines("foo-bar/baz.qux_1@v10.20.30/a.json")
    assert hyphen[0].owner == "foo-bar"


def test_a_tag_that_is_also_a_branch_is_refused():
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, COMMIT + "\trefs/heads/v1.2.3\n", "")

    with pytest.raises(Refusal, match="branch"):
        resolve_ref(parse_lines(LINE)[0], resolve=_resolve, run=run)
    assert calls[-1][-1] == "refs/heads/v1.2.3"


def test_a_tag_without_a_same_name_branch_resolves_and_a_commit_is_used_as_is():
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    assert resolve_ref(parse_lines(LINE)[0], resolve=_resolve, run=run) == COMMIT

    def unused(*args, **kwargs):
        raise AssertionError("a commit ref is not resolved")

    assert resolve_ref(parse_lines(SHA_LINE)[0], resolve=unused, run=unused) == COMMIT


def test_the_contract_tag_resolves_to_the_released_commit():
    pin = parse_lines(CONTRACT)[0]
    assert pin.ref == "v1.10.0"
    assert pin.repository == "Living-Proof-Labs/livingproof-evaluator"
    assert resolve_tag(pin.repository, pin.ref) == "d46062a9300611e32ad76749e67a8f035489ebce"


def test_the_loader_reads_the_format_from_the_schema():
    schema = _implementation()
    banned = [schema["properties"]["format"]["const"]]
    if "const" in schema["properties"]["result"]:
        banned.append(schema["properties"]["result"]["const"])
    root = ROOT / "src" / "capability_verifier"
    for name in ("evaluators.py", "evaluator_contract.py"):
        text = (root / name).read_text(encoding="utf-8")
        for item in banned:
            assert item not in text, (name, item)


def test_the_loader_names_no_evaluator():
    root = ROOT / "src" / "capability_verifier"
    for name in ("evaluators.py", "evaluator_contract.py"):
        text = (root / name).read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN:
            assert forbidden.lower() not in text, (name, forbidden)


def _prepare(body: bytes, implementation: dict | None = None, line: str = LINE):
    return prepare(
        line + "\n",
        fetch=lambda url: _schema_fetch(url, implementation),
        resolve=_resolve,
        run=_no_branch,
        injected={line: body},
    )


def test_an_invalid_descriptor_is_refused_before_install():
    body = json.dumps(_descriptor()).encode().replace(b'"id"', b'"nope"', 1)
    calls = []
    with pytest.raises(Refusal, match="implementation schema"):
        prepare(
            LINE + "\n",
            fetch=lambda url: _schema_fetch(url),
            resolve=_resolve,
            run=lambda *args, **kwargs: calls.append(args) or _no_branch(*args, **kwargs),
            injected={LINE: body},
        )
    assert calls == [] or all(args[0][:2] == ["git", "ls-remote"] for args in calls)


def test_an_unknown_kind_is_refused_before_install():
    schema = _implementation()
    schema["properties"]["kind"]["enum"] = ["integrity", "other"]
    document = _descriptor(kind="other")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] != ["git", "ls-remote"]:
            raise AssertionError(command)
        return _no_branch(command, **kwargs)

    with pytest.raises(Refusal, match="unknown kind"):
        prepare(
            LINE + "\n",
            fetch=lambda url: _schema_fetch(url, schema),
            resolve=_resolve,
            run=run,
            injected={LINE: json.dumps(document).encode()},
        )
    assert all(command[:2] == ["git", "ls-remote"] for command in calls)


def test_the_injected_descriptor_declares_result_1_2_0():
    raw = (ROOT / "tests/fixtures/evaluators/run-integrity.json").read_bytes()
    assert json.loads(raw)["result"] == "livingproof-evaluator-result/1.2.0"
    prepared = _prepare(raw)
    assert prepared[0].document["result"] == "livingproof-evaluator-result/1.2.0"


@pytest.mark.parametrize(
    "document",
    [
        _descriptor(result="livingproof-evaluator-result/1.2.0\n"),
        _descriptor(arguments=["{evidence_manifest}\x00", "--out", "{result}"]),
        _descriptor(arguments=["{evidence_manifest}", "{media_dir}", "--out", "{result}"]),
        _descriptor(
            arguments=[
                "{evidence_manifest}",
                {"if": "evidence_manifest", "then": ["{media_dir}"]},
                "--out",
                "{result}",
            ]
        ),
        _descriptor(exit_codes={"0": "result", "256": "refused"}),
        _descriptor(package={"name": "example-integrity\n"}),
    ],
    ids=[
        "newline",
        "nul",
        "media-at-top-level",
        "media-in-other-conditional",
        "exit-256",
        "newline-name",
    ],
)
def test_schema_guards_are_refused_before_install(document):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] != ["git", "ls-remote"]:
            raise AssertionError(command)
        return _no_branch(command, **kwargs)

    with pytest.raises(Refusal, match="implementation schema"):
        prepare(
            LINE + "\n",
            fetch=lambda url: _schema_fetch(url),
            resolve=_resolve,
            run=run,
            injected={LINE: json.dumps(document).encode()},
        )
    assert all(command[:2] == ["git", "ls-remote"] for command in calls)


def test_exit_code_255_is_accepted():
    prepared = _prepare(
        json.dumps(_descriptor(exit_codes={"0": "result", "255": "refused"})).encode()
    )
    assert prepared[0].document["exit_codes"]["255"] == "refused"


def test_a_duplicate_key_in_the_descriptor_is_refused_before_install():
    raw = b'{"format": "x", "format": "y", "nested": {"id": "a", "id": "b"}}'
    with pytest.raises(Refusal, match="duplicate key"):
        _prepare(raw)


def test_a_missing_descriptor_is_refused():
    def fetch(url):
        if "/schema/" in url:
            return _schema_fetch(url)
        raise NotFound(url)

    with pytest.raises(Refusal, match="not found"):
        prepare(LINE + "\n", fetch=fetch, resolve=_resolve, run=_no_branch)


EVALUATOR_RUN_LINE = "Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json"
EVALUATOR_RUN_COPY = ROOT / "tests" / "fixtures" / "evaluators" / "run-integrity.json"


def test_workflow_and_docs_declare_the_released_evaluator_line():
    for path in (
        ROOT / ".github" / "workflows" / "scenario.yml",
        ROOT / ".github" / "workflows" / "walk-hallway.yml",
        ROOT / "README.md",
        ROOT / "docs" / "locks.md",
    ):
        assert EVALUATOR_RUN_LINE in path.read_text(encoding="utf-8"), path


def test_evaluator_run_descriptor_matches_the_release():
    repository = "Living-Proof-Labs/livingproof-evaluator-run"
    commit = resolve_tag(repository, "v1.9.0")
    assert commit == "73176b6bf92067d805aaaa52b947cb5817976788"
    url = f"https://raw.githubusercontent.com/{repository}/{commit}/livingproof-evaluator.json"
    with urllib.request.urlopen(url, timeout=60) as response:
        assert response.read() == EVALUATOR_RUN_COPY.read_bytes()


def test_fixture_schemas_match_the_pin():
    pin = parse_lines(CONTRACT)[0]
    commit = (
        pin.ref if re.fullmatch(r"[0-9a-f]{40}", pin.ref) else resolve_tag(pin.repository, pin.ref)
    )
    for path in sorted(FIXTURES.rglob("*.json")):
        relative = path.relative_to(FIXTURES).as_posix()
        url = f"https://raw.githubusercontent.com/{pin.owner}/{pin.repo}/{commit}/schema/{relative}"
        with urllib.request.urlopen(url, timeout=60) as response:
            assert response.read() == path.read_bytes(), relative


def _plant(venv: Path, name: str, version: str, commit: str) -> None:
    dist = (
        venv
        / "lib"
        / "python3.12"
        / "site-packages"
        / f"{name.replace('-', '_')}-{version}.dist-info"
    )
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    (dist / "direct_url.json").write_text(
        json.dumps(
            {"url": "https://github.com/acme/widgets.git", "vcs_info": {"commit_id": commit}}
        ),
        encoding="utf-8",
    )


def _prepared_for_install(line: str = LINE):
    return _prepare(json.dumps(_descriptor()).encode(), line=line)[0]


def test_install_runs_the_exact_commands_and_a_tag_pretends_its_version(tmp_path):
    calls = []
    prepared = _prepared_for_install()

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[1] == "pip":
            _plant(Path(command[4]), "example-integrity", "1.2.3", prepared.commit)
        return subprocess.CompletedProcess(command, 0, "", "")

    assert install(prepared, tmp_path, run) == "1.2.3"
    venv = str(tmp_path / "example-integrity" / ".venv")
    assert calls[0][0] == ["uv", "venv", "--python", "3.12", venv]
    assert calls[1][0] == [
        "uv",
        "pip",
        "install",
        "--python",
        venv,
        f"git+https://github.com/acme/widgets@{prepared.commit}",
    ]
    assert calls[1][1]["env"]["SETUPTOOLS_SCM_PRETEND_VERSION"] == "1.2.3"


def test_a_commit_install_does_not_pretend_a_version(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION", "9.9.9")
    prepared = _prepare(json.dumps(_descriptor()).encode(), line=SHA_LINE)[0]
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs.get("env"))
        if command[1] == "pip":
            _plant(Path(command[4]), "example-integrity", "0.0.0", prepared.commit)
        return subprocess.CompletedProcess(command, 0, "", "")

    install(prepared, tmp_path, run)
    assert "SETUPTOOLS_SCM_PRETEND_VERSION" not in calls[1]


@pytest.mark.parametrize(
    ("name", "commit", "match"),
    [
        ("other-package", COMMIT, "not example-integrity"),
        ("example-integrity", "b" * 40, "direct_url.json records"),
    ],
)
def test_a_wrong_name_or_commit_is_refused(tmp_path, name, commit, match):
    prepared = _prepared_for_install()

    def run(command, **kwargs):
        if command[1] == "pip":
            _plant(Path(command[4]), name, "1.2.3", commit)
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(Refusal, match=match):
        install(prepared, tmp_path, run)


def _runner(code: int, stderr: str, payload: bytes | None, calls: list):
    def run(command, **kwargs):
        calls.append(list(command))
        if command[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "uv" and command[1] == "pip":
            _plant(Path(command[4]), "example-integrity", "1.2.3", COMMIT)
        if command[0] != "uv" and payload is not None and "--out" in command:
            out = command[command.index("--out") + 1]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(payload)
        status = 0 if command[0] in {"uv", "git"} else code
        return subprocess.CompletedProcess(command, status, "", stderr)

    return run


def _execute(tmp_path, code, stderr, payload, *, media=None, arguments=None):
    document = _descriptor()
    if arguments is not None:
        document["arguments"] = arguments
    line = LINE
    calls = []
    records = run_lines(
        line + "\n",
        evidence=tmp_path / "manifest.json",
        out=tmp_path / "out",
        media_dir=media,
        work=tmp_path / "work",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_runner(code, stderr, payload, calls),
        injected={line: json.dumps(document).encode()},
    )
    return records, calls


def test_exit_zero_keeps_a_validated_result(tmp_path):
    payload = json.dumps(_result(_declared_result())).encode()
    records, calls = _execute(tmp_path, 0, "", payload)
    assert records[0].outcome == "result"
    assert records[0].exit_code == 0
    assert records[0].installed_version == "1.2.3"
    result = tmp_path / "out" / "example-integrity" / "result.json"
    stored = json.loads(result.read_text())
    assert stored["evaluator"]["id"] == "example-integrity"
    provenance = json.loads((tmp_path / "out" / "evaluators-provenance.json").read_text())
    assert provenance[0]["descriptor_sha256"]
    assert provenance[0]["outcome"] == "result"
    assert any(
        command[command.index("--out") + 1] == str(result)
        for command in calls
        if "--out" in command
    )


def test_a_refused_exit_keeps_a_note_and_the_command_exits_1(tmp_path, monkeypatch):
    records, _calls = _execute(tmp_path, 2, "first\nthe reason\n", b"{}")
    assert records[0].outcome == "refused"
    assert records[0].note == "the reason"
    assert records[0].exit_code == 2
    assert not (tmp_path / "out" / "example-integrity" / "result.json").exists()
    assert (tmp_path / "out" / "example-integrity" / "note.txt").read_text() == "the reason\n"
    assert _cli(tmp_path, monkeypatch, 2, "the reason\n", None) == 1


def _cli(tmp_path, monkeypatch, code, stderr, payload) -> int:
    lines = tmp_path / "lines.txt"
    lines.write_text(LINE + "\n", encoding="utf-8")

    def fetch(url):
        if url.endswith("/widget.json"):
            return json.dumps(_descriptor()).encode()
        return _schema_fetch(url)

    monkeypatch.setattr("capability_verifier.evaluators.fetch_bytes", fetch)
    monkeypatch.setattr("capability_verifier.evaluators.resolve_tag", _resolve)
    monkeypatch.setattr(
        "capability_verifier.evaluators.subprocess.run",
        _runner(code, stderr, payload, []),
    )
    return main(
        [
            "run",
            "--lines",
            str(lines),
            "--evidence-manifest",
            str(tmp_path / "manifest.json"),
            "--out",
            str(tmp_path / "cli"),
        ]
    )


def test_an_argparse_usage_error_on_exit_2_is_a_failure_not_a_refusal(tmp_path):
    stderr = "usage: example [-h]\nexample: error: unrecognized arguments: --nope\n"
    records, _calls = _execute(tmp_path, 2, stderr, None)
    assert records[0].exit_code == 2
    assert records[0].outcome == "failure"


def test_exit_1_is_an_unlisted_failure(tmp_path):
    records, _calls = _execute(tmp_path, 1, "boom\n", None)
    assert records[0].exit_code == 1
    assert records[0].outcome == "failure"


def test_an_unlisted_exit_is_a_failure_with_a_note_and_a_non_zero_command(tmp_path, monkeypatch):
    payload = json.dumps(_result(_declared_result())).encode()
    records, _calls = _execute(tmp_path, 3, "boom\n", payload)
    assert records[0].outcome == "failure"
    assert records[0].note == "boom"
    assert not (tmp_path / "out" / "example-integrity" / "result.json").exists()
    assert _cli(tmp_path, monkeypatch, 3, "boom\n", payload) != 0


def test_a_leftover_result_from_an_earlier_run_is_a_failure(tmp_path):
    planted = tmp_path / "out" / "example-integrity" / "result.json"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(json.dumps(_result(_declared_result())).encode())
    records, _calls = _execute(tmp_path, 0, "", None)
    assert records[0].outcome == "failure"
    assert records[0].note == "the result file is missing"
    assert not planted.is_file()


def test_a_missing_or_invalid_result_is_a_failure(tmp_path):
    missing, _calls = _execute(tmp_path, 0, "", None)
    assert missing[0].outcome == "failure"
    assert "missing" in missing[0].note

    mismatch, _calls = _execute(
        tmp_path / "id",
        0,
        "",
        json.dumps(_result(_declared_result(), evaluator_id="someone-else")).encode(),
    )
    assert mismatch[0].outcome == "failure"
    assert "someone-else" in mismatch[0].note

    invalid, _calls = _execute(tmp_path / "bad", 0, "", b'{"format": "nope"}')
    assert invalid[0].outcome == "failure"
    assert "evaluator-result schema" in invalid[0].note


def test_a_duplicate_key_in_the_result_is_a_failure(tmp_path):
    raw = b'{"format": "a", "format": "b"}'
    records, _calls = _execute(tmp_path, 0, "", raw)
    assert records[0].outcome == "failure"
    assert "duplicate key" in records[0].note


def test_result_format_must_equal_the_declaration_and_use_a_supported_major():
    accept_result_format("livingproof-evaluator-result/1.2.0", "livingproof-evaluator-result/1.2.0")
    with pytest.raises(Refusal):
        accept_result_format(
            "livingproof-evaluator-result/1.2.0", "livingproof-evaluator-result/1.1.0"
        )
    with pytest.raises(Refusal):
        accept_result_format(
            "livingproof-evaluator-result/2.0.0", "livingproof-evaluator-result/2.0.0"
        )
    with pytest.raises(Refusal):
        accept_result_format(
            "livingproof-evaluator-result/1.2.0", "livingproof-evaluator-result/2.0.0"
        )


def test_a_later_minor_than_the_descriptor_is_refused(tmp_path):
    produced = _result("livingproof-evaluator-result/1.2.0")
    if produced["format"] == _declared_result():
        produced["format"] = "livingproof-evaluator-result/1.3.0"
    records, _calls = _execute(tmp_path, 0, "", json.dumps(produced).encode())
    assert records[0].outcome == "failure"
    assert "not the declared" in records[0].note


def test_media_is_passed_only_when_media_json_is_present(tmp_path):
    arguments = [
        "{evidence_manifest}",
        {"if": "media_dir", "then": ["--media", "{media_dir}"]},
        "--out",
        "{result}",
    ]
    bare, calls = _execute(
        tmp_path / "bare",
        0,
        "",
        json.dumps(_result(_declared_result())).encode(),
        arguments=arguments,
    )
    launched = next(command for command in calls if "-m" in command)
    assert "--media" not in launched
    assert bare[0].outcome == "result"

    media = tmp_path / "media"
    media.mkdir()
    (media / "media.json").write_text("{}\n", encoding="utf-8")
    _records, calls = _execute(
        tmp_path / "with",
        0,
        "",
        json.dumps(_result(_declared_result())).encode(),
        media=media,
        arguments=arguments,
    )
    command = next(item for item in calls if "-m" in item)
    assert command[command.index("--media") + 1] == str(media)

    empty = tmp_path / "empty-media"
    empty.mkdir()
    _records, calls = _execute(
        tmp_path / "empty",
        0,
        "",
        json.dumps(_result(_declared_result())).encode(),
        media=empty,
        arguments=arguments,
    )
    launched = next(item for item in calls if "-m" in item)
    assert "--media" not in launched


def test_a_moved_contract_tag_is_refused():
    def resolve(repository, tag):
        if (repository, tag) == ("Living-Proof-Labs/livingproof-evaluator", "v1.10.0"):
            return "b" * 40
        return COMMIT

    with pytest.raises(Refusal, match="d46062a9300611e32ad76749e67a8f035489ebce"):
        prepare(
            LINE + "\n",
            fetch=lambda url: _schema_fetch(url),
            resolve=resolve,
            run=_no_branch,
            injected={LINE: json.dumps(_descriptor()).encode()},
        )


def test_place_refuses_an_unknown_kind(tmp_path):
    bundle = tmp_path / "manifest.json"
    bundle.write_text('{"assessment": {"runs": []}}\n', encoding="utf-8")
    record = Record(
        line=LINE,
        repo="acme/widgets",
        ref="v1.2.3",
        commit=COMMIT,
        descriptor_sha256="ab",
        package_name="example-integrity",
        installed_version="1.2.3",
        exit_code=0,
        outcome="result",
        evaluator_id="example-integrity",
        kind="other",
        document=_result(_declared_result()),
    )
    with pytest.raises(Refusal, match="unknown kind"):
        _place(bundle, [record])
    assert "integrity_results" not in json.loads(bundle.read_text())


def test_a_result_is_stored_by_id_and_grades_stay(tmp_path):
    bundle, directory = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(bundle), encoding="utf-8")
    payload = json.dumps(_result(_declared_result())).encode()
    records = run_lines(
        LINE + "\n",
        evidence=tmp_path / "evidence.json",
        out=tmp_path / "out",
        bundle=manifest,
        work=tmp_path / "work",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_runner(0, "", payload, []),
        injected={LINE: json.dumps(_descriptor()).encode()},
    )
    stored = json.loads(manifest.read_text())
    assert records[0].outcome == "result"
    assert stored["assessment"]["integrity_results"]["example-integrity"]["evaluator"]["id"] == (
        "example-integrity"
    )
    assert _robot_grades(stored) == grades
    assert "integrity_not_run" not in stored["assessment"]


def test_a_refusal_is_attached_under_the_descriptor_id(tmp_path):
    bundle, directory = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(bundle), encoding="utf-8")
    run_lines(
        LINE + "\n",
        evidence=tmp_path / "evidence.json",
        out=tmp_path / "out",
        bundle=manifest,
        work=tmp_path / "work",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_runner(2, "no trials\n", None, []),
        injected={LINE: json.dumps(_descriptor()).encode()},
    )
    stored = json.loads(manifest.read_text())
    missing = stored["assessment"]["integrity_not_run"]
    assert missing["evaluator"] == "example-integrity"
    assert missing["refused"] is True
    assert missing["reason"] == "no trials"
    assert _robot_grades(stored) == grades


def test_attach_not_run_defaults_to_todays_evaluator_and_accepts_another():
    bundle = {"assessment": {"runs": [{"grade": "PASS", "conclusion": "PASS"}]}}
    _attach_not_run(bundle, "failure", "gone")
    assert bundle["assessment"]["integrity_not_run"]["evaluator"] == "livingproof-run-integrity"
    _attach_not_run(bundle, "failure", "other", evaluator="example-integrity")
    assert bundle["assessment"]["integrity_not_run"]["evaluator"] == "example-integrity"


def test_collect_without_an_integrity_job_stores_no_not_run(tmp_path):
    bundle, _directory = _bundle(tmp_path)
    collect(bundle, tmp_path / "absent", integrity_job=None)
    assert "integrity_not_run" not in bundle["assessment"]


def test_run_integrity_is_skipped_when_evaluators_are_set_and_the_wrapper_forwards_them():
    scenario = yaml.safe_load((ROOT / ".github/workflows/scenario.yml").read_text())
    job = scenario["jobs"]["run-integrity"]
    assert "inputs.evaluators == ''" in job["if"]
    report = scenario["jobs"]["report"]
    step = next(item for item in report["steps"] if "RUN_INTEGRITY_RESULT" in item.get("env", {}))
    assert step["env"]["RUN_INTEGRITY_RESULT"] == (
        "${{ inputs.evaluators == '' && needs.run-integrity.result || '' }}"
    )
    wrapper = yaml.safe_load((ROOT / ".github/workflows/walk-hallway.yml").read_text())
    assert "evaluators" in wrapper[True]["workflow_call"]["inputs"]
    assert wrapper["jobs"]["scenario"]["with"]["evaluators"] == "${{ inputs.evaluators }}"
    assert wrapper[True]["workflow_call"]["inputs"]["evaluators"]["default"] == ""


ADVISORY = ROOT / "tests" / "fixtures" / "advisory" / "battery-thermal"
NOTES_SHA256 = "672452afe3c3af41a8580455addb04c6e6696ce19e3117e4446a962fb448d7bc"
RESULT_SHA256 = "b1d66c7c005d471972857c9716377d9303245b1a7ff3394ba2758d509898c3e5"
IMPLEMENTATION_SHA256 = "146a7ab7b52feba7a2f3985ad294655d34ef74ff55826dd00979451f98e90770"
# No pre-existing case-insensitive "health" hit under src/capability_verifier.
_HEALTH_HITS: frozenset[str] = frozenset()


def _advisory_descriptor() -> dict:
    return json.loads((ADVISORY / "implementation.json").read_text(encoding="utf-8"))


def _advisory_runner(code, stderr, payload, calls, package):
    def run(command, **kwargs):
        calls.append(list(command))
        if command[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "uv" and command[1] == "pip":
            _plant(Path(command[4]), package, "0.1.0", COMMIT)
        if command[0] != "uv" and payload is not None and "--out" in command:
            out = command[command.index("--out") + 1]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_bytes(payload)
        status = 0 if command[0] in {"uv", "git"} else code
        return subprocess.CompletedProcess(command, status, "", stderr)

    return run


def _run_advisory(tmp_path, code, stderr, payload, *, descriptor=None, bundle=None):
    document = _advisory_descriptor() if descriptor is None else descriptor
    return run_lines(
        LINE + "\n",
        evidence=tmp_path / "evidence.json",
        out=tmp_path / "out",
        bundle=bundle,
        work=tmp_path / "work",
        fetch=lambda url: _schema_fetch(url),
        resolve=_resolve,
        run=_advisory_runner(code, stderr, payload, [], document["package"]["name"]),
        injected={LINE: json.dumps(document).encode()},
    )


def _conclusions(bundle):
    return [run.get("conclusion") for run in bundle["assessment"]["runs"]]


def test_battery_thermal_fixture_matches_the_contract_example():
    commit = "d46062a9300611e32ad76749e67a8f035489ebce"
    files = {
        "result.json": (
            RESULT_SHA256,
            f"https://raw.githubusercontent.com/Living-Proof-Labs/livingproof-evaluator/{commit}/examples/advisory/battery-thermal/result.json",
        ),
        "notes.txt": (
            NOTES_SHA256,
            f"https://raw.githubusercontent.com/Living-Proof-Labs/livingproof-evaluator/{commit}/examples/advisory/battery-thermal/notes.txt",
        ),
        "implementation.json": (
            IMPLEMENTATION_SHA256,
            f"https://raw.githubusercontent.com/Living-Proof-Labs/livingproof-evaluator/{commit}/examples/implementation/battery-thermal.json",
        ),
    }
    for name, (digest, url) in files.items():
        data = (ADVISORY / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        with urllib.request.urlopen(url, timeout=60) as response:
            assert response.read() == data, name


def test_source_does_not_name_health():
    root = ROOT / "src" / "capability_verifier"
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if "health" not in line.lower():
                continue
            rel = f"{path.relative_to(root).as_posix()}:{number}"
            if rel not in _HEALTH_HITS:
                hits.append(f"{rel}:{line.strip()}")
    assert hits == []


def test_an_advisory_result_is_stored_by_id_and_leaves_grade_and_integrity(tmp_path):
    bundle, directory = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    lines = integrity_report_lines(bundle)
    conclusions = _conclusions(bundle)
    integrity = bundle["assessment"].get("integrity_results")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(bundle), encoding="utf-8")
    records = _run_advisory(
        tmp_path, 0, "", (ADVISORY / "result.json").read_bytes(), bundle=manifest
    )
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert records[0].outcome == "result"
    placed = stored["assessment"]["advisory_results"]["battery-thermal"]
    assert isinstance(placed, dict)
    assert placed["evaluator"]["id"] == "battery-thermal"
    assert placed["evaluator"]["kind"] == "advisory"
    assert placed["advisory"]["summary"]
    assert "advisory_not_run" not in stored["assessment"]
    assert _robot_grades(stored) == grades
    assert stored["assessment"].get("integrity_results") == integrity
    assert integrity_report_lines(stored) == lines
    assert _conclusions(stored) == conclusions


def test_an_advisory_failure_writes_a_not_run_note_without_grading(tmp_path):
    descriptor = _advisory_descriptor()
    descriptor["id"] = "pack-temperature"
    descriptor["package"] = {"name": "pack-temperature"}
    bundle, directory = _bundle(tmp_path)
    grades = _robot_grades(bundle)
    lines = integrity_report_lines(bundle)
    conclusions = _conclusions(bundle)
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(bundle), encoding="utf-8")
    records = _run_advisory(
        tmp_path, 1, "no pack sample\n", None, descriptor=descriptor, bundle=manifest
    )
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    assert records[0].outcome == "failure"
    missing = stored["assessment"]["advisory_not_run"]["pack-temperature"]
    assert missing["evaluator"] == "pack-temperature"
    assert missing["job_result"] == "failure"
    assert missing["reason"] == "no pack sample"
    assert missing["note"] == "pack-temperature: NOT RUN (failure): no pack sample."
    assert advisory_report_lines(stored) == [missing["note"]]
    assert "INCONCLUSIVE" not in missing["note"]
    assert "advisory_results" not in stored["assessment"]
    assert "integrity_not_run" not in stored["assessment"]
    assert "integrity_results" not in stored["assessment"]
    assert _robot_grades(stored) == grades
    assert integrity_report_lines(stored) == lines
    assert _conclusions(stored) == conclusions


def _advisory_trials(indexes) -> bytes:
    document = json.loads((ADVISORY / "result.json").read_text(encoding="utf-8"))
    document["advisory"]["trials"] = [
        {"index": index, "observation_sha256": f"{position:064x}"}
        for position, index in enumerate(indexes)
    ]
    return json.dumps(document).encode()


def test_a_repeated_advisory_trial_index_is_refused(tmp_path):
    records = _run_advisory(tmp_path, 0, "", _advisory_trials([1, 2, 1]))
    assert records[0].outcome == "failure"
    assert "repeats" in records[0].note
    assert not (tmp_path / "out" / "battery-thermal" / "result.json").exists()


def test_a_whole_number_float_repeats_an_advisory_trial_index(tmp_path):
    raw = _advisory_trials([1, 1.0])
    assert b"1.0" in raw
    records = _run_advisory(tmp_path, 0, "", raw)
    assert records[0].outcome == "failure"
    assert "repeats" in records[0].note


def test_a_duplicate_key_in_an_advisory_result_is_refused(tmp_path):
    raw = (ADVISORY / "result.json").read_bytes()
    duplicated = raw.replace(
        b'"format":', b'"format": "livingproof-evaluator-result/1.3.0", "format":', 1
    )
    records = _run_advisory(tmp_path, 0, "", duplicated)
    assert records[0].outcome == "failure"
    assert "duplicate key" in records[0].note
    assert not (tmp_path / "out" / "battery-thermal" / "result.json").exists()


def test_a_nested_duplicate_key_inside_advisory_is_refused(tmp_path):
    raw = (ADVISORY / "result.json").read_bytes()
    duplicated = raw.replace(b'"summary":', b'"summary": "Pack stayed in range.", "summary":', 1)
    records = _run_advisory(tmp_path, 0, "", duplicated)
    assert records[0].outcome == "failure"
    assert "duplicate key 'summary'" in records[0].note
