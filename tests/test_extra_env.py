"""Tests for --env parsing."""

from __future__ import annotations

import pytest

from paude.extra_env import env_entries_to_dict, parse_env_options


class TestParseEnvOptions:
    def test_key_value(self) -> None:
        assert parse_env_options(
            ["ANTHROPIC_BASE_URL=https://gw.example.com", "EMPTY="], environ={}
        ) == ["ANTHROPIC_BASE_URL=https://gw.example.com", "EMPTY="]

    def test_value_may_contain_equals(self) -> None:
        assert parse_env_options(["A=b=c"], environ={}) == ["A=b=c"]

    def test_bare_key_copies_host_value(self) -> None:
        assert parse_env_options(["MODEL"], environ={"MODEL": "m1"}) == ["MODEL=m1"]

    def test_bare_key_unset_on_host_fails(self) -> None:
        with pytest.raises(ValueError, match="not set"):
            parse_env_options(["MODEL"], environ={})

    def test_last_occurrence_wins(self) -> None:
        assert parse_env_options(["A=1", "B=2", "A=3"], environ={}) == [
            "B=2",
            "A=3",
        ]

    def test_none_is_empty(self) -> None:
        assert parse_env_options(None, environ={}) == []

    @pytest.mark.parametrize("name", ["1BAD", "BAD-NAME", ""])
    def test_rejects_invalid_names(self, name: str) -> None:
        with pytest.raises(ValueError, match="Invalid --env name"):
            parse_env_options([f"{name}=x"], environ={})

    @pytest.mark.parametrize(
        "name",
        ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"],
    )
    def test_rejects_proxy_held_credentials(self, name: str) -> None:
        with pytest.raises(ValueError, match="held by the proxy"):
            parse_env_options([f"{name}=x"], environ={})

    @pytest.mark.parametrize(
        "name",
        [
            "ANTHROPIC_AUTH_TOKEN",
            "AZURE_API_KEY",
            "CEREBRAS_API_KEY",
            "CO_API_KEY",
            "COHERE_API_KEY",
            "GEMINI_API_KEY",
            "GITHUB_COPILOT_TOKEN",
            "GOOGLE_API_KEY",
            "GITHUB_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN",
            "OPENROUTER_API_KEY",
            "MISTRAL_API_KEY",
            "PERPLEXITY_API_KEY",
            "TOGETHER_API_KEY",
        ],
    )
    def test_rejects_agent_credentials(self, name: str) -> None:
        with pytest.raises(ValueError, match="stored in plaintext"):
            parse_env_options([f"{name}=x"], environ={})

    def test_agent_credential_hint_for_gateway_token(self) -> None:
        with pytest.raises(ValueError, match="anthropic-oauth"):
            parse_env_options(["ANTHROPIC_AUTH_TOKEN=x"], environ={})

    @pytest.mark.parametrize(
        "name",
        [
            "github_token",
            "GITHUB_token",
            "anthropic_auth_token",
            "gemini_api_key",
        ],
    )
    def test_rejects_agent_credentials_case_insensitively(self, name: str) -> None:
        with pytest.raises(ValueError, match="stored in plaintext"):
            parse_env_options([f"{name}=x"], environ={})

    def test_rejects_proxy_held_credentials_case_insensitively(self) -> None:
        with pytest.raises(ValueError, match="held by the proxy"):
            parse_env_options(["anthropic_api_key=x"], environ={})

    @pytest.mark.parametrize(
        "name",
        ["HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "GIT_CONFIG_GLOBAL", "PAUDE_X"],
    )
    def test_rejects_paude_managed(self, name: str) -> None:
        with pytest.raises(ValueError, match="paude manages it"):
            parse_env_options([f"{name}=x"], environ={})


@pytest.mark.parametrize("name", ["GH_TOKEN", "GITHUB_TOKEN", "gh_token"])
def test_github_token_hint_points_at_paude_github_token(name: str) -> None:
    with pytest.raises(ValueError, match="PAUDE_GITHUB_TOKEN") as exc:
        parse_env_options([f"{name}=x"], environ={})
    assert "--credential-domain" not in str(exc.value)


@pytest.mark.parametrize(
    "name", ["GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "gh_enterprise_token"]
)
def test_github_enterprise_hint_states_the_limitation(name: str) -> None:
    with pytest.raises(ValueError, match="api.github.com") as exc:
        parse_env_options([f"{name}=x"], environ={})
    assert "PAUDE_GITHUB_TOKEN" not in str(exc.value)


def test_api_key_hint_points_at_credential_domain() -> None:
    with pytest.raises(ValueError, match="--credential-domain"):
        parse_env_options(["ANTHROPIC_API_KEY=x"], environ={})


def test_env_entries_to_dict() -> None:
    assert env_entries_to_dict(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}
