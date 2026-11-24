# SPDX-License-Identifier: Apache-2.0
"""The verification-lock page matches the fields and messages in the code."""

import ast
import copy
import json
import subprocess
from pathlib import Path

from capability_verifier import definition as d
from capability_verifier import evidence_lock

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "docs" / "locks.md"
FIXTURES = (
    ROOT / "tests" / "fixtures" / "g1-862cf979-verification.lock.json",
    ROOT / "tests" / "fixtures" / "repin" / "verification.lock.json",
)
DIGEST = "sha256:" + "ab" * 32
FILE_SHA = "cd" * 32


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _page():
    return PAGE.read_text()


def _inventory(text):
    marker = "<!-- lock-field-inventory -->"
    start = text.index(marker)
    fence = text.index("```json", start)
    body = text[fence + len("```json") :]
    raw, _rest = body.split("```", 1)
    return json.loads(raw)


def _prose(text):
    marker = "<!-- lock-field-inventory -->"
    start = text.index(marker)
    fence = text.index("```json", start)
    close = text.index("```", fence + len("```json"))
    return text[:start] + text[close + 3 :]


def _node_paths(value, prefix=""):
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        child = f"{prefix}.{key}" if prefix else key
        yield child
        if isinstance(item, dict):
            yield from _node_paths(item, child)
        elif isinstance(item, list) and item and isinstance(item[0], dict):
            yield from _node_paths(item[0], child + "[]")


def _leaves_of(paths):
    paths = list(paths)
    leaves = []
    for path in paths:
        dot = path + "."
        listed = path + "[]"
        if any(other.startswith((dot, listed)) for other in paths):
            continue
        leaves.append(path)
    return leaves


def _scalar_leaves(value, prefix=""):
    if isinstance(value, dict):
        found = set()
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else key
            found |= _scalar_leaves(item, child)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _scalar_leaves(item, prefix + "[]")
        return found
    return {prefix}


def _delete(value, path):
    parts = path.split(".")
    cursor = value
    for part in parts[:-1]:
        cursor = cursor[part[:-2]][0] if part.endswith("[]") else cursor[part]
    last = parts[-1]
    if isinstance(cursor, list):
        cursor = cursor[0]
    del cursor[last]


def _set_path(value, path, item):
    parts = path.split(".")
    cursor = value
    for part in parts[:-1]:
        cursor = cursor[part[:-2]][0] if part.endswith("[]") else cursor[part]
    last = parts[-1]
    if isinstance(cursor, list):
        cursor = cursor[0]
    cursor[last] = item


def _read_error(lock, directory):
    path = directory / "lock.json"
    path.write_text(json.dumps(lock))
    try:
        d.read_lock(path)
    except d.DefinitionError as exc:
        return str(exc)
    return None


def _minimal_v1():
    return {
        "format": d.FORMAT,
        "definition": {"sha256": FILE_SHA},
        "procedure": "walk-hallway",
        "robot": {
            "repository": "Org/robot",
            "commit": "a" * 40,
            "configurations": ["stock"],
            "lock": {"path": "verification.lock.json", "sha256": FILE_SHA},
        },
        "releases": {
            "verifier": {"repository": "Org/verifier", "tag": "v1.2.3", "commit": "b" * 40}
        },
    }


def _pin(name):
    ref = {"repository": f"Org/{name}", "tag": "v1.2.3", "commit": "a" * 40}
    if name == "reporter":
        ref["image"] = "ghcr.io/example/reporter"
        ref["digest"] = DIGEST
    return ref


def _minimal_v2():
    return {
        "format": d.FORMAT_V2,
        "definition": {"sha256": FILE_SHA},
        "procedure": "walk-hallway",
        "scenario": "walk-hallway@0.1.0",
        "robot": {
            "repository": "Org/robot",
            "commit": "c" * 40,
            "configurations": ["stock"],
            "adapter": "pkg.adapter:ROBOT",
        },
        "releases": {name: _pin(name) for name in d.V2_SHAPE["releases"]},
        "world": {"id": "hallway-open", "version": "0.1.0"},
        "configurations": [
            {
                "name": "stock",
                "version": "1.1.0",
                "path": "configurations/stock.json",
                "sha256": FILE_SHA,
            }
        ],
        "plan": {"command_speed": 0.5},
        "evaluators": {
            name: {"repository": "Org/evaluator", "tag": "v1.2.3", "commit": "e" * 40}
            for name in d.V2_SHAPE["evaluators"]
        },
    }


def _required_leaves(lock, directory):
    assert _read_error(lock, directory) is None
    required = []
    for path in _node_paths(lock):
        trial = copy.deepcopy(lock)
        _delete(trial, path)
        if _read_error(trial, directory):
            required.append(path)
    return sorted(_leaves_of(required))


