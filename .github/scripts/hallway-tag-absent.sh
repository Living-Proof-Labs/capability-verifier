#!/usr/bin/env bash
# Exit 0 only when the registry explicitly says IMAGE:TAG does not exist (MANIFEST_UNKNOWN or
# "<ref>: not found"); fail when it exists (version tags are never retagged), when docker or
# buildx is missing, or on any other error (auth, denied, repository not found, network, rate limit).
set -uo pipefail
ref="${1:?usage: hallway-tag-absent.sh IMAGE:TAG}"
if ! command -v docker >/dev/null 2>&1; then
  echo "::error::docker is not installed; cannot confirm $ref is absent" >&2
  exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
  echo "::error::docker buildx is not available; cannot confirm $ref is absent" >&2
  exit 1
fi
if out=$(docker buildx imagetools inspect "$ref" 2>&1); then
  echo "::error::$ref already exists; version tags are never retagged" >&2
  exit 1
fi
# Registry not-found only. A bare "not found" would also match "command not found" and
# "denied: repository not found", so require the error code or the exact "<ref>: not found" line.
if grep -Eq 'MANIFEST_UNKNOWN|manifest unknown' <<<"$out" \
  || grep -Fxq "ERROR: $ref: not found" <<<"$out"; then
  echo "$ref is absent"
  exit 0
fi
echo "::error::could not confirm $ref is absent: $out" >&2
exit 1
