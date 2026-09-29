"""Entrypoint for the factory runner. State machine lands in a later task."""

import sys


def cli() -> None:
    """Placeholder CLI. Subcommands (status/pause/resume/report) land later."""
    print("project-factory: not yet implemented", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    cli()
