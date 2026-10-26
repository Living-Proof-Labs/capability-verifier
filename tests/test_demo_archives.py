# SPDX-License-Identifier: Apache-2.0
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_demo import export_demo  # noqa: E402
from run_archives import archive, checksums, extract, name_for, safe_path  # noqa: E402


def test_repeated_executions_have_unique_stable_identities(tmp_path):
    runs = []
    for name in ("first", "second"):
        path = tmp_path / name
        export_demo(path)
        manifests = [json.loads(p.read_text()) for p in path.glob("*/manifest.json")]
        assert len(manifests) == 5
        ids = {m["provenance"]["bundle_run_id"] for m in manifests}
        assert len(ids) == 5
        runs.append(ids)
        related = {}
        for manifest in manifests:
            for result in manifest["results"]:
                execution = result["provenance"]["execution_id"]
                assert related.setdefault(result["id"], execution) == execution
                assert result["spec"]["tag"] in ("v1.1.1", "v1.2.0")
                assert result["provenance"]["executor"]["tag"] in ("v1.0.1", "v1.1.0")
                assert result["provenance"]["run_id"] == manifest["provenance"]["collection_id"]
        assert len(set(related.values())) == 8
    assert runs[0].isdisjoint(runs[1])


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../b", "C:/drive", "a\\b", "a//b", "./a"]
)
def test_rejects_unsafe_archive_paths(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        safe_path(name)
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as zipped:
        zipped.writestr(name, "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        extract(source, tmp_path / "unpacked")
    assert not (tmp_path / "unpacked").exists()


def test_rejects_symlinks_and_duplicate_members(tmp_path):
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as zipped:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "outside")
    with pytest.raises(ValueError, match="Symlink"):
        extract(source, tmp_path / "link")
    with zipfile.ZipFile(source, "w") as zipped:
        zipped.writestr("same", "one")
        with pytest.warns(UserWarning):
            zipped.writestr("same", "two")
    with pytest.raises(ValueError, match="Duplicate"):
        extract(source, tmp_path / "duplicate")


def test_checksum_detects_corruption_and_unindexed_files(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "evidence.json").write_text("original")
    checksums(root)
    source = archive(root, tmp_path, "valid")
    extract(source, tmp_path / "valid-extracted")
    (root / "evidence.json").write_text("tampered")
    source = archive(root, tmp_path, "corrupt")
    with pytest.raises(ValueError, match="Checksum"):
        extract(source, tmp_path / "corrupt-extracted")
    checksums(root)
    (root / "unexpected").write_text("not indexed")
    source = archive(root, tmp_path, "extra")
    with pytest.raises(ValueError, match="Incomplete"):
        extract(source, tmp_path / "extra-extracted")


def test_names_include_version_run_architecture_and_attempt():
    metadata = dict(
        verifier_version="v1.2.0",
        demo="baseline",
        bundle_run_id="execution-123",
        architecture="amd64",
        ci_run="987",
        ci_attempt="1",
    )
    assert name_for(metadata) == "capability-verifier-v1.2.0-baseline-execution-123-amd64-987-1"
    assert name_for(metadata) != name_for({**metadata, "ci_attempt": "2"})
    with pytest.raises(ValueError):
        name_for({**metadata, "verifier_version": "../bad"})
