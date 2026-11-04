#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a hallway image index holds exactly linux/amd64 + linux/arm64 image manifests.

Reads an OCI image index (JSON) on stdin: the output of
`docker buildx imagetools create --dry-run` before anything is pushed, or of
`imagetools inspect --raw` afterwards. buildx attestation manifests (platform
unknown/unknown) are kept in the index but are not platforms, so they are ignored.
Exits non-zero, naming what it found, unless the real platforms are exactly the expected pair.
"""

import json
import sys

EXPECTED = ("linux/amd64", "linux/arm64")
ATTESTATION = "attestation-manifest"


def is_attestation(entry):
    """True for a buildx attestation manifest: unknown/unknown or annotated as one."""
    platform = entry.get("platform") or {}
    annotations = entry.get("annotations") or {}
    if annotations.get("vnd.docker.reference.type") == ATTESTATION:
        return True
    return platform.get("os") == "unknown" and platform.get("architecture") == "unknown"


def platforms(index):
    """Sorted os/arch[/variant] of the index's real (non-attestation) image manifests."""
    found = []
    for entry in index.get("manifests") or []:
        if is_attestation(entry):
            continue
        platform = entry.get("platform") or {}
        name = f"{platform.get('os')}/{platform.get('architecture')}"
        if platform.get("variant"):
            name += f"/{platform['variant']}"
        found.append(name)
    return sorted(found)


def check(index):
    """Raise ValueError unless the real platforms are exactly EXPECTED."""
    found = platforms(index)
    if tuple(found) != EXPECTED:
        raise ValueError(f"index platforms {found}, expected exactly {list(EXPECTED)}")
    return found


def main():
    try:
        index = json.load(sys.stdin)
        found = check(index)
    except (ValueError, AttributeError) as error:
        print(f"hallway index check failed: {error}", file=sys.stderr)
        return 1
    print(" + ".join(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
