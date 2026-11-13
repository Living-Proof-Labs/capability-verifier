# SPDX-License-Identifier: Apache-2.0
"""Which robot-capability-specs reference this verifier accepts.

The reference is a released tag. A commit recorded beside that tag is evidence of
what the tag resolved to, not the check.
"""

from __future__ import annotations

SPECS = "https://github.com/Living-Proof-Labs/robot-capability-specs"
# The catalog this verifier fetches and resolves thresholds from.
SPECS_TAG = "v2.2.0"
# Tags already graded by this policy. v2.2.0 is the catalog reference above.
# Older released tags stay so a stored bundle is still graded. Commits are not listed.
ACCEPTED_TAGS = frozenset({"v1.6.0", "v1.6.1", "v1.9.0", SPECS_TAG})


def accepted_reference(spec: dict) -> bool:
    """True when ``spec`` names this repository and an accepted tag.

    ``spec["commit"]`` is ignored. A wrong tag is rejected even when its commit
    is one this policy used to list.
    """
    if not isinstance(spec, dict):
        return False
    if spec.get("repository") != SPECS:
        return False
    tag = spec.get("tag")
    return isinstance(tag, str) and tag in ACCEPTED_TAGS


def catalog_reference_ok(
    checked_out_tag: str, locked_tag: str, head: str, locked_commit: str
) -> bool:
    """True when the checkout is the locked tag.

    ``head`` and ``locked_commit`` are recorded and are not compared.
    """
    del head, locked_commit
    return checked_out_tag == locked_tag
