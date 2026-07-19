from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .errors import WorkerError
from .models import Transcript, TranscriptSegment, TranscriptWord


MAX_SOURCE_DURATION_SECONDS = 2 * 60 * 60
AUDIO_CHUNK_SECONDS = 20 * 60
AUDIO_OVERLAP_SECONDS = 1.5


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_seconds: float
    duration_seconds: float


def ffmpeg_path() -> str | None:
    configured = os.getenv("CUTTOCLIP_FFMPEG")
    return configured if configured and Path(configured).exists() else shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    configured = os.getenv("CUTTOCLIP_FFPROBE")
    if configured and Path(configured).exists():
        return configured
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.exists():
            return str(sibling)
    return shutil.which("ffprobe")


async def run_command(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def probe_media(path: str | Path) -> dict[str, float | int | str]:
    executable = ffprobe_path()
    if not executable:
        raise WorkerError(
            "FFPROBE_UNAVAILABLE",
            "FFprobe is required to read source metadata.",
            status_code=503,
            retryable=True,
        )
    code, stdout, stderr = await run_command(
        executable,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,duration",
        "-of",
        "json",
        str(path),
    )
    if code != 0:
        raise WorkerError(
            "MEDIA_PROBE_FAILED",
            "The selected source is not a readable video.",
            status_code=400,
            details=stderr[-1000:] or None,
        )
    try:
        payload = json.loads(stdout)
        streams = payload.get("streams") or []
        video = next(item for item in streams if item.get("codec_type") == "video")
        duration = float((payload.get("format") or {}).get("duration") or video.get("duration") or 0)
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
        raise WorkerError(
            "MEDIA_METADATA_INVALID",
            "FFprobe did not return valid video metadata.",
            status_code=400,
            details=str(error),
        ) from error
    if duration <= 0 or width <= 0 or height <= 0:
        raise WorkerError(
            "MEDIA_METADATA_INVALID",
            "The source must contain a video stream with a known duration and resolution.",
            status_code=400,
        )
    if duration > MAX_SOURCE_DURATION_SECONDS + 0.01:
        raise WorkerError(
            "SOURCE_TOO_LONG",
            "Sources longer than 2 hours are not supported.",
            status_code=422,
            details={"durationSeconds": duration, "maximumSeconds": MAX_SOURCE_DURATION_SECONDS},
        )
    return {
        "durationSeconds": duration,
        "width": width,
        "height": height,
        "resolution": f"{width} x {height}",
    }


async def extract_audio_chunks(
    source_path: str | Path,
    destination: Path,
    duration_seconds: float,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[AudioChunk]:
    executable = ffmpeg_path()
    if not executable:
        raise WorkerError(
            "FFMPEG_UNAVAILABLE",
            "FFmpeg is required to extract audio.",
            status_code=503,
            retryable=True,
        )
    chunks_dir = destination / "audio-chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    # Clean only generated chunks; source media and manifests are untouched.
    for stale in chunks_dir.glob("chunk-*.mp3"):
        stale.unlink(missing_ok=True)
    step = AUDIO_CHUNK_SECONDS - AUDIO_OVERLAP_SECONDS
    chunk_count = max(1, math.ceil(max(0.0, duration_seconds - AUDIO_OVERLAP_SECONDS) / step))
    chunks: list[AudioChunk] = []
    for index in range(chunk_count):
        offset = index * step
        if offset >= duration_seconds:
            break
        length = min(float(AUDIO_CHUNK_SECONDS), duration_seconds - offset)
        target = chunks_dir / f"chunk-{index + 1:03d}.mp3"
        code, _, stderr = await run_command(
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{offset:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{length:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(target),
        )
        if code != 0 or not target.exists() or target.stat().st_size == 0:
            raise WorkerError(
                "AUDIO_EXTRACTION_FAILED",
                f"Audio extraction failed for chunk {index + 1}.",
                retryable=True,
                details=stderr[-1000:] or None,
            )
        chunks.append(AudioChunk(target, offset, length))
        if on_progress:
            await on_progress(index + 1, chunk_count)
    return chunks


def _number(item: dict[str, object], *keys: str, default: float = 0) -> float:
    for key in keys:
        value = item.get(key)
        if value is not None:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return default


def _normalize_word(item: dict[str, object], duration_hint: float) -> TranscriptWord | None:
    text = str(item.get("text") or item.get("word") or "").strip()
    if not text:
        return None
    start = _number(item, "startSeconds", "start")
    end = _number(item, "endSeconds", "end", default=start)
    if end < start:
        end = start
    if duration_hint > 0:
        start = min(start, duration_hint)
        end = min(max(start, end), duration_hint)
    return TranscriptWord(text=text, startSeconds=start, endSeconds=end)


def normalize_transcript(payload: dict[str, object], duration_hint: float = 0) -> Transcript:
    duration = _number(payload, "durationSeconds", "duration", default=duration_hint)
    language = str(payload.get("language") or "unknown")
    raw_words = payload.get("words") if isinstance(payload.get("words"), list) else []
    words = [word for item in raw_words if isinstance(item, dict) and (word := _normalize_word(item, duration))]

    segments: list[TranscriptSegment] = []
    raw_segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        start = _number(item, "startSeconds", "start")
        end = _number(item, "endSeconds", "end", default=start)
        if end < start:
            end = start
        segment_words_raw = item.get("words") if isinstance(item.get("words"), list) else []
        segment_words = [
            word
            for raw in segment_words_raw
            if isinstance(raw, dict) and (word := _normalize_word(raw, duration))
        ]
        text = str(item.get("text") or "").strip() or " ".join(word.text for word in segment_words)
        if text or segment_words:
            segments.append(
                TranscriptSegment(text=text, startSeconds=start, endSeconds=end, words=segment_words)
            )
    if not words:
        words = [word for segment in segments for word in segment.words]
    if not segments and words:
        segments = segments_from_words(words)
    text = str(payload.get("text") or "").strip()
    if not text:
        text = " ".join(segment.text for segment in segments).strip() or " ".join(word.text for word in words)
    if duration <= 0:
        duration = max([0.0, *(word.endSeconds for word in words), *(segment.endSeconds for segment in segments)])
    if not segments and text:
        segments = [TranscriptSegment(text=text, startSeconds=0, endSeconds=duration or duration_hint)]
    return Transcript(text=text, language=language, durationSeconds=duration, words=words, segments=segments)


def segments_from_words(words: list[TranscriptWord]) -> list[TranscriptSegment]:
    if not words:
        return []
    segments: list[TranscriptSegment] = []
    current: list[TranscriptWord] = []
    for word in words:
        if current and (word.startSeconds - current[-1].endSeconds > 1.0 or len(current) >= 18):
            segments.append(_segment_for_words(current))
            current = []
        current.append(word)
        if re.search(r"[.!?][\"']?$", word.text) and len(current) >= 4:
            segments.append(_segment_for_words(current))
            current = []
    if current:
        segments.append(_segment_for_words(current))
    return segments


def _segment_for_words(words: list[TranscriptWord]) -> TranscriptSegment:
    return TranscriptSegment(
        text=" ".join(word.text for word in words),
        startSeconds=words[0].startSeconds,
        endSeconds=words[-1].endSeconds,
        words=list(words),
    )


def _token(value: str) -> str:
    return re.sub(r"\W+", "", value, flags=re.UNICODE).casefold()


def _dedupe_prefix(existing: list[TranscriptWord], incoming: list[TranscriptWord]) -> int:
    if not existing or not incoming:
        return 0
    maximum = min(48, len(existing), len(incoming))
    for size in range(maximum, 0, -1):
        left = [_token(item.text) for item in existing[-size:]]
        right = [_token(item.text) for item in incoming[:size]]
        if left == right and all(left):
            return size
    # Groq can split punctuation differently. Remove only same-token words that
    # occupy effectively the same timestamp in the 1.5 second overlap.
    count = 0
    tail = existing[-24:]
    for word in incoming:
        match = next(
            (
                old
                for old in reversed(tail)
                if _token(old.text) == _token(word.text)
                and abs(old.startSeconds - word.startSeconds) <= AUDIO_OVERLAP_SECONDS + 0.5
            ),
            None,
        )
        if not match:
            break
        count += 1
    return count


def merge_transcripts(chunks: list[tuple[float, Transcript]], source_duration: float) -> Transcript:
    merged_words: list[TranscriptWord] = []
    merged_segments: list[TranscriptSegment] = []
    languages: list[str] = []
    for offset, transcript in sorted(chunks, key=lambda item: item[0]):
        if transcript.language and transcript.language != "unknown":
            languages.append(transcript.language)
        incoming_words = [
            TranscriptWord(
                text=word.text,
                startSeconds=min(source_duration, word.startSeconds + offset),
                endSeconds=min(source_duration, word.endSeconds + offset),
            )
            for word in transcript.words
        ]
        drop = _dedupe_prefix(merged_words, incoming_words)
        merged_words.extend(incoming_words[drop:])

        for segment in transcript.segments:
            shifted = TranscriptSegment(
                text=segment.text,
                startSeconds=min(source_duration, segment.startSeconds + offset),
                endSeconds=min(source_duration, segment.endSeconds + offset),
                words=[
                    TranscriptWord(
                        text=word.text,
                        startSeconds=min(source_duration, word.startSeconds + offset),
                        endSeconds=min(source_duration, word.endSeconds + offset),
                    )
                    for word in segment.words
                ],
            )
            duplicate = any(
                _token(old.text) == _token(shifted.text)
                and abs(old.startSeconds - shifted.startSeconds) <= AUDIO_OVERLAP_SECONDS + 0.5
                for old in merged_segments[-8:]
            )
            if not duplicate:
                merged_segments.append(shifted)

    merged_words.sort(key=lambda word: (word.startSeconds, word.endSeconds))
    merged_segments.sort(key=lambda segment: (segment.startSeconds, segment.endSeconds))
    if merged_words:
        # Rebuilding segments from the deduplicated word stream prevents overlap
        # text from leaking into highlight window boundaries.
        merged_segments = segments_from_words(merged_words)
    text = " ".join(segment.text for segment in merged_segments).strip()
    return Transcript(
        text=text,
        language=languages[0] if languages else "unknown",
        durationSeconds=source_duration,
        words=merged_words,
        segments=merged_segments,
    )

