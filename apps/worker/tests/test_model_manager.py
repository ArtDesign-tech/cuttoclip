from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from app import model_manager
from app.errors import WorkerError
from app.model_manager import ModelSpec


CONTENT = b"fake-onnx-model-bytes"
CONTENT_SHA = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def spec() -> ModelSpec:
    return ModelSpec(key="test", file_name="test.onnx", url="https://example.test/test.onnx", sha256=CONTENT_SHA)


@pytest.fixture(autouse=True)
def isolated_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(model_manager, "default_data_root", lambda: tmp_path)
    return tmp_path


class _FakeResponse:
    def __init__(self, chunks: list[bytes], *, fail: bool = False) -> None:
        self._chunks = chunks
        self._fail = fail

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk
        if self._fail:
            raise httpx.ConnectError("connection dropped mid-stream")


class _FakeClient:
    def __init__(self, chunks: list[bytes], *, fail: bool = False, **_: object) -> None:
        self._chunks = chunks
        self._fail = fail

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def stream(self, _method: str, _url: str) -> _FakeResponse:
        return _FakeResponse(self._chunks, fail=self._fail)


def _patch_client(monkeypatch: pytest.MonkeyPatch, chunks: list[bytes], *, fail: bool = False) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(chunks, fail=fail, **kwargs))


def test_is_cached_requires_matching_checksum(spec: ModelSpec, isolated_models: Path) -> None:
    path = model_manager.model_path(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTENT)
    assert model_manager.is_cached(spec) is True

    path.write_bytes(b"corrupted")
    assert model_manager.is_cached(spec) is False


@pytest.mark.asyncio
async def test_ensure_model_returns_cache_without_downloading(spec: ModelSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    path = model_manager.model_path(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTENT)

    def explode(**_: object):
        raise AssertionError("cache hit must not trigger a download")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    assert await model_manager.ensure_model(spec) == path


@pytest.mark.asyncio
async def test_ensure_model_streams_and_verifies_download(spec: ModelSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [CONTENT[:8], CONTENT[8:]])
    path = await model_manager.ensure_model(spec)
    assert path.read_bytes() == CONTENT
    assert not path.with_name(f"{spec.file_name}.partial").exists()


@pytest.mark.asyncio
async def test_download_checksum_mismatch_is_retryable_and_cleans_partial(spec: ModelSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [b"different-bytes"])
    with pytest.raises(WorkerError) as caught:
        await model_manager.ensure_model(spec)
    assert caught.value.code == "MODEL_CHECKSUM_MISMATCH"
    assert caught.value.retryable is True
    assert not model_manager.model_path(spec).exists()
    assert not model_manager.model_path(spec).with_name(f"{spec.file_name}.partial").exists()


@pytest.mark.asyncio
async def test_download_failure_is_retryable_and_cleans_partial(spec: ModelSpec, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, [CONTENT[:8]], fail=True)
    with pytest.raises(WorkerError) as caught:
        await model_manager.ensure_model(spec)
    assert caught.value.code == "MODEL_DOWNLOAD_FAILED"
    assert caught.value.retryable is True
    assert not model_manager.model_path(spec).exists()
    assert not model_manager.model_path(spec).with_name(f"{spec.file_name}.partial").exists()
