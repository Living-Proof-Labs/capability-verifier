# SPDX-License-Identifier: Apache-2.0
"""Assess hallway evidence and export a reporter bundle (schema 1.0.0).

    python tools/verify_hallway.py <evidence>/manifest.json <out> [--prototype]

Writes ``<out>/verification.json`` (per-trial assessments and the k/N summary) and
``<out>/bundle/`` (``manifest.json`` plus ``evidence/``) for
livingproof-capability-report. The bundle schema accepts only released catalog tags;
with an UNRELEASED hallway lock the export refuses unless ``--prototype`` is given,
which writes the placeholder tag ``v0.0.0`` and says so in every result's provenance.
"""

import argparse
import json
import os
import re
import shutil
import signal
import tempfile
import subprocess
import threading
from pathlib import Path

from capability_verifier import evaluate, hallway

ROOT = Path(__file__).resolve().parents[1]
# specs #30: human-readable mission and world text, passed through as-is into the bundle's
# top-level ``description`` when the locked catalog carries it (absent before the release
# that adds it; then nothing is written). Resolved limits go in each result's provenance.
DESCRIPTION = ROOT / ".specs-hallway/specs/scenarios/walk-hallway/description.json"


class ProducerError(RuntimeError):
    """The producer version or commit cannot be stated honestly."""


RELEASE = re.compile(r"\d+\.\d+\.\d+")
DEV = "dev"
DEV_VERSION = "0.0.0"  # the bundle schema takes only X.Y.Z; a dev build is labelled, never tagged
# 1.0.0 is what capability report 1.x validates; 1.1.0 carries the verifier's decisions.
BUNDLE_VERSIONS = ("1.0.0", "1.1.0")


def producer_commit() -> str:
    commit = os.environ.get("SOURCE_COMMIT", "").strip()
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            raise ProducerError("SOURCE_COMMIT is unset and there is no git checkout") from None
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ProducerError(f"producer commit {commit!r} is not a full 40-hex commit")
    return commit


def producer() -> tuple[str, str, str | None]:
    """(version, commit, note) for the bundle producer; never derived from git describe.

    LIVINGPROOF_VERIFIER_VERSION is required. Task sets it to X.Y.Z only when HEAD is exactly
    the release tag vX.Y.Z, and to ``dev`` otherwise. ``dev`` records version 0.0.0 (the bundle
    schema accepts only X.Y.Z) together with the commit and a DEV note in every result, so an
    unreleased commit never carries the last release's label. Unset or anything else fails.
    """
    raw = os.environ.get("LIVINGPROOF_VERIFIER_VERSION", "").strip()
    if not raw:
        raise ProducerError(
            "LIVINGPROOF_VERIFIER_VERSION is unset; set X.Y.Z for a release build "
            "(HEAD exactly at vX.Y.Z) or 'dev'"
        )
    commit = producer_commit()
    if raw == DEV:
        return (
            DEV_VERSION,
            commit,
            (
                f"DEV: capability-verifier unreleased build at {commit}; "
                f"producer version {DEV_VERSION} is a placeholder, not a release"
            ),
        )
    version = raw.removeprefix("v")
    if not RELEASE.fullmatch(version):
        raise ProducerError(f"LIVINGPROOF_VERIFIER_VERSION {raw!r} is neither X.Y.Z nor 'dev'")
    return version, commit, None


