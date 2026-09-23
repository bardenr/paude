"""Codex CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    nodejs_prereq_install_lines,
    pipefail_install_lines,
    rust_build_prereq_install_lines,
    rust_runtime_prereq_install_lines,
)
from paude.constants import CONTAINER_HOME

# Use a subshell so the runtime fallback does not change the caller's cwd/PATH.
# The source tree selects its own Rust version via rust-toolchain.toml.
_INSTALL_SCRIPT = (
    "("
    'export PATH="$HOME/.cargo/bin:$PATH" && '
    "if ! command -v rustup >/dev/null 2>&1; then "
    "curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs "
    "| sh -s -- -y --profile minimal --default-toolchain none; fi && "
    "CODEX_SOURCE=$(mktemp -d) && "
    "trap 'rm -rf \"$CODEX_SOURCE\"' EXIT && "
    "CODEX_TAG=$(curl -fsSL "
    "https://api.github.com/repos/openai/codex/releases/latest "
    "| jq -er '.tag_name') && "
    'git clone --depth 1 --branch "$CODEX_TAG" '
    'https://github.com/openai/codex.git "$CODEX_SOURCE" && '
    'cd "$CODEX_SOURCE/codex-rs" && '
    "CARGO_PROFILE_RELEASE_STRIP=symbols cargo build --locked --release "
    "--bin codex --bin codex-code-mode-host && "
    'mkdir -p "$HOME/.local/bin" && '
    "install -m 0755 target/release/codex target/release/codex-code-mode-host "
    '"$HOME/.local/bin/" && '
    'test -x "$HOME/.local/bin/codex-code-mode-host")'
)

_BUILD_HOME = "/opt/paude-codex-build"


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
            # auth.json holds proxy-synthesized (not real) tokens; strip it from
            # backups anyway so bundles never carry auth material.
            credential_file_names=[".codex/auth.json"],
            activity_files=[],
            yolo_flag="--dangerously-bypass-approvals-and-sandbox",
            clear_command=None,
            extra_domain_aliases=creds.chatgpt_domain_aliases,
            required_domain_aliases=creds.chatgpt_domain_aliases,
            provider=creds.resolved_provider_name,
        )
        self._config.build_stage_lines = [
            *rust_build_prereq_install_lines(),
            f"ENV HOME={_BUILD_HOME}",
            f"WORKDIR {_BUILD_HOME}",
            *pipefail_install_lines(self._config, _BUILD_HOME),
        ]

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install Node.js for Codex documentation tooling",
            *nodejs_prereq_install_lines(),
            *rust_runtime_prereq_install_lines(),
            "",
            "# Install Codex CLI",
            "USER paude",
            f"WORKDIR {container_home}",
            f"COPY --from=codex-builder --chown=paude:0 "
            f"{_BUILD_HOME}/.local/bin/ {container_home}/.local/bin/",
            f"RUN {container_home}/.local/bin/codex --version && "
            f"{container_home}/.local/bin/codex-code-mode-host --help >/dev/null",
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
