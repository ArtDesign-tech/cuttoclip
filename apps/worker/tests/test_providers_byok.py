from __future__ import annotations

from pathlib import Path

import pytest

from app import main, providers
from app.errors import WorkerError
from app.media import AudioChunk
from app.models import Candidate, Project, ProjectSettings, Transcript, TranscriptSegment


def _segment(start: float, end: float, text: str = "segment") -> TranscriptSegment:
    return TranscriptSegment(text=text, startSeconds=start, endSeconds=end)


def _candidate(start: float, end: float, score: int, cid: str = "c") -> Candidate:
    return Candidate(id=cid, startSeconds=start, endSeconds=end, score=score)


class FakeResponse:
    def __init__(self, status_code: int, payload: object, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._payload


class FakeClient:
    """Records every request and replays a queue of responses."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0)


# --- provider mode / configuration ---


def test_provider_mode_defaults_to_managed(monkeypatch) -> None:
    monkeypatch.delenv("CUTTOCLIP_PROVIDER_MODE", raising=False)
    assert providers.provider_mode() == "managed"


def test_provider_mode_unknown_value_falls_back_to_managed(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "cloud")
    assert providers.provider_mode() == "managed"


def test_byok_configuration_error_reports_missing_keys(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.delenv("CUTTOCLIP_GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CUTTOCLIP_GEMINI_API_KEY", raising=False)
    error = providers.byok_configuration_error()
    assert error is not None and error.code == "BYOK_GROQ_KEY_MISSING"

    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-test")
    error = providers.byok_configuration_error()
    assert error is not None and error.code == "BYOK_GEMINI_KEY_MISSING"

    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-test")
    assert providers.byok_configuration_error() is None


def test_gemini_url_never_embeds_the_key(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "super-secret-key")
    config = providers.gemini_config()
    assert "super-secret-key" not in config.generate_content_url
    assert config.generate_content_url.endswith(":generateContent")


# --- windowing / ranking / dedup ---


def test_create_highlight_windows_matches_12min_30s_layout() -> None:
    segments = [_segment(i * 60, i * 60 + 30) for i in range(30)]  # 0..1800s
    windows = providers.create_highlight_windows(segments, 1800)
    assert windows[0].startSeconds == 0
    assert windows[0].endSeconds == 720
    # step = 720 - 30 = 690
    assert windows[1].startSeconds == 690
    assert all(w.segments for w in windows)


def test_create_highlight_windows_skips_silent_windows() -> None:
    segments = [_segment(0, 30)]
    windows = providers.create_highlight_windows(segments, 2000)
    # Only the first window overlaps any segment.
    assert len(windows) == 1
    assert windows[0].index == 0


def test_rank_and_dedupe_drops_overlapping_and_relabels() -> None:
    candidates = [
        _candidate(0, 30, 90, "a"),
        _candidate(5, 35, 80, "b"),   # >50% overlap with a → dropped
        _candidate(100, 130, 70, "c"),
    ]
    kept = providers.rank_and_dedupe_candidates(candidates, clip_count=5)
    assert [c.startSeconds for c in kept] == [0, 100]
    assert [c.id for c in kept] == ["clip-01", "clip-02"]
    assert [c.accent for c in kept] == ["coral", "mint"]


def test_rank_and_dedupe_respects_clip_count() -> None:
    candidates = [_candidate(i * 100, i * 100 + 30, 50 + i, str(i)) for i in range(5)]
    kept = providers.rank_and_dedupe_candidates(candidates, clip_count=2)
    assert len(kept) == 2
    # highest scores kept
    assert kept[0].score >= kept[1].score


# --- Gemini response parsing / validation ---


def test_extract_gemini_clips_from_generate_content_envelope() -> None:
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": '{"clips":[{"startSeconds":0,"endSeconds":30,"title":"t","hook":"h","reason":"r","score":80}]}'}]}}
        ]
    }
    clips = providers.extract_gemini_clips(payload)
    assert clips[0]["startSeconds"] == 0


def test_extract_gemini_clips_accepts_unwrapped_object() -> None:
    clips = providers.extract_gemini_clips({"clips": [{"startSeconds": 1}]})
    assert clips == [{"startSeconds": 1}]


def test_extract_gemini_clips_rejects_missing_candidates() -> None:
    with pytest.raises(ValueError):
        providers.extract_gemini_clips({"nope": True})


def test_validate_window_candidates_drops_out_of_range() -> None:
    window = providers.HighlightWindow(0, 0, 720, [_segment(0, 720)])
    clips = [
        {"startSeconds": 0, "endSeconds": 30, "title": "ok", "hook": "h", "reason": "r", "score": 80},
        {"startSeconds": 0, "endSeconds": 5, "title": "too short", "hook": "h", "reason": "r", "score": 90},
        {"startSeconds": 700, "endSeconds": 800, "title": "past window", "hook": "h", "reason": "r", "score": 90},
    ]
    validated = providers.validate_window_candidates(clips, window, 720, min_duration=15, max_duration=90)
    assert len(validated) == 1
    assert validated[0].endSeconds == 30


# --- direct Groq transcription (BYOK) ---


@pytest.mark.asyncio
async def test_byok_transcription_calls_groq_directly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-secret")
    # A gateway URL is set to prove BYOK never uses it.
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_URL", "https://gateway.example.test")

    client = FakeClient([
        FakeResponse(200, {
            "text": "hello",
            "language": "en",
            "duration": 1,
            "segments": [{"text": "hello", "start": 0, "end": 1}],
            "words": [{"word": "hello", "start": 0, "end": 1}],
        })
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    audio_path = tmp_path / "chunk.mp3"
    audio_path.write_bytes(b"audio")
    transcript = await main.transcribe_audio(AudioChunk(audio_path, 0, 1), "auto")

    assert transcript.text == "hello"
    assert len(client.calls) == 1
    assert client.calls[0]["url"] == providers.DEFAULT_GROQ_TRANSCRIPTION_URL
    assert client.calls[0]["headers"]["Authorization"] == "Bearer gsk-secret"


@pytest.mark.asyncio
async def test_byok_transcription_errors_when_key_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.delenv("CUTTOCLIP_GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CUTTOCLIP_GEMINI_API_KEY", raising=False)
    audio_path = tmp_path / "chunk.mp3"
    audio_path.write_bytes(b"audio")
    with pytest.raises(WorkerError, match="Groq API key"):
        await main.transcribe_audio(AudioChunk(audio_path, 0, 1), "auto")


# --- direct Gemini highlights (BYOK) ---


def _ready_project() -> Project:
    project = Project(id="p1", sourceLabel="clip.mp4", sourceKind="file")
    project.durationSeconds = 300
    project.transcript = Transcript(
        text="hello world",
        language="en",
        durationSeconds=300,
        segments=[_segment(0, 120, "hello"), _segment(120, 240, "world")],
    )
    project.transcriptReady = True
    return project


@pytest.mark.asyncio
async def test_byok_highlights_call_gemini_and_never_touch_gateway(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-secret")
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_URL", "https://gateway.example.test")

    gemini_payload = {
        "candidates": [
            {"content": {"parts": [{"text": '{"clips":[{"startSeconds":0,"endSeconds":30,"title":"Great","hook":"Hook","reason":"Reason","score":88}]}'}]}}
        ]
    }
    client = FakeClient([FakeResponse(200, gemini_payload)])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    project = _ready_project()
    kept = await main.request_highlights(project, ProjectSettings(clipCount=3))

    assert len(kept) == 1
    assert kept[0].id == "clip-01"
    assert kept[0].title == "Great"
    for call in client.calls:
        assert "gateway.example.test" not in str(call["url"])
        assert ":generateContent" in str(call["url"])
        assert call["headers"]["x-goog-api-key"] == "gem-secret"


@pytest.mark.asyncio
async def test_byok_highlights_error_on_malformed_gemini_output(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-secret")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-secret")

    client = FakeClient([FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    with pytest.raises(WorkerError, match="malformed"):
        await main.request_highlights(_ready_project(), ProjectSettings(clipCount=3))


# --- capabilities never leak keys ---


def test_provider_capability_report_hides_key_values(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-supersecret")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-supersecret")
    report = main._provider_capability_report("byok")
    serialized = str(report)
    assert "gsk-supersecret" not in serialized
    assert "gem-supersecret" not in serialized
    assert report["transcription"]["keyPresent"] is True
    assert report["highlights"]["model"] == providers.DEFAULT_GEMINI_MODEL


# --- multi-key parsing ---


def test_key_lists_parse_plural_and_legacy_singular(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEYS", " k1 , k2 ,, k1 ")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "k3")
    # order preserved, blanks + duplicates dropped, legacy singular appended
    assert providers.groq_api_keys() == ["k1", "k2", "k3"]

    monkeypatch.delenv("CUTTOCLIP_GROQ_API_KEYS", raising=False)
    assert providers.groq_api_keys() == ["k3"]


def test_is_key_rotate_status() -> None:
    assert providers.is_key_rotate_status(429)
    assert providers.is_key_rotate_status(401)
    assert providers.is_key_rotate_status(402)
    assert providers.is_key_rotate_status(403)
    assert not providers.is_key_rotate_status(500)
    assert not providers.is_key_rotate_status(200)


def test_capability_report_counts_keys_without_leaking(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEYS", "gsk-a,gsk-b,gsk-c")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEYS", "gem-a,gem-b")
    report = main._provider_capability_report("byok")
    assert report["transcription"]["keyCount"] == 3
    assert report["highlights"]["keyCount"] == 2
    serialized = str(report)
    for secret in ("gsk-a", "gsk-b", "gsk-c", "gem-a", "gem-b"):
        assert secret not in serialized


# --- transcription key rotation ---


def _groq_ok_payload() -> dict:
    return {
        "text": "hello",
        "language": "en",
        "duration": 1,
        "segments": [{"text": "hello", "start": 0, "end": 1}],
        "words": [{"word": "hello", "start": 0, "end": 1}],
    }


@pytest.mark.asyncio
async def test_transcription_rotates_to_next_key_on_429(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEYS", "gsk-first,gsk-second")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-any")

    client = FakeClient([
        FakeResponse(429, {"error": {"code": "rate_limited", "message": "slow down"}}),
        FakeResponse(200, _groq_ok_payload()),
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    audio = tmp_path / "chunk.mp3"
    audio.write_bytes(b"audio")
    transcript = await main.transcribe_audio(AudioChunk(audio, 0, 1), "auto")

    assert transcript.text == "hello"
    # first key got 429 → rotated to second key which succeeded
    assert client.calls[0]["headers"]["Authorization"] == "Bearer gsk-first"
    assert client.calls[1]["headers"]["Authorization"] == "Bearer gsk-second"


@pytest.mark.asyncio
async def test_transcription_errors_when_all_keys_exhausted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEYS", "gsk-1,gsk-2")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-any")

    client = FakeClient([
        FakeResponse(401, {"error": {"code": "invalid", "message": "bad key"}}),
        FakeResponse(429, {"error": {"code": "rate", "message": "limited"}}),
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    audio = tmp_path / "chunk.mp3"
    audio.write_bytes(b"audio")
    with pytest.raises(WorkerError, match="KEYS_EXHAUSTED|rate-limited or rejected") as info:
        await main.transcribe_audio(AudioChunk(audio, 0, 1), "auto")
    assert info.value.code == "TRANSCRIPTION_KEYS_EXHAUSTED"
    assert len(client.calls) == 2  # one attempt per key, no wasted retries


@pytest.mark.asyncio
async def test_transcription_does_not_rotate_on_server_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEYS", "gsk-1,gsk-2")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEY", "gem-any")
    # 5xx is a transient server error, not a key problem: retry the SAME key,
    # then succeed — the second key must never be touched.
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)

    client = FakeClient([
        FakeResponse(503, {"error": {"code": "busy", "message": "retry"}}),
        FakeResponse(200, _groq_ok_payload()),
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    audio = tmp_path / "chunk.mp3"
    audio.write_bytes(b"audio")
    transcript = await main.transcribe_audio(AudioChunk(audio, 0, 1), "auto")

    assert transcript.text == "hello"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer gsk-1"
    assert client.calls[1]["headers"]["Authorization"] == "Bearer gsk-1"


# --- highlight key rotation ---


def _gemini_ok_payload() -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": '{"clips":[{"startSeconds":0,"endSeconds":30,"title":"T","hook":"H","reason":"R","score":80}]}'}]}}
        ]
    }


@pytest.mark.asyncio
async def test_highlights_rotate_key_and_stick_to_it(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-any")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEYS", "gem-first,gem-second")

    # First window: gem-first 429 → rotate to gem-second (ok). Second window must
    # go straight to gem-second without retrying the dead key.
    client = FakeClient([
        FakeResponse(429, {"error": {"code": "rate", "message": "limited"}}),
        FakeResponse(200, _gemini_ok_payload()),
        FakeResponse(200, _gemini_ok_payload()),
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    # duration 1000 with a 720s window / 690s step yields exactly 2 windows.
    project = _ready_project()
    project.durationSeconds = 1000
    project.transcript.durationSeconds = 1000
    project.transcript.segments = [_segment(0, 700, "a"), _segment(750, 1000, "b")]

    kept = await main.request_highlights(project, ProjectSettings(clipCount=3))

    assert kept  # got candidates
    assert client.calls[0]["headers"]["x-goog-api-key"] == "gem-first"
    assert client.calls[1]["headers"]["x-goog-api-key"] == "gem-second"
    # third call (second window) uses the surviving key, never gem-first again
    assert client.calls[2]["headers"]["x-goog-api-key"] == "gem-second"


@pytest.mark.asyncio
async def test_highlights_error_when_all_keys_exhausted(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_PROVIDER_MODE", "byok")
    monkeypatch.setenv("CUTTOCLIP_GROQ_API_KEY", "gsk-any")
    monkeypatch.setenv("CUTTOCLIP_GEMINI_API_KEYS", "gem-1,gem-2")

    client = FakeClient([
        FakeResponse(429, {"error": {"code": "rate", "message": "limited"}}),
        FakeResponse(429, {"error": {"code": "rate", "message": "limited"}}),
    ])
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: client)

    with pytest.raises(WorkerError) as info:
        await main.request_highlights(_ready_project(), ProjectSettings(clipCount=3))
    assert info.value.code == "HIGHLIGHTS_KEYS_EXHAUSTED"


def _no_sleep(*_args, **_kwargs):
    async def _noop():
        return None
    return _noop()
