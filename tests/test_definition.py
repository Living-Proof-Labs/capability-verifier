# SPDX-License-Identifier: Apache-2.0
"""Declarative verification definitions: lock, check and the shared workflow steps."""

import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from capability_verifier import definition as d

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/walk-hallway.yml").read_text()
RELEASES = {
    "verifier": {
        "repository": "Living-Proof-Labs/capability-verifier",
        "tag": "v1.9.0",
        "commit": "f" * 40,
    }
}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def robot(tmp_path, monkeypatch):
    """A local robot repository named Org/robot with stock and playground in its lock."""
    base = tmp_path / "git"
    repo = base / "Org" / "robot.git"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    lock = {"configurations": [{"name": "stock"}, {"name": "playground"}], "releases": RELEASES}
    (repo / "verification.lock.json").write_text(json.dumps(lock))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    monkeypatch.setattr(d, "GITHUB", f"file://{base}")
    return repo


def _definition(tmp_path, **robot):
    r = {"repository": "Org/robot", "ref": "main", "configurations": ["stock"], **robot}
    path = tmp_path / "proj" / "g1.toml"
    path.parent.mkdir(exist_ok=True)
    confs = json.dumps(r["configurations"])
    path.write_text(
        f'format = "{d.DEFINITION_FORMAT}"\nid = "g1-stock"\nprocedure = "walk-hallway"\n'
        f'[robot]\nrepository = "{r["repository"]}"\nref = "{r["ref"]}"\n'
        f"configurations = {confs}\n"
    )
    return path


def _lock(tmp_path, path):
    d.main(["lock", str(path), "--work", str(tmp_path / "w")])
    return json.loads(d.lock_path(path).read_text())


def _workflow(tmp_path, lock, **change):
    v = lock["releases"]["verifier"]
    values = {
        "uses": f"{v['repository']}/.github/workflows/walk-hallway.yml@{v['commit']}",
        "repository": lock["robot"]["repository"],
        "ref": lock["robot"]["commit"],
        "configurations": f"'{json.dumps(lock['robot']['configurations'])}'",
        **change,
    }
    path = tmp_path / "verify.yml"
    path.write_text(
        "jobs:\n  g1:\n    uses: {uses}\n    with:\n      repository: {repository}\n"
        "      ref: {ref}\n      configurations: {configurations}\n".format(**values)
    )
    return path


def test_lock_records_the_robot_commit_lock_digest_and_releases(tmp_path, robot):
    lock = _lock(tmp_path, _definition(tmp_path))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=robot, capture_output=True, text=True
    ).stdout.strip()
    assert lock["robot"]["commit"] == head
    assert lock["robot"]["configurations"] == ["stock"]
    assert lock["releases"] == RELEASES
    assert len(lock["robot"]["lock"]["sha256"]) == 64


