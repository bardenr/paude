"""Proxy and CA certificate configuration for paude backends."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentComposition, AgentConfig

PROXY_BLOCKED_LOG_PATH = "/tmp/paude-proxy-blocked.log"  # noqa: S108

CA_CERT_CONTAINER_PATH = "/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"

CA_BUNDLE_PATH = "/tmp/paude-ca-bundle.pem"  # noqa: S108

# System CA bundle paths across distros (RHEL/CentOS, Debian/Ubuntu,
# openSUSE, Alpine).  Keep in sync with _find_sys_ca_bundle() in
# containers/paude/entrypoint-lib-credentials.sh.
SYS_CA_BUNDLE_PATHS = (
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)

CA_CERT_POLL_INTERVAL = 1
CA_CERT_POLL_TIMEOUT = 30

PROXY_MANAGED_CREDENTIAL = "paude-proxy-managed"  # noqa: S105

STUB_ADC_JSON = (
    '{"type": "authorized_user",'
    ' "client_id": "paude-proxy-managed",'
    ' "client_secret": "paude-proxy-managed",'
    ' "refresh_token": "paude-proxy-managed"}'
)

PROXY_GCP_ADC_ENV = "GCP_ADC_JSON"
PROXY_GITHUB_ENV = "GH_TOKEN"
GCP_ADC_SOURCE = "gcp-adc"

# Host-side source for proxy targets not read from a same-named env var.
_HOST_SOURCE_BY_TARGET = {
    PROXY_GITHUB_ENV: "PAUDE_GITHUB_TOKEN",
    PROXY_GCP_ADC_ENV: GCP_ADC_SOURCE,
}

# Codex ChatGPT OAuth is fully proxy-managed and file-free on paude's side.
# paude only signals that a session wants ChatGPT-plan mode via
# PROXY_CHATGPT_AUTH_STATE_ENV; the user runs `codex login` inside the
# container, and paude-proxy captures, persists, and refreshes the resulting
# real tokens independently per session (see paude-proxy's ChatGPTInjector /
# TokenVendor). No host file is ever read or shared across sessions.
#
# paude never pre-seeds `codex login` state either: doing so made Codex
# believe it was already authenticated on a fresh session (skipping its own
# login prompt) while paude-proxy had no real tokens yet for that session.
# Codex's own login flow writes its auth.json itself (under CODEX_HOME,
# i.e. /home/paude/.codex, which is symlinked onto /pvc); paude-proxy swaps
# in synthetic values at the token exchange so real tokens never land in the
# agent container. The synthetic id_token does carry real, non-secret claims
# (the ChatGPT account/workspace ID and plan type), because Codex needs the
# real account ID to pass its workspace routing discovery check.
# paude's reset of that auth.json lives with the other codex
# on-volume paths as CODEX_AUTH_TARGET in agents/codex_config.py.
PROXY_CHATGPT_AUTH_STATE_ENV = "PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE"


@dataclass
class ProxyCredentials(Mapping[str, str]):
    """Credentials and mode signals for a proxy container.

    ``environment`` contains ordinary proxy environment credentials.
    ``chatgpt_oauth_mode`` signals that this session wants Codex ChatGPT-plan
    OAuth support; no file credential is ever attached for it. Implementing
    the mapping protocol keeps existing environment-only backend consumers
    source-compatible.
    """

    environment: dict[str, str] = field(default_factory=dict)
    chatgpt_oauth_mode: bool = False

    def __getitem__(self, key: str) -> str:
        return self.environment[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.environment)

    def __len__(self) -> int:
        return len(self.environment)


# Python snippet executed inside containers to extract the OpenClaw auth token.
OPENCLAW_AUTH_READER_SCRIPT = (
    "import json,sys,os\n"
    "try:\n"
    "  h=os.environ.get('HOME','/home/paude')\n"
    "  f=open(h+'/.openclaw/openclaw.json')\n"
    "  t=json.load(f).get('gateway',{}).get('auth',{}).get('token','')\n"
    "  print(t) if t else sys.exit(1)\n"
    "except: sys.exit(1)"
)


def derive_agent_ip(proxy_ip: str) -> str:
    """Derive the expected agent container IP from the proxy IP.

    The agent container is the next host on the internal network
    after the proxy (e.g. proxy=10.89.0.2 → agent=10.89.0.3).
    Used for defense-in-depth source IP filtering.
    """
    return str(ipaddress.ip_address(proxy_ip) + 1)


def local_gcp_adc_path() -> Path | None:
    """Return the local GCP ADC file path, or None if it doesn't exist."""
    from paude.constants import GCP_ADC_FILENAME

    path = Path.home() / ".config" / "gcloud" / GCP_ADC_FILENAME
    return path if path.is_file() else None


