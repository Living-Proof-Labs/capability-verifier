# SPDX-License-Identifier: Apache-2.0
"""Extra lock-format documentation checks from the #95 review.

tests/test_lock_format_docs.py rebuilds the field inventory and checks that
checker message text appears on the page. Those tests still pass if a field
table describes the wrong type, because the later message list can keep the
code's wording. These checks lock the table and spelling rules to the
code, and run the documented type rejections against real locks.
"""

import copy
import json
from pathlib import Path

from capability_verifier import definition as d

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "docs" / "locks.md"
DIGEST = "sha256:" + "ab" * 32
FILE_SHA = "cd" * 32


def _page():
    return PAGE.read_text()


def _section(text, start, end):
    begin = text.index(start)
    stop = text.index(end, begin + 1)
    return text[begin:stop]


def _row(table, field):
    marker = f"| `{field}` |"
    start = table.index(marker)
    end = table.index("\n", start)
    return table[start:end]


def _minimal_v2():
    pin = {"repository": "Org/x", "tag": "v1.2.3", "commit": "a" * 40}
    reporter = {**pin, "image": "ghcr.io/example/reporter", "digest": DIGEST}
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
        "releases": {
            "specs": dict(pin),
            "runner": dict(pin),
            "verifier": dict(pin),
            "reporter": reporter,
        },
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
        "evaluators": {"livingproof-evaluator-scenario": dict(pin)},
    }


def _read_error(lock, directory):
    path = directory / "lock.json"
    path.write_text(json.dumps(lock))
    try:
        d.read_lock(path)
    except d.DefinitionError as exc:
        return str(exc)
    return None


def test_the_zero_two_field_table_states_the_type_rules():
    """The 0.2.0 table, not just the later message list, states each type rule."""
    table = _section(_page(), "## Caller verification lock 0.2.0", "## Field inventory")
    rules = {
        "plan.command_speed": ("JSON number", "boolean"),
        "configurations[].sha256": ("64 lowercase hexadecimal",),
        "configurations[].path": ("relative path", ".."),
        "configurations[].version": ("X.Y.Z",),
        "robot.adapter": ("[tool.livingproof]",),
        "evaluators.livingproof-evaluator-scenario.tag": ("vX.Y.Z", "resolve"),
        "world.sha256": ("Optional", "64 lowercase hexadecimal"),
        "releases.reporter.digest": ("sha256:",),
        "robot.commit": ("40 lowercase hexadecimal",),
        "scenario": ("name@version",),
    }
    missing = []
    for field, needles in rules.items():
        row = _row(table, field)
        missing.extend(f"{field}: {needle}" for needle in needles if needle not in row)
    assert missing == []
    assert "image alone fails" in table or "image alone fails" in _section(
        _page(), "## Caller verification lock 0.2.0", "## Field inventory"
    )
    after = _section(_page(), "## Caller verification lock 0.2.0", "## Field inventory")
    assert "{label}.digest must be a sha256 digest" in after


def test_the_spellings_section_names_the_patterns():
    section = _section(_page(), "## Spellings", "## Robot verification lock")
    assert "40 lowercase hexadecimal characters" in section
    assert "64 lowercase hexadecimal characters, with no prefix" in section
    assert "`sha256:` and then 64 lowercase hexadecimal characters" in section
    assert "`v` and then `X.Y.Z`" in section
    assert "`OWNER/NAME`" in section
    assert "`name@version`" in section
    assert "An uppercase hash or commit fails the pattern check" in section
    assert "A tag without the leading `v` fails" in section
    assert d.SHA.fullmatch("a" * 40) and not d.SHA.fullmatch("A" * 40)
    assert d.FILE_SHA.fullmatch("ab" * 32) and not d.FILE_SHA.fullmatch("sha256:" + "ab" * 32)
    from capability_verifier.repin import DIGEST as IMAGE
    from capability_verifier.repin import TAG

    assert IMAGE.fullmatch("sha256:" + "ab" * 32) and not IMAGE.fullmatch("ab" * 32)
    assert TAG.fullmatch("v1.2.3") and not TAG.fullmatch("1.2.3")


def test_file_and_digest_spellings_are_not_interchangeable(tmp_path):
    lock = _minimal_v2()
    assert _read_error(lock, tmp_path) is None
    mixed = copy.deepcopy(lock)
    mixed["configurations"][0]["sha256"] = DIGEST
    err = _read_error(mixed, tmp_path)
    assert err is not None and "configurations[0].sha256 must be 64 hex characters" in err
    mixed = copy.deepcopy(lock)
    mixed["releases"]["reporter"]["digest"] = FILE_SHA
    err = _read_error(mixed, tmp_path)
    assert err is not None and "releases.reporter.digest must be a sha256 digest" in err
    mixed = copy.deepcopy(lock)
    mixed["plan"]["command_speed"] = True
    err = _read_error(mixed, tmp_path)
    assert err is not None and "plan.command_speed must be a number" in err
    mixed = copy.deepcopy(lock)
    mixed["releases"]["verifier"]["commit"] = "A" * 40
    err = _read_error(mixed, tmp_path)
    assert err is not None and "releases.verifier.commit must be a full commit sha" in err
    mixed = copy.deepcopy(lock)
    mixed["evaluators"]["livingproof-evaluator-scenario"]["tag"] = "1.2.3"
    err = _read_error(mixed, tmp_path)
    assert err is not None and "tag must be a vX.Y.Z release tag" in err


def test_the_evaluator_tag_and_robot_path_sections_name_the_rules():
    text = _page()
    tag = _section(text, "## Evaluator tag", "## A caller lock committed in the robot repository")
    assert "pyproject.toml" in tag and "uv.lock" in tag
    assert "git ls-remote" in tag
    assert "tag is required" in tag
    assert "the verifier requests" in tag and "not the locked" in tag
    assert "the verifier resolves" in tag
    assert "cannot resolve tag" in tag
    assert "not the locked commit" in tag
    assert "empty tag fails earlier" in tag
    assert "uv sync --locked" in tag
    assert "evaluator/src" in tag
    assert "livingproof_evaluator_scenario imported from" in tag
    path = _section(
        text, "## A caller lock committed in the robot repository", "## Recorded hashes"
    )
    assert (
        "caller-verification.lock.json is committed in the robot repository; "
        "pass verification-lock or remove that file"
    ) in path
    assert "before `jq`" in path
    assert "When `EVIDENCE_SOURCE` is `caller`" in path
    assert "When `EVIDENCE_SOURCE` is anything else, including empty and `robot`" in path
