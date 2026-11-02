# SPDX-License-Identifier: Apache-2.0
"""``livingproof capability verify``: the livingproof plugin entry for definition runs.

Click comes with livingproof; this module is only imported through its entry point.
"""

import click

from capability_verifier import definition


@click.command(
    context_settings={"ignore_unknown_options": True, "help_option_names": []},
    add_help_option=False,
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def verify(args):
    """Lock or run a declarative verification (capability_verifier.definition)."""
    raise SystemExit(definition.main(list(args) or ["--help"]))
