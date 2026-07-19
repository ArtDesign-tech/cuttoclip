from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

from .media import run_command
from .storage import default_data_root


DENO_VERSION = "v2.9.2"
MIN_DENO = (2, 3, 0)

_download_lock = asyncio.Lock()


def _bin_dir() -> Path:
    return default_data_root() / "bin"


def deno_path() -> str | None:
    """Return the Deno binary path if configured or already installed. Never downloads."""
    configured = os.getenv("CUTTOCLIP_DENO")
    if configured and Path(configured).exists():
        return configured
    candidate = _bin_dir() / ("deno.exe" if os.name == "nt" else "deno")
    return str(candidate) if candidate.exists() else None


def _deno_target() -> str | None:
    """Map the current OS/arch to a GitHub release asset triple, or None if unsupported."""
    machine = platform.machine().lower()
    is_arm = machine in {"arm64", "aarch64"}
    is_x64 = machine in {"x86_64", "amd64", "x64"}
    if sys.platform == "win32":
        return "x86_64-pc-windows-msvc" if is_x64 else None
    if sys.platform == "darwin":
        if is_arm:
            return "aarch64-apple-darwin"
        if is_x64:
            return "x86_64-apple-darwin"
        return None
    if sys.platform.startswith("linux"):
        if is_arm:
            return "aarch64-unknown-linux-gnu"
        if is_x64:
            return "x86_64-unknown-linux-gnu"
        return None
    return None


async def _version_ok(path: str) -> bool:
    try:
        code, stdout, stderr = await run_command(path, "--version")
    except (OSError, ValueError):
        return False
    if code != 0:
        return False
    match = re.search(r"deno\s+(\d+)\.(\d+)\.(\d+)", stdout + stderr)
    if not match:
        return False
    version = tuple(int(part) for part in match.groups())
    return version >= MIN_DENO


def _extract_deno(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        member = next(
            (name for name in bundle.namelist() if Path(name).name in {"deno", "deno.exe"}),
            None,
        )
        if member is None:
            raise RuntimeError("Deno archive did not contain a deno binary")
        with bundle.open(member) as source, tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False
        ) as target:
            shutil.copyfileobj(source, target)
            temp_path = Path(target.name)
    if os.name != "nt":
        temp_path.chmod(temp_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(temp_path, destination)


async def ensure_deno() -> str | None:
    """Return a usable Deno binary path, downloading the pinned version if needed.

    Returns None on any failure so YouTube downloads fall back to yt-dlp's own JS interpreter.
    """
    existing = deno_path()
    if existing and await _version_ok(existing):
        return existing

    triple = _deno_target()
    if triple is None:
        print(f"[deno] no prebuilt binary for this platform ({platform.machine()}); running without Deno", flush=True)
        return None

    async with _download_lock:
        # Another job may have finished the download while we waited for the lock.
        existing = deno_path()
        if existing and await _version_ok(existing):
            return existing

        url = f"https://github.com/denoland/deno/releases/download/{DENO_VERSION}/deno-{triple}.zip"
        target = _bin_dir() / ("deno.exe" if os.name == "nt" else "deno")
        try:
            import httpx

            print(f"[deno] downloading {DENO_VERSION} ({triple})...", flush=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                archive = Path(tmp.name)
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with archive.open("wb") as sink:
                            async for chunk in response.aiter_bytes():
                                sink.write(chunk)
                await asyncio.to_thread(_extract_deno, archive, target)
            finally:
                archive.unlink(missing_ok=True)
        except Exception as error:
            print(f"[deno] download failed, running without Deno: {error}", flush=True)
            return None

        if not await _version_ok(str(target)):
            print("[deno] downloaded binary failed version check, running without Deno", flush=True)
            return None
        print(f"[deno] using Deno {DENO_VERSION} at {target}", flush=True)
        return str(target)


def deno_installed() -> str | None:
    """Return the installed Deno version string without downloading, or None.

    Runs synchronously so the capabilities endpoint can call it without triggering
    a download or spinning up an event loop.
    """
    import subprocess

    path = deno_path()
    if not path:
        return None
    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"deno\s+(\d+\.\d+\.\d+)", completed.stdout + completed.stderr)
    return match.group(1) if match else None