def gather_proxy_credentials(
    agent_config: AgentConfig | AgentComposition | Agent,
    *,
    credential_providers: list[str] | None = None,
    gcp_adc_path: Path | None = None,
) -> ProxyCredentials:
    """Gather real credentials from the host for the proxy container.

    Reads secret env vars (API keys) and GH_TOKEN from the host
    environment. If a GCP ADC file exists locally, its content is
    passed as ``GCP_ADC_JSON`` so the proxy has it at startup.

    Args:
        agent_config: Agent configuration with secret_env_vars.
        gcp_adc_path: Path to local GCP ADC file, or None if absent.

    Returns:
        Dict of environment variables for the proxy container.
    """
    effective_providers = _effective_providers(agent_config, credential_providers)
    targets = session_proxy_credential_targets(agent_config, credential_providers)

    creds: dict[str, str] = {}
    for key in sorted(targets - {PROXY_GCP_ADC_ENV}):
        env_value = os.environ.get(proxy_credential_source(key))
        if env_value:
            creds[key] = env_value

    if gcp_adc_path is not None and PROXY_GCP_ADC_ENV in targets:
        creds[PROXY_GCP_ADC_ENV] = gcp_adc_path.read_text()

    chatgpt_oauth_mode = "chatgpt" in effective_providers

    return ProxyCredentials(environment=creds, chatgpt_oauth_mode=chatgpt_oauth_mode)


def proxy_credential_source(target: str) -> str:
    """Return the host-side name a user supplies for a proxy target."""
    return _HOST_SOURCE_BY_TARGET.get(target, target)


def session_proxy_credential_targets(
    agent_config: AgentConfig | AgentComposition | Agent,
    credential_providers: list[str] | None = None,
) -> set[str]:
    """Return the credential targets this session gathers from the host.

    Unlike :func:`proxy_credential_targets`, which lists every binding paude
    may preserve, this is limited to the session's own agents and providers.
    """
    providers = _effective_providers(agent_config, credential_providers)
    targets = {PROXY_GITHUB_ENV}
    targets.update(
        key for config in _agent_configs(agent_config) for key in config.secret_env_vars
    )
    targets.update(_provider_secret_targets(providers))
    if any(provider in {"vertex", "google"} for provider in providers):
        targets.add(PROXY_GCP_ADC_ENV)
    return targets


def _provider_secret_targets(providers: list[str]) -> set[str]:
    """Return the secret env vars the given providers bind through the proxy."""
    from paude.providers import get_provider

    return {
        key
        for provider_name in providers
        for key in get_provider(provider_name).secret_env_vars
    }


def _effective_providers(
    agent_config: AgentConfig | AgentComposition | Agent,
    credential_providers: list[str] | None,
) -> list[str]:
    """Return explicit providers, else the providers the agents configure."""
    if credential_providers:
        return credential_providers
    configs = _agent_configs(agent_config)
    return list(dict.fromkeys(config.provider for config in configs if config.provider))


def proxy_credential_targets(
    agent_config: AgentConfig | AgentComposition | Agent,
) -> set[str]:
    """Return every environment credential paude may bind to a proxy.

    Domain-only updates use this allow-list when reading Docker's inspected
    environment.  Keeping the list explicit prevents ordinary proxy settings
    from being replayed as credentials while still preserving credentials for
    providers other than the session's primary provider.
    """
    targets = all_proxy_credential_env_vars()
    targets.update(
        key for config in _agent_configs(agent_config) for key in config.secret_env_vars
    )
    return targets


def all_proxy_credential_env_vars() -> set[str]:
    """Every env var paude may hand to the proxy as a credential.

    Covers all providers and agent/provider pairings, independent of any one
    session's composition.
    """
    from paude.providers import get_provider, list_providers
    from paude.providers.agent_providers import AGENT_PROVIDERS

    targets = {PROXY_GITHUB_ENV, PROXY_GCP_ADC_ENV}
    targets.update(
        key
        for provider_name in list_providers()
        for key in get_provider(provider_name).secret_env_vars
    )
    targets.update(
        key
        for configs in AGENT_PROVIDERS.values()
        for config in configs.values()
        for key in config.extra_secret_env_vars
    )
    return targets


def required_proxy_credential_targets(
    agent_config: AgentConfig | AgentComposition | Agent,
    credential_providers: list[str],
) -> set[str]:
    """Return credential targets required by the active authentication modes."""
    from paude.providers import get_provider

    provider_targets = _provider_secret_targets(credential_providers)
    required = {
        key
        for provider_name in credential_providers
        for key in get_provider(provider_name).required_secret_env_vars
    }
    config_targets = {
        key for config in _agent_configs(agent_config) for key in config.secret_env_vars
    }
    optional_provider_targets = provider_targets - required
    return required | (config_targets - optional_provider_targets)


def _agent_configs(
    agent_config: AgentConfig | AgentComposition | Agent,
) -> list[AgentConfig]:
    """Normalize an agent, composition, or config to its configs."""
    if hasattr(agent_config, "agents"):
        return [agent.config for agent in agent_config.agents]
    if hasattr(agent_config, "config"):
        return [agent_config.config]
    return [agent_config]