def _optional_pins(lock, directory):
    digests, images = [], []
    pins = [f"releases.{name}" for name in d.V2_SHAPE["releases"] if name != "reporter"]
    pins += [f"evaluators.{name}" for name in d.V2_SHAPE["evaluators"]]
    for pin in pins:
        valid = copy.deepcopy(lock)
        _set_path(valid, pin + ".digest", DIGEST)
        assert _read_error(valid, directory) is None
        invalid = copy.deepcopy(lock)
        _set_path(invalid, pin + ".digest", FILE_SHA)
        assert _read_error(invalid, directory)
        image_only = copy.deepcopy(lock)
        _set_path(image_only, pin + ".image", "ghcr.io/example/image")
        assert _read_error(image_only, directory)
        both = copy.deepcopy(lock)
        _set_path(both, pin + ".image", "ghcr.io/example/image")
        _set_path(both, pin + ".digest", DIGEST)
        assert _read_error(both, directory) is None
        digests.append(pin + ".digest")
        images.append(pin + ".image")
    return sorted(digests), sorted(images)


def _generated_v1(tmp_path, monkeypatch):
    base = tmp_path / "git"
    repo = base / "Org" / "robot.git"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    robot_lock = {
        "configurations": [{"name": "stock"}],
        "releases": {
            "verifier": {"repository": "Org/verifier", "tag": "v1.2.3", "commit": "b" * 40}
        },
    }
    (repo / "verification.lock.json").write_text(json.dumps(robot_lock))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    monkeypatch.setattr(d, "GITHUB", f"file://{base}")
    path = tmp_path / "proj" / "g1.toml"
    path.parent.mkdir()
    path.write_text(
        f'format = "{d.DEFINITION_FORMAT}"\n'
        'id = "g1-stock"\n'
        'procedure = "walk-hallway"\n'
        "[robot]\n"
        'repository = "Org/robot"\n'
        'ref = "main"\n'
        'configurations = ["stock"]\n'
    )
    d.main(["lock", str(path), "--work", str(tmp_path / "work")])
    return json.loads(d.lock_path(path).read_text())


