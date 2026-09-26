"""Exercise the Codex shell installer with complete and broken release archives."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from paude.agents.codex import CodexAgent

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None, reason="installer requires jq"
)


def _archive(path: Path, arch: str, broken: str) -> None:
    manifest = {
        "layoutVersion": 1,
        "version": "0.157.0",
        "target": f"{arch}-unknown-linux-musl",
        "variant": "codex",
        "entrypoint": "bin/codex",
        "resourcesDir": "codex-resources",
        "pathDir": "codex-path",
    }
    if broken == "wrong-target":
        manifest["target"] = "wrong-target"
    files = {
        "codex-package.json": json.dumps(manifest).encode(),
        "bin/codex": b"#!/bin/sh\necho codex-test\n",
        "bin/codex-code-mode-host": b"#!/bin/sh\nexit 0\n",
        "codex-path/rg": b"#!/bin/sh\nexit 0\n",
        "codex-resources/bwrap": b"#!/bin/sh\nexit 0\n",
        "codex-resources/voice/NOTICE.md": b"extra upstream resource\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            if name == broken:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644 if name.endswith((".json", ".md")) else 0o755
            if broken == "not-executable" and name == "bin/codex-code-mode-host":
                info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
@pytest.mark.parametrize(
    "broken",
    [
        "",
        "codex-package.json",
        "bin/codex-code-mode-host",
        "codex-path/rg",
        "codex-resources/bwrap",
        "wrong-target",
        "not-executable",
        "curl-failure",
    ],
)
def test_install_preserves_package_layout_and_rejects_partial_downloads(
    tmp_path: Path, arch: str, broken: str
) -> None:
    archive = tmp_path / "release.tar.gz"
    _archive(archive, arch, broken)
    home = tmp_path / "home"
    old_package = home / ".local/lib/codex"
    old_package.mkdir(parents=True)
    (old_package / "old-install").write_text("old")
    bin_dir = home / ".local/bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("old binary")
    commands = tmp_path / "commands"
    commands.mkdir()
    for name, body in {
        "uname": 'printf "%s\\n" "$TEST_ARCH"',
        "curl": 'printf "%s\\n" "$*" > "$CURL_LOG"; cat "$ARCHIVE"; exit "$CURL_EXIT"',
    }.items():
        command = commands / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)
    log = tmp_path / "curl.log"
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
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TEST_ARCH": arch,
            "ARCHIVE": str(archive),
            "CURL_LOG": str(log),
            "CURL_EXIT": "22" if broken == "curl-failure" else "0",
        },
        capture_output=True,
        text=True,
    )
    assert not list((home / ".local/lib").glob(".codex-install.*"))
    assert f"codex-package-{arch}-unknown-linux-musl.tar.gz" in log.read_text()
    if broken:
        assert result.returncode != 0
        assert (old_package / "old-install").read_text() == "old"
        assert (bin_dir / "codex").read_text() == "old binary"
        return
    assert result.returncode == 0, result.stderr
    assert not (old_package / "old-install").exists()
    for binary in ("codex", "codex-code-mode-host"):
        assert (bin_dir / binary).is_symlink()
        assert (bin_dir / binary).resolve() == old_package / "bin" / binary
        assert os.access(bin_dir / binary, os.X_OK)
    assert (
        json.loads((old_package / "codex-package.json").read_text())["version"]
        == "0.157.0"
    )
    assert (
        old_package / "codex-resources/voice/NOTICE.md"
    ).read_text() == "extra upstream resource\n"
