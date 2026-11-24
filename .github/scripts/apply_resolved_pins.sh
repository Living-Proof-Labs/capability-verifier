#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# shellcheck disable=SC2016 # jq filters are single-quoted so $c and $n stay jq variables
# Decide the four resolved pins and, when an input supplies a value, write it
# into the lock files in the current directory before repin and the evidence copy.
#
# Empty RESOLVED_* keeps today's jq output, including a missing commit printed
# as null and a reporter image with empty components printed as :tag@.
# An empty component is absent: it is not a present value that can disagree.
# A supplied image semver must match the lock tag with one leading v removed.
# A supplied commit must match the commit already recorded for that tag.
set -euo pipefail

lock=verification.lock.json
pin=${RESOLVED_PIN_FILE:-$lock}

verifier_commit_raw=$(jq -r '.releases.verifier.commit' "$lock")
verifier_image_raw=$(jq -r '.releases.verifier | if .image then .image + ":" + (.tag | ltrimstr("v")) + "@" + .digest else "" end' "$lock")
reporter_image_raw=$(jq -r '.releases.reporter.image + ":" + (.releases.reporter.tag | ltrimstr("v")) + "@" + .releases.reporter.digest' "$lock")
evaluator_commit_raw=$(jq -r '(.evaluators // {})["livingproof-evaluator-scenario"].commit // empty' "$pin")

commit_pattern='^[0-9a-f]{40}$'

# tag-commit: a supplied commit must be the commit recorded for that tag.
# image-semver: a supplied image tag must be the lock tag without one leading v.
# empty-field-absent: null, missing and "" do not count as a different value.
# record-resolved: inputs are written into the lock before repin and evidence.

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

# The image patterns live in capability_verifier.resolved_pins. This script does not copy them.
# A failed load or an empty pattern is a hard stop: an empty regex matches every ref.
script_dir=$(cd "$(dirname "$0")" && pwd)
src_dir=$(cd "$script_dir/../../src" && pwd)
load_error="could not load the image patterns from capability_verifier.resolved_pins"
if ! image_patterns=$(
  PYTHONPATH="$src_dir${PYTHONPATH:+:$PYTHONPATH}" python3 -c '
from capability_verifier.resolved_pins import VERIFIER_IMAGE, REPORTER_IMAGE
print(VERIFIER_IMAGE.pattern)
print(REPORTER_IMAGE.pattern)
'
); then
  fail "$load_error"
fi
verifier_image_pattern=$(printf '%s\n' "$image_patterns" | sed -n '1p')
reporter_image_pattern=$(printf '%s\n' "$image_patterns" | sed -n '2p')
if [ -z "$verifier_image_pattern" ] || [ -z "$reporter_image_pattern" ]; then
  fail "$load_error"
fi

field() {
  jq -r "$1" "$2"
}

