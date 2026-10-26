# SPDX-License-Identifier: Apache-2.0
"""Subprocess adapter: import only the selected released executor's code."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

source, contract_json, fixture, tag = sys.argv[1:]
source = Path(source)
contract = json.loads(contract_json)
sys.path.insert(0, str(source / "src"))
if tag == "v1.0.1":
    from capability_verifier.core import verify

    result = verify(source / "fixtures" / f"{fixture}.json", contract)
else:
    spec = importlib.util.spec_from_file_location(
        "released_export", source / "tools/export_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as temporary:
        bundle = module.export(Path(temporary) / "bundle")
    result = next(
        r["original_result"] for r in bundle["results"] if r["id"] == f"{contract['tag']}-{fixture}"
    )
print(json.dumps(result))
