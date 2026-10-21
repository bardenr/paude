"""Rollback-safe proxy replacement used by ProxyRunner.swap_session_proxy."""

from __future__ import annotations

from dataclasses import dataclass

from paude.container.runner import ContainerRunner

# Engine states in which a container is definitely not using its proxy.
_STOPPED_STATES = frozenset(
    {"exited", "stopped", "created", "configured", "dead", "removing"}
)


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


@dataclass
class ProxySwap:
    """A started candidate proxy whose retained predecessor can be restored."""

    runner: ContainerRunner
    name: str
    backup_name: str
    network: str
    ip: str | None
    old_running: bool
    keep_running_for: str | None = None
    old_renamed: bool = False
    old_disconnected: bool = False
    candidate_created: bool = False

    def commit(self) -> None:
        """Remove the retained predecessor after all update state is durable.

        A predecessor that was stopped (e.g. the session is stopped) leaves
        the candidate stopped too, so an update never revives a proxy of a
        stopped session. ``keep_running_for`` names a container (e.g. the
        agent) whose running state keeps the candidate up anyway, since it
        depends on the proxy for egress; it is only inspected when needed.
        """
        if not (self.old_running or self._dependent_running()):
            self._run_checked("stop replacement proxy", "stop", "-t", "1", self.name)
        self._run_checked("remove retained proxy", "rm", "-f", self.backup_name)

    def _dependent_running(self) -> bool:
        """Whether the dependent may be live; any state not known stopped is.

        Stopping the candidate under a live (or paused, restarting, ...)
        agent cuts its egress, while leaving it up for a stopped agent is
        harmless, so unknown states and failed inspects count as live. A
        dependent that no longer exists is not live.
        """
        if self.keep_running_for is None:
            return False
        state = self.runner.get_container_state(self.keep_running_for)
        if state is None:
            return self.runner.container_exists(self.keep_running_for)
        return state.lower() not in _STOPPED_STATES

    def rollback(self) -> None:
        """Remove the candidate and restore the predecessor's identity/state."""
        failures: list[str] = []
        if self.candidate_created:
            self._try(failures, "remove candidate proxy", "rm", "-f", self.name)
        if self.old_disconnected:
            args = ["network", "connect"]
            if self.ip:
                args.extend(["--ip", self.ip])
            args.extend([self.network, self.backup_name])
            self._try(failures, "reconnect retained proxy", *args)
        if self.old_renamed:
            self._try(
                failures,
                "restore retained proxy name",
                "rename",
                self.backup_name,
                self.name,
            )
        if self.old_running:
            self._try(failures, "restart retained proxy", "start", self.name)
        if failures:
            raise ProxyStartError("; ".join(failures))

    def _try(self, failures: list[str], operation: str, *args: str) -> None:
        try:
            self._run_checked(operation, *args)
        except ProxyStartError as exc:
            failures.append(str(exc))

    def _run_checked(self, operation: str, *args: str) -> None:
        result = self.runner.engine.run(*args, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or "container engine command failed"
            raise ProxyStartError(f"Failed to {operation}: {detail}")
