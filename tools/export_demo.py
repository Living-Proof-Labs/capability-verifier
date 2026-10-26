# SPDX-License-Identifier: Apache-2.0
"""Compose demonstrations from released executions; never grade here."""

import argparse
import copy
import json
import shutil
import uuid
from pathlib import Path

from export_matrix import export

DESCRIPTIONS = {
    "compatibility-matrix": "Four spec/executor coordinates: PASS, PASS, INCOMPATIBLE and FAIL.",
    "baseline": "Released original executor: 120 ms passes the original 500 ms contract.",
    "strict": "Released newer executor: 300 ms fails the strict 250 ms contract.",
    "negative": "Mandatory inhibition failure under both contracts; retained failure evidence.",
    "missing-measurement": "Elapsed time absent under both contracts; insufficient evidence.",
}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def export_demo(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    collection_id = str(uuid.uuid4())
    matrix = export(destination / "source", run_id=collection_id)
    controls = json.loads((destination / "source/controls.json").read_text())
    selections = {
        "compatibility-matrix": matrix["results"],
        "baseline": matrix["results"][:1],
        "strict": matrix["results"][-1:],
        "negative": [r for r in controls["results"] if r["id"].endswith("-fail")],
        "missing-measurement": [
            r for r in controls["results"] if r["id"].endswith("-insufficient")
        ],
    }
    for name, results in selections.items():
        root = destination / name
        root.mkdir()
        bundle = copy.deepcopy(matrix)
        bundle["results"] = copy.deepcopy(results)
        bundle["provenance"] = {
            "collection_id": collection_id,
            "bundle_run_id": str(uuid.uuid5(uuid.UUID(collection_id), name)),
            "demo": name,
            "description": DESCRIPTIONS[name],
            "fixture": True,
        }
        for result in bundle["results"]:
            result["provenance"]["execution_id"] = str(
                uuid.uuid5(uuid.UUID(collection_id), result["id"])
            )
            for evidence in result["evidence"]:
                target = root / evidence["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(destination / "source" / evidence["path"], target)
        write_json(root / "manifest.json", bundle)
    shutil.rmtree(destination / "source")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    export_demo(parser.parse_args().destination)
