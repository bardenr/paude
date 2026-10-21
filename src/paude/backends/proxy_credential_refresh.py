"""Select which host-supplied proxy credentials a refresh rebinds.

Proxy bindings are keyed by the target name the proxy sees (e.g.
``GH_TOKEN``), but users supply them through host-side sources (e.g.
``PAUDE_GITHUB_TOKEN`` or the local gcloud ADC file). Everything shown to
users and accepted from them uses the host-side source name.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

from paude.backends.proxy_config import (
    GCP_ADC_SOURCE,
    ProxyCredentials,
    proxy_credential_source,
)


@dataclass(frozen=True)
class ProxyCredentialStatus:
    """A session credential by host-side name, without its value."""

    source: str
    bound: bool
    available: bool


def describe_missing_credentials(
    targets: Iterable[str],
    session_name: str,
    refreshing: Iterable[str] = (),
) -> str:
    """Explain how to supply missing required bindings, by host source name.

    ``refreshing`` holds the targets a refresh was already rebinding; the
    suggested command keeps them so retrying it still rotates them. A
    refresh has already passed the upgrade check, so only other updates
    mention ``paude upgrade``.
    """
    sources = sorted(_sources(targets))
    refreshed = _sources(refreshing)
    command = " ".join(sorted(refreshed | set(sources)))
    message = (
        "Cannot update the proxy because required credential bindings are "
        f"missing: {', '.join(sources)}. Credential refreshes only pick up "
        "values present on the host, so export them first, then run "
        f"'paude refresh-credentials {session_name} {command}'."
    )
    if not refreshed:
        message += (
            " Sessions created before allowed-endpoints support need "
            f"'paude upgrade {session_name}' first."
        )
    if GCP_ADC_SOURCE in sources:
        message += (
            f" For {GCP_ADC_SOURCE}, run 'gcloud auth application-default login'."
        )
    return message


def credential_statuses(
    session_targets: Iterable[str],
    bound_targets: Iterable[str],
    *,
    adc_available: bool,
) -> list[ProxyCredentialStatus]:
    """Describe each session credential: bound on the proxy, supplied by host."""
    bound = set(bound_targets)
    statuses = []
    for target in session_targets:
        source = proxy_credential_source(target)
        available = (
            adc_available if source == GCP_ADC_SOURCE else bool(os.environ.get(source))
        )
        statuses.append(
            ProxyCredentialStatus(
                source=source, bound=target in bound, available=available
            )
        )
    return sorted(statuses, key=lambda status: status.source)


def check_refresh_names(
    names: Iterable[str],
    targets: Iterable[str],
    session_targets: Iterable[str],
) -> list[str]:
    """Validate the names given to a refresh, deduplicated in order.

    Needs no credential values, so callers run it before gathering any
    (in particular before reading the local gcloud ADC file).

    Raises:
        ValueError: If no name is given, or a name is unknown or unused by
            the session.
    """
    names = list(dict.fromkeys(names))
    usable = _sources(session_targets)
    if not names:
        raise ValueError(
            f"Name the credential(s) to refresh: {', '.join(sorted(usable))}."
        )
    _check_names(names, _sources(targets), usable)
    return names


def select_refresh_credentials(
    gathered: ProxyCredentials,
    targets: Iterable[str],
    session_targets: Iterable[str],
    names: Iterable[str],
) -> ProxyCredentials:
    """Return the subset of ``gathered`` named for a refresh.

    Exactly the named host sources are rebound; each must be one the
    session's agents or providers use and must be present on the host.
    Naming a credential the proxy lacks attaches it. Nothing is ever picked
    up implicitly, so a stale value in the shell cannot replace a working
    binding.

    Raises:
        ValueError: If :func:`check_refresh_names` rejects the names, or a
            named source is absent.
    """
    names = check_refresh_names(names, targets, session_targets)
    found = {proxy_credential_source(target): target for target in gathered.environment}
    absent = set(names) - set(found)
    if GCP_ADC_SOURCE in absent:
        raise ValueError(
            f"No local gcloud ADC file found for {GCP_ADC_SOURCE}. Run "
            "'gcloud auth application-default login' and retry."
        )
    if absent:
        raise ValueError(
            f"Not found in the current environment: {', '.join(sorted(absent))}. "
            "Export the new value and retry."
        )
    return ProxyCredentials(
        environment={found[name]: gathered.environment[found[name]] for name in names},
        chatgpt_oauth_mode=gathered.chatgpt_oauth_mode,
    )


def _sources(targets: Iterable[str]) -> set[str]:
    return {proxy_credential_source(target) for target in targets}


def _check_names(names: list[str], valid: set[str], usable: set[str]) -> None:
    session_names = f"This session's credentials: {', '.join(sorted(usable))}."
    unknown = sorted(set(names) - valid)
    if unknown:
        raise ValueError(
            f"Unknown credential(s): {', '.join(unknown)}. {session_names}"
        )
    unused = sorted(set(names) - usable)
    if unused:
        raise ValueError(
            "Not used by this session's agent or provider: "
            f"{', '.join(unused)}. {session_names}"
        )