def export(
    manifest_path: Path,
    out: Path,
    prototype: bool = False,
    bundle_version: str = "1.0.0",
    description_path: Path = DESCRIPTION,
) -> dict:
    """Build into a sibling temporary directory and move it to ``out`` only on success."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} already exists")
    if bundle_version not in BUNDLE_VERSIONS:
        raise ValueError(f"bundle version {bundle_version!r} is not one of {BUNDLE_VERSIONS}")
    producer_id = producer()  # fail before any work when the label would be dishonest
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    previous = signal.getsignal(signal.SIGTERM)

    def terminated(signum, frame):
        raise SystemExit(128 + signum)  # unwinds through the cleanup below

    in_main = threading.current_thread() is threading.main_thread()
    if in_main:
        signal.signal(signal.SIGTERM, terminated)
    try:
        result = _export(
            Path(manifest_path), staging, prototype, producer_id, bundle_version, description_path
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if in_main:
            signal.signal(signal.SIGTERM, previous)
    staging.chmod(0o755)  # mkdtemp creates 0700
    staging.rename(out)
    return result


def absent_evidence(manifest_path: Path, result: dict) -> list[str]:
    """Evidence ids whose observation file is absent.

    An absent observation stays in the bundle: its result keeps the manifest's recorded path
    and digest (nothing is invented or substituted) and no file is copied, so the report reads
    it as missing and the run stays INSUFFICIENT_EVIDENCE over the planned N. A file that is
    present with another digest is not listed here: it is copied as before, and its digest
    mismatch is what the report sees.
    """
    return [
        t["evidence"]["id"]
        for t in result["trials"]
        if not (Path(manifest_path).parent / t["evidence"]["path"]).exists()
    ]


def description(path: Path = DESCRIPTION, commit: str | None = None) -> dict | None:
    """The catalog's scenario description with its path, sha256 and catalog commit."""
    if not path.is_file():
        return None
    raw = path.read_bytes()
    text = json.loads(raw)
    if text.get("format") != "livingproof-scenario-description/0.1":
        raise SystemExit(f"{path}: unknown scenario description format {text.get('format')!r}")
    source = {"path": "specs/scenarios/walk-hallway/description.json", "sha256": hallway._sha(path)}
    if commit:
        source["commit"] = commit
    return {"source": source, **text}