parse_image() {
  local ref=$1
  parsed_digest=${ref##*@}
  local rest=${ref%"@$parsed_digest"}
  parsed_semver=${rest##*:}
  parsed_name=${rest%:"$parsed_semver"}
}

semver_of_tag() {
  local tag=$1
  printf '%s\n' "${tag#v}"
}

resolve_commit() {
  local raw=$1 given=$2 tag=$3 label=$4
  local recorded
  recorded=$(field "$5" "$6")
  if [ -z "$given" ]; then
    pin_value=$raw
    pin_source=lock
    return 0
  fi
  if [[ ! "$given" =~ $commit_pattern ]]; then
    fail "$label must be a 40-character lowercase hexadecimal SHA (got $(printf '%q' "$given"))"
  fi
  # livingproof-evaluator-scenario has no default. v1.5.0 belongs to
  # livingproof-evaluator-run, the run-integrity pin.
  if [ -z "$tag" ]; then
    fail "$label: lock has no tag"
  fi
  if [ -n "$recorded" ] && [ "$recorded" != "$given" ]; then
    fail "$label $given does not match the commit for tag ${tag:-<missing>} ($recorded)"
  fi
  pin_value=$given
  pin_source=input
}

resolve_image() {
  local raw=$1 given=$2 name_expr=$3 digest_expr=$4 tag_expr=$5 file=$6 label=$7 requirement=$8
  local recorded_name recorded_digest tag want
  if [ -z "$given" ]; then
    pin_value=$raw
    pin_source=lock
    return 0
  fi
  if [[ ! "$given" =~ $pattern ]]; then
    fail "$label must be $requirement (got $(printf '%q' "$given"))"
  fi
  parse_image "$given"
  tag=$(field "$tag_expr" "$file")
  if [ -z "$tag" ]; then
    fail "$label: lock has no tag"
  fi
  want=$(semver_of_tag "$tag")
  if [ "$parsed_semver" != "$want" ]; then
    if [ "$label" = "verifier-image" ]; then
      fail "verifier-image semver $parsed_semver does not match releases.verifier.tag ${tag:-<missing>}"
    fi
    fail "reporter-image semver $parsed_semver does not match releases.reporter.tag ${tag:-<missing>}"
  fi
  recorded_name=$(field "$name_expr" "$file")
  recorded_digest=$(field "$digest_expr" "$file")
  if [ -n "$recorded_name" ] && [ "$recorded_name" != "$parsed_name" ]; then
    fail "$label $given does not match releases.${label%-image}.image ($recorded_name); the verification lock is stale"
  fi
  if [ -n "$recorded_digest" ] && [ "$recorded_digest" != "$parsed_digest" ]; then
    fail "$label $given does not match releases.${label%-image}.digest ($recorded_digest); the verification lock is stale"
  fi
  pin_value=$given
  pin_source=input
}

pattern=$verifier_image_pattern
resolve_commit "$verifier_commit_raw" "${RESOLVED_VERIFIER_COMMIT:-}" \
  "$(field '.releases.verifier.tag // empty' "$lock")" \
  "verifier-commit" '.releases.verifier.commit // empty' "$lock"
verifier_commit=$pin_value
verifier_commit_source=$pin_source

pattern=$verifier_image_pattern
resolve_image "$verifier_image_raw" "${RESOLVED_VERIFIER_IMAGE:-}" \
  '.releases.verifier.image // empty' '.releases.verifier.digest // empty' \
  '.releases.verifier.tag // empty' "$lock" \
  "verifier-image" \
  "ghcr.io/living-proof-labs/capability-verifier-hallway:<semver>@sha256:<64 hex>"
verifier_image=$pin_value
verifier_image_source=$pin_source

pattern=$reporter_image_pattern
resolve_image "$reporter_image_raw" "${RESOLVED_REPORTER_IMAGE:-}" \
  '.releases.reporter.image // empty' '.releases.reporter.digest // empty' \
  '.releases.reporter.tag // empty' "$lock" \
  "reporter-image" \
  "ghcr.io/living-proof-labs/livingproof-capability-report image, tag <semver>, digest sha256:<64 hex>"
reporter_image=$pin_value
reporter_image_source=$pin_source

resolve_commit "$evaluator_commit_raw" "${RESOLVED_EVALUATOR_COMMIT:-}" \
  "$(field '(.evaluators // {})["livingproof-evaluator-scenario"].tag // empty' "$pin")" \
  "evaluator-commit" '(.evaluators // {})["livingproof-evaluator-scenario"].commit // empty' "$pin"
evaluator_commit=$pin_value
evaluator_commit_source=$pin_source

write_json() {
  local file=$1
  local filter=$2
  shift 2
  local tmp
  tmp=$(mktemp)
  jq "$@" "$filter" "$file" > "$tmp"
  mv "$tmp" "$file"
}

if [ "$verifier_commit_source" = "input" ]; then
  write_json "$lock" '.releases.verifier.commit = $c' --arg c "$verifier_commit"
  if [ -f caller-verification.lock.json ]; then
    write_json caller-verification.lock.json '.releases.verifier.commit = $c' --arg c "$verifier_commit"
  fi
fi
if [ "$verifier_image_source" = "input" ]; then
  parse_image "$verifier_image"
  write_json "$lock" \
    '.releases.verifier = (.releases.verifier // {}) | .releases.verifier.image = $n | .releases.verifier.digest = $d' \
    --arg n "$parsed_name" --arg d "$parsed_digest"
  if [ -f caller-verification.lock.json ]; then
    write_json caller-verification.lock.json \
      '.releases.verifier = (.releases.verifier // {}) | .releases.verifier.image = $n | .releases.verifier.digest = $d' \
      --arg n "$parsed_name" --arg d "$parsed_digest"
  fi
fi
if [ "$reporter_image_source" = "input" ]; then
  parse_image "$reporter_image"
  write_json "$lock" \
    '.releases.reporter = (.releases.reporter // {}) | .releases.reporter.image = $n | .releases.reporter.digest = $d' \
    --arg n "$parsed_name" --arg d "$parsed_digest"
  if [ -f caller-verification.lock.json ]; then
    write_json caller-verification.lock.json \
      '.releases.reporter = (.releases.reporter // {}) | .releases.reporter.image = $n | .releases.reporter.digest = $d' \
      --arg n "$parsed_name" --arg d "$parsed_digest"
  fi
fi
if [ "$evaluator_commit_source" = "input" ]; then
  write_json "$pin" '
    .evaluators = (.evaluators // {})
    | .evaluators["livingproof-evaluator-scenario"] = (.evaluators["livingproof-evaluator-scenario"] // {})
    | .evaluators["livingproof-evaluator-scenario"].commit = $c
  ' --arg c "$evaluator_commit"
fi

printf 'verifier-commit=%s\n' "$verifier_commit"
printf 'verifier-image=%s\n' "$verifier_image"
printf 'reporter-image=%s\n' "$reporter_image"
printf 'evaluator-commit=%s\n' "$evaluator_commit"
printf 'verifier-commit-source=%s\n' "$verifier_commit_source"
printf 'verifier-image-source=%s\n' "$verifier_image_source"
printf 'reporter-image-source=%s\n' "$reporter_image_source"
printf 'evaluator-commit-source=%s\n' "$evaluator_commit_source"
