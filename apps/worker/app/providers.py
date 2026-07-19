"""Provider-mode configuration and the BYOK (bring-your-own-key) adapters.

The worker supports two provider modes:

* ``managed`` — transcription and highlight selection are proxied through the
  operator's gateway (the historical behaviour). All gateway plumbing lives in
  ``main.py``.
* ``byok`` — the worker calls Groq (transcription) and Gemini (AI Moments)
  directly using keys supplied by the user. The gateway is never contacted.

This module owns everything unique to ``byok`` mode: reading the provider
configuration from the environment, the highlight windowing/ranking/dedup
algorithm (ported from the gateway's ``domain.ts`` so BYOK output matches the
managed path), and building/parsing the Gemini structured-output request.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

try:
    from .errors import WorkerError
    from .models import Candidate, TranscriptSegment
except ImportError:  # PyInstaller can execute the worker entrypoint as a script.
    from errors import WorkerError
    from models import Candidate, TranscriptSegment


ProviderMode = Literal["managed", "byok"]

DEFAULT_GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

HIGHLIGHT_WINDOW_SECONDS = 12 * 60
HIGHLIGHT_OVERLAP_SECONDS = 30
OVERLAP_DEDUPE_RATIO = 0.5
CANDIDATE_ACCENTS = ("coral", "mint", "violet")


def provider_mode() -> ProviderMode:
    """Return the configured provider mode, defaulting to ``managed``.

    Any unrecognised value falls back to ``managed`` so a malformed environment
    never silently routes the user's own keys through code paths that expect a
    gateway, and vice versa.
    """

    raw = os.getenv("CUTTOCLIP_PROVIDER_MODE", "").strip().lower()
    return "byok" if raw == "byok" else "managed"


@dataclass(frozen=True)
class GroqConfig:
    api_key: str
    transcription_url: str
    model: str


@dataclass(frozen=True)
class GeminiConfig:
    api_key: str
    model: str
    base_url: str

    @property
    def generate_content_url(self) -> str:
        # ``:generateContent`` is appended without the key; the key travels in
        # the ``x-goog-api-key`` header so it never lands in a URL/log line.
        return f"{self.base_url.rstrip('/')}/models/{self.model}:generateContent"


# HTTP statuses that mean "this key is exhausted/rejected" — rotate to the next
# key rather than retrying the same one. 401/403 = invalid/revoked key,
# 402 = quota/billing, 429 = rate limit. Temp/free-tier keys hit these often.
KEY_ROTATE_STATUSES = frozenset({401, 402, 403, 429})


def is_key_rotate_status(status_code: int) -> bool:
    return status_code in KEY_ROTATE_STATUSES


def _parse_key_list(plural_var: str, singular_var: str) -> list[str]:
    """Read a comma-separated key list, falling back to the legacy single-key var.

    Order is preserved and duplicates/blanks are dropped. The singular var is
    appended if present and not already listed, so an older config keeps working.
    """

    keys: list[str] = []
    raw_plural = os.getenv(plural_var, "")
    for part in raw_plural.split(","):
        candidate = part.strip()
        if candidate and candidate not in keys:
            keys.append(candidate)
    single = os.getenv(singular_var, "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def groq_api_keys() -> list[str]:
    return _parse_key_list("CUTTOCLIP_GROQ_API_KEYS", "CUTTOCLIP_GROQ_API_KEY")


def gemini_api_keys() -> list[str]:
    return _parse_key_list("CUTTOCLIP_GEMINI_API_KEYS", "CUTTOCLIP_GEMINI_API_KEY")


def groq_config(api_key: str = "") -> GroqConfig:
    """Groq config for a specific key. Defaults to the first configured key."""

    key = api_key or next(iter(groq_api_keys()), "")
    return GroqConfig(
        api_key=key,
        transcription_url=os.getenv("CUTTOCLIP_GROQ_TRANSCRIPTION_URL", "").strip()
        or DEFAULT_GROQ_TRANSCRIPTION_URL,
        model=os.getenv("CUTTOCLIP_GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL,
    )


def gemini_config(api_key: str = "") -> GeminiConfig:
    """Gemini config for a specific key. Defaults to the first configured key."""

    key = api_key or next(iter(gemini_api_keys()), "")
    return GeminiConfig(
        api_key=key,
        model=os.getenv("CUTTOCLIP_GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
        base_url=os.getenv("CUTTOCLIP_GEMINI_BASE_URL", "").strip() or DEFAULT_GEMINI_BASE_URL,
    )


def byok_configuration_error() -> WorkerError | None:
    """Validate that at least one key per provider is present before any call.

    There is intentionally no silent fallback to the gateway: a missing key is a
    hard, actionable error so the UI can prompt ``Perbarui API key`` or
    ``Beralih ke Managed Beta``.
    """

    if not groq_api_keys():
        return WorkerError(
            "BYOK_GROQ_KEY_MISSING",
            "A Groq API key is required for transcription in API Key mode.",
            status_code=503,
            retryable=False,
        )
    if not gemini_api_keys():
        return WorkerError(
            "BYOK_GEMINI_KEY_MISSING",
            "A Gemini API key is required for AI Moments in API Key mode.",
            status_code=503,
            retryable=False,
        )
    return None


def groq_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# --- Highlight windowing / ranking / dedup (ported from gateway domain.ts) ---


@dataclass(frozen=True)
class HighlightWindow:
    index: int
    startSeconds: float
    endSeconds: float
    segments: list[TranscriptSegment]


def create_highlight_windows(
    segments: list[TranscriptSegment],
    source_duration_seconds: float,
    window_seconds: float = HIGHLIGHT_WINDOW_SECONDS,
    overlap_seconds: float = HIGHLIGHT_OVERLAP_SECONDS,
) -> list[HighlightWindow]:
    if window_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= window_seconds:
        raise ValueError("Highlight window configuration is invalid.")
    windows: list[HighlightWindow] = []
    step_seconds = window_seconds - overlap_seconds
    start_seconds = 0.0
    index = 0
    while start_seconds < source_duration_seconds:
        end_seconds = min(source_duration_seconds, start_seconds + window_seconds)
        window_segments = [
            segment
            for segment in segments
            if segment.endSeconds > start_seconds and segment.startSeconds < end_seconds
        ]
        if window_segments:
            windows.append(HighlightWindow(index, start_seconds, end_seconds, window_segments))
        if end_seconds >= source_duration_seconds:
            break
        start_seconds += step_seconds
        index += 1
    return windows


def overlap_ratio(left: Candidate, right: Candidate) -> float:
    overlap = max(
        0.0,
        min(left.endSeconds, right.endSeconds) - max(left.startSeconds, right.startSeconds),
    )
    shortest = min(left.endSeconds - left.startSeconds, right.endSeconds - right.startSeconds)
    return overlap / shortest if shortest > 0 else 0.0


def rank_and_dedupe_candidates(candidates: list[Candidate], clip_count: int) -> list[Candidate]:
    ranked = sorted(candidates, key=lambda item: (-item.score, item.startSeconds))
    selected: list[Candidate] = []
    for candidate in ranked:
        if any(overlap_ratio(existing, candidate) > OVERLAP_DEDUPE_RATIO for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= clip_count:
            break
    finalized: list[Candidate] = []
    for index, candidate in enumerate(selected):
        finalized.append(
            candidate.model_copy(
                update={
                    "id": f"clip-{index + 1:02d}",
                    "accent": CANDIDATE_ACCENTS[index % len(CANDIDATE_ACCENTS)],
                    "source": "ai",
                }
            )
        )
    return finalized


# --- Gemini structured-output request/response ---

_HIGHLIGHT_SYSTEM_INSTRUCTION = " ".join(
    [
        "Select self-contained, compelling clips only from the supplied timed window.",
        "Return one JSON object with a clips array and no prose.",
        "Each clip must contain numeric startSeconds/endSeconds, title, hook, reason, and an integer score from 0 to 100.",
        "Every clip must stay inside the supplied window and requested duration range. Use absolute source timestamps.",
        "Return at most the requested clipCount candidates, or an empty array when no honest candidate exists.",
    ]
)

# JSON schema handed to Gemini so it emits structured output we can parse without
# stripping markdown fences or tolerating prose.
_HIGHLIGHT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "startSeconds": {"type": "number"},
                    "endSeconds": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["startSeconds", "endSeconds", "title", "hook", "reason", "score"],
            },
        }
    },
    "required": ["clips"],
}


def build_gemini_highlight_request(
    window: HighlightWindow,
    source_duration_seconds: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    prompt = {
        "sourceDurationSeconds": source_duration_seconds,
        "window": {"startSeconds": window.startSeconds, "endSeconds": window.endSeconds},
        "settings": settings,
        "segments": [
            {
                "text": segment.text,
                "startSeconds": segment.startSeconds,
                "endSeconds": segment.endSeconds,
            }
            for segment in window.segments
        ],
    }
    return {
        "systemInstruction": {"parts": [{"text": _HIGHLIGHT_SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(prompt)}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": _HIGHLIGHT_RESPONSE_SCHEMA,
        },
    }


def extract_gemini_clips(payload: Any) -> list[dict[str, Any]]:
    """Pull the ``clips`` array out of a Gemini ``generateContent`` response.

    Accepts either an already-unwrapped ``{"clips": [...]}`` object (useful for a
    provider that returns structured output verbatim) or the standard
    ``candidates[].content.parts[].text`` envelope holding a JSON string.
    """

    if isinstance(payload, dict) and isinstance(payload.get("clips"), list):
        return payload["clips"]
    if not isinstance(payload, dict):
        raise ValueError("Gemini response is not a JSON object.")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response is missing candidates.")
    first = candidates[0]
    content = first.get("content") if isinstance(first, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise ValueError("Gemini candidate is missing content parts.")
    text = "".join(
        part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()
    if not text:
        raise ValueError("Gemini candidate content is empty.")
    parsed = json.loads(text)
    clips = parsed.get("clips") if isinstance(parsed, dict) else None
    if not isinstance(clips, list):
        raise ValueError("Gemini structured output has no clips array.")
    return clips


def validate_window_candidates(
    clips: list[dict[str, Any]],
    window: HighlightWindow,
    source_duration_seconds: float,
    min_duration: float,
    max_duration: float,
) -> list[Candidate]:
    """Validate raw clips against window/source/duration bounds.

    Out-of-range candidates are dropped rather than raising, so one greedy window
    cannot poison the whole analysis. The caller decides what to do with an empty
    result across all windows.
    """

    validated: list[Candidate] = []
    for index, raw in enumerate(clips):
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw["startSeconds"])
            end = float(raw["endSeconds"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = end - start
        within_window = (
            start >= window.startSeconds - 0.001 and end <= window.endSeconds + 0.001
        )
        within_source = end <= source_duration_seconds + 0.001
        if (
            end <= start
            or duration < min_duration - 0.001
            or duration > max_duration + 0.001
            or not within_window
            or not within_source
        ):
            continue
        try:
            candidate = Candidate.model_validate(
                {
                    "id": f"clip-w{window.index}-{index + 1:02d}",
                    "startSeconds": start,
                    "endSeconds": end,
                    "title": str(raw.get("title") or "").strip(),
                    "hook": str(raw.get("hook") or "").strip(),
                    "reason": str(raw.get("reason") or "").strip(),
                    "score": int(raw.get("score") or 0),
                    "source": "ai",
                }
            )
        except Exception:
            continue
        validated.append(candidate)
    return validated
