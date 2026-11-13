# SPDX-License-Identifier: Apache-2.0
"""Declarative verification definitions: lock, check and the shared workflow steps."""

import json
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from capability_verifier import definition as d

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/walk-hallway.yml").read_text()
IMPLEMENTATION = (ROOT / ".github/workflows/scenario.yml").read_text()
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


def _workflow(tmp_path, lock, job="g1-stock", **change):
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
        f"jobs:\n  {job}:\n    uses: {{uses}}\n    with:\n      repository: {{repository}}\n"
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
    assert lock["format"] == d.FORMAT
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
        {"configurations": "'[\"playground\"]'"},
    ],
)
def test_a_workflow_call_that_drifts_from_the_lock_is_refused(tmp_path, robot, change):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    with pytest.raises(SystemExit, match="verify.yml: job g1-stock: must"):
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
        "  g1-stock:\n"
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
    with pytest.raises(SystemExit, match="job g1-stock: must call"):
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
    with pytest.raises(SystemExit, match="job extra: calls .*no known definition"):
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
        ("Scenario catalog", "fetch_hallway_specs.py"),
        ("Assess every trial", "verify_hallway.py"),
        ("Validate, then render", "livingproof-capability-report render"),
    ],
)
def test_run_executes_the_reusable_workflow_steps_not_a_copy(name, needle):
    # Every step run() uses exists in this commit's walk-hallway.yml, with its script.
    script = d.step(IMPLEMENTATION, name)
    assert needle in script
    assert "${{" not in script  # inputs reach the scripts through env, as in CI


def test_missing_step_is_an_error():
    with pytest.raises(SystemExit, match="no step"):
        d.step(IMPLEMENTATION, "Nonexistent step")


def test_livingproof_plugin_entry_point():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    entry = meta["project"]["entry-points"]["livingproof.capability.commands"]
    assert entry == {"verify": "capability_verifier.plugin:verify"}
    click = pytest.importorskip("click")
    from capability_verifier.plugin import verify

    assert isinstance(verify, click.Command)


def _workflows_dir(tmp_path, lock):
    """A repository layout: .github/workflows/verify.yml holding the locked call."""
    wf_dir = tmp_path / "repo" / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "verify.yml"
    wf.write_text(_workflow(tmp_path, lock).read_text())
    return wf_dir, wf


def _call(uses, lock, ref="main"):
    return (
        f"jobs:\n  g1-stock:\n    uses: {uses}\n    with:\n"
        f"      repository: {lock['robot']['repository']}\n      ref: {ref}\n"
        "      configurations: '[\"stock\"]'\n"
    )


def test_the_signature_matches_the_procedure_workflow():
    import yaml

    call = yaml.safe_load(WORKFLOW)[True]["workflow_call"]
    sig = d.SIGNATURES["walk-hallway"]
    assert sig["inputs"] <= set(call["inputs"])
    assert sig["outputs"] == set(call["outputs"])


@pytest.mark.parametrize(
    "uses",
    [
        "./.github/workflows/hallway-copy.yml",
        "someone-else/fork/.github/workflows/hallway.yml@main",
        "Living-Proof-Labs/capability-verifier/.github/workflows/walk-hallway.yml@main",
    ],
    ids=["renamed-local", "renamed-fork", "other-ref"],
)
def test_calls_in_every_workflow_file_are_checked(tmp_path, robot, uses):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf_dir, wf = _workflows_dir(tmp_path, lock)
    assert d.check(path, tmp_path / "w2", wf)
    (wf_dir / "nightly.yaml").write_text(_call(uses, lock))
    with pytest.raises(SystemExit, match=r"nightly.yaml: job g1-stock: must call"):
        d.check(path, tmp_path / "w2", wf)


