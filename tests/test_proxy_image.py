"""Debian proxy builds use packaged assets and a separate, reusable image."""

from pathlib import Path
from unittest.mock import patch

import pytest

from paude import __version__
from paude.container.image import ImageManager
from paude.container.proxy_image import debian_proxy_context, is_debian_proxy
from tests.fakes import make_engine


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
@pytest.mark.parametrize(
    ("cached", "force"), [(False, False), (True, False), (True, True)]
)
def test_debian_proxy_build_and_cache(platform: str, cached: bool, force: bool) -> None:
    engine = make_engine()
    manager = ImageManager(engine=engine, platform=platform)
    with (
        patch.object(engine, "image_exists", return_value=cached),
        patch.object(manager, "build_image") as build,
        patch.object(engine, "run") as run,
    ):
        tag = manager.ensure_proxy_image(force_rebuild=force, debian=True)
    assert tag == f"paude-proxy-debian12:{__version__}-{platform.replace('/', '-')}"
    run.assert_not_called()  # Never pull the published CentOS proxy.
    if force or not cached:
        context = debian_proxy_context(None)
        build.assert_called_once_with(
            context / "Dockerfile.debian", tag, context, fresh=force
        )
    else:
        build.assert_not_called()


def test_debian_proxy_propagates_build_failure() -> None:
    manager = ImageManager(engine=make_engine())
    with (
        patch.object(manager._engine, "image_exists", return_value=False),
        patch.object(manager, "build_image", side_effect=RuntimeError("build failed")),
        pytest.raises(RuntimeError, match="build failed"),
    ):
        manager.ensure_proxy_image(debian=True)


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (None, False),
        ("quay.io/bbrowning/paude-proxy-centos10:0.20.4", False),
        ("paude-proxy-debian12:0.20.4-linux-amd64", True),
        ("localhost/paude-proxy-debian12:0.20.4-linux-arm64", True),
    ],
)
def test_recovers_choice_from_stored_image(image: str | None, expected: bool) -> None:
    assert is_debian_proxy(image) is expected


def test_installed_package_build_context(tmp_path: Path) -> None:
    module = tmp_path / "proxy_image.py"
    context = tmp_path / "data" / "proxy"
    context.mkdir(parents=True)
    for name in ("Dockerfile.debian", "entrypoint.sh"):
        (context / name).write_text("asset")
    with patch("paude.container.proxy_image.__file__", str(module)):
        assert debian_proxy_context(None) == context


def test_missing_checkout_assets_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing Debian proxy build asset"):
        debian_proxy_context(tmp_path)
