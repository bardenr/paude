"""Source builds stay isolated and fail before installing partial results."""

import os
import subprocess
from pathlib import Path

import pytest

from paude.agents import get_agent, get_agents
from paude.agents.codex import CodexAgent
from paude.config.claude_layer import generate_claude_layer_dockerfile
from paude.config.dockerfile import (
    generate_pip_install_dockerfile,
    generate_workspace_dockerfile,
)
from paude.config.models import PaudeConfig


@pytest.mark.parametrize("kind", ["layer", "pip", "workspace"])
@pytest.mark.parametrize("names", [["codex"], ["claude", "codex", "gemini"]])
def test_build_stage_preserves_runtime_agents(kind: str, names: list[str]) -> None:
    composition = get_agents(names)
    if kind == "layer":
        dockerfile = generate_claude_layer_dockerfile(composition=composition)
    elif kind == "pip":
        dockerfile = generate_pip_install_dockerfile(
            PaudeConfig(packages=["htop"]),
            include_claude_install=True,
            composition=composition,
        )
    else:
        dockerfile = generate_workspace_dockerfile(
            PaudeConfig(packages=["htop"]), composition=composition
        )
    base, builder, runtime = dockerfile.split("FROM ")[1:]
    assert base.startswith("${BASE_IMAGE} AS paude-base")
    if kind != "layer":
        assert "htop" in base
    assert builder.startswith("paude-base AS codex-builder")
    assert "cargo build --locked --release" in builder
    assert runtime.startswith("paude-base\n")
    assert "COPY --from=codex-builder" in runtime
    assert "cargo build" not in runtime
    assert "rustup" not in runtime
    for name in names:
        if name != "codex":
            assert get_agent(name).config.install_script in runtime
    assert "USER paude" in runtime


def test_other_agents_do_not_get_builder() -> None:
    dockerfile = generate_claude_layer_dockerfile(agent=get_agent("claude"))
    assert dockerfile.count("FROM ") == 1
    assert "cargo" not in dockerfile


@pytest.mark.parametrize("has_rustup", [True, False])
@pytest.mark.parametrize("failure", ["", "curl", "git", "cargo"])
def test_source_install_failure_propagation(
    tmp_path: Path, failure: str, has_rustup: bool
) -> None:
    """Exercise the actual shell chain even when its caller uses && / ||."""
    commands = tmp_path / "commands"
    commands.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "commands.log"
    scripts = {
        "rustup": "exit 0",
        "curl": (
            'case "$*" in *sh.rustup.rs*) echo "exit 0" ;; '
            '*) echo \'{"tag_name":"rust-v1.2.3"}\' ;; esac'
        ),
        "jq": "cat >/dev/null; echo rust-v1.2.3",
        "git": 'for dest in "$@"; do :; done; mkdir -p "$dest/codex-rs"',
        "cargo": (
            "mkdir -p target/release; "
            "printf '#!/bin/sh\\nexit 0\\n' > target/release/codex; "
            "cp target/release/codex target/release/codex-code-mode-host"
        ),
    }
    if not has_rustup:
        del scripts["rustup"]
    for name, script in scripts.items():
        command = commands / name
        command.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "$BUILD_LOG"\n'
            + ("exit 42" if name == failure else script)
            + "\n"
        )
        command.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-o",
            "pipefail",
            "-c",
            CodexAgent().config.install_script + " && true",
        ],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{commands}:/usr/bin:/bin",
            "BUILD_LOG": str(log),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (not failure), result.stderr
    assert (home / ".local/bin/codex").exists() == (not failure)
    assert (home / ".local/bin/codex-code-mode-host").exists() == (not failure)
    if not failure:
        assert ("sh.rustup.rs" in log.read_text()) == (not has_rustup)
        assert "git clone --depth 1 --branch rust-v1.2.3" in log.read_text()
        assert (
            "cargo build --locked --release --bin codex --bin codex-code-mode-host"
            in log.read_text()
        )
