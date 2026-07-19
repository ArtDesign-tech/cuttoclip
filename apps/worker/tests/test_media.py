from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import media
from app.errors import WorkerError
from app.media import AUDIO_CHUNK_SECONDS, AUDIO_OVERLAP_SECONDS, extract_audio_chunks, merge_transcripts, normalize_transcript
from app.models import Transcript, TranscriptSegment, TranscriptWord


def word(text: str, start: float, end: float) -> TranscriptWord:
    return TranscriptWord(text=text, startSeconds=start, endSeconds=end)


def test_normalize_transcript_accepts_groq_and_canonical_timestamp_shapes() -> None:
    transcript = normalize_transcript(
        {
            "text": "Hello world",
            "language": "en",
            "duration": 2.0,
            "words": [
                {"word": "Hello", "start": 0.1, "end": 0.8},
                {"text": "world", "startSeconds": 0.9, "endSeconds": 1.8},
            ],
            "segments": [{"text": "Hello world", "start": 0, "end": 2}],
        }
    )
    assert transcript.durationSeconds == 2
    assert [item.text for item in transcript.words] == ["Hello", "world"]
    assert transcript.segments[0].endSeconds == 2


def test_merge_transcripts_offsets_chunks_and_removes_overlap_prefix() -> None:
    first_words = [word("hello", 1197.5, 1198.2), word("world", 1198.5, 1199.2)]
    second_words = [word("world", 0.0, 0.7), word("again", 0.8, 1.4)]
    merged = merge_transcripts(
        [
            (0, Transcript(text="hello world", durationSeconds=1200, words=first_words, segments=[])),
            (1198.5, Transcript(text="world again", durationSeconds=2, words=second_words, segments=[])),
        ],
        1300,
    )
    assert [item.text for item in merged.words] == ["hello", "world", "again"]
    assert merged.words[-1].startSeconds == pytest.approx(1199.3)


@pytest.mark.asyncio
async def test_audio_chunks_are_20_minutes_with_1_5_second_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "ffmpeg_path", lambda: "ffmpeg")

    async def fake_run(*args: str, cwd: str | None = None):
        Path(args[-1]).write_bytes(b"mp3")
        return 0, "", ""

    monkeypatch.setattr(media, "run_command", fake_run)
    progress: list[tuple[int, int]] = []

    async def on_progress(current: int, total: int) -> None:
        progress.append((current, total))

    chunks = await extract_audio_chunks("source.mp4", tmp_path, 2400, on_progress)
    assert [item.offset_seconds for item in chunks] == [0, AUDIO_CHUNK_SECONDS - AUDIO_OVERLAP_SECONDS, 2397]
    assert [round(item.duration_seconds, 1) for item in chunks] == [1200, 1200, 3]
    assert progress[-1] == (3, 3)


@pytest.mark.asyncio
async def test_probe_rejects_sources_above_two_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "ffprobe_path", lambda: "ffprobe")
    payload = {
        "format": {"duration": "7200.1"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
    }

    async def fake_run(*args: str, cwd: str | None = None):
        return 0, json.dumps(payload), ""

    monkeypatch.setattr(media, "run_command", fake_run)
    with pytest.raises(WorkerError) as caught:
        await media.probe_media("too-long.mp4")
    assert caught.value.code == "SOURCE_TOO_LONG"

