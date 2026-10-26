# SPDX-License-Identifier: Apache-2.0
"""Exercise ZIP consumption with the installed reporter and publish individual JUnit cases."""

import argparse
import json
import os
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from run_archives import build, extract, release, reporter

EXPECTED = {
    "compatibility-matrix": ["PASS", "PASS", "INCOMPATIBLE", "FAIL"],
    "baseline": ["PASS"],
    "strict": ["FAIL"],
    "negative": ["FAIL", "FAIL"],
    "missing-measurement": ["INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
}


def verify_archive(source):
    with tempfile.TemporaryDirectory(prefix="extracted demo ") as temporary:
        root = Path(temporary) / "unrelated-location"
        extract(source, root)
        manifest = json.loads((root / "manifest.json").read_text())
        saved = json.loads((root / "report.json").read_text())
        provenance = json.loads((root / "provenance.json").read_text())
        assert saved["source_bundle"] == manifest
        assert provenance["bundle_run_id"] in source.name
        assert provenance["verifier_version"] in source.name
        assert provenance["source_commit"] == manifest["producer"]["commit"]
        assert {d["status"] for d in saved["decisions"]} == set(EXPECTED[provenance["demo"]])
        assert len(saved["decisions"]) == len(EXPECTED[provenance["demo"]])
        for result in manifest["results"]:
            assert result["provenance"]["execution_id"] != provenance["bundle_run_id"]
            for evidence in result["evidence"]:
                assert (root / evidence["path"]).is_file()
        reporter("validate", "manifest.json", cwd=root)
        reporter("grade", "manifest.json", "-o", "regenerated.json", cwd=root)
        assert json.loads((root / "regenerated.json").read_text()) == saved
        for fmt, ext in (("html", "html"), ("csv", "csv"), ("table", "txt")):
            reporter("render", "regenerated.json", "--format", fmt, "-o", f"new.{ext}", cwd=root)
            assert (root / f"new.{ext}").read_bytes() == (root / f"report.{ext}").read_bytes()
        if provenance["demo"] == "compatibility-matrix":
            assert len(saved["groups"]) == 4
            assert {d["result_id"]: d["status"] for d in saved["decisions"]} == {
                "v1.1.1-v1.0.1-pass": "PASS",
                "v1.1.1-v1.1.0-slow": "PASS",
                "v1.2.0-v1.0.1-pass": "INCOMPATIBLE",
                "v1.2.0-v1.1.0-slow": "FAIL",
            }


class PublishedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xml = ET.Element("testsuite", name="Installed reporter archive consumption")
        self.started = time.perf_counter()

    def startTest(self, test):
        super().startTest(test)
        self.case_started = time.perf_counter()
        self.case = ET.SubElement(
            self.xml,
            "testcase",
            name=test.id(),
            classname="demo_archives",
            file="tools/check_demo.py",
        )

    def stopTest(self, test):
        self.case.set("time", f"{time.perf_counter() - self.case_started:.6f}")
        super().stopTest(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        ET.SubElement(self.case, "failure", message=str(err[1])).text = self._exc_info_to_string(
            err, test
        )

    def addError(self, test, err):
        super().addError(test, err)
        ET.SubElement(self.case, "error", message=str(err[1])).text = self._exc_info_to_string(
            err, test
        )


def check(inputs, destination, version, architecture, junit):
    suite = unittest.TestSuite()

    def case(name, function):
        test = unittest.FunctionTestCase(function, description=name)
        test.id = lambda: name
        suite.addTest(test)

    case(
        "build_with_released_reporter_1_6_0",
        lambda: build(inputs, destination, version, architecture),
    )
    for demo in EXPECTED:

        def consume(demo=demo):
            matches = list(destination.glob(f"*-{demo}-*.zip"))
            assert len(matches) == 1
            verify_archive(matches[0])

        case(f"extract_and_regenerate_{demo}", consume)

    def promotion():
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "release"
            release(destination, target, "v9.8.7")
            assert len(list(target.glob("*.zip"))) == 5
            for source in target.glob("*.zip"):
                verify_archive(source)
                original = next(destination.glob(source.name.replace("v9.8.7", version, 1)))
                with zipfile.ZipFile(original) as before, zipfile.ZipFile(source) as after:
                    for name in before.namelist():
                        if name not in ("provenance.json", "SHA256SUMS"):
                            assert before.read(name) == after.read(name)

    case("release_tag_names_preserve_tested_reports_and_execution_identity", promotion)

    def attempts():
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("GITHUB_RUN_ATTEMPT")
            try:
                os.environ["GITHUB_RUN_ATTEMPT"] = str(int(previous or "1") + 1)
                target = Path(temporary) / "retry"
                build(inputs, target, version, architecture)
                assert not (
                    {p.name for p in destination.glob("*.zip")}
                    & {p.name for p in target.glob("*.zip")}
                )

                def identities(directory):
                    values = set()
                    for path in directory.glob("*.zip"):
                        with zipfile.ZipFile(path) as archive:
                            values.add(json.loads(archive.read("provenance.json"))["bundle_run_id"])
                    return values

                assert identities(target) == identities(destination)
            finally:
                if previous is None:
                    os.environ.pop("GITHUB_RUN_ATTEMPT", None)
                else:
                    os.environ["GITHUB_RUN_ATTEMPT"] = previous

    case("ci_attempt_changes_names_not_execution_identity", attempts)
    if os.environ.get("DEMO_TEST_FAILURE") == "1":

        def deliberate_failure():
            raise AssertionError("Deliberate publisher verification fixture")

        case("deliberate_publisher_failure", deliberate_failure)
    result = unittest.TextTestRunner(verbosity=2, resultclass=PublishedResult).run(suite)
    result.xml.set("time", f"{time.perf_counter() - result.started:.6f}")
    result.xml.set("skipped", "0")
    result.xml.set("tests", str(result.testsRun))
    result.xml.set("failures", str(len(result.failures)))
    result.xml.set("errors", str(len(result.errors)))
    junit.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(result.xml).write(junit, encoding="utf-8", xml_declaration=True)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", default="amd64")
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    check(args.inputs, args.destination, args.version, args.architecture, args.junit)