def test_check_passes_for_a_fresh_lock_and_its_workflow_call(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    assert d.check(path, tmp_path / "w2", _workflow(tmp_path, lock)) == lock


def test_check_keeps_the_locked_commit_when_the_robot_moves_on(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    (robot / "verification.lock.json").write_text(json.dumps({"releases": {}}))
    _git(robot, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "move")
    assert d.check(path, tmp_path / "w2") == lock


def test_an_edited_definition_must_be_relocked(tmp_path, robot):
    path = _definition(tmp_path)
    _lock(tmp_path, path)
    path.write_text(path.read_text().replace('["stock"]', '["stock", "playground"]'))
    with pytest.raises(SystemExit, match="changed since it was locked"):
        d.check(path, tmp_path / "w2")


def test_a_tampered_lock_is_refused(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    lock["robot"]["lock"]["sha256"] = "0" * 64
    d.lock_path(path).write_text(json.dumps(lock))
    with pytest.raises(SystemExit, match="lock differs"):
        d.check(path, tmp_path / "w2")


@pytest.mark.parametrize(
    "change",
    [
        {"uses": "Living-Proof-Labs/capability-verifier/.github/workflows/walk-hallway.yml@main"},
        {"ref": "main"},
        {"repository": "Org/other"},
        {"configurations": "'[\"playground\"]'"},
    ],
)
def test_a_workflow_call_that_drifts_from_the_lock_is_refused(tmp_path, robot, change):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    with pytest.raises(SystemExit, match="verify.yml: job g1: must"):
        d.check(path, tmp_path / "w2", _workflow(tmp_path, lock, **change))


def _pin(lock):
    v = lock["releases"]["verifier"]
    return f"{v['repository']}/.github/workflows/walk-hallway.yml@{v['commit']}"


def test_a_commented_out_pin_is_refused(tmp_path, robot):
    # Regression: the pinned line as a comment next to a floating ref used to pass.
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    floating = _pin(lock).split("@")[0] + "@main"
    wf = _workflow(tmp_path, lock, uses=f"{floating}\n    # uses: {_pin(lock)}")
    assert f"# uses: {_pin(lock)}" in wf.read_text()
    with pytest.raises(SystemExit, match="must call .*@f{40} \\(calls .*@main\\)"):
        d.check(path, tmp_path / "w2", wf)


def test_commented_out_ref_and_configurations_are_refused(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _workflow(
        tmp_path,
        lock,
        ref=f"main\n      # ref: {lock['robot']['commit']}",
        configurations="'[\"playground\"]'\n      # configurations: '[\"stock\"]'",
    )
    with pytest.raises(SystemExit) as e:
        d.check(path, tmp_path / "w2", wf)
    assert "locked robot commit" in str(e.value) and "locked configurations" in str(e.value)


def test_the_pin_in_another_job_or_key_does_not_count(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = tmp_path / "verify.yml"
    wf.write_text(
        "env:\n"
        f"  PIN: 'uses: {_pin(lock)}'\n"
        "jobs:\n"
        "  g1:\n"
        f"    uses: {_pin(lock).split('@')[0]}@main\n"
        "    with:\n"
        f"      repository: {lock['robot']['repository']}\n"
        f"      ref: {lock['robot']['commit']}\n"
        "      configurations: '[\"stock\"]'\n"
        "  other:\n"
        "    runs-on: ubuntu-24.04\n"
        "    steps:\n"
        f"      - uses: {_pin(lock)}\n"
    )
    with pytest.raises(SystemExit, match="job g1: must call"):
        d.check(path, tmp_path / "w2", wf)


@pytest.mark.parametrize(
    "uses",
    [
        "living-proof-labs/capability-verifier/.github/workflows/walk-hallway.yml@main",
        "someone-else/capability-verifier/.github/workflows/walk-hallway.yml@main",
        "./.github/workflows/walk-hallway.yml",
        "./.github/workflows/Walk-Hallway.yml",
    ],
    ids=["lowercase-owner", "fork", "local", "local-case"],
)
def test_a_second_call_to_any_walk_hallway_is_refused(tmp_path, robot, uses):
    # Regression (b19): only the exact owner/repo was treated as a verifier call.
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _workflow(tmp_path, lock)
    wf.write_text(
        wf.read_text() + f"  extra:\n    uses: {uses}\n    with:\n"
        f"      repository: {lock['robot']['repository']}\n      ref: main\n"
        "      configurations: '[\"stock\"]'\n"
    )
    with pytest.raises(SystemExit, match="job extra: must call"):
        d.check(path, tmp_path / "w2", wf)


def test_the_pin_matches_owner_and_repo_case_insensitively(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    owner, rest = _pin(lock).split("/", 1)
    assert d.check(path, tmp_path / "w2", _workflow(tmp_path, lock, uses=f"{owner.lower()}/{rest}"))
    upper = _pin(lock).replace("f" * 40, "F" * 40)
    with pytest.raises(SystemExit, match="must call"):
        d.check(path, tmp_path / "w2", _workflow(tmp_path, lock, uses=upper))


def test_edited_lock_configurations_are_refused(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    lock["robot"]["configurations"] = ["stock", "playground"]
    d.lock_path(path).write_text(json.dumps(lock))
    with pytest.raises(SystemExit, match="robot.configurations .* differs from g1.toml"):
        d.check(path, tmp_path / "w2")


def test_a_failing_step_is_a_message_not_a_traceback(tmp_path):
    with pytest.raises(d.DefinitionError, match=r"step failed \(exit 3\) in .*: echo refused"):
        d.sh("echo refused\nexit 3\n", tmp_path, {})


def test_a_failing_command_is_a_message_not_a_traceback(monkeypatch, tmp_path):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.CalledProcessError(128, ["git", "fetch"], stderr="fatal: no such ref\n")

    monkeypatch.setattr(d, "_main", boom)
    with pytest.raises(d.DefinitionError, match="command failed \\(exit 128\\): git fetch: fatal"):
        d.main(["lock", str(tmp_path / "x.toml")])


def test_a_workflow_without_the_call_is_refused(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = tmp_path / "verify.yml"
    wf.write_text(f"# uses: {_pin(lock)}\njobs:\n  t:\n    runs-on: ubuntu-24.04\n")
    with pytest.raises(SystemExit, match="no job calls"):
        d.check(path, tmp_path / "w2", wf)


def test_configurations_compare_as_json_not_text(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _workflow(tmp_path, lock, configurations="'[ \"stock\" ]'")
    assert d.check(path, tmp_path / "w2", wf) == lock


@pytest.mark.parametrize(
    "text, message",
    [
        ("{not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"format": "livingproof-verification-lock/0.1.0"}', "missing definition"),
    ],
)
def test_a_malformed_lock_is_refused_with_a_message(tmp_path, robot, text, message):
    path = _definition(tmp_path)
    _lock(tmp_path, path)
    d.lock_path(path).write_text(text)
    with pytest.raises(d.DefinitionError, match=message):
        d.check(path, tmp_path / "w2")


def test_a_lock_missing_a_key_is_refused_with_a_message(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    del lock["robot"]["lock"]
    d.lock_path(path).write_text(json.dumps(lock))
    with pytest.raises(d.DefinitionError, match="robot must have"):
        d.check(path, tmp_path / "w2")


@pytest.mark.parametrize("command", [["lock", "--check"], ["lock"], ["run"]])
def test_a_missing_definition_is_named(tmp_path, command):
    missing = tmp_path / "nope.toml"
    with pytest.raises(d.DefinitionError, match="nope.toml: no such definition"):
        d.main([*command, str(missing), *(["--out", str(tmp_path)] if command == ["run"] else [])])


def test_help_names_the_livingproof_command(capsys):
    pytest.importorskip("click")
    from click.testing import CliRunner

    from capability_verifier.plugin import verify

    out = CliRunner().invoke(verify, ["--help"]).output
    assert out.startswith("usage: livingproof capability verify")
    with pytest.raises(SystemExit):
        d.main(["--help"])
    assert "usage: python3 -m capability_verifier.definition" in capsys.readouterr().out


def test_unknown_configuration_is_refused_when_locking(tmp_path, robot):
    with pytest.raises(SystemExit, match="no configuration"):
        _lock(tmp_path, _definition(tmp_path, configurations=["rough"]))


@pytest.mark.parametrize(
    "text, message",
    [
        ('format = "x"\n', "format must be"),
        ('procedure = "fly"\n', "procedure must be"),
        ('id = "G1 Stock"\n', "id must be"),
    ],
)
def test_definition_is_validated(tmp_path, text, message):
    path = tmp_path / "bad.toml"
    good = {
        "format": f'"{d.DEFINITION_FORMAT}"',
        "id": '"g1"',
        "procedure": '"walk-hallway"',
    }
    key = text.split(" = ")[0]
    good[key] = text.split(" = ", 1)[1].strip()
    path.write_text(
        "".join(f"{k} = {v}\n" for k, v in good.items())
        + '[robot]\nrepository = "Org/robot"\nref = "main"\nconfigurations = ["stock"]\n'
    )
    with pytest.raises(SystemExit, match=message):
        d.load_definition(path)


@pytest.mark.parametrize(
    "name, needle",
    [
        ("Refs from verification.lock.json", "not in the lock"),
        ("Every ref is a released tag", "repin.py --root robot --check"),
        ("Media and bundle capability", "bundle-version="),
        ("Robot and configuration profiles valid", "robot.configuration(n)"),
        ("Setup (locked environment", "uv sync --locked"),
        ("Robot adapter from pyproject.toml", '["tool"]["livingproof"]["adapter"]'),
        ("Evidence (observations only", "livingproof_sim_mujoco.evidence"),
        ("Walk-hallway catalog", "fetch_hallway_specs.py"),
        ("Assess every trial", "verify_hallway.py"),
        ("Validate, then render", "livingproof-capability-report render"),
    ],
)
def test_run_executes_the_reusable_workflow_steps_not_a_copy(name, needle):
    # Every step run() uses exists in this commit's walk-hallway.yml, with its script.
    script = d.step(WORKFLOW, name)
    assert needle in script
    assert "${{" not in script  # inputs reach the scripts through env, as in CI


def test_missing_step_is_an_error():
    with pytest.raises(SystemExit, match="no step"):
        d.step(WORKFLOW, "Nonexistent step")


def test_livingproof_plugin_entry_point():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    entry = meta["project"]["entry-points"]["livingproof.capability.commands"]
    assert entry == {"verify": "capability_verifier.plugin:verify"}
    click = pytest.importorskip("click")
    from capability_verifier.plugin import verify

    assert isinstance(verify, click.Command)
