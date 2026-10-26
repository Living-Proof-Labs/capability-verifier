#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=$(git -C "$root" describe --tags --always --long)
# A checkout name is a source observation, not an assertion of a new release.
if test -n "$(git -C "$root" status --porcelain)"; then version="$version-dirty"; fi
docker run --rm --entrypoint python \
  --mount "type=bind,src=$root/tools,dst=/tools,readonly" \
  --mount "type=bind,src=$root/artifacts,dst=/artifacts" \
  -e GITHUB_RUN_ID -e GITHUB_RUN_ATTEMPT -e GITHUB_REPOSITORY -e DEMO_TEST_FAILURE \
  ghcr.io/living-proof-labs/livingproof-capability-report:1.6.0 \
  /tools/check_demo.py /artifacts/demo-inputs /artifacts/demo-runs \
  --version "$version" --architecture "$(docker version --format '{{.Server.Arch}}')" \
  --junit /artifacts/archives.xml
