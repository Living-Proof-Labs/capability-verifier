# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import run_archives  # noqa: E402

HINT = "install reporter 1.6.0 (grade was removed in 3.0.0)"


def fake_reporter(directory: Path, script: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / run_archives.REPORTER
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return directory


def test_missing_reporter_names_the_release_to_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(run_archives.ReporterUnavailable, match=r"not on PATH; install reporter"):
        run_archives.require_reporter()


def test_reporter_that_cannot_start_is_reported_clearly(tmp_path, monkeypatch):
    bin_dir = fake_reporter(tmp_path / "bin", "exit 1\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(run_archives.ReporterUnavailable, match="--version failed") as error:
        run_archives.require_reporter()
    assert HINT in str(error.value)


def test_reporter_3_is_refused_with_the_install_hint(tmp_path, monkeypatch):
    bin_dir = fake_reporter(tmp_path / "bin", "echo 'version 3.0.1'; echo 'Package: 3.0.1;'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(run_archives.ReporterUnavailable, match="different reporter") as error:
        run_archives.require_reporter()
    assert HINT in str(error.value)


def test_reporter_1_6_0_is_accepted(tmp_path, monkeypatch):
    bin_dir = fake_reporter(tmp_path / "bin", "echo 'version 1.6.0'; echo 'Package: 1.6.0;'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert "1.6.0" in run_archives.require_reporter()


def test_check_demo_exits_with_the_message_not_a_traceback(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(TOOLS / "check_demo.py"),
            "in",
            "out",
            "--version",
            "v1.0.0",
            "--junit",
            str(tmp_path / "j.xml"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(tmp_path)},
    )
    assert result.returncode == 1
    assert "check_demo:" in result.stderr and HINT in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "j.xml").exists()
