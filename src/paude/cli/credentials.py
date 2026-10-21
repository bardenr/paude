"""Credential management command: refresh-credentials."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.base import Backend
from paude.cli.app import BackendType, app
from paude.cli.helpers import resolve_session_backend


def _list_credentials(backend_obj: Backend, name: str) -> None:
    """Print the session's credentials and how to refresh them."""
    statuses = backend_obj.list_proxy_credentials(name)
    if statuses is None:
        typer.echo(
            f"Session '{name}' has no proxy (unrestricted network); "
            "there are no proxy credentials to refresh."
        )
        return

    width = max((len(status.source) for status in statuses), default=0)
    typer.echo(f"Credentials for session '{name}' (nothing was changed):")
    for status in statuses:
        bound = "on proxy" if status.bound else "not on proxy"
        available = "available on host" if status.available else "not set on host"
        typer.echo(f"  {status.source:<{width}}  {bound:<12}  {available}")
    typer.echo("")
    typer.echo(
        f"Export the new value, then run: paude refresh-credentials {name} NAME..."
    )


@app.command("refresh-credentials")
def refresh_credentials_cmd(
    name: Annotated[str, typer.Argument(help="Session name.")],
    credentials: Annotated[
        list[str] | None,
        typer.Argument(
            help=(
                "Credentials to rebind, by host-side name (e.g. "
                "PAUDE_GITHUB_TOKEN, ANTHROPIC_API_KEY, gcp-adc). "
                "Omit to list the session's credentials."
            ),
            show_default=False,
        ),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
) -> None:
    """Rebind rotated proxy credentials from the host environment.

    Only the named credentials are rebound, using values from the current
    environment (gcp-adc reads the local gcloud ADC file). Naming one the
    proxy lacks attaches it. Allowed domains, endpoints, and all other
    credential bindings are preserved; a stopped session stays stopped.
    With no names, lists the session's credentials and changes nothing.
    """
    backend_obj = resolve_session_backend(name, backend)

    try:
        if not credentials:
            _list_credentials(backend_obj, name)
            return
        refreshed = backend_obj.refresh_proxy_credentials(name, credentials)
    except (SessionNotFoundError, ValueError, NotImplementedError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error refreshing credentials: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Refreshed proxy credentials for session '{name}': {', '.join(refreshed)}"
    )