def test_a_renamed_local_copy_is_found_by_its_interface(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf_dir, wf = _workflows_dir(tmp_path, lock)
    (wf_dir / "hallway-copy.yml").write_text(WORKFLOW)
    # Called without the procedure's inputs, so only the copy's interface identifies it.
    # The job id is the definition's, so the recognized call is checked against the lock.
    (wf_dir / "other.yml").write_text(
        "jobs:\n  g1-stock:\n    uses: ./.github/workflows/hallway-copy.yml\n"
    )
    with pytest.raises(SystemExit, match=r"other.yml: job g1-stock: must call"):
        d.check(path, tmp_path / "w2", wf)


def test_unrelated_reusable_workflows_and_steps_are_ignored(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf_dir, wf = _workflows_dir(tmp_path, lock)
    (wf_dir / "lint.yml").write_text(
        "on:\n  workflow_call:\n    inputs:\n      path: {type: string}\n"
        "jobs:\n  lint:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n"
    )
    (wf_dir / "ci.yml").write_text(
        "jobs:\n  lint:\n    uses: ./.github/workflows/lint.yml\n    with:\n      path: .\n"
        "  docs:\n    uses: Org/docs/.github/workflows/build.yml@main\n"
    )
    assert d.check(path, tmp_path / "w2", wf) == lock


@pytest.mark.parametrize(
    ("repository", "message"),
    [
        ("Org/other-robot", "robot.repository 'Org/other-robot' differs from g1.toml"),
        ("not a repo", "is not OWNER/REPO"),
    ],
)
def test_an_edited_robot_repository_is_refused_before_any_fetch(
    tmp_path, robot, monkeypatch, repository, message
):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    lock["robot"]["repository"] = repository
    d.lock_path(path).write_text(json.dumps(lock))

    def no_fetch(*args, **kwargs):
        raise AssertionError("fetched a repository the definition does not name")

    monkeypatch.setattr(d, "show", no_fetch)
    with pytest.raises(SystemExit, match=message):
        d.check(path, tmp_path / "w2")


def test_a_call_for_another_repository_is_not_this_definitions_call(tmp_path, robot):
    # Job g1 is not the definition id, so the call is not this definition's.
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    with pytest.raises(SystemExit, match="no job calls .* for Org/robot"):
        d.check(path, tmp_path / "w2", _workflow(tmp_path, lock, job="g1", repository="Org/other"))


def _multi(tmp_path, lock, **mine):
    """verify.yml with another robot's call (other pins) and this definition's job g1-stock."""
    v = lock["releases"]["verifier"]
    call = f"{v['repository']}/.github/workflows/walk-hallway.yml"
    w = {
        "uses": f"{call}@{v['commit']}",
        "repository": lock["robot"]["repository"],
        "ref": lock["robot"]["commit"],
        "configurations": "'[\"stock\"]'",
        **mine,
    }
    # The other robot is a known definition beside g1.toml.
    (tmp_path / "proj" / "t1.toml").write_text(
        f'format = "{d.DEFINITION_FORMAT}"\nid = "booster-t1-stock"\nprocedure = "walk-hallway"\n'
        '[robot]\nrepository = "Org/t1"\nref = "main"\nconfigurations = ["playground"]\n'
    )
    path = tmp_path / "verify.yml"
    path.write_text(
        "jobs:\n"
        f"  booster-t1-stock:\n    uses: {call}@{'e' * 40}\n    with:\n"
        f"      repository: Org/t1\n      ref: {'1' * 40}\n"
        "      configurations: '[\"playground\"]'\n"
        f"  g1-stock:\n    uses: {w['uses']}\n    with:\n"
        f"      repository: {w['repository']}\n      ref: {w['ref']}\n"
        f"      configurations: {w['configurations']}\n"
    )
    return path


def test_one_workflow_holds_several_robots(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    assert d.check(path, tmp_path / "w2", _multi(tmp_path, lock)) == lock


@pytest.mark.parametrize(
    "change",
    [
        {"uses": "Living-Proof-Labs/capability-verifier/.github/workflows/walk-hallway.yml@main"},
        {"ref": "2" * 40},
        {"configurations": "'[\"playground\"]'"},
        # Matched by job id: a wrong repository is refused, not ignored.
        {"repository": "Org/t1"},
    ],
)
def test_a_mismatched_pin_for_the_matched_robot_fails_beside_other_robots(tmp_path, robot, change):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    with pytest.raises(SystemExit, match="verify.yml: job g1-stock: must") as e:
        d.check(path, tmp_path / "w2", _multi(tmp_path, lock, **change))
    assert "booster-t1-stock" not in str(e.value)


def test_a_definition_without_its_call_fails_beside_other_robots(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _multi(tmp_path, lock)
    wf.write_text(wf.read_text().split("  g1-stock:")[0])
    with pytest.raises(SystemExit, match="no job calls"):
        d.check(path, tmp_path / "w2", wf)


@pytest.mark.parametrize(
    "repository",
    ["org/ROBOT", "Org/robot.git", "${{ matrix.robot }}", "Org/unknown"],
    ids=["case", "dot-git", "expression", "unknown"],
)
def test_a_differently_spelled_or_unknown_robot_call_is_refused(tmp_path, robot, repository):
    """Vera on #30: such a call used to match no definition and slip through."""
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _multi(tmp_path, lock)
    assert d.check(path, tmp_path / "w2", wf) == lock
    wf.write_text(
        wf.read_text() + f"  extra:\n    uses: {lock['releases']['verifier']['repository']}"
        f"/.github/workflows/walk-hallway.yml@main\n    with:\n"
        f"      repository: {repository}\n      ref: main\n"
    )
    with pytest.raises(SystemExit, match="job extra: (must|calls)"):
        d.check(path, tmp_path / "w2", wf)


def test_another_known_robots_call_still_passes(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _multi(tmp_path, lock)
    (tmp_path / "proj" / "t1.toml").unlink()
    with pytest.raises(SystemExit, match="booster-t1-stock: calls .*no known definition"):
        d.check(path, tmp_path / "w2", wf)


def test_two_definitions_on_one_repository_are_paired_by_job_id(tmp_path, robot):
    """Each definition takes the job with its id, including when they share a repository."""
    stock = _definition(tmp_path)
    stock_lock = _lock(tmp_path, stock)
    playground = tmp_path / "proj" / "g1-playground.toml"
    playground.write_text(
        f'format = "{d.DEFINITION_FORMAT}"\nid = "g1-playground"\nprocedure = "walk-hallway"\n'
        '[robot]\nrepository = "Org/robot"\nref = "main"\nconfigurations = ["playground"]\n'
    )
    play_lock = _lock(tmp_path, playground)
    v = stock_lock["releases"]["verifier"]
    call = f"{v['repository']}/.github/workflows/walk-hallway.yml@{v['commit']}"
    wf = tmp_path / "verify.yml"
    wf.write_text(
        "jobs:\n"
        f"  g1-stock:\n    uses: {call}\n    with:\n"
        f"      repository: {stock_lock['robot']['repository']}\n"
        f"      ref: {stock_lock['robot']['commit']}\n"
        "      configurations: '[\"stock\"]'\n"
        f"  g1-playground:\n    uses: {call}\n    with:\n"
        f"      repository: {play_lock['robot']['repository']}\n"
        f"      ref: {play_lock['robot']['commit']}\n"
        "      configurations: '[\"playground\"]'\n"
    )
    assert d.check(stock, tmp_path / "w-stock", wf) == stock_lock
    assert d.check(playground, tmp_path / "w-play", wf) == play_lock
    # The other job's configurations stay with that definition.
    wf.write_text(wf.read_text().replace('["playground"]', '["stock"]', 1))
    assert d.check(stock, tmp_path / "w-stock2", wf) == stock_lock
    with pytest.raises(SystemExit, match="job g1-playground: must pass the locked configurations"):
        d.check(playground, tmp_path / "w-play2", wf)


def test_a_missing_job_id_is_refused(tmp_path, robot):
    """A call for this robot under another job id does not stand in for the definition."""
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _workflow(tmp_path, lock, job="g1")
    with pytest.raises(SystemExit, match=r"no job calls .* \(job g1-stock\)"):
        d.check(path, tmp_path / "w2", wf)


def test_an_unknown_job_id_is_refused(tmp_path, robot):
    """A procedure call whose job id is not a definition is refused, pin and repository aside."""
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    wf = _workflow(tmp_path, lock)
    pinned = _pin(lock)
    wf.write_text(
        wf.read_text() + f"  extra:\n    uses: {pinned}\n    with:\n"
        f"      repository: {lock['robot']['repository']}\n"
        f"      ref: {lock['robot']['commit']}\n"
        "      configurations: '[\"stock\"]'\n"
    )
    with pytest.raises(
        SystemExit, match=r"job extra: calls the procedure, but job id 'extra' is no known"
    ):
        d.check(path, tmp_path / "w2", wf)


THIN = Path(__file__).parent / "fixtures" / "thin-robot"
EVALUATOR_COMMIT = "752a6f0a9f9d448c950e56a8e345a69a6693a907"


def _release(commit, *, image=None):
    ref = {
        "repository": "Living-Proof-Labs/example",
        "tag": "v1.2.3",
        "commit": commit,
    }
    if image is not None:
        ref["image"] = image
        ref["digest"] = "sha256:" + "ab" * 32
    return ref


@pytest.fixture
def thin_robot(tmp_path, monkeypatch):
    """A robot commit with adapter, robot.json and configuration files, and no lock file."""
    base = tmp_path / "git"
    repo = base / "Org" / "robot.git"
    shutil.copytree(THIN, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    monkeypatch.setattr(d, "GITHUB", f"file://{base}")
    return repo


def _v2_lock(repo, definition):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    profile = json.loads((repo / "robot.json").read_text())
    versions = {"stock": "1.1.0", "playground": "1.0.0"}
    configurations = []
    for item in profile["configurations"]:
        raw = (repo / item["path"]).read_bytes()
        configurations.append(
            {
                "name": item["name"],
                "version": versions[item["name"]],
                "path": item["path"],
                "sha256": d.sha256(raw),
            }
        )
    adapter = tomllib.loads((repo / "pyproject.toml").read_text())["tool"]["livingproof"]["adapter"]
    return {
        "format": d.FORMAT_V2,
        "definition": {"path": definition.name, "sha256": d.sha256(definition.read_bytes())},
        "procedure": "walk-hallway",
        "scenario": "walk-hallway@0.1.0",
        "robot": {
            "repository": "Org/robot",
            "ref": "main",
            "commit": commit,
            "configurations": ["stock"],
            "adapter": adapter,
        },
        "releases": {
            "specs": _release("a" * 40),
            "runner": _release("b" * 40),
            "verifier": _release(
                "c" * 40, image="ghcr.io/living-proof-labs/capability-verifier-hallway"
            ),
            "reporter": _release(
                "d" * 40, image="ghcr.io/living-proof-labs/livingproof-capability-report"
            ),
        },
        "world": {"id": "hallway-open", "version": "0.1.0"},
        "configurations": configurations,
        "plan": {"trials": {"count": 3}, "command_speed": 0.5},
        "evaluators": {
            "livingproof-evaluator-scenario": {
                "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
                "tag": "v1.0.0",
                "commit": EVALUATOR_COMMIT,
            }
        },
    }


def _write_v2(path, lock):
    d.lock_path(path).write_text(json.dumps(lock))
    return lock


def test_v1_still_compares_releases_to_the_robot_lock(tmp_path, robot):
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    lock["releases"] = {
        "verifier": {
            "repository": "Living-Proof-Labs/capability-verifier",
            "tag": "v9.9.9",
            "commit": "a" * 40,
        }
    }
    d.lock_path(path).write_text(json.dumps(lock))
    with pytest.raises(SystemExit, match="releases in g1.lock.json differ from the robot lock"):
        d.check(path, tmp_path / "w2")


def test_v2_check_compares_configuration_sha_adapter_and_robot_json(tmp_path, thin_robot):
    path = _definition(tmp_path)
    lock = _write_v2(path, _v2_lock(thin_robot, path))
    assert not (thin_robot / "verification.lock.json").exists()
    assert d.check(path, tmp_path / "w2", _workflow(tmp_path, lock)) == lock


def test_v2_does_not_compare_releases_to_a_robot_verification_lock(tmp_path, thin_robot):
    (thin_robot / "verification.lock.json").write_text(
        json.dumps(
            {
                "format": "livingproof-verification-lock/1.0.0",
                "releases": {"verifier": {"tag": "v0.0.1"}},
            }
        )
    )
    _git(thin_robot, "add", "-A")
    _git(thin_robot, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add lock")
    path = _definition(tmp_path)
    lock = _write_v2(path, _v2_lock(thin_robot, path))
    robot_releases = json.loads((thin_robot / "verification.lock.json").read_text())["releases"]
    assert robot_releases != lock["releases"]
    assert d.check(path, tmp_path / "w2") == lock


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ("sha", "sha256 is"),
        ("adapter", "adapter is"),
        ("names", "configuration names are"),
        ("missing", "is missing"),
    ],
)
def test_v2_check_refuses_a_robot_tree_that_drifted(tmp_path, thin_robot, mutate, message):
    path = _definition(tmp_path)
    lock = _v2_lock(thin_robot, path)
    if mutate == "sha":
        lock["configurations"][0]["sha256"] = "0" * 64
    elif mutate == "adapter":
        lock["robot"]["adapter"] = "other.adapter:ROBOT"
    elif mutate == "names":
        lock["configurations"].reverse()
    else:
        lock["configurations"][0]["path"] = "configurations/missing.json"
    _write_v2(path, lock)
    with pytest.raises(SystemExit, match=message):
        d.check(path, tmp_path / "w2")


def test_v2_refuses_a_configuration_path_that_leaves_the_robot(tmp_path, thin_robot, monkeypatch):
    path = _definition(tmp_path)
    lock = _v2_lock(thin_robot, path)
    lock["configurations"][0]["path"] = "../secret"
    _write_v2(path, lock)

    def no_fetch(*args, **kwargs):
        raise AssertionError("fetched a path that leaves the robot repository")

    monkeypatch.setattr(d, "checkout", no_fetch)
    with pytest.raises(SystemExit, match="must stay inside the robot repository"):
        d.check(path, tmp_path / "w2")


def test_v2_pending_release_is_refused(tmp_path, thin_robot):
    path = _definition(tmp_path)
    lock = _v2_lock(thin_robot, path)
    lock["evaluators"]["livingproof-evaluator-scenario"]["tag"] = "PENDING_RELEASE"
    lock["evaluators"]["livingproof-evaluator-scenario"]["commit"] = "PENDING_RELEASE"
    _write_v2(path, lock)
    with pytest.raises(SystemExit, match="vX.Y.Z release tag"):
        d.check(path, tmp_path / "w2")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"uses": "Org/fork/.github/workflows/walk-hallway.yml@main"}, "must call"),
        ({"ref": "main"}, "locked robot commit"),
        ({"repository": "Org/other"}, "locked robot repository"),
        ({"configurations": "'[\"playground\"]'"}, "locked configurations"),
        ({"job": "other"}, "no job calls"),
    ],
)
def test_v2_workflow_call_keeps_every_equality(tmp_path, thin_robot, change, message):
    path = _definition(tmp_path)
    lock = _write_v2(path, _v2_lock(thin_robot, path))
    assert d.check(path, tmp_path / "pass", _workflow(tmp_path, lock)) == lock
    with pytest.raises(SystemExit, match=message):
        d.check(path, tmp_path / "drift", _workflow(tmp_path, lock, **change))


def test_a_workflow_dispatch_preview_ref_is_ignored(tmp_path, robot):
    """The preview input belongs on livingproof-verification's workflow_dispatch.

    This checker does not read it. The committed with.ref stays the locked commit.
    An expression written into with.ref is still a drift.
    """
    path = _definition(tmp_path)
    lock = _lock(tmp_path, path)
    commit = lock["robot"]["commit"]
    wf = tmp_path / "verify.yml"
    wf.write_text(
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      preview-ref:\n"
        "        description: Robot commit to preview\n"
        "        type: string\n"
        "        default: ''\n"
        "jobs:\n"
        "  g1-stock:\n"
        f"    uses: {_pin(lock)}\n"
        "    with:\n"
        f"      repository: {lock['robot']['repository']}\n"
        f"      ref: {commit}\n"
        "      configurations: '[\"stock\"]'\n"
        "      preview-ref: ${{ inputs.preview-ref }}\n"
    )
    assert d.check(path, tmp_path / "w2", wf) == lock
    wf.write_text(wf.read_text().replace(f"ref: {commit}", "ref: ${{ inputs.preview-ref }}"))
    with pytest.raises(SystemExit, match="locked robot commit"):
        d.check(path, tmp_path / "w3", wf)
