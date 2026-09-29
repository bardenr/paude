"""Optional Stream 9 builds preserve default behavior and isolate image caches."""

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

from paude import __version__
from paude.agents import get_agent
from paude.config.models import PaudeConfig
from paude.container.image import ImageManager
from paude.container.local_images import (
    is_centos9_proxy,
    local_image_context,
    validate_centos9_base,
)
from tests.fakes import make_engine

KINDS: tuple[Literal["base", "proxy"], ...] = ("base", "proxy")


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
@pytest.mark.parametrize(
    ("cached", "force"), [(False, False), (True, False), (True, True)]
)
def test_stream9_builds_both_images_and_reuses_cache(
    platform: str, cached: bool, force: bool
) -> None:
    engine = make_engine()
    manager = ImageManager(engine=engine, platform=platform, centos9=True)
    with (
        patch.object(engine, "image_exists", return_value=cached),
        patch.object(engine, "run") as run,
        patch.object(manager, "build_image") as build,
        patch.object(
            manager, "_ensure_runtime_image", return_value="runtime"
        ) as runtime,
    ):
        assert manager.ensure_default_image(force_rebuild=force) == "runtime"
        proxy_tag = manager.ensure_proxy_image(force_rebuild=force)
    suffix = f"{__version__}-{platform.replace('/', '-')}"
    base_tag = f"paude-base-centos9:{suffix}"
    assert proxy_tag == f"paude-proxy-centos9:{suffix}"
    runtime.assert_called_once_with(base_tag, force_rebuild=force)
    run.assert_not_called()  # Never pull a published Paude image in this mode.
    if force or not cached:
        assert build.call_count == 2
        for call, kind, tag in zip(
            build.call_args_list, KINDS, [base_tag, proxy_tag], strict=True
        ):
            context = local_image_context(None, kind)
            assert call.args == (context / "Dockerfile", tag, context)
            assert call.kwargs == {"build_args": {"CENTOS_STREAM": "9"}, "fresh": force}
    else:
        build.assert_not_called()


def test_stream9_custom_workspace_cache_differs_from_default(tmp_path: Path) -> None:
    config_file = tmp_path / "paude.json"
    config_file.write_text('{"packages": ["make"]}')
    config = PaudeConfig(config_file=config_file, packages=["make"])
    engine = make_engine()
    with patch.object(engine, "image_exists", return_value=True):
        default = ImageManager(engine=engine).ensure_custom_image(config)
        stream9 = ImageManager(engine=engine, centos9=True).ensure_custom_image(config)
    assert default != stream9


@pytest.mark.parametrize(
    "config",
    [
        PaudeConfig(base_image="debian:bookworm"),
        PaudeConfig(dockerfile=Path("Dockerfile")),
    ],
)
def test_rejects_conflicting_base_before_reusing_cache(config: PaudeConfig) -> None:
    manager = ImageManager(engine=make_engine(), centos9=True)
    with patch.object(manager._engine, "image_exists") as exists:
        with pytest.raises(ValueError, match="custom base or build Dockerfile"):
            manager.ensure_custom_image(config)
        exists.assert_not_called()


def test_rejects_agents_requiring_their_own_base() -> None:
    with pytest.raises(ValueError, match="requires its own base"):
        validate_centos9_base(None, get_agent("openclaw"))


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (None, False),
        ("quay.io/bbrowning/paude-proxy-centos10:0.20.4", False),
        ("paude-proxy-centos9:0.20.4-linux-amd64", True),
        ("localhost/paude-proxy-centos9:0.20.4-linux-arm64", True),
    ],
)
def test_recovers_build_mode_from_session_image(
    image: str | None, expected: bool
) -> None:
    assert is_centos9_proxy(image) is expected


def test_installed_package_contexts(tmp_path: Path) -> None:
    for kind in KINDS:
        subdir = "data" if kind == "base" else "data/proxy"
        context = tmp_path / subdir
        context.mkdir(parents=True, exist_ok=True)
        (context / "Dockerfile").write_text("FROM example")
        with patch(
            "paude.container.local_images.__file__", str(tmp_path / "module.py")
        ):
            assert local_image_context(None, kind) == context


def test_default_dockerfiles_remain_stream10_and_build_files_exist() -> None:
    import re

    for kind in ("base", "proxy"):
        context = local_image_context(None, kind)
        content = (context / "Dockerfile").read_text()
        assert "ARG CENTOS_STREAM=10\n" in content
        assert "FROM quay.io/centos/centos:stream${CENTOS_STREAM}" in content
        for flags, source in re.findall(
            r"^COPY\s+((?:--\S+\s+)*)(\S+)\s+\S+", content, re.MULTILINE
        ):
            if "--from=" not in flags:
                assert (context / source).is_file()


def test_stream9_builds_are_dispatched_to_remote_engine() -> None:
    manager = ImageManager(engine=make_engine(is_remote=True), centos9=True)
    with (
        patch.object(manager._engine, "image_exists", return_value=False),
        patch.object(manager, "_build_image_remote") as remote,
        patch.object(manager, "_ensure_runtime_image", return_value="runtime"),
    ):
        manager.ensure_default_image()
        manager.ensure_proxy_image()
    assert remote.call_count == 2
    for call in remote.call_args_list:
        assert call.args[3] == {"CENTOS_STREAM": "9"}
        assert "centos9" in call.args[1]


@pytest.mark.parametrize("stream", ["9", "10"])
def test_python_setup_preserves_stream9_system_interpreter(
    tmp_path: Path, stream: str
) -> None:
    import os
    import subprocess

    dockerfile = (local_image_context(None, "base") / "Dockerfile").read_text()
    branch = dockerfile.split('if [ "$CENTOS_STREAM" = 9 ]; then', 1)[1].split(
        "fi &&", 1
    )[0]
    script = 'if [ "$CENTOS_STREAM" = 9 ]; then' + branch + "fi"
    log = tmp_path / "calls"
    for command in ("ln", "alternatives"):
        executable = tmp_path / command
        executable.write_text(f'#!/bin/sh\necho "{command} $*" >> "$CALL_LOG"\n')
        executable.chmod(0o755)
    subprocess.run(
        ["bash", "-c", script],
        check=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CENTOS_STREAM": stream,
            "CALL_LOG": str(log),
        },
    )
    calls = log.read_text()
    if stream == "9":
        assert "alternatives" not in calls
        assert "/usr/local/bin/python3" in calls
    else:
        assert "alternatives --install /usr/bin/python3 python3" in calls
