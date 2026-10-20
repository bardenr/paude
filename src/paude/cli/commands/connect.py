"""Session connect command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _get_backend_instance,
    _parse_forward_ports,
    find_session_backend,
)
from paude.session_discovery import resolve_session_for_backend
from paude.subprocess_utils import called_process_stderr

if TYPE_CHECKING:
    from paude.backends.base import Backend
    from paude.backends.port_forward_utils import ForwardPort


def _connect_and_exit(
    backend_obj: Backend, name: str, forward_ports: list[ForwardPort]
) -> None:
    """Connect and exit with the attach's exit code, or a clean error.

    connect_session recreates a missing proxy, which can fail (e.g. a
    persisted --credential-domain spec that now collides); report that as
    a one-line error like `paude start` does instead of a traceback.
    """
    try:
        exit_code = backend_obj.connect_session(name, forward_ports)
        raise typer.Exit(exit_code)
    except typer.Exit:
        # A clean connect raises typer.Exit(exit_code); let it propagate
        # instead of being caught by the broad handler below and
        # reported as a failure.
        raise
    except Exception as e:
        typer.echo(f"Error connecting to session: {e}", err=True)
        if detail := called_process_stderr(e):
            typer.echo(detail, err=True)
        raise typer.Exit(1) from None


@app.command("connect")
def session_connect(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    forward_port: Annotated[
        list[str] | None,
        typer.Option(
            "--forward-port",
            help=(
                "Forward a container port to the host for this connection. "
                "Repeatable. Accepts PORT (same on both), HOST:CONTAINER, or "
                "HOST_IP:HOST:CONTAINER. Binds 127.0.0.1 by default."
            ),
        ),
    ] = None,
) -> None:
    """Attach to a running session."""
    forward_ports = _parse_forward_ports(forward_port)

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name)
        if result:
            backend, backend_obj = result
            _connect_and_exit(backend_obj, name, forward_ports)
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        session, backend_obj = _auto_select_session(
            status_filter="running",
            no_sessions_hints=[
                "No running sessions to connect to.",
                "",
                "To see all sessions:",
                "  paude list",
                "",
                "To start a session:",
                "  paude start",
            ],
            multi_hint_format="  paude connect {name}  # {backend_type}, {workspace}",
        )
        typer.echo(f"Connecting to '{session.name}' ({session.backend_type})...")
        _connect_and_exit(backend_obj, session.name, forward_ports)

    # Backend specified explicitly
    backend_instance = _get_backend_instance(backend)  # type: ignore[arg-type]
    if not name:
        name = resolve_session_for_backend(backend_instance, status_filter="running")
        if not name:
            raise typer.Exit(1)

    _connect_and_exit(backend_instance, name, forward_ports)
