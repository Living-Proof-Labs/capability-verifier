#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Write the verification.lock.json that livingproof-sim-mujoco v2.0.3 evidence.py
# reads. A non-empty LOCK_REL is a caller livingproof-verification-lock/0.2.0 and
# controls the run even when the robot checkout already has verification.lock.json.
# An empty LOCK_REL leaves a robot file untouched.
#
# Usage: write_evidence_inputs.sh ROBOT_DIR CALLER_DIR LOCK_REL WORKFLOW_SRC
# Stdout is one line, source=robot or source=caller, for $GITHUB_OUTPUT.
# Stderr logs the caller decision: EVIDENCE_SOURCE: caller, then either the sha256
# of the robot file this write replaced or that the robot commit has no such file.
# COPY_CALLER_LOCK=1 also copies the caller lock to
# ROBOT_DIR/caller-verification.lock.json so repin.py --check reads it.
set -eu

robot=$1
caller=$2
lock_rel=$3
src=$4

if [ -z "$lock_rel" ]; then
  if [ -f "$robot/verification.lock.json" ]; then
    echo "source=robot"
    exit 0
  fi
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

overridden=
if [ -f "$robot/verification.lock.json" ]; then
  overridden=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$robot/verification.lock.json")
fi

run_writer() {
  PYTHONPATH="$src/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m capability_verifier.evidence_lock "$@"
}

set -- --lock "$lock_path" --out "$robot/verification.lock.json"
if [ "${COPY_CALLER_LOCK:-}" = "1" ]; then
  set -- "$@" --copy-caller "$robot/caller-verification.lock.json"
fi
if [ -n "$overridden" ]; then
  set -- "$@" --overridden-sha256 "$overridden"
fi
run_writer "$@"

echo "EVIDENCE_SOURCE: caller" >&2
if [ -n "$overridden" ]; then
  echo "overridden robot verification.lock.json sha256: $overridden" >&2
else
  echo "robot commit has no verification.lock.json" >&2
fi
echo "source=caller"
