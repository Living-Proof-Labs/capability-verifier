#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Write the verification.lock.json that livingproof-sim-mujoco v2.0.3 evidence.py
# reads, from a caller livingproof-verification-lock/0.2.0, when the robot
# checkout has no verification.lock.json. A robot file is left untouched.
#
# Usage: write_evidence_inputs.sh ROBOT_DIR CALLER_DIR LOCK_REL WORKFLOW_SRC
# Stdout is one line, source=robot or source=caller, for $GITHUB_OUTPUT.
# COPY_CALLER_LOCK=1 also copies the caller lock to
# ROBOT_DIR/caller-verification.lock.json so repin.py --check reads it.
set -eu

robot=$1
caller=$2
lock_rel=$3
src=$4

if [ -f "$robot/verification.lock.json" ]; then
  echo "source=robot"
  exit 0
fi

if [ -z "$lock_rel" ]; then
  echo "the robot commit has no verification.lock.json; pass verification-lock (a path in the caller repository)" >&2
  exit 1
fi

case "$lock_rel" in
  /*|*..*)
    echo "verification-lock must be a relative path inside the caller repository" >&2
    exit 1
    ;;
esac

lock_path=$caller/$lock_rel
if [ ! -f "$lock_path" ]; then
  echo "caller has no verification lock at $lock_rel" >&2
  exit 1
fi

run_writer() {
  PYTHONPATH="$src/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m capability_verifier.evidence_lock "$@"
}

if [ "${COPY_CALLER_LOCK:-}" = "1" ]; then
  run_writer --lock "$lock_path" --out "$robot/verification.lock.json" \
    --copy-caller "$robot/caller-verification.lock.json"
else
  run_writer --lock "$lock_path" --out "$robot/verification.lock.json"
fi

echo "source=caller"
