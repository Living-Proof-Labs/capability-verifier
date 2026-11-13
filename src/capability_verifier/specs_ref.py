# SPDX-License-Identifier: Apache-2.0
"""Which robot-capability-specs reference this verifier accepts.

A graded result names a released tag, and its commit is the commit that tag
resolves to. A revision, when the bundle carries one, is that tag's scenario
revision. The catalog fetch still checks the tag (``catalog_reference_ok``).
"""

from __future__ import annotations

SPECS = "https://github.com/Living-Proof-Labs/robot-capability-specs"
# The catalog this verifier fetches and resolves thresholds from.
SPECS_TAG = "v2.2.0"
# Released tag -> (commit the tag resolves to, scenario revision at that commit).
# Older tags stay so a stored bundle is still graded. v0.0.0 is not here: it is the
# placeholder an unreleased catalog writes, and it is not a release.
RELEASED = {
    "v1.6.0": ("34681d20bf13ba0a836e03ac09869c7fb13665f1", "0.1.0"),
    "v1.6.1": ("0bdf1db3f975b044692c0b8f1ec37737ad8b889f", "0.1.0"),
    "v1.9.0": ("d677bd9770e60a6d42ea597966799e119dc7dd18", "0.1.0"),
    SPECS_TAG: ("23bec516bda29be7b26555286cbf2e8217ac5c81", "0.1.0"),
}
ACCEPTED_TAGS = frozenset(RELEASED)
PLACEHOLDER_TAG = "v0.0.0"
PLACEHOLDER_REASON = "tag v0.0.0 is a placeholder, not a release"


def contract_problem(spec: dict) -> str | None:
    """Why ``spec`` is not a released contract, or None when it is.

    The repository is checked first, so tag v0.0.0 from another repository is
    an unsupported contract. The commit must be the commit the tag resolves to.
    A revision is compared only when the bundle carries one. Tag v0.0.0 on this
    repository is the unreleased-catalog placeholder, and the reason says so.
    """
    if not isinstance(spec, dict) or spec.get("repository") != SPECS:
        return "Unsupported scenario contract"
    if spec.get("tag") == PLACEHOLDER_TAG:
        return PLACEHOLDER_REASON
    tag = spec.get("tag")
    released = RELEASED.get(tag) if isinstance(tag, str) else None
    if released is None:
        return "Unsupported scenario contract"
    commit, revision = released
    if spec.get("commit") != commit:
        return f"{tag} resolves to {commit}, not {spec.get('commit')}"
    if "revision" in spec and spec.get("revision") != revision:
        return f"{tag} is revision {revision}, not {spec.get('revision')}"
    return None


def accepted_reference(spec: dict) -> bool:
    """True when ``spec`` matches a released tag's commit and any carried revision."""
    return contract_problem(spec) is None


def reported_contract(spec: dict) -> bool:
    """True when a k-of-n result is still one run.

    A released tag and the v0.0.0 placeholder are reported even when the commit
    or revision does not match. Pause results name this repository too; their
    tag is neither, so they are not a k-of-n run.
    """
    if not isinstance(spec, dict):
        return False
    tag = spec.get("tag")
    return isinstance(tag, str) and (tag in RELEASED or tag == PLACEHOLDER_TAG)


def catalog_reference_ok(
    checked_out_tag: str, locked_tag: str, head: str, locked_commit: str
) -> bool:
    """True when the checkout is the locked tag.

    ``head`` and ``locked_commit`` are recorded and are not compared.
    """
    del head, locked_commit
    return checked_out_tag == locked_tag
