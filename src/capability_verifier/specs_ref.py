# SPDX-License-Identifier: Apache-2.0
"""Which robot-capability-specs reference this verifier accepts.

The reference is a released tag. A commit recorded beside that tag is evidence of
what the tag resolved to, not the check. Tag v0.0.0 is the placeholder an
unreleased catalog writes, and it is not a release. The catalog fetch still
checks the tag (``catalog_reference_ok``).
"""

from __future__ import annotations

SPECS = "https://github.com/Living-Proof-Labs/robot-capability-specs"
# The catalog this verifier fetches and resolves thresholds from.
SPECS_TAG = "v2.2.0"
# Tags already graded by this policy. v2.2.0 is the catalog reference above.
# Older released tags stay so a stored bundle is still graded. Commits are not listed.
ACCEPTED_TAGS = frozenset({"v1.6.0", "v1.6.1", "v1.9.0", SPECS_TAG})
PLACEHOLDER_TAG = "v0.0.0"
PLACEHOLDER_REASON = "tag v0.0.0 is a placeholder, not a release"


def contract_problem(spec: dict) -> str | None:
    """Why ``spec`` is not a released contract, or None when it is.

    The repository is checked first, so tag v0.0.0 from another repository is
    an unsupported contract. A released tag is accepted with whatever commit or
    revision the bundle records. Tag v0.0.0 on this repository is the
    unreleased-catalog placeholder, and the reason says so.
    """
    if not isinstance(spec, dict) or spec.get("repository") != SPECS:
        return "Unsupported scenario contract"
    if spec.get("tag") == PLACEHOLDER_TAG:
        return PLACEHOLDER_REASON
    tag = spec.get("tag")
    if not isinstance(tag, str) or tag not in ACCEPTED_TAGS:
        return "Unsupported scenario contract"
    return None


def accepted_reference(spec: dict) -> bool:
    """True when ``spec`` names this repository and an accepted tag."""
    return contract_problem(spec) is None


def reported_contract(spec: dict) -> bool:
    """True when a k-of-n result is still one run.

    A released tag is reported, and so is the v0.0.0 placeholder, including
    when the repository is not this one. Pause results name this repository
    too; their tag is neither, so they are not a k-of-n run.
    """
    if not isinstance(spec, dict):
        return False
    tag = spec.get("tag")
    return isinstance(tag, str) and (tag in ACCEPTED_TAGS or tag == PLACEHOLDER_TAG)


def catalog_reference_ok(
    checked_out_tag: str, locked_tag: str, head: str, locked_commit: str
) -> bool:
    """True when the checkout is the locked tag.

    ``head`` and ``locked_commit`` are recorded and are not compared.
    """
    del head, locked_commit
    return checked_out_tag == locked_tag
