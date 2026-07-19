from __future__ import annotations

from pathlib import Path

import pytest

from app.captions import ass_filter, build_ass
from app.models import Transcript, TranscriptSegment, TranscriptWord


@pytest.fixture
def timed_transcript() -> Transcript:
    words = [
        TranscriptWord(text="Ship", startSeconds=10.0, endSeconds=10.4),
        TranscriptWord(text="the", startSeconds=10.5, endSeconds=10.8),
        # Deliberate one-second natural pause before "tiny".
        TranscriptWord(text="tiny", startSeconds=11.8, endSeconds=12.2),
        TranscriptWord(text="version", startSeconds=12.3, endSeconds=12.9),
    ]
    return Transcript(
        text="Ship the tiny version",
        language="en",
        durationSeconds=30,
        words=words,
        segments=[TranscriptSegment(text="Ship the tiny version", startSeconds=10, endSeconds=13, words=words)],
    )


@pytest.mark.parametrize("preset", ["clean", "bold_focus", "karaoke", "subtitle_box"])
def test_all_caption_presets_emit_ass_events(tmp_path: Path, timed_transcript: Transcript, preset: str) -> None:
    path = build_ass(timed_transcript, 10, 25, preset, "portrait", tmp_path / f"{preset}.ass")
    content = path.read_text(encoding="utf-8-sig")
    assert "PlayResX: 1080" in content
    assert "Fontname" in content and "Style: Caption,Inter" in content
    assert "Dialogue:" in content
    if preset == "subtitle_box":
        assert ",3,0,0,2," in content
    if preset == "bold_focus":
        assert r"\c&H005F6BFF&" in content


def test_gaming_portrait_uses_portrait_canvas_and_standard_margin(tmp_path: Path, timed_transcript: Transcript) -> None:
    path = build_ass(timed_transcript, 10, 25, "clean", "gaming_portrait", tmp_path / "gaming.ass")
    content = path.read_text(encoding="utf-8-sig")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Caption,Inter,72" in content
    assert ",2,64,64,220,1" in content


def test_karaoke_preserves_inter_word_gap(tmp_path: Path, timed_transcript: Transcript) -> None:
    path = build_ass(timed_transcript, 10, 25, "karaoke", "portrait", tmp_path / "karaoke.ass")
    content = path.read_text(encoding="utf-8-sig")
    assert r"{\k100}\h{\kf40}tiny" in content


def test_ass_filter_escapes_windows_drive_and_includes_bundled_fonts(tmp_path: Path) -> None:
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    expression = ass_filter(r"C:\Cut To Clip\caption.ass", fonts)
    assert r"C\:/Cut To Clip/caption.ass" in expression
    assert ":fontsdir=" in expression

