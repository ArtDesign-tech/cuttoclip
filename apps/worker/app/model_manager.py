"""Download-on-first-use manager for the local ONNX vision models.

YuNet (face detection) and Silero VAD (speech activity) are fetched from pinned
commits, verified by SHA-256, and cached under the data root so later renders run
fully offline. Every failure surfaces as a retryable WorkerError so a single Smart
Portrait clip can fail without taking down non-smart clips in the same job.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import WorkerError
from .storage import default_data_root


@dataclass(frozen=True)
class ModelSpec:
    key: str
    file_name: str
    url: str
    sha256: str


YUNET = ModelSpec(
    key="yunet",
    file_name="face_detection_yunet_2026may.onnx",
    url=(
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        "47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/"
        "face_detection_yunet_2026may.onnx"
    ),
    sha256="ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
)

SILERO_VAD = ModelSpec(
    key="silero_vad",
    file_name="silero_vad.onnx",
    url=(
        "https://raw.githubusercontent.com/snakers4/silero-vad/"
        "7e30209a3e901f9842f81b225f3e93d8199902b1/src/silero_vad/data/silero_vad.onnx"
    ),
    sha256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
)

MODELS: dict[str, ModelSpec] = {YUNET.key: YUNET, SILERO_VAD.key: SILERO_VAD}

_download_lock = asyncio.Lock()


def models_dir() -> Path:
    return default_data_root() / "models"


def model_path(spec: ModelSpec) -> Path:
    return models_dir() / spec.file_name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_cached(spec: ModelSpec) -> bool:
    """True only when the file exists and its checksum matches the pinned value."""
    path = model_path(spec)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        return _sha256(path) == spec.sha256
    except OSError:
        return False


def all_cached() -> bool:
    return all(is_cached(spec) for spec in MODELS.values())


async def _download(spec: ModelSpec) -> Path:
    try:
        import httpx
    except ImportError as error:
        raise WorkerError(
            "MODEL_DOWNLOAD_UNAVAILABLE",
            "httpx is required to download vision models.",
            status_code=503,
            retryable=True,
        ) from error

    target = model_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A per-file .partial guards against a half-written model being cached if the
    # process dies mid-download; the checksum is verified before the atomic rename.
    partial = target.with_name(f"{spec.file_name}.partial")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", spec.url) as response:
                response.raise_for_status()
                with partial.open("wb") as sink:
                    async for chunk in response.aiter_bytes():
                        sink.write(chunk)
                        digest.update(chunk)
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise WorkerError(
            "MODEL_DOWNLOAD_FAILED",
            f"Could not download the {spec.key} model.",
            status_code=502,
            retryable=True,
            details=str(error),
        ) from error

    if digest.hexdigest() != spec.sha256:
        partial.unlink(missing_ok=True)
        raise WorkerError(
            "MODEL_CHECKSUM_MISMATCH",
            f"The downloaded {spec.key} model failed checksum verification.",
            status_code=502,
            retryable=True,
        )
    os.replace(partial, target)
    return target


async def ensure_model(spec: ModelSpec) -> Path:
    """Return a validated local model path, downloading it once if needed."""
    if is_cached(spec):
        return model_path(spec)
    async with _download_lock:
        if is_cached(spec):
            return model_path(spec)
        return await _download(spec)


async def ensure_yunet_model() -> Path:
    """Return the face detector without downloading the speech model."""
    return await ensure_model(YUNET)


async def ensure_vision_models() -> tuple[Path, Path]:
    yunet = await ensure_yunet_model()
    silero = await ensure_model(SILERO_VAD)
    return yunet, silero


def onnxruntime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def vision_status() -> str:
    """Reported through capabilities so Settings can distinguish the three states."""
    if not onnxruntime_available():
        return "unavailable"
    return "ready" if all_cached() else "download-on-first-use"
