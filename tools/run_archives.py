# SPDX-License-Identifier: Apache-2.0
"""Package existing reporter inputs/outputs. This is not a grading implementation."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

REPORTER = "livingproof-capability-report"
IMAGE = "ghcr.io/living-proof-labs/livingproof-capability-report:1.6.0"


def safe_path(name):
    if (
        not name
        or "\\" in name
        or ":" in name
        or PurePosixPath(name).is_absolute()
        or any(p in ("", ".", "..") for p in name.split("/"))
    ):
        raise ValueError(f"Unsafe archive path: {name!r}")
    return name


def token(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", value):
        raise ValueError(f"Unsafe name component: {value!r}")
    return value


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def checksums(root):
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join(f"{digest(p)}  {p.relative_to(root).as_posix()}\n" for p in files)
    )


def archive(root, destination, name):
    target = destination / (token(name) + ".zip")
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Symlink archive member")
            if path.is_file():
                out.write(path, safe_path(path.relative_to(root).as_posix()))
    return target


def extract(source, destination):
    with zipfile.ZipFile(source) as zipped:
        names = zipped.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive member")
        for info in zipped.infolist():
            safe_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Symlink archive member")
        destination.mkdir(parents=True, exist_ok=False)
        zipped.extractall(destination)
    listed = set()
    for line in (destination / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        safe_path(name)
        if name in listed or digest(destination / name) != expected:
            raise ValueError(f"Checksum mismatch or duplicate: {name}")
        listed.add(name)
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if actual != listed | {"SHA256SUMS"}:
        raise ValueError("Incomplete checksum index")


def reporter(*args, cwd=None):
    return subprocess.check_output([REPORTER, *map(str, args)], cwd=cwd, text=True)


def name_for(provenance):
    components = (
        "verifier_version",
        "demo",
        "bundle_run_id",
        "architecture",
        "ci_run",
        "ci_attempt",
    )
    return "capability-verifier-" + "-".join(token(str(provenance[k])) for k in components)


def catalog(destination):
    rows = [
        "# Verifier demonstration runs\n",
        "Recorded fixtures, scripted mock-agent and human assessments; no live robot or review.",
        "Each ZIP contains manifest.json, evidence/, report.json/html/csv/txt, provenance.json,",
        "README.md and SHA256SUMS. Unzip into a new directory and follow README.md.",
        "Each report keeps separate spec/software contexts; there is no combined grade.\n",
        "| Bundle | Manifest demonstration |",
        "| --- | --- |",
    ]
    for path in sorted(destination.glob("*.zip")):
        with zipfile.ZipFile(path) as zipped:
            provenance = json.loads(zipped.read("provenance.json"))
        rows.append(f"| [{path.name}]({path.name}) | {provenance['description']} |")
    (destination / "CATALOG.md").write_text("\n".join(rows) + "\n")
    checksums(destination)


def build(inputs, destination, version, architecture):
    token(version)
    installed = reporter("--version")
    if "version 1.6.0\n" not in installed or "Package: 1.6.0;" not in installed:
        raise ValueError(f"Expected installed released reporter 1.6.0: {installed}")
    destination.mkdir(parents=True, exist_ok=False)
    for manifest in sorted(inputs.glob("*/manifest.json")):
        bundle = json.loads(manifest.read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            if any(path.is_symlink() for path in manifest.parent.rglob("*")):
                raise ValueError("Symlink input is unsupported")
            shutil.copytree(manifest.parent, root)
            reporter("validate", "manifest.json", cwd=root)
            reporter("grade", "manifest.json", "-o", "report.json", cwd=root)
            for fmt, ext in (("html", "html"), ("csv", "csv"), ("table", "txt")):
                reporter("render", "report.json", "--format", fmt, "-o", f"report.{ext}", cwd=root)
            provenance = {
                **bundle["provenance"],
                "verifier_version": version,
                "source_commit": bundle["producer"]["commit"],
                "producer": bundle["producer"],
                "reporter_image": IMAGE,
                "reporter_version_output": installed,
                "architecture": token(architecture),
                "ci_run": token(os.environ.get("GITHUB_RUN_ID", "local")),
                "ci_attempt": token(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
                "ci_repository": os.environ.get("GITHUB_REPOSITORY"),
            }
            write_json(root / "provenance.json", provenance)
            (root / "README.md").write_text(
                f"# {provenance['demo']}\n\n{provenance['description']}\n\n"
                "Recorded fixture data, not robot safety certification. Spec, executor and fixture "
                "versions stay in manifest.json; provenance.json identifies packaging and CI.\n\n"
                "With installed livingproof-capability-report 1.6.0, from this directory:\n\n"
                "```sh\nsha256sum -c SHA256SUMS\n"
                "livingproof-capability-report validate manifest.json\n"
                "livingproof-capability-report grade manifest.json -o regenerated.json\n"
                "livingproof-capability-report render regenerated.json --format html -o regenerated.html\n"
                "livingproof-capability-report render regenerated.json --format csv -o regenerated.csv\n"
                "livingproof-capability-report render regenerated.json --format table -o regenerated.txt\n"
                "```\n\nUse new output names; the reporter refuses overwrites. Evidence paths are "
                "relative to manifest.json; JSON retains those references and HTML is offline.\n"
            )
            checksums(root)
            archive(root, destination, name_for(provenance))
    if not list(destination.glob("*.zip")):
        raise ValueError("No manifests found")
    catalog(destination)


def release(inputs, destination, version):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("Expected actual release tag vX.Y.Z")
    destination.mkdir(parents=True, exist_ok=False)
    for source in sorted(inputs.glob("*.zip")):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            extract(source, root)
            provenance = json.loads((root / "provenance.json").read_text())
            provenance["tested_verifier_version"] = provenance["verifier_version"]
            provenance["verifier_version"] = version
            write_json(root / "provenance.json", provenance)
            checksums(root)
            archive(root, destination, name_for(provenance))
    if not list(destination.glob("*.zip")):
        raise ValueError("No verified archives found")
    catalog(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "release"))
    parser.add_argument("inputs", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", default="amd64")
    args = parser.parse_args()
    if args.operation == "release":
        release(args.inputs, args.destination, args.version)
    else:
        build(args.inputs, args.destination, args.version, args.architecture)
