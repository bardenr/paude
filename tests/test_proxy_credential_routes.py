"""Tests for custom proxy credential routing (--credential-domain)."""

from __future__ import annotations

import pytest

from paude.backends.proxy_config import ProxyCredentials
from paude.proxy_credential_routes import (
    DEFAULT_CREDENTIAL_ROUTES,
    build_credential_routes_config,
    credential_domain_endpoints,
    credential_domain_hosts,
    parse_credential_domains,
    validate_credential_domain_specs,
)


class TestParseCredentialDomains:
    def test_defaults_port_and_normalizes_host(self) -> None:
        assert parse_credential_domains(
            ["anthropic=GW.Example.COM."], ["anthropic"]
        ) == ["anthropic=gw.example.com:443"]

    def test_keeps_explicit_port(self) -> None:
        assert parse_credential_domains(
            ["anthropic=gw.example.com:8443"], ["anthropic"]
        ) == ["anthropic=gw.example.com:8443"]

    def test_deduplicates(self) -> None:
        specs = ["anthropic=gw.example.com", "anthropic=gw.example.com:443"]
        assert parse_credential_domains(specs, ["anthropic"]) == [
            "anthropic=gw.example.com:443"
        ]

    def test_none_is_empty(self) -> None:
        assert parse_credential_domains(None, ["anthropic"]) == []

    @pytest.mark.parametrize("spec", ["anthropic", "=gw.example.com", "anthropic="])
    def test_rejects_malformed_spec(self, spec: str) -> None:
        with pytest.raises(ValueError, match="expected PROVIDER=HOST"):
            parse_credential_domains([spec], ["anthropic"])

    def test_rejects_provider_not_in_session(self) -> None:
        with pytest.raises(ValueError, match="does not configure"):
            parse_credential_domains(["openai=gw.example.com"], ["anthropic"])

    def test_rejects_provider_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="no API-key credential"):
            parse_credential_domains(["vertex=gw.example.com"], ["vertex"])

    @pytest.mark.parametrize(
        "target", ["https://gw.example.com", "*.example.com", ".example.com"]
    )
    def test_rejects_scheme_and_wildcards(self, target: str) -> None:
        with pytest.raises(ValueError, match="Invalid credential domain"):
            parse_credential_domains([f"anthropic={target}"], ["anthropic"])

    @pytest.mark.parametrize(
        "specs",
        [
            ["anthropic=gw.example.com", "openai=gw.example.com"],
            ["anthropic=gw.example.com:443", "openai=gw.example.com:8443"],
            [
                "anthropic=gw.example.com",
                "openai=gw.example.com:8443",
                "cursor=gw.example.com",
            ],
        ],
    )
    def test_two_providers_on_one_host_rejected_at_build(
        self, specs: list[str]
    ) -> None:
        """Parse normalizes all; the routing-table build rejects the set."""
        normalized = parse_credential_domains(specs, ["anthropic", "openai", "cursor"])
        with pytest.raises(ValueError, match="first matching credential"):
            build_credential_routes_config(normalized)
        with pytest.raises(ValueError, match="first matching credential"):
            validate_credential_domain_specs(normalized)

    def test_allows_same_provider_on_one_host(self) -> None:
        specs = ["anthropic=gw.example.com", "anthropic=gw.example.com:8443"]
        assert parse_credential_domains(specs, ["anthropic"]) == [
            "anthropic=gw.example.com:443",
            "anthropic=gw.example.com:8443",
        ]

    def test_allows_providers_on_distinct_hosts(self) -> None:
        assert parse_credential_domains(
            ["anthropic=a.example.com", "openai=b.example.com"],
            ["anthropic", "openai"],
        ) == ["anthropic=a.example.com:443", "openai=b.example.com:443"]


class TestHostsAndEndpoints:
    def test_hosts_strip_ports(self) -> None:
        specs = ["anthropic=gw.example.com:8443", "openai=gw.example.com:443"]
        assert credential_domain_hosts(specs) == ["gw.example.com"]

    def test_only_nonstandard_ports_become_endpoints(self) -> None:
        specs = ["anthropic=a.example.com:443", "openai=b.example.com:8443"]
        assert credential_domain_endpoints(specs) == ["b.example.com:8443"]


