"""User-supplied ``--env`` variables for the agent container."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import NoReturn

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_RESERVED_PREFIX = "PAUDE_"

# Credential env vars the bundled agents read directly. Unlike the
# proxy-held set from all_proxy_credential_env_vars(), paude never routes
# these itself, but they are secrets all the same: --env values land in
# plaintext container env and in the paude.io/extra-env label. Keep this
# list curated to well-known names and review it when adding a bundled
# agent; opencode reads provider blocks from the workspace's
# opencode.json, so the key names of the providers it supports (azure,
# cerebras, cohere, github-copilot, perplexity, together, ...) belong
# here too. paude has no mechanism for passing arbitrary secrets into
# the agent container.
_AGENT_CREDENTIAL_ENV_VARS = {
    "ANTHROPIC_AUTH_TOKEN",
    "AZURE_API_KEY",
    "CEREBRAS_API_KEY",
    "CO_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_COPILOT_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "PERPLEXITY_API_KEY",
    "TOGETHER_API_KEY",
    "XAI_API_KEY",
}

# Remediation hints for credential vars --env rejects; _check_name falls
# back to a generic hint for vars not listed here. Lookups use the
# uppercased name, matching the case-insensitive rejection checks.
_GITHUB_HINT = "Export PAUDE_GITHUB_TOKEN on the host instead."
_GITHUB_ENTERPRISE_HINT = (
    "paude injects the GitHub token only for api.github.com; it cannot "
    "authenticate GitHub Enterprise hosts."
)
_CREDENTIAL_HINTS = {
    "GH_TOKEN": _GITHUB_HINT,
    "GITHUB_TOKEN": _GITHUB_HINT,
    "GH_ENTERPRISE_TOKEN": _GITHUB_ENTERPRISE_HINT,
    "GITHUB_ENTERPRISE_TOKEN": _GITHUB_ENTERPRISE_HINT,
    "ANTHROPIC_AUTH_TOKEN": (
        "For a Bearer-auth gateway, export the token as "
        "CLAUDE_CODE_OAUTH_TOKEN and pass --provider anthropic-oauth "
        "--credential-domain anthropic-oauth=HOST; for an x-api-key "
        "gateway, export ANTHROPIC_API_KEY and pass --provider anthropic "
        "--credential-domain anthropic=HOST."
    ),
}

_GENERIC_CREDENTIAL_HINT = (
    "Export it on the host and use --provider (plus "
    "--credential-domain to send it to a custom host)."
)


def _credential_hint(name: str) -> str:
    """Remediation hint for an uppercased credential variable name."""
    return _CREDENTIAL_HINTS.get(name, _GENERIC_CREDENTIAL_HINT)


def _reject_credential(name: str, reason: str, hint: str) -> NoReturn:
    """Raise the shared ``--env`` credential rejection."""
    raise ValueError(f"--env cannot set credential '{name}'; {reason}. {hint}")


def _reserved_names() -> set[str]:
    """Variables paude sets on the agent container itself.

    Letting a user override them would break proxy routing, CA trust, or git
    configuration.
    """
    from paude.environment import build_proxy_environment

    reserved = {name.upper() for name in build_proxy_environment("proxy")}
    reserved.add("GIT_CONFIG_GLOBAL")
    return reserved


def _check_name(name: str, secrets: set[str], reserved: set[str]) -> None:
    """Reject invalid, credential, and paude-managed names.

    Credential and reserved-name checks compare case-insensitively: env
    names are conventionally uppercase, but a lowercase or mixed-case
    credential still lands its value in plaintext container env.
    """
    if not _ENV_NAME.match(name):
        raise ValueError(f"Invalid --env name '{name}'.")
    upper = name.upper()
    if upper in secrets:
        _reject_credential(
            name, "it is held by the proxy sidecar", _credential_hint(upper)
        )
    if upper in _AGENT_CREDENTIAL_ENV_VARS:
        _reject_credential(
            name, "--env values are stored in plaintext", _credential_hint(upper)
        )
    if upper in reserved or upper.startswith(_RESERVED_PREFIX):
        raise ValueError(f"--env cannot set '{name}'; paude manages it.")


def parse_env_options(
    values: list[str] | None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Parse ``--env KEY=VALUE`` / ``--env KEY`` into ``KEY=VALUE`` entries.

    A bare ``KEY`` copies the value from the host environment. Later
    occurrences of a key replace earlier ones.

    Raises:
        ValueError: On an invalid, credential, or reserved name (credential
            and reserved checks are case-insensitive), or a bare ``KEY``
            that is unset on the host.
    """
    if not values:
        return []
    from paude.backends.proxy_config import all_proxy_credential_env_vars

    host_env = os.environ if environ is None else environ
    secrets = {value.upper() for value in all_proxy_credential_env_vars()}
    reserved = _reserved_names()
    parsed: dict[str, str] = {}
    for value in values:
        name, sep, val = value.partition("=")
        _check_name(name, secrets, reserved)
        if not sep:
            if name not in host_env:
                raise ValueError(
                    f"--env {name} copies the host value, but {name} is not set."
                )
            val = host_env[name]
        parsed.pop(name, None)
        parsed[name] = val
    return [f"{name}={val}" for name, val in parsed.items()]


def env_entries_to_dict(entries: list[str]) -> dict[str, str]:
    """Turn stored ``KEY=VALUE`` entries back into a mapping."""
    return {key: val for key, _, val in (e.partition("=") for e in entries)}
