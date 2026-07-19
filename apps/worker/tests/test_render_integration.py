from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import main
from app.models import Candidate, Project, ProjectSettings, Transcript, TranscriptSegment, TranscriptWord
from app.vision import dual_facecam_filter, gaming_facecam_filter


@pytest.mark.asyncio
async def test_real_ffmpeg_render_burns_ass_and_produces_playable_mp4(isolated_store, tmp_path: Path) -> None:
    executable = main.ffmpeg_path()
    if not executable:
        pytest.skip("FFmpeg is not available")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=2:d=16",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=16",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    metadata = await main.probe_media(source)
    words = [
        TranscriptWord(text="Real", startSeconds=1, endSeconds=2),
        TranscriptWord(text="caption", startSeconds=2.1, endSeconds=3),
        TranscriptWord(text="render", startSeconds=3.1, endSeconds=4),
    ]
    transcript = Transcript(
        text="Real caption render",
        language="en",
        durationSeconds=16,
        words=words,
        segments=[TranscriptSegment(text="Real caption render", startSeconds=1, endSeconds=4, words=words)],
    )
    project = Project(
        id="render-project",
        sourceLabel="Fixture.mp4",
        sourceKind="file",
        sourcePath=str(source),
        durationSeconds=float(metadata["durationSeconds"]),
        width=int(metadata["width"]),
        height=int(metadata["height"]),
        resolution=str(metadata["resolution"]),
        transcriptReady=True,
        transcriptText=transcript.text,
        transcript=transcript,
    )
    isolated_store.save_project(project)
    settings = ProjectSettings(layout="landscape", captionPreset="clean")
    clip = Candidate(id="clip-1", startSeconds=0, endSeconds=15, title="Rendered clip")

    output = await main.render_one_clip(project, clip, settings, 1, isolated_store.project_output_dir(project))
    rendered = Path(output.path)
    rendered_metadata = await main.probe_media(rendered)

    assert output.status == "succeeded"
    assert rendered.is_file() and rendered.stat().st_size > 0
    assert rendered_metadata["resolution"] == "1920 x 1080"
    assert float(rendered_metadata["durationSeconds"]) == pytest.approx(15, abs=0.2)


@pytest.mark.asyncio
async def test_dual_facecam_filter_renders_1080x1920_with_audio(tmp_path: Path) -> None:
    executable = main.ffmpeg_path()
    if not executable:
        pytest.skip("FFmpeg is not available")
    source = tmp_path / "dual-source.mp4"
    subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=1920x1080:r=5:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=6",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    facecams = [
        {"x": 0, "y": 40, "w": 506, "h": 284},
        {"x": 1412, "y": 40, "w": 506, "h": 284},
    ]
    graph = dual_facecam_filter(facecams)
    rendered = tmp_path / "dual-output.mp4"
    code = subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-t",
            "5",
            "-filter_complex",
            graph,
            "-map",
            "[stacked]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(rendered),
        ],
    ).returncode

    assert code == 0 and rendered.is_file() and rendered.stat().st_size > 0
    rendered_metadata = await main.probe_media(rendered)
    assert rendered_metadata["resolution"] == "1080 x 1920"
    assert float(rendered_metadata["durationSeconds"]) == pytest.approx(5, abs=0.2)
    # Audio stream must survive the filter_complex render.
    probe = subprocess.run(
        [
            main.ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(rendered),
        ],
        capture_output=True,
        text=True,
    )
    assert "audio" in probe.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("facecam_count", [1, 2])
async def test_gaming_facecam_filter_renders_1080x1920_with_audio(tmp_path: Path, facecam_count: int) -> None:
    executable = main.ffmpeg_path()
    if not executable:
        pytest.skip("FFmpeg is not available")
    source = tmp_path / f"gaming-{facecam_count}-source.mp4"
    subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=1920x1080:r=5:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    facecams = [
        {"x": 0, "y": 40, "w": 506, "h": 284},
        {"x": 1412, "y": 40, "w": 506, "h": 284},
    ][:facecam_count]
    rendered = tmp_path / f"gaming-{facecam_count}-output.mp4"
    code = subprocess.run(
        [
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-t",
            "2",
            "-filter_complex",
            gaming_facecam_filter(facecams),
            "-map",
            "[stacked]",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(rendered),
        ],
    ).returncode

    assert code == 0 and rendered.is_file() and rendered.stat().st_size > 0
    rendered_metadata = await main.probe_media(rendered)
    assert rendered_metadata["resolution"] == "1080 x 1920"
    assert float(rendered_metadata["durationSeconds"]) == pytest.approx(2, abs=0.2)
    probe = subprocess.run(
        [
            main.ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(rendered),
        ],
        capture_output=True,
        text=True,
    )
    assert "audio" in probe.stdout

