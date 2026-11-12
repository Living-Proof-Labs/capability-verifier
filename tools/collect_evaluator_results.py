# SPDX-License-Identifier: Apache-2.0
"""Append evaluator result documents onto a bundle manifest.

The report job runs this with the standard library only. It is the same
function as ``capability_verifier.collect``.
"""

from capability_verifier.collect import main

if __name__ == "__main__":
    raise SystemExit(main())
