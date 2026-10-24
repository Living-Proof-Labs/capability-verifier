# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_bundle", ROOT / "tools/export_bundle.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_portable_bundle_retains_distinct_contract_results(tmp_path):
    bundle = module.export(tmp_path / "bundle")
    results = {r["id"]: r for r in bundle["results"]}
    assert len(results) == 8
    assert results["v1.1.1-slow"]["original_result"]["status"] == "PASS"
    assert results["v1.2.0-slow"]["original_result"]["status"] == "FAIL"
    for tag in ("v1.1.1", "v1.2.0"):
        assert results[f"{tag}-pass"]["original_result"]["status"] == "PASS"
        assert results[f"{tag}-fail"]["original_result"]["status"] == "FAIL"
        assert (
            results[f"{tag}-insufficient"]["original_result"]["status"] == "INSUFFICIENT_EVIDENCE"
        )
