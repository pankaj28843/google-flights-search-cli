"""CLI shell placeholder for the red e2e bootstrap slice."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Evidence-backed Google Flights search CLI.",
    no_args_is_help=True,
)


@app.command("bootstrap-status", hidden=True)
def bootstrap_status() -> None:
    """Keep the bootstrap CLI importable before public commands are implemented."""
    typer.echo("bootstrap")


def main() -> None:
    """Run the CLI shell."""
    app()


if __name__ == "__main__":
    main()
