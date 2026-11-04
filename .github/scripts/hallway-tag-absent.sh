#!/usr/bin/env bash
# Exit 0 only when the registry explicitly says IMAGE:TAG does not exist; fail when it exists
# (version tags are never retagged) or on any other error (auth, network, rate limit).
set -uo pipefail
ref="${1:?usage: hallway-tag-absent.sh IMAGE:TAG}"
if out=$(docker buildx imagetools inspect "$ref" 2>&1); then
  echo "::error::$ref already exists; version tags are never retagged" >&2
  exit 1
fi
if grep -Eqi 'not found|manifest unknown|MANIFEST_UNKNOWN' <<<"$out"; then
  echo "$ref is absent"
  exit 0
fi
echo "::error::could not confirm $ref is absent: $out" >&2
exit 1
