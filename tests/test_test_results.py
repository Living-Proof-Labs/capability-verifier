# SPDX-License-Identifier: Apache-2.0
"""Check-run API failures warn; JUnit failures still fail. No live API calls."""

from http.client import HTTPMessage
from io import BytesIO
import importlib.util
from pathlib import Path
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("junit_gate", ROOT / "tools/test_results.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SHA = "a" * 40
COMPLETED_AT = "2026-11-13T08:00:00Z"
HTML = "https://github.com/Living-Proof-Labs/capability-verifier/runs/131765014502"
REPOSITORY = "Living-Proof-Labs/capability-verifier"
PUBLISH_CHECK = (
    "${{ github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository }}"
)
HEAD_SHA = "${{ github.event.pull_request.head.sha || github.sha }}"


class FakeChecks:
    """In-memory checks API. Update statuses are consumed in call order."""

    def __init__(self, *, create_status=201, response_status=None, update_statuses=None):
        self.create_status = create_status
        self.response_status = response_status
        self.update_statuses = list(update_statuses or [])
        self.runs = []
        self.updates = []
        self.created_body = None

    def create_check_run(self, body):
        if self.create_status == 404:
            raise gate.CheckApiError(404, "create")
        if self.create_status != 201:
            raise gate.CheckApiError(self.create_status, "create")
        status = self.response_status or body["status"]
        run = {
            "id": 131765014502,
            "status": status,
            "conclusion": body.get("conclusion") if status == "completed" else None,
            "html_url": HTML,
            "name": body["name"],
        }
        self.runs.append(run)
        self.created_body = body
        return dict(run)

    def update_check_run(self, check_id, body):
        self.updates.append({"id": check_id, "body": body})
        status = self.update_statuses.pop(0) if self.update_statuses else 200
        if status == 404:
            raise gate.CheckApiError(404, "update")
        run = self.runs[-1]
        run["status"] = body.get("status", run["status"])
        if "conclusion" in body:
            run["conclusion"] = body["conclusion"]
        return dict(run)


def write_junit(path, cases):
    suite = ET.Element("testsuite")
    for name, result, detail in cases:
        case = ET.SubElement(suite, "testcase", name=name, classname="tests.example")
        if result in ("failure", "error"):
            ET.SubElement(case, result, message=detail).text = detail
        elif result == "skipped":
            ET.SubElement(case, "skipped", message=detail)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding="unicode")


def run_cli(tmp_path, monkeypatch, capsys, api, *, publish="true", pattern="artifacts/tests.xml"):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("HEAD_SHA", SHA)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setattr(gate, "client_from_env", lambda env=None: api)
    code = gate.main(
        [
            "--name",
            "registry-tests",
            "--path",
            pattern,
            "--publish-check",
            publish,
            "--root",
            str(tmp_path),
            "--completed-at",
            COMPLETED_AT,
        ]
    )
    printed = capsys.readouterr().out
    output = (tmp_path / "output").read_text()
    summary = (tmp_path / "summary").read_text()
    assert "test-token" not in printed + output + summary
    return code, printed, output, summary


def workflow_text():
    return "\n".join(
        path.read_text() for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
    )


def reporter_steps():
    """Each dorny step and the step that immediately follows it."""
    found = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        document = yaml.safe_load(path.read_text())
        for job_name, job in document["jobs"].items():
            steps = job.get("steps") or []
            for index, step in enumerate(steps):
                uses = step.get("uses") or ""
                if uses.startswith("dorny/test-reporter@"):
                    found.append((path, job_name, job, step, steps[index + 1]))
    return found


def test_update_404_after_create_warns_and_finishes_the_check(tmp_path, monkeypatch, capsys):
    write_junit(tmp_path / "artifacts/tests.xml", [("test_ok", "pass", "")])
    api = FakeChecks(response_status="in_progress", update_statuses=[404, 200])
    code, printed, output, summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 0
    assert "::warning::" in printed
    assert "404" in printed
    assert "update" in printed
    assert api.created_body["status"] == "completed"
    assert api.created_body["conclusion"] == "success"
    assert api.created_body["head_sha"] == SHA
    assert "output" in api.updates[0]["body"]
    assert "output" not in api.updates[1]["body"]
    assert api.updates[1]["body"]["conclusion"] == "success"
    assert api.runs[0]["status"] == "completed"
    assert api.runs[0]["conclusion"] == "success"
    assert f"url_html={HTML}" in output
    assert "passed 1, failed 0, skipped 0" in summary


def test_create_404_warns_and_creates_no_check(tmp_path, monkeypatch, capsys):
    write_junit(
        tmp_path / "artifacts/tests.xml",
        [("test_ok", "pass", ""), ("test_skip", "skipped", "")],
    )
    api = FakeChecks(create_status=404)
    code, printed, output, summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 0
    assert "::warning::" in printed
    assert "404" in printed
    assert "create" in printed
    assert api.runs == []
    assert api.updates == []
    assert "url_html=\n" in output
    assert "passed 1, failed 0, skipped 1" in summary