def _export(
    manifest_path: Path,
    out: Path,
    prototype: bool,
    producer_id,
    bundle_version: str = "1.0.0",
    description_path: Path = DESCRIPTION,
) -> dict:
    lock = json.loads((ROOT / "hallway-specs.lock.json").read_text())
    released = re.fullmatch(r"v\d+\.\d+\.\d+", lock["tag"]) is not None
    if not released and not prototype:
        raise SystemExit("hallway lock is UNRELEASED; pass --prototype to export a labelled bundle")
    result = hallway.verify(manifest_path)  # raises ManifestError for JSON, fields, plan, trials
    if result["summary"]["grade"] == hallway.UNSUPPORTED:
        # The reporter bundle has no unsupported result yet (verifier #20); export nothing.
        raise SystemExit(f"{hallway.UNSUPPORTED}: {result['summary']['reason']}; no bundle written")
    missing = absent_evidence(manifest_path, result)
    if missing:
        result["missing_evidence"] = missing
    evidence = json.loads(manifest_path.read_text())
    version, commit, dev_note = producer_id
    (out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    bundle_dir = out / "bundle"
    (bundle_dir / "evidence").mkdir(parents=True)
    note = (
        None
        if released
        else (
            f"PROTOTYPE: catalog unreleased at {lock['commit']}; tag v0.0.0 is a placeholder, not a release"
        )
    )
    spec = {
        "repository": lock["repository"],
        "tag": lock["tag"] if released else "v0.0.0",
        "commit": lock["commit"],
        "capability": lock["capability"],
        "revision": lock["revision"],
    }
    conf = evidence["configuration"]
    # The catalog commit is stated only for the locked catalog's own file.
    catalog = Path(description_path) == DESCRIPTION
    text = description(description_path, lock["commit"] if catalog else None)
    if text is not None and (
        (text["scenario"]["id"], text["scenario"]["revision"])
        != (lock["capability"], lock["revision"])
        or (text["world"]["id"], text["world"]["content_version"])
        != (evidence["world"]["id"], evidence["world"]["version"])
    ):
        raise SystemExit(
            "scenario description names "
            f"{text['scenario']['id']}@{text['scenario']['revision']} on "
            f"{text['world']['id']} {text['world']['content_version']}, not this bundle's"
        )
    results = []
    for t in result["trials"]:
        name = Path(t["evidence"]["path"]).name
        if t["evidence"]["id"] not in missing:
            shutil.copyfile(
                manifest_path.parent / t["evidence"]["path"], bundle_dir / "evidence" / name
            )
        assessments = []
        for a in t["assessments"]:
            kind = hallway.REPORTER_KIND[a["assessor"]["type"]]
            if kind is None:
                raise SystemExit("agent assessments have no reporter bundle kind yet")
            assessments.append(
                {
                    "id": a["id"],
                    "evaluator": a["evaluator"],
                    "kind": kind,
                    "version": a["version"],
                    "status": a["status"],
                    "rationale": a["rationale"],
                    "mandatory": a["mandatory"],
                    "evidence_ids": a["evidence_ids"],
                    "provenance": {"assessor": a["assessor"], "details": a["details"]},
                }
            )
        provenance = {
            "run_id": evidence.get("run_id"),
            "plan": result["plan"],
            "thresholds": result["thresholds"],
            "limits": result["limits"],
            "policy": result["policy"],
            "lock": evidence.get("lock"),
            "simulator": evidence.get("environment"),
            "summary": result["summary"],
        }
        if missing:
            provenance["missing_evidence"] = missing
        if result.get("findings"):
            # Copied, not graded. A reproducibility finding stays a test-system record.
            provenance["findings"] = result["findings"]
        if note:
            provenance["prototype"] = note
        if dev_note:
            provenance["producer"] = dev_note
        original = {"status": t["status"]}
        if "index" in t:
            original["index"] = t["index"]
            # The reporter's seed column and evidence-link match have no index field.
            # For evidence 2.0.0 this integer is the trial index.
            original["seed"] = t["index"]
        else:
            original["seed"] = t["seed"]
        results.append(
            {
                "id": t["id"],
                "spec": spec,
                "system": {
                    "id": f"{evidence['robot']['id']}/{conf['name']}",
                    "version": conf["version"],
                },
                "environment": {
                    "id": evidence["world"]["id"],
                    "version": evidence["world"]["version"],
                },
                "evidence": [
                    {
                        "id": t["evidence"]["id"],
                        "path": f"evidence/{name}",
                        "sha256": t["evidence"]["sha256"],
                    }
                ],
                "assessments": assessments,
                "provenance": provenance,
                "original_result": original,
            }
        )
    if bundle_version == "1.1.0":
        complete = {
            r["id"]: all(
                (bundle_dir / e["path"]).is_file()
                and hallway._sha(bundle_dir / e["path"]) == e["sha256"]
                for e in r["evidence"]
            )
            and bool(r["evidence"])
            for r in results
        }
        assessment = evaluate.assess_bundle(results, complete)
        for r, d in zip(results, assessment["decisions"]):
            r["decision"] = {k: v for k, v in d.items() if k != "result_id"}
            r["decision"]["policy"] = assessment["policy"]
    bundle = {
        "schema_version": bundle_version,
        "producer": {
            "id": "capability-verifier",
            "version": version,
            "commit": commit,
        },
        "results": results,
    }
    if bundle_version == "1.1.0":
        bundle["assessment"] = assessment
    if text is not None:
        # Once per bundle, not per result: one scenario revision on one world (checked above).
        bundle["description"] = text
    (bundle_dir / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("manifest", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--prototype", action="store_true")
    p.add_argument(
        "--bundle-version",
        choices=BUNDLE_VERSIONS,
        default="1.0.0",
        help="1.1.0 adds per-result decisions and per-run assessments; "
        "capability report 1.x accepts only 1.0.0",
    )
    a = p.parse_args()
    try:
        r = export(a.manifest, a.out, a.prototype, a.bundle_version)
    except hallway.ManifestError as e:
        raise SystemExit(f"invalid evidence manifest {a.manifest}: {e}") from None
    except ProducerError as e:
        raise SystemExit(f"cannot label the bundle producer: {e}") from None
    print("\n".join(hallway.summary_lines(r)))
