"""Codex CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    nodejs_prereq_install_lines,
    pipefail_install_lines,
)
from paude.constants import CONTAINER_HOME

_INSTALL_SCRIPT = (
    'mkdir -p "$HOME/.local/bin" "$HOME/.local/share/codex" && '
    "ARCH=$(uname -m) && "
    'case "$ARCH" in '
    'x86_64) CODEX_ARCH="x86_64-unknown-linux-musl" ;; '
    'aarch64) CODEX_ARCH="aarch64-unknown-linux-musl" ;; '
    '*) echo "Unsupported architecture: $ARCH" && exit 1 ;; '
    "esac && "
    # Recent Codex releases refuse to start their background app-server
    # daemon ("this CLI has no complete local package") unless the codex
    # binary lives inside a full package: codex-package.json plus the
    # companion code-mode host, bundled rg, and bundled bwrap. Install the
    # whole package outside the persistent CODEX_HOME volume (so it ships
    # with the image) and expose its entrypoint on PATH via a symlink, as
    # the standalone installer does.
    "curl -fsSL "
    '"https://github.com/openai/codex/releases/latest/download/'
    'codex-package-${CODEX_ARCH}.tar.gz"'
    ' | tar xz -C "$HOME/.local/share/codex" && '
    'test -f "$HOME/.local/share/codex/codex-package.json" && '
    # The build only verifies the codex entrypoint (pipefail_install_lines),
    # so verify the companion code-mode host too — a missing companion fails
    # every tool call at runtime.
    'test -x "$HOME/.local/share/codex/bin/codex-code-mode-host" && '
    'ln -sfn ../share/codex/bin/codex "$HOME/.local/bin/codex"'
)


class CodexAgent:
    """Codex CLI agent implementation."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("codex", provider)
        self._config = AgentConfig(
            name="codex",
            display_name="Codex CLI",
            process_name="codex",
            session_name="codex",
            install_script=_INSTALL_SCRIPT,
            install_dir=".local/bin",
            env_vars={"CODEX_HOME": f"{CONTAINER_HOME}/.codex", **creds.extra_env_vars},
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".codex",
            extra_persistent_dir_names=[".agents"],
            config_file_name=None,
            # auth.json holds proxy-synthesized (not real) tokens plus the real,
            # non-secret ChatGPT account ID; strip it from backups anyway so
            # bundles never carry auth material.
            credential_file_names=[".codex/auth.json"],
            # The app-server daemon copies the full Codex package (~430 MB)
            # here on first start and recreates it if missing; the image
            # already ships the same package.
            backup_exclude_dir_names=[".codex/packages"],
            activity_files=[],
            yolo_flag="--dangerously-bypass-approvals-and-sandbox",
            clear_command=None,
            extra_domain_aliases=creds.chatgpt_domain_aliases,
            required_domain_aliases=creds.chatgpt_domain_aliases,
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install Node.js for Codex documentation tooling",
            *nodejs_prereq_install_lines(),
            "",
            "# Install Codex CLI",
            "USER paude",
            f"WORKDIR {container_home}",
            *pipefail_install_lines(self._config, container_home),
            "",
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return f'#!/bin/bash\nmkdir -p "{home}/.codex" 2>/dev/null || true\n'

    def launch_command(self, args: str) -> str:
        if args:
            return f"codex {args}"
        return "codex"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
