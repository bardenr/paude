"""Build context and identity for the optional Debian proxy image."""

from pathlib import Path

DEBIAN_PROXY_IMAGE = "paude-proxy-debian12"


def is_debian_proxy(image: str | None) -> bool:
    """Recover the proxy choice from the image already stored in session labels."""
    return bool(image and image.rsplit("/", 1)[-1].startswith(DEBIAN_PROXY_IMAGE + ":"))


def debian_proxy_context(script_dir: Path | None) -> Path:
    """Find build assets in a checkout or an installed wheel."""
    if script_dir is not None:
        context = script_dir / "containers" / "proxy"
    else:
        context = Path(__file__).parent / "data" / "proxy"
        if not context.is_dir():
            # Editable installs use the source checkout rather than wheel data.
            context = Path(__file__).resolve().parents[3] / "containers" / "proxy"
    for filename in ("Dockerfile.debian", "entrypoint.sh"):
        if not (context / filename).is_file():
            raise FileNotFoundError(
                f"Missing Debian proxy build asset: {context / filename}"
            )
    return context
