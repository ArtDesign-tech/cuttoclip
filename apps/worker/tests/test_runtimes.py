from __future__ import annotations

from pathlib import Path

import pytest

from app import runtimes


def test_deno_target_windows_x64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes.sys, "platform", "win32")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "AMD64")
    assert runtimes._deno_target() == "x86_64-pc-windows-msvc"


def test_deno_target_linux_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes.sys, "platform", "linux")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "aarch64")
    assert runtimes._deno_target() == "aarch64-unknown-linux-gnu"


def test_deno_target_macos_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes.sys, "platform", "darwin")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "arm64")
    assert runtimes._deno_target() == "aarch64-apple-darwin"


def test_deno_target_macos_x64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes.sys, "platform", "darwin")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "x86_64")
    assert runtimes._deno_target() == "x86_64-apple-darwin"


def test_deno_target_unknown_arch_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes.sys, "platform", "linux")
    monkeypatch.setattr(runtimes.platform, "machine", lambda: "riscv64")
    assert runtimes._deno_target() is None


@pytest.mark.asyncio
async def test_version_ok_accepts_new_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(*args: str, cwd: str | None = None):
        return 0, "deno 2.9.2 (stable)", ""

    monkeypatch.setattr(runtimes, "run_command", fake_run)
    assert await runtimes._version_ok("deno") is True


@pytest.mark.asyncio
async def test_version_ok_rejects_too_old(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(*args: str, cwd: str | None = None):
        return 0, "deno 2.2.9 (stable)", ""

    monkeypatch.setattr(runtimes, "run_command", fake_run)
    assert await runtimes._version_ok("deno") is False


@pytest.mark.asyncio
async def test_version_ok_rejects_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(*args: str, cwd: str | None = None):
        return 0, "not a version string", ""

    monkeypatch.setattr(runtimes, "run_command", fake_run)
    assert await runtimes._version_ok("deno") is False


@pytest.mark.asyncio
async def test_version_ok_rejects_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(*args: str, cwd: str | None = None):
        return 1, "", "boom"

    monkeypatch.setattr(runtimes, "run_command", fake_run)
    assert await runtimes._version_ok("deno") is False


def test_deno_path_honors_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "custom-deno"
    binary.write_text("")
    monkeypatch.setenv("CUTTOCLIP_DENO", str(binary))
    assert runtimes.deno_path() == str(binary)


def test_deno_path_ignores_missing_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_DENO", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(runtimes, "_bin_dir", lambda: tmp_path / "bin")
    assert runtimes.deno_path() is None


def test_deno_path_finds_installed_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CUTTOCLIP_DENO", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = "deno.exe" if runtimes.os.name == "nt" else "deno"
    binary = bin_dir / name
    binary.write_text("")
    monkeypatch.setattr(runtimes, "_bin_dir", lambda: bin_dir)
    assert runtimes.deno_path() == str(binary)