@pytest.mark.parametrize("create_status,update_statuses", [(404, []), (201, [404, 200])])
def test_failed_junit_still_fails_when_the_check_api_returns_404(
    tmp_path, monkeypatch, capsys, create_status, update_statuses
):
    write_junit(tmp_path / "artifacts/passed-tests.xml", [("test_ok", "pass", "")])
    write_junit(
        tmp_path / "artifacts/failed-tests.xml",
        [
            ("test_explicit_plugin_management", "failure", "AssertionError: assert 1 == 0"),
            ("test_setup", "error", "Error: boom"),
        ],
    )
    api = FakeChecks(
        create_status=create_status,
        response_status="in_progress",
        update_statuses=update_statuses,
    )
    code, printed, output, _summary = run_cli(
        tmp_path, monkeypatch, capsys, api, pattern="artifacts/*tests.xml"
    )
    assert code == 1
    assert "failed=2" in output
    if create_status == 404:
        assert api.runs == []
        assert "create" in printed and "404" in printed
    else:
        assert "::warning::" in printed and "404" in printed
        assert api.created_body["conclusion"] == "failure"
        assert "test_explicit_plugin_management" in api.created_body["output"]["summary"]
        assert api.runs[0]["status"] == "completed"
        assert api.runs[0]["conclusion"] == "failure"


def test_missing_report_fails_the_gate(tmp_path, monkeypatch, capsys):
    api = FakeChecks()
    code, printed, _output, summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 1
    assert printed == ""
    assert api.runs == []
    assert "no test cases were found" in summary


def test_malformed_report_fails_without_creating_a_check(tmp_path, monkeypatch, capsys):
    path = tmp_path / "artifacts/tests.xml"
    path.parent.mkdir()
    path.write_text("not xml")
    api = FakeChecks()
    code, printed, _output, summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 1
    assert printed == ""
    assert api.runs == []
    assert "could not be read" in summary


def test_create_502_warns_and_creates_no_check(tmp_path, monkeypatch, capsys):
    write_junit(tmp_path / "artifacts/tests.xml", [("test_ok", "pass", "")])
    api = FakeChecks(create_status=502)
    code, printed, output, _summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 0
    assert "::warning::" in printed
    assert "502" in printed
    assert "create" in printed
    assert api.runs == []
    assert api.updates == []
    assert "url_html=\n" in output


def test_both_updates_404_warns_and_leaves_the_check_in_progress(tmp_path, monkeypatch, capsys):
    write_junit(tmp_path / "artifacts/tests.xml", [("test_ok", "pass", "")])
    api = FakeChecks(response_status="in_progress", update_statuses=[404, 404])
    code, printed, output, _summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 0
    assert "::warning::" in printed
    assert "404" in printed
    assert "finish" in printed
    assert api.created_body["status"] == "completed"
    assert "output" in api.updates[0]["body"]
    assert "output" not in api.updates[1]["body"]
    assert api.runs[0]["status"] == "in_progress"
    assert api.runs[0]["conclusion"] is None
    assert "url_html=\n" in output


def test_failed_junit_still_fails_when_both_updates_404(tmp_path, monkeypatch, capsys):
    write_junit(
        tmp_path / "artifacts/tests.xml",
        [("test_explicit_plugin_management", "failure", "AssertionError: assert 1 == 0")],
    )
    api = FakeChecks(response_status="in_progress", update_statuses=[404, 404])
    code, printed, output, _summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 1
    assert "failed=1" in output
    assert "::warning::" in printed
    assert "404" in printed
    assert api.created_body["conclusion"] == "failure"
    assert api.runs[0]["status"] == "in_progress"
    assert api.runs[0]["conclusion"] is None


def test_empty_report_fails_without_creating_a_check(tmp_path, monkeypatch, capsys):
    path = tmp_path / "artifacts/tests.xml"
    path.parent.mkdir()
    path.write_text("<testsuite></testsuite>")
    api = FakeChecks()
    code, printed, _output, summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 1
    assert printed == ""
    assert api.runs == []
    assert "no test cases were found" in summary


def test_completed_create_does_not_update_or_warn(tmp_path, monkeypatch, capsys):
    write_junit(tmp_path / "artifacts/tests.xml", [("test_ok", "pass", "")])
    api = FakeChecks()
    code, printed, output, _summary = run_cli(tmp_path, monkeypatch, capsys, api)
    assert code == 0
    assert printed == ""
    assert api.updates == []
    assert api.created_body["status"] == "completed"
    assert api.runs[0]["status"] == "completed"
    assert "passed=1\nfailed=0\nskipped=0\n" in output


def test_skipping_publication_does_not_call_the_api(tmp_path, monkeypatch, capsys):
    write_junit(tmp_path / "artifacts/tests.xml", [("test_ok", "pass", "")])
    api = FakeChecks()

    def refuse_create(body):
        raise AssertionError("publication was skipped")

    def refuse_update(check_id, body):
        raise AssertionError("publication was skipped")

    api.create_check_run = refuse_create
    api.update_check_run = refuse_update
    code, printed, output, _summary = run_cli(tmp_path, monkeypatch, capsys, api, publish="false")
    assert code == 0
    assert printed == ""
    assert "url_html=\n" in output


