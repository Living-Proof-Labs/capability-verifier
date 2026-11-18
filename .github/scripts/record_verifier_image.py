#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the verifier image digest this job's container was created from.

The lock names the verifier image as image:tag@digest and the jobs run in exactly that
reference, so a digest read from the lock would agree with the lock by construction. This
asks the container engine instead: the job container is inspected for the image it was
created from, and that image is inspected for the registry digests it was pulled by. The
record keeps the engine's digest next to the locked one, and `matches` says whether they
agree. It never fails the job on a difference or on an engine it cannot reach; the record
says so (`digest` null, `matches` null, `unavailable` with the reason) and the Run Integrity
`pins` check reports it.

Usage: record_verifier_image.py --locked IMAGE:TAG@sha256:DIGEST --out PATH
       [--socket /var/run/docker.sock] [--container ID]
"""

import argparse
import http.client
import json
import os
import re
import socket
import sys
from pathlib import Path

FORMAT = "livingproof-verifier-image/0.1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MOUNT_ID = re.compile(r"/containers/([0-9a-f]{64})/")


def split_reference(reference: str) -> tuple[str, str]:
    """(repository, digest) of a repository[:tag]@sha256:digest reference."""
    name, separator, digest = reference.partition("@")
    if not separator or not name or _DIGEST.fullmatch(digest) is None:
        raise ValueError(f"not a repository[:tag]@sha256:<64 hex> reference: {reference!r}")
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name[: len(name) - len(last)] + last.split(":", 1)[0]
    return name, digest


def container_ids(hostname: str, mountinfo: str) -> list[str]:
    """Candidate ids of this container: the hostname Docker sets, then the mount table's."""
    ids = [hostname] if hostname else []
    for match in _MOUNT_ID.finditer(mountinfo):
        if match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=30)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _get(path: str, endpoint: str) -> dict | None:
    connection = _Unix(path)
    try:
        connection.request("GET", endpoint)
        response = connection.getresponse()
        body = response.read()
        if response.status == 404:
            return None
        if response.status != 200:
            raise OSError(f"{endpoint}: HTTP {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def observe(socket_path: str, ids: list[str]) -> dict:
    """The engine's account of this container's image; raises OSError when it has none."""
    if not os.path.exists(socket_path):
        raise OSError(f"no container engine socket at {socket_path}")
    for identifier in ids:
        container = _get(socket_path, f"/containers/{identifier}/json")
        if container is None:
            continue
        image_id = container.get("Image")
        image = _get(socket_path, f"/images/{image_id}/json") if image_id else None
        if image is None:
            raise OSError(f"the engine does not know image {image_id} of container {identifier}")
        return {
            "container": identifier,
            "image_id": image_id,
            "repo_digests": list(image.get("RepoDigests") or []),
        }
    raise OSError(f"the engine does not know this container ({', '.join(ids) or 'no id'})")


def build_record(locked: str, seen: dict | None, problem: str | None = None) -> dict:
    repository, locked_digest = split_reference(locked)
    record = {
        "format": FORMAT,
        "image": repository,
        "locked_digest": locked_digest,
        "digest": None,
        "matches": None,
        "source": "container engine: image of this job's container",
    }
    if seen is None:
        record["unavailable"] = f"container engine unavailable: {problem}"
        return record
    record["container"] = seen["container"]
    record["image_id"] = seen["image_id"]
    record["repo_digests"] = seen["repo_digests"]
    ours = [d.split("@", 1)[1] for d in seen["repo_digests"] if d.split("@", 1)[0] == repository]
    if not ours:
        record["unavailable"] = f"the engine reports no digest of {repository} for this image"
    elif locked_digest in ours:
        record["digest"] = locked_digest
        record["matches"] = True
    else:
        record["digest"] = ours[0]
        record["matches"] = False
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--locked", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--socket", default="/var/run/docker.sock")
    parser.add_argument("--container", default=None, help="container id (default: hostname)")
    args = parser.parse_args(argv)
    try:
        split_reference(args.locked)
    except ValueError as error:
        parser.error(str(error))
    if args.container:
        ids = [args.container]
    else:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            mountinfo = ""
        ids = container_ids(socket.gethostname(), mountinfo)
    try:
        record = build_record(args.locked, observe(args.socket, ids))
    except (OSError, ValueError) as error:
        record = build_record(args.locked, None, str(error))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    if record["matches"] is True:
        print(f"Verifier image {record['digest']} is the locked digest", file=sys.stderr)
    elif record["matches"] is False:
        print(
            f"::warning::verifier image digest {record['digest']} differs from the locked "
            f"{record['locked_digest']}",
            file=sys.stderr,
        )
    else:
        print(
            f"::warning::verifier image digest not recorded: {record['unavailable']}",
            file=sys.stderr,
        )
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
