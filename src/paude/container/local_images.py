"""Optional locally built CentOS Stream 9 images."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from paude.agents.base import Agent
    from paude.config.models import PaudeConfig
    from paude.container.image import ImageManager


def is_centos9_proxy(image: str | None) -> bool:
    """Recover the build mode from the image recorded in session metadata."""
    return bool(image and image.rsplit("/", 1)[-1].startswith("paude-proxy-centos9:"))


def validate_centos9_base(config: PaudeConfig | None, agent: Agent) -> None:
    """Avoid silently using a custom base instead of the requested Stream 9 base."""
    if config and (config.base_image or config.dockerfile):
        raise ValueError(
            "--centos9 cannot be combined with a custom base or build Dockerfile "
            "in paude.json; remove that setting (packages and setup are supported)."
        )
    if agent.config.default_base_image:
        raise ValueError(
            f"--centos9 is unavailable for {agent.config.name}: "
            "this agent requires its own base image."
        )


def local_image_context(
    script_dir: Path | None, kind: Literal["base", "proxy"]
) -> Path:
    """Resolve checkout or wheel assets, including editable installations."""
    directory = "paude" if kind == "base" else "proxy"
    if script_dir is not None:
        context = script_dir / "containers" / directory
    else:
        context = Path(__file__).parent / "data"
        if kind == "proxy":
            context /= "proxy"
        if not (context / "Dockerfile").is_file():
            context = Path(__file__).resolve().parents[3] / "containers" / directory
    if not (context / "Dockerfile").is_file():
        raise FileNotFoundError(f"Missing local image build assets: {context}")
    return context


def ensure_centos9_image(
    manager: ImageManager, kind: Literal["base", "proxy"], force: bool
) -> str:
    """Build on the selected engine; never pull a published Paude image."""
    platform = manager.platform.replace("/", "-") if manager.platform else "native"
    tag = f"paude-{kind}-centos9:{manager.version}-{platform}"
    if force or not manager._engine.image_exists(tag):
        context = local_image_context(manager.script_dir, kind)
        print(f"Building {tag} locally...", file=sys.stderr)
        manager.build_image(
            context / "Dockerfile",
            tag,
            context,
            build_args={"CENTOS_STREAM": "9"},
            fresh=force,
        )
    return tag
