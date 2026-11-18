# SPDX-License-Identifier: Apache-2.0
""".github/scripts/record_verifier_image.py: the image digest the job ran in, from the daemon.

The lock names the verifier image as image:tag@digest and the jobs run in exactly that
reference, so a record copied from the lock would agree with it by construction. The script
asks the container engine which image this container was created from and records the
digest the engine reports, next to the locked one, so a difference is visible.
"""

import importlib.util
import json
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "record_verifier_image.py"

IMAGE = "ghcr.io/living-proof-labs/capability-verifier-hallway"
LOCKED = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
IMAGE_ID = "sha256:" + "c" * 64
CONTAINER = "0123456789ab"
REFERENCE = f"{IMAGE}:2.3.1@{LOCKED}"


def _script():
    spec = importlib.util.spec_from_file_location("record_verifier_image", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Daemon:
    """A container engine that answers the two inspect calls over a unix socket."""

    def __init__(self, tmp_path, repo_digests, containers=(CONTAINER,), config_image=REFERENCE):
        self.path = str(tmp_path / "docker.sock")
        self.requests = []
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                daemon.requests.append(self.path)
                parts = self.path.strip("/").split("/")
                if parts[0] == "containers" and parts[-1] == "json" and parts[1] in containers:
                    # Config.Image echoes what the job was asked to run: not evidence.
                    body = {
                        "Id": parts[1] * 5,
                        "Image": IMAGE_ID,
                        "Config": {"Image": config_image},
                    }
                elif parts[0] == "images" and parts[-1] == "json" and parts[1] == IMAGE_ID:
                    body = {"Id": IMAGE_ID, "RepoDigests": repo_digests}
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        self.server = Server(self.path, Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _run(tmp_path, daemon_path, reference=REFERENCE, container=CONTAINER):
    out = tmp_path / "evidence" / "go1" / "verifier-image.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--locked",
            reference,
            "--out",
            str(out),
            "--socket",
            daemon_path,
            "--container",
            container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    record = json.loads(out.read_text()) if out.exists() else None
    return result, record


def test_a_matching_image_is_recorded_with_the_digest_the_daemon_reports(tmp_path):
    with _Daemon(tmp_path, [f"{IMAGE}@{LOCKED}"]) as daemon:
        result, record = _run(tmp_path, daemon.path)
    assert result.returncode == 0, result.stderr
    assert record["format"] == "livingproof-verifier-image/0.1"
    assert record["image"] == IMAGE
    assert record["locked_digest"] == LOCKED
    assert record["digest"] == LOCKED
    assert record["matches"] is True
    assert record["image_id"] == IMAGE_ID
    assert record["repo_digests"] == [f"{IMAGE}@{LOCKED}"]
    assert record["container"] == CONTAINER
    assert "::warning" not in result.stderr


def test_a_digest_that_differs_from_the_lock_is_recorded_as_different(tmp_path):
    # The container was asked for REFERENCE (Config.Image says so) but the engine reports
    # that the image under it is OTHER: the record follows the engine, not the request.
    with _Daemon(tmp_path, [f"{IMAGE}@{OTHER}"]) as daemon:
        result, record = _run(tmp_path, daemon.path)
    assert result.returncode == 0, result.stderr
    assert record["digest"] == OTHER
    assert record["locked_digest"] == LOCKED
    assert record["matches"] is False
    assert "::warning" in result.stderr
    assert OTHER in result.stderr and LOCKED in result.stderr


def test_the_lock_is_among_several_digests_of_one_image(tmp_path):
    with _Daemon(tmp_path, [f"{IMAGE}@{OTHER}", f"{IMAGE}@{LOCKED}"]) as daemon:
        _, record = _run(tmp_path, daemon.path)
    assert record["digest"] == LOCKED
    assert record["matches"] is True
    assert record["repo_digests"] == [f"{IMAGE}@{OTHER}", f"{IMAGE}@{LOCKED}"]


def test_a_digest_of_another_repository_does_not_count(tmp_path):
    with _Daemon(tmp_path, [f"ghcr.io/example/other@{LOCKED}"]) as daemon:
        result, record = _run(tmp_path, daemon.path)
    assert result.returncode == 0
    assert record["digest"] is None
    assert record["matches"] is None
    assert "no digest of" in record["unavailable"]


def test_an_image_with_no_registry_digest_is_recorded_as_unknown(tmp_path):
    with _Daemon(tmp_path, []) as daemon:
        _, record = _run(tmp_path, daemon.path)
    assert record["digest"] is None and record["matches"] is None
    assert record["image_id"] == IMAGE_ID


def test_no_container_engine_is_recorded_as_unknown_not_as_a_match(tmp_path):
    result, record = _run(tmp_path, str(tmp_path / "missing.sock"))
    assert result.returncode == 0
    assert record["digest"] is None
    assert record["matches"] is None
    assert record["locked_digest"] == LOCKED
    assert "container engine" in record["unavailable"]
    assert "::warning" in result.stderr


def test_an_unknown_container_is_recorded_as_unknown(tmp_path):
    with _Daemon(tmp_path, [f"{IMAGE}@{LOCKED}"]) as daemon:
        _, record = _run(tmp_path, daemon.path, container="ffffffffffff")
    assert record["digest"] is None and record["matches"] is None
    assert "ffffffffffff" in record["unavailable"]


def test_the_container_is_found_from_the_hostname_or_the_mount_table():
    module = _script()
    full = "d" * 64
    mountinfo = (
        f"1 2 0:1 /var/lib/docker/containers/{full}/resolv.conf /etc/resolv.conf rw - ext4 /dev/sda1 rw\n"
        f"3 4 0:2 /var/lib/docker/containers/{full}/hostname /etc/hostname rw - ext4 /dev/sda1 rw\n"
    )
    assert module.container_ids("abc123def456", mountinfo) == ["abc123def456", full]
    assert module.container_ids("abc123def456", "") == ["abc123def456"]
    assert module.container_ids("", mountinfo) == [full]


def test_a_lock_reference_must_carry_a_sha256_digest(tmp_path):
    out = tmp_path / "x.json"
    for reference in (f"{IMAGE}:2.3.1", f"{IMAGE}@sha256:abc", "", f"@{LOCKED}"):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--locked", reference, "--out", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, reference
        assert not out.exists()


def test_the_digest_is_split_from_the_reference_the_way_the_plan_writes_it():
    module = _script()
    assert module.split_reference(REFERENCE) == (IMAGE, LOCKED)
    assert module.split_reference(f"{IMAGE}@{LOCKED}") == (IMAGE, LOCKED)
    # A registry port is not a tag.
    assert module.split_reference(f"localhost:5000/v@{LOCKED}") == ("localhost:5000/v", LOCKED)


def _workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_the_evidence_job_records_the_image_into_the_artifact_run_integrity_reads():
    job = _workflow("scenario.yml")["jobs"]["evidence"]
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    record = next(s for s in steps if "record_verifier_image.py" in s.get("run", ""))
    source = next(s for s in steps if s.get("with", {}).get("path") == "image-record-src")
    evidence = next(s for s in steps if s.get("name", "").startswith("Evidence (observations"))
    upload = next(s for s in steps if "upload-artifact" in s.get("uses", ""))
    # The script comes from the workflow's own commit, not from the image under test.
    assert source["with"]["repository"] == "${{ job.workflow_repository }}"
    assert source["with"]["ref"] == "${{ job.workflow_sha }}"
    assert steps.index(source) < steps.index(evidence) < steps.index(record) < steps.index(upload)
    # Recorded even when the evidence step fails, into the directory that is uploaded.
    assert "always()" in record["if"]
    assert "needs.plan.outputs.verifier-image != ''" in record["if"]
    assert "needs.plan.outputs.verifier-image != ''" in source["if"]
    assert record["env"]["LOCKED_IMAGE"] == "${{ needs.plan.outputs.verifier-image }}"
    assert '--out "artifacts/evidence/$CONFIGURATION/verifier-image.json"' in record["run"]
    # The engine is asked about the container the runner reports, not a guess from the hostname.
    assert record["env"]["CONTAINER_ID"] == "${{ job.container.id }}"
    assert '--container "$CONTAINER_ID"' in record["run"]
    assert upload["with"]["path"] == "artifacts/evidence/${{ matrix.configuration }}/"
    assert "image-record-src" in record["run"] and "rm -rf" in record["run"]
    assert names  # steps are named


def test_ci_runs_the_script_in_the_real_image_pulled_by_digest():
    document = _workflow("verify.yml")
    job = document["jobs"]["verifier-image-record"]
    reference = job["container"]["image"]
    repository, digest = _script().split_reference(reference)
    assert repository == IMAGE
    assert reference.endswith("@" + digest) and digest != LOCKED
    assert job["env"]["LOCKED_IMAGE"] == reference
    text = "\n".join(step.get("run", "") + json.dumps(step.get("env", {})) for step in job["steps"])
    assert "record_verifier_image.py" in text
    # The job fails unless the engine's digest is the pinned one.
    assert 'record["matches"] is not True' in text
    assert "--container" in text and "job.container.id" in text
    assert "verifier-image-record" in document["jobs"]["release"]["needs"]
