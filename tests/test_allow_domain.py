"""Tests for the allowed-domains subcommand (get/update domain operations)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError
from paude.backends.podman.backend import PodmanBackend
from paude.backends.podman.proxy import PodmanProxyManager
from paude.container.proxy_inspect import ProxyInspectionError
from tests.fakes import make_backend

# ---------------------------------------------------------------------------
# ProxyManager: get_deployment_domains
# ---------------------------------------------------------------------------
# PodmanBackend: get_allowed_domains
# ---------------------------------------------------------------------------


def _inspect_env(backend: PodmanBackend, env: dict[str, str] | None) -> MagicMock:
    """Make the proxy env inspect return ``env``, or fail for None."""
    inspector = MagicMock()
    if env is None:
        inspector.environment.side_effect = ProxyInspectionError("inspect failed")
    else:
        inspector.environment.return_value = env
    backend._proxy._inspector = inspector
    return inspector


class TestPodmanGetAllowedDomains:
    """Tests for PodmanBackend.get_allowed_domains method."""

    def test_returns_none_when_no_proxy(self) -> None:
        """get_allowed_domains returns None when no proxy exists (unrestricted)."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )

        backend = make_backend(mock_runner)

        result = backend.get_allowed_domains("my-session")
        assert result is None

    def test_returns_domain_list_when_proxy_exists(self) -> None:
        """get_allowed_domains returns domains from proxy container env."""
        mock_runner = MagicMock()
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        backend = make_backend(mock_runner)
        inspector = _inspect_env(
            backend, {"ALLOWED_DOMAINS": ".googleapis.com,.pypi.org"}
        )

        result = backend.get_allowed_domains("my-session")

        assert result == [".googleapis.com", ".pypi.org"]
        inspector.environment.assert_called_once_with("paude-proxy-my-session")

    def test_returns_empty_list_when_proxy_has_no_domains(self) -> None:
        """get_allowed_domains returns empty list when ALLOWED_DOMAINS is empty."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        backend = make_backend(mock_runner)
        _inspect_env(backend, {"PROXY_DNS": "1.1.1.1"})

        result = backend.get_allowed_domains("my-session")
        assert result == []

    def test_failed_inspect_raises_instead_of_empty_list(self) -> None:
        """An unreadable proxy must not look like an empty allow-list."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        backend = make_backend(mock_runner)
        _inspect_env(backend, None)

        with pytest.raises(ValueError, match="Cannot read ALLOWED_DOMAINS"):
            backend.get_allowed_domains("my-session")

    def test_failed_inspect_raises_for_endpoints(self) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        backend = make_backend(mock_runner)
        _inspect_env(backend, None)

        with pytest.raises(ValueError, match="Cannot read ALLOWED_ENDPOINTS"):
            backend.get_allowed_endpoints("my-session")

    def test_raises_session_not_found(self) -> None:
        """get_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.get_allowed_domains("nonexistent")


# ---------------------------------------------------------------------------
# PodmanBackend: update_allowed_domains
# ---------------------------------------------------------------------------


class TestPodmanUpdateAllowedDomains:
    """Tests for PodmanBackend.update_allowed_domains method."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_recreates_proxy_with_new_domains(self, mock_dns: MagicMock) -> None:
        """update_allowed_domains recreates proxy with new domain list."""
        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"

        def run(*args: str, **_kwargs: object) -> MagicMock:
            if args[:3] == ("inspect", "-f", "{{json .Config.CreateCommand}}"):
                return MagicMock(
                    returncode=0, stdout=json.dumps(["podman", "create"]), stderr=""
                )
            if args[:3] == ("inspect", "-f", "{{.State.Running}}"):
                return MagicMock(returncode=0, stdout="true\n", stderr="")
            if args and args[0] == "run" and any("test -e" in arg for arg in args):
                if any("printf" in arg for arg in args):
                    return MagicMock(
                        returncode=0,
                        stdout="\0".join(["0", "", "0", "", ""]),
                        stderr="",
                    )
                return MagicMock(returncode=3, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_runner.engine.run.side_effect = run
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        # Disable CA verification polling (not the focus of this test)
        mock_runner.container_running.return_value = False
        mock_dns.return_value = None

        backend = make_backend(mock_runner)
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"
        backend._proxy = PodmanProxyManager(mock_runner, mock_network)
        backend._network_manager = mock_network

        backend.update_allowed_domains(
            "my-session", [".googleapis.com", ".example.com"]
        )

        # Verify proxy was recreated via engine.run (create + start)
        engine_calls = [str(c) for c in mock_runner.engine.run.call_args_list]
        assert any("create" in c for c in engine_calls)
        assert any("start" in c for c in engine_calls)

    def test_raises_session_not_found(self) -> None:
        """update_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.update_allowed_domains("nonexistent", [".example.com"])

    def test_raises_value_error_when_no_proxy(self) -> None:
        """update_allowed_domains raises ValueError when session has no proxy."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )

        backend = make_backend(mock_runner)

        with pytest.raises(ValueError, match="no proxy"):
            backend.update_allowed_domains("my-session", [".example.com"])

    @patch(
        "paude.backends.podman.helpers.get_session_credential_providers",
        return_value=["anthropic-oauth"],
    )
    @patch("paude.backends.podman.backend.get_session_composition")
    def test_default_update_never_gathers_ambient_credentials(
        self,
        mock_composition: MagicMock,
        mock_providers: MagicMock,
    ) -> None:
        runner = MagicMock()
        runner.container_exists.return_value = True
        backend = make_backend(runner)
        backend._proxy = MagicMock()
        backend._setup.gather_proxy_credentials = MagicMock()  # type: ignore[method-assign]
        composition = MagicMock()
        composition.agents = []
        mock_composition.return_value = composition

        backend.update_allowed_domains("my-session", [".example.com"])

        backend._setup.gather_proxy_credentials.assert_not_called()
        credentials = backend._proxy.update_domains.call_args.kwargs["credentials"]
        assert credentials.environment == {}


class TestPodmanUpdateAllowedEndpoints:
    """Tests for PodmanBackend.update_allowed_endpoints."""

    @patch(
        "paude.backends.podman.helpers.get_session_credential_providers",
        return_value=["anthropic-oauth"],
    )
    @patch("paude.backends.podman.backend.get_session_composition")
    def test_checks_policy_once_and_passes_image(
        self,
        mock_composition: MagicMock,
        mock_providers: MagicMock,
    ) -> None:
        runner = MagicMock()
        runner.container_exists.return_value = True
        backend = make_backend(runner)
        backend._proxy = MagicMock()
        backend._proxy.policy_update_proxy_image.return_value = "proxy:cfg"
        mock_composition.return_value.agents = []

        backend.update_allowed_endpoints("my-session", ["api.example.com:8443"])

        backend._proxy.policy_update_proxy_image.assert_called_once_with(
            "my-session", "changing endpoints"
        )
        call = backend._proxy.update_endpoints.call_args
        assert call.kwargs["proxy_image"] == "proxy:cfg"