def test_github_client_maps_not_found_without_a_live_request():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        raise HTTPError(request.full_url, 404, "Not Found", HTTPMessage(), BytesIO(b"{}"))

    client = gate.GitHubChecks("test-token", REPOSITORY, opener=opener)
    with pytest.raises(gate.CheckApiError) as caught:
        client.update_check_run(131765014502, {"status": "completed", "conclusion": "success"})
    assert caught.value.status == 404
    assert caught.value.operation == "update"
    assert "test-token" not in str(caught.value)
    request = seen[0]
    assert request.get_method() == "PATCH"
    assert request.full_url.endswith(f"/repos/{REPOSITORY}/check-runs/131765014502")
    assert request.get_header("Authorization") == "Bearer test-token"
    assert "test-token" not in request.full_url
    assert (
        gate.RejectRedirect().redirect_request(
            None, None, 302, "Found", None, "https://evil.example"
        )
        is None
    )


def test_workflows_do_not_set_use_actions_summary_false():
    text = workflow_text()
    assert "use-actions-summary: 'false'" not in text
    assert 'use-actions-summary: "false"' not in text
    assert "Test results check and failure annotations" not in text
    reporters = reporter_steps()
    assert [job_name for _path, job_name, _job, _step, _gate in reporters] == [
        "verify",
        "report-results",
        "documentation",
    ]
    for path, job_name, _job, step, gate_step in reporters:
        assert step["continue-on-error"] is True, (path.name, job_name)
        assert step["with"]["use-actions-summary"] == "true"
        assert step["with"]["fail-on-error"] == "true"
        assert step["with"]["fail-on-empty"] == "true"
        assert "tools/test_results.py" in gate_step["run"]
        assert gate_step["env"]["REPORT_PATH"] == step["with"]["path"]
        assert gate_step["env"]["REPORT_NAME"] == step["with"]["name"]


def test_verify_keeps_checks_write_permission():
    document = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    assert document["permissions"] == {"contents": "read"}
    assert document["jobs"]["verify"]["permissions"] == {"contents": "read", "checks": "write"}
    for job_name in ("report-results", "documentation", "report"):
        assert "permissions" not in document["jobs"][job_name]


def test_workflows_reference_no_new_secrets():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        text = path.read_text()
        assert "secrets." not in text, path.name
    for _path, job_name, _job, _step, gate_step in reporter_steps():
        assert gate_step["env"]["GITHUB_TOKEN"] == "${{ github.token }}", job_name
        assert gate_step["env"]["HEAD_SHA"] == HEAD_SHA
        assert gate_step["env"]["PUBLISH_CHECK"] == PUBLISH_CHECK
        assert "secrets." not in gate_step["run"]


def test_report_results_reporter_failure_does_not_replace_the_junit_gate():
    """An upstream reporter error is continue-on-error; the gate still reads the files."""
    matches = [item for item in reporter_steps() if item[1] == "report-results"]
    assert len(matches) == 1
    _path, _job_name, _job, step, gate_step = matches[0]
    assert step["continue-on-error"] is True
    assert step.get("if") == "${{ !cancelled() }}"
    assert gate_step.get("if") == "${{ !cancelled() }}"
    assert step["with"]["path"] == "native-results/**/archives.xml"
    assert "tools/test_results.py" in gate_step["run"]
    assert "--publish-check" in gate_step["run"]


def _sample_for(pattern, root, *, failed=False):
    result = ("test_case", "failure", "AssertionError") if failed else ("test_case", "pass", "")
    for piece in pattern.split(","):
        relative = piece.strip().replace("**", "nested").replace("*", "sample")
        write_junit(root / relative, [result])


def test_workflow_report_paths_pass_when_every_listed_file_passes(tmp_path, monkeypatch, capsys):
    for _path, _job_name, _job, step, gate_step in reporter_steps():
        workspace = tmp_path / step["name"].replace(" ", "-")
        pattern = gate_step["env"]["REPORT_PATH"]
        _sample_for(pattern, workspace)
        api = FakeChecks()
        code, printed, _output, _summary = run_cli(
            workspace, monkeypatch, capsys, api, pattern=pattern
        )
        assert code == 0, pattern
        assert printed == ""
        assert api.runs[0]["conclusion"] == "success"


def test_workflow_report_paths_fail_when_a_listed_file_fails(tmp_path, monkeypatch, capsys):
    for _path, _job_name, _job, step, gate_step in reporter_steps():
        workspace = tmp_path / step["name"].replace(" ", "-")
        pattern = gate_step["env"]["REPORT_PATH"]
        _sample_for(pattern, workspace, failed=True)
        api = FakeChecks()
        code, printed, output, _summary = run_cli(
            workspace, monkeypatch, capsys, api, pattern=pattern
        )
        assert code == 1, pattern
        assert printed == ""
        assert "failed=" in output
        assert int(output.split("failed=")[1].split("\n", 1)[0]) >= 1
        assert api.created_body["conclusion"] == "failure"