def _pieces(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [part.value for part in node.values if isinstance(part, ast.Constant)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _pieces(node.left) + _pieces(node.right)
    return []


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _message_pieces():
    specs = {
        ROOT / "src/capability_verifier/definition.py": (
            {
                "load_definition",
                "read_lock",
                "_v2_details",
                "_pin_problems",
                "check",
                "pinned_robot_problems",
                "workflow_problems",
                "resolve",
                "prepare_evidence_inputs",
                "_load_jobs",
            },
            {"append", "SystemExit", "DefinitionError"},
        ),
        ROOT / "src/capability_verifier/evaluator_pin.py": (
            {"evaluator_pin_problems", "require_evaluator_pin"},
            {"append", "SystemExit"},
        ),
        ROOT / "src/capability_verifier/evidence_lock.py": (
            {"evidence_document", "note_overridden_robot_lock", "main"},
            {"append", "SystemExit"},
        ),
        ROOT / "src/capability_verifier/repin.py": (
            {"check", "resolve", "image_digest", "repin", "main"},
            {"append", "SystemExit", "error"},
        ),
    }
    found = []
    for path, (functions, kinds) in specs.items():
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in functions:
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call) or _call_name(sub) not in kinds or not sub.args:
                    continue
                for piece in _pieces(sub.args[0]):
                    if not isinstance(piece, str):
                        continue
                    text = " ".join(piece.split())
                    if len(text) >= 15 and text not in found:
                        found.append(text)
    return found


def test_documented_fields_match_the_code(tmp_path, monkeypatch):
    directory = tmp_path / "probe"
    directory.mkdir()
    v1 = _minimal_v1()
    v2 = _minimal_v2()
    required_v1 = _required_leaves(v1, directory)
    required_v2 = _required_leaves(v2, directory)
    digests, images = _optional_pins(v2, directory)
    world = copy.deepcopy(v2)
    _set_path(world, "world.sha256", FILE_SHA)
    assert _read_error(world, directory) is None
    invalid_world = copy.deepcopy(v2)
    _set_path(invalid_world, "world.sha256", "zz")
    assert _read_error(invalid_world, directory)

    generated = _generated_v1(tmp_path, monkeypatch)
    generated_leaves = set(_leaves_of(list(_node_paths(generated))))
    written_unchecked = sorted(generated_leaves - set(required_v1))
    for path in written_unchecked:
        for value in ("kept", ""):
            trial = copy.deepcopy(v2)
            _set_path(trial, path, value)
            assert _read_error(trial, directory) is None

    fixture_fields = set()
    for path in FIXTURES:
        fixture_fields |= _scalar_leaves(json.loads(path.read_text()))

    evidence = {"format": evidence_lock.ROBOT_LOCK_FORMAT, "scenario": "walk-hallway@0.1.0"}
    dest = directory / "verification.lock.json"
    dest.write_text(json.dumps(evidence))
    evidence_lock.note_overridden_robot_lock(dest, FILE_SHA)
    added = _scalar_leaves(json.loads(dest.read_text())) - _scalar_leaves(evidence)

    expected = {
        "caller_0_1_0": {
            "format": d.FORMAT,
            "required": required_v1,
            "written_unchecked": written_unchecked,
        },
        "caller_0_2_0": {
            "format": d.FORMAT_V2,
            "required": required_v2,
            "optional_digest": digests,
            "optional_image_requires_digest": images,
            "optional_file_sha256": ["world.sha256"],
            "accepted_unchecked": written_unchecked,
        },
        "robot_1_0_0": {
            "format": evidence_lock.ROBOT_LOCK_FORMAT,
            "evidence_fields": list(evidence_lock.EVIDENCE_FIELDS),
            "not_copied": ["$comment"],
            "caller_run_adds": sorted(added),
            "repin_optional": ["releases.<name>.expected"],
            "fixture_fields": sorted(fixture_fields),
        },
    }
    documented = _inventory(_page())
    assert documented == expected
    prose = _prose(_page())
    missing = [
        path
        for section in documented.values()
        for value in section.values()
        if isinstance(value, list)
        for path in value
        if f"`{path}`" not in prose
    ]
    assert missing == []
    assert "ref.get('expected'" in (ROOT / "src/capability_verifier/repin.py").read_text()
    assert "$comment" in FIXTURES[0].read_text()


def test_checker_messages_are_documented():
    text = _page()
    missing = [piece for piece in _message_pieces() if piece not in text]
    assert missing == []
    extras = {
        "cannot be read as YAML": ROOT / "src/capability_verifier/definition.py",
        "has no jobs": ROOT / "src/capability_verifier/definition.py",
        "no job calls": ROOT / "src/capability_verifier/definition.py",
        "must call": ROOT / "src/capability_verifier/definition.py",
        "sha256 is": ROOT / "src/capability_verifier/definition.py",
        "hand-edited commit": ROOT / "src/capability_verifier/repin.py",
        "tags are never invented": ROOT / "src/capability_verifier/repin.py",
        "NOT RELEASED:": ROOT / "src/capability_verifier/repin.py",
        "every ref is a released tag": ROOT / "src/capability_verifier/repin.py",
        "committed in the robot repository": ROOT / ".github/workflows/scenario.yml",
        "not evaluator/src": ROOT / ".github/workflows/scenario.yml",
        "the verification lock is stale": ROOT / ".github/scripts/apply_resolved_pins.sh",
        "a 40-character lowercase hexadecimal SHA": ROOT / ".github/scripts/apply_resolved_pins.sh",
        "does not match the commit for tag": ROOT / ".github/scripts/apply_resolved_pins.sh",
        "does not match releases.verifier.tag": ROOT / ".github/scripts/apply_resolved_pins.sh",
        "ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex>": (
            ROOT / ".github/scripts/apply_resolved_pins.sh"
        ),
        "ghcr.io/living-proof-labs/livingproof-capability-report image, tag <semver>, digest sha256:<64 hex>": (
            ROOT / ".github/scripts/apply_resolved_pins.sh"
        ),
        "verification-lock must be a relative path inside the caller repository": (
            ROOT / ".github/scripts/write_evidence_inputs.sh"
        ),
        "caller has no verification lock at": ROOT / ".github/scripts/write_evidence_inputs.sh",
        "EVIDENCE_SOURCE: caller": ROOT / "src/capability_verifier/evidence_lock.py",
        "overridden robot verification.lock.json sha256:": (
            ROOT / "src/capability_verifier/evidence_lock.py"
        ),
        "review it before committing": ROOT / "src/capability_verifier/definition.py",
    }
    for phrase, source in extras.items():
        assert phrase in source.read_text(), phrase
        assert phrase in text, phrase
    checkout = (ROOT / "src/capability_verifier/definition.py").read_text()
    assert '"-f"' in checkout and '"--detach"' in checkout and '"FETCH_HEAD"' in checkout
    assert "checkout -f" in text


def test_the_lock_page_is_linked_from_the_manual():
    assert "Verification locks: docs/locks.md" in (ROOT / "mkdocs.yml").read_text()
    source = (ROOT / "tools/docs.py").read_text()
    assert '"docs/locks.md": "docs/locks.md"' in source
    assert "(docs/locks.md)" in (ROOT / "README.md").read_text()
    assert "(locks.md)" in (ROOT / "docs/guides.md").read_text()
