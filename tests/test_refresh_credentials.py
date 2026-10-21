"""Tests for refreshing and listing proxy credentials."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError
from paude.backends.podman.backend import PodmanBackend
from paude.backends.proxy_config import PROXY_GCP_ADC_ENV, ProxyCredentials
from paude.backends.proxy_credential_refresh import (
    ProxyCredentialStatus,
    credential_statuses,
    describe_missing_credentials,
    select_refresh_credentials,
)
from tests.fakes import make_backend


@pytest.fixture
def composition() -> Iterator[MagicMock]:
    comp = MagicMock()
    comp.agents = []
    with (
        patch(
            "paude.backends.podman.helpers.get_session_credential_providers",
            return_value=["anthropic"],
        ),
        patch(
            "paude.backends.podman.backend.get_session_composition",
            return_value=comp,
        ),
    ):
        yield comp


def _backend(fresh: ProxyCredentials) -> PodmanBackend:
    runner = MagicMock()
    runner.container_exists.return_value = True
    backend = make_backend(runner)
    backend._proxy = MagicMock()
    backend._proxy.bound_credential_targets.return_value = {
        "GH_TOKEN",
        "ANTHROPIC_API_KEY",
    }
    backend._setup.gather_proxy_credentials = MagicMock(  # type: ignore[method-assign]
        return_value=fresh
    )
    return backend


class TestRefreshProxyCredentials:
    def test_rebinds_named_credentials_reporting_host_names(
        self, composition: MagicMock
    ) -> None:
        fresh = ProxyCredentials(
            environment={"GH_TOKEN": "new", "ANTHROPIC_API_KEY": "k"}
        )
        backend = _backend(fresh)

        refreshed = backend.refresh_proxy_credentials(
            "my-session", ["PAUDE_GITHUB_TOKEN", "ANTHROPIC_API_KEY"]
        )

        assert refreshed == ["ANTHROPIC_API_KEY", "PAUDE_GITHUB_TOKEN"]
        backend._setup.gather_proxy_credentials.assert_called_once_with(  # type: ignore[attr-defined]
            composition, ["anthropic"], include_adc=False
        )
        call = backend._proxy.update_domains.call_args  # type: ignore[attr-defined]
        assert call.args == ("my-session", None)
        assert call.kwargs["operation_label"] == "credentials"
        assert call.kwargs["credentials"].environment == fresh.environment
        assert "GH_TOKEN" in call.kwargs["credential_targets"]

    def test_unnamed_exported_credentials_are_never_rebound(
        self, composition: MagicMock
    ) -> None:
        fresh = ProxyCredentials(
            environment={"GH_TOKEN": "new", "ANTHROPIC_API_KEY": "stale"}
        )
        backend = _backend(fresh)

        refreshed = backend.refresh_proxy_credentials(
            "my-session", ["PAUDE_GITHUB_TOKEN"]
        )

        assert refreshed == ["PAUDE_GITHUB_TOKEN"]
        call = backend._proxy.update_domains.call_args  # type: ignore[attr-defined]
        assert call.kwargs["credentials"].environment == {"GH_TOKEN": "new"}

    def test_attaches_unbound_credential_without_inspecting_bindings(
        self, composition: MagicMock
    ) -> None:
        backend = _backend(ProxyCredentials(environment={"GH_TOKEN": "new"}))
        backend._proxy.bound_credential_targets.return_value = set()  # type: ignore[attr-defined]

        refreshed = backend.refresh_proxy_credentials(
            "my-session", ["PAUDE_GITHUB_TOKEN"]
        )

        assert refreshed == ["PAUDE_GITHUB_TOKEN"]
        backend._proxy.bound_credential_targets.assert_not_called()  # type: ignore[attr-defined]

    def test_rejects_credential_session_does_not_use(
        self, composition: MagicMock
    ) -> None:
        backend = _backend(ProxyCredentials(environment={"GH_TOKEN": "new"}))

        with pytest.raises(ValueError, match="Not used by this session.*OPENAI"):
            backend.refresh_proxy_credentials("my-session", ["OPENAI_API_KEY"])

        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_no_names_never_touches_proxy(self, composition: MagicMock) -> None:
        backend = _backend(ProxyCredentials(environment={"GH_TOKEN": "new"}))

        with pytest.raises(ValueError, match="Name the credential"):
            backend.refresh_proxy_credentials("my-session", [])

        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_no_proxy_reported_before_credential_checks(
        self, composition: MagicMock
    ) -> None:
        backend = _backend(ProxyCredentials())
        backend._proxy.require_proxy.side_effect = ValueError(  # type: ignore[attr-defined]
            "Session 'my-session' has no proxy (unrestricted network)."
        )

        with pytest.raises(ValueError, match="has no proxy"):
            backend.refresh_proxy_credentials("my-session", ["PAUDE_GITHUB_TOKEN"])

        backend._setup.gather_proxy_credentials.assert_not_called()  # type: ignore[attr-defined]
        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_upgrade_required_reported_before_credential_checks(
        self, composition: MagicMock
    ) -> None:
        backend = _backend(ProxyCredentials())
        backend._proxy.policy_update_proxy_image.side_effect = ValueError(  # type: ignore[attr-defined]
            "Run 'paude upgrade my-session' before refreshing credentials."
        )

        with pytest.raises(ValueError, match="paude upgrade my-session"):
            backend.refresh_proxy_credentials("my-session", ["PAUDE_GITHUB_TOKEN"])

        backend._setup.gather_proxy_credentials.assert_not_called()  # type: ignore[attr-defined]
        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_checked_image_passed_to_proxy(self, composition: MagicMock) -> None:
        backend = _backend(ProxyCredentials(environment={"GH_TOKEN": "new"}))
        backend._proxy.policy_update_proxy_image.return_value = "proxy:cfg"  # type: ignore[attr-defined]

        backend.refresh_proxy_credentials("my-session", ["PAUDE_GITHUB_TOKEN"])

        call = backend._proxy.update_domains.call_args  # type: ignore[attr-defined]
        assert call.kwargs["proxy_image"] == "proxy:cfg"

    def test_adc_read_only_when_named(self, composition: MagicMock) -> None:
        backend = _backend(ProxyCredentials(environment={PROXY_GCP_ADC_ENV: "{}"}))

        with patch(
            "paude.backends.podman.helpers.get_session_credential_providers",
            return_value=["vertex"],
        ):
            refreshed = backend.refresh_proxy_credentials("my-session", ["gcp-adc"])

        assert refreshed == ["gcp-adc"]
        backend._setup.gather_proxy_credentials.assert_called_once_with(  # type: ignore[attr-defined]
            composition, ["vertex"], include_adc=True
        )

    @pytest.mark.parametrize(
        ("names", "error"),
        [
            (["gcp-adc", "TYPO"], "Unknown credential"),
            # anthropic doesn't use ADC.
            (["gcp-adc"], "Not used by this session"),
            ([], "Name the credential"),
        ],
    )
    def test_bad_names_rejected_before_gathering(
        self, composition: MagicMock, names: list[str], error: str
    ) -> None:
        backend = _backend(ProxyCredentials(environment={PROXY_GCP_ADC_ENV: "{}"}))

        with pytest.raises(ValueError, match=error):
            backend.refresh_proxy_credentials("my-session", names)

        backend._setup.gather_proxy_credentials.assert_not_called()  # type: ignore[attr-defined]
        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_missing_session_raises(self) -> None:
        runner = MagicMock()
        runner.container_exists.return_value = False
        backend = make_backend(runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.refresh_proxy_credentials("nonexistent", ["PAUDE_GITHUB_TOKEN"])


class TestListProxyCredentials:
    def test_lists_session_credentials_by_host_name(
        self, composition: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAUDE_GITHUB_TOKEN", "new")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        backend = _backend(ProxyCredentials())
        backend._proxy.has_proxy.return_value = True  # type: ignore[attr-defined]
        backend._proxy.bound_credential_targets.return_value = {"ANTHROPIC_API_KEY"}  # type: ignore[attr-defined]

        statuses = backend.list_proxy_credentials("my-session")

        assert statuses == [
            ProxyCredentialStatus("ANTHROPIC_API_KEY", bound=True, available=False),
            ProxyCredentialStatus("PAUDE_GITHUB_TOKEN", bound=False, available=True),
        ]
        backend._setup.gather_proxy_credentials.assert_not_called()  # type: ignore[attr-defined]
        backend._proxy.update_domains.assert_not_called()  # type: ignore[attr-defined]

    def test_no_proxy_returns_none(self, composition: MagicMock) -> None:
        backend = _backend(ProxyCredentials())
        backend._proxy.has_proxy.return_value = False  # type: ignore[attr-defined]

        assert backend.list_proxy_credentials("my-session") is None
        backend._proxy.bound_credential_targets.assert_not_called()  # type: ignore[attr-defined]

    def test_missing_session_raises(self) -> None:
        runner = MagicMock()
        runner.container_exists.return_value = False
        backend = make_backend(runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.list_proxy_credentials("nonexistent")


class TestCredentialStatuses:
    def test_reports_adc_from_file_and_env_sources_from_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAUDE_GITHUB_TOKEN", "t")

        statuses = credential_statuses(
            {"GH_TOKEN", PROXY_GCP_ADC_ENV},
            {PROXY_GCP_ADC_ENV},
            adc_available=True,
        )

        assert statuses == [
            ProxyCredentialStatus("PAUDE_GITHUB_TOKEN", bound=False, available=True),
            ProxyCredentialStatus("gcp-adc", bound=True, available=True),
        ]

    def test_empty_env_value_is_not_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAUDE_GITHUB_TOKEN", "")

        (status,) = credential_statuses({"GH_TOKEN"}, set(), adc_available=False)

        assert not status.available


class TestSelectRefreshCredentials:
    TARGETS = {"GH_TOKEN", PROXY_GCP_ADC_ENV, "ANTHROPIC_API_KEY"}

    def test_rebinds_exactly_the_named_sources(self) -> None:
        gathered = ProxyCredentials(
            environment={"GH_TOKEN": "t", "ANTHROPIC_API_KEY": "stale"},
            chatgpt_oauth_mode=True,
        )

        selected = select_refresh_credentials(
            gathered, self.TARGETS, self.TARGETS, ["PAUDE_GITHUB_TOKEN"]
        )

        assert selected.environment == {"GH_TOKEN": "t"}
        assert selected.chatgpt_oauth_mode

    def test_duplicate_names_rebind_once(self) -> None:
        gathered = ProxyCredentials(environment={"GH_TOKEN": "t"})

        selected = select_refresh_credentials(
            gathered,
            self.TARGETS,
            self.TARGETS,
            ["PAUDE_GITHUB_TOKEN", "PAUDE_GITHUB_TOKEN"],
        )

        assert selected.environment == {"GH_TOKEN": "t"}

    def test_no_names_lists_session_credentials(self) -> None:
        with pytest.raises(
            ValueError, match="Name the credential.*ANTHROPIC_API_KEY.*gcp-adc"
        ):
            select_refresh_credentials(
                ProxyCredentials(environment={"GH_TOKEN": "t"}),
                self.TARGETS,
                self.TARGETS,
                [],
            )

    def test_gcp_adc_rebinds_adc_alone(self) -> None:
        gathered = ProxyCredentials(
            environment={"GH_TOKEN": "t", PROXY_GCP_ADC_ENV: "{}"}
        )

        selected = select_refresh_credentials(
            gathered, self.TARGETS, self.TARGETS, ["gcp-adc"]
        )

        assert selected.environment == {PROXY_GCP_ADC_ENV: "{}"}

    def test_rejects_proxy_target_names_listing_session_names(self) -> None:
        gathered = ProxyCredentials(environment={"GH_TOKEN": "t"})

        with pytest.raises(
            ValueError,
            match="Unknown credential.*GH_TOKEN.*session's credentials: "
            "ANTHROPIC_API_KEY, PAUDE_GITHUB_TOKEN, gcp-adc",
        ):
            select_refresh_credentials(
                gathered, self.TARGETS, self.TARGETS, ["GH_TOKEN"]
            )

    def test_requires_named_source_present(self) -> None:
        gathered = ProxyCredentials(environment={"ANTHROPIC_API_KEY": "k"})

        with pytest.raises(ValueError, match="Not found.*PAUDE_GITHUB_TOKEN"):
            select_refresh_credentials(
                gathered, self.TARGETS, self.TARGETS, ["PAUDE_GITHUB_TOKEN"]
            )

    def test_rejects_source_unused_by_session(self) -> None:
        gathered = ProxyCredentials(environment={"GH_TOKEN": "t"})

        with pytest.raises(
            ValueError,
            match="Not used by this session.*ANTHROPIC.*credentials: "
            "PAUDE_GITHUB_TOKEN",
        ):
            select_refresh_credentials(
                gathered,
                self.TARGETS,
                session_targets={"GH_TOKEN"},
                names=["ANTHROPIC_API_KEY"],
            )

    def test_gcp_adc_without_file_suggests_gcloud_login(self) -> None:
        gathered = ProxyCredentials(environment={"GH_TOKEN": "t"})

        with pytest.raises(ValueError, match="gcloud auth application-default"):
            select_refresh_credentials(
                gathered, self.TARGETS, self.TARGETS, ["gcp-adc"]
            )


class TestDescribeMissingCredentials:
    def test_names_host_sources_and_session(self) -> None:
        message = describe_missing_credentials(
            {"GH_TOKEN", "ANTHROPIC_API_KEY"}, "my-session"
        )

        assert "GH_TOKEN," not in message
        assert (
            "paude refresh-credentials my-session ANTHROPIC_API_KEY "
            "PAUDE_GITHUB_TOKEN" in message
        )
        assert "gcloud" not in message
        assert "paude upgrade my-session" in message
        assert "--refresh-credentials" not in message
        assert "--only" not in message
        assert "export them first" in message

    def test_refresh_keeps_requested_names_in_suggested_command(self) -> None:
        message = describe_missing_credentials(
            {"ANTHROPIC_API_KEY"}, "s", refreshing={"GH_TOKEN"}
        )

        assert "missing: ANTHROPIC_API_KEY." in message
        assert (
            "'paude refresh-credentials s ANTHROPIC_API_KEY PAUDE_GITHUB_TOKEN'"
            in message
        )
        # A refresh has already passed the upgrade check.
        assert "paude upgrade" not in message

    def test_adc_explains_gcloud_login(self) -> None:
        message = describe_missing_credentials({PROXY_GCP_ADC_ENV}, "s")

        assert "gcp-adc" in message
        assert "gcloud auth application-default login" in message


class TestSessionProxyCredentialTargets:
    def test_limits_targets_to_session_providers(self) -> None:
        from paude.backends.proxy_config import session_proxy_credential_targets

        comp = MagicMock()
        comp.agents = []

        anthropic = session_proxy_credential_targets(comp, ["anthropic"])
        vertex = session_proxy_credential_targets(comp, ["vertex"])

        assert anthropic == {"GH_TOKEN", "ANTHROPIC_API_KEY"}
        assert PROXY_GCP_ADC_ENV in vertex
        assert "ANTHROPIC_API_KEY" not in vertex