class TestBuildConfig:
    def test_custom_route_first_then_all_defaults(self) -> None:
        config = build_credential_routes_config(["anthropic=gw.example.com:8443"])

        routes = config["credentials"]
        assert routes[0] == {
            "env_var": "ANTHROPIC_API_KEY",
            "injector": "api_key",
            "params": {"header_name": "x-api-key"},
            "domains": ["gw.example.com"],
        }
        assert routes[1:] == DEFAULT_CREDENTIAL_ROUTES

    def test_does_not_mutate_defaults(self) -> None:
        before = [dict(route) for route in DEFAULT_CREDENTIAL_ROUTES]
        build_credential_routes_config(["anthropic=gw.example.com:443"])
        assert before == DEFAULT_CREDENTIAL_ROUTES

    def test_no_specs_is_default_table(self) -> None:
        assert build_credential_routes_config([]) == {
            "credentials": DEFAULT_CREDENTIAL_ROUTES
        }

    def test_allows_same_provider_two_ports_on_one_host(self) -> None:
        config = build_credential_routes_config(
            ["anthropic=gw.example.com:443", "anthropic=gw.example.com:8443"]
        )
        assert [route["domains"] for route in config["credentials"][:2]] == [
            ["gw.example.com"],
            ["gw.example.com"],
        ]

    @pytest.mark.parametrize(
        "spec",
        [
            "openai=api.anthropic.com:443",
            "openai=api.claude.ai:443",
            "anthropic=chatgpt.com:443",
            "anthropic=sub.chatgpt.com:443",
            "anthropic=api.github.com:443",
            "anthropic=api.cursor.com:443",
            "anthropic=storage.googleapis.com:443",
        ],
    )
    def test_rejects_spec_shadowing_builtin_route(self, spec: str) -> None:
        """No credentials passed: every built-in route is assumed live."""
        with pytest.raises(ValueError, match="a built-in route"):
            build_credential_routes_config([spec])

    @pytest.mark.parametrize(
        "spec", ["anthropic=cursor.com:443", "anthropic=claude.ai:443"]
    )
    def test_allows_apex_of_builtin_suffix_route(self, spec: str) -> None:
        """The proxy's suffix match needs the dot; the apex host is free."""
        build_credential_routes_config([spec])

    def test_skips_builtin_route_whose_env_var_is_not_delivered(self) -> None:
        """The proxy skips routes whose env var is unset, so a spec that
        merely repeats a dead built-in route is redundant, not conflicting."""
        build_credential_routes_config(
            ["anthropic=api.anthropic.com:443"], {"ANTHROPIC_API_KEY": "sk"}
        )
        validate_credential_domain_specs(
            ["anthropic=api.anthropic.com:443"], {"ANTHROPIC_API_KEY": "sk"}
        )

    def test_rejects_when_rival_env_var_is_delivered(self) -> None:
        credentials = ProxyCredentials(environment={"OPENAI_API_KEY": "sk"})
        with pytest.raises(ValueError, match="a built-in route") as exc:
            build_credential_routes_config(["anthropic=chatgpt.com:443"], credentials)
        assert "OPENAI_API_KEY" in str(exc.value)

    def test_chatgpt_mode_makes_chatgpt_route_live(self) -> None:
        """The chatgpt route falls back to the OAuth mode signal."""
        with pytest.raises(ValueError, match="CHATGPT_AUTH_FILE"):
            build_credential_routes_config(
                ["anthropic=chatgpt.com:443"],
                ProxyCredentials(chatgpt_oauth_mode=True),
            )

    def test_gcp_adc_makes_googleapis_route_live(self) -> None:
        """The gcloud route falls back to GCP_ADC_JSON."""
        with pytest.raises(ValueError, match="GOOGLE_APPLICATION_CREDENTIALS"):
            build_credential_routes_config(
                ["anthropic=storage.googleapis.com:443"], {"GCP_ADC_JSON": "{}"}
            )

    def test_builtin_collision_error_names_both_credentials(self) -> None:
        with pytest.raises(ValueError, match="a built-in route") as exc:
            build_credential_routes_config(["openai=api.anthropic.com:443"])
        message = str(exc.value)
        assert "OPENAI_API_KEY" in message
        assert "api.anthropic.com" in message
        assert "first matching credential" in message

    def test_validate_accepts_clean_specs(self) -> None:
        validate_credential_domain_specs(
            ["anthropic=a.example.com:443", "openai=b.example.com:443"]
        )
