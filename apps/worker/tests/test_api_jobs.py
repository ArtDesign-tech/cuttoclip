from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.errors import WorkerError
from app.media import AudioChunk
from app.models import Candidate, ErrorInfo, Job, Project, RenderOutput, Transcript, TranscriptSegment, TranscriptWord


def wait_for_terminal(client: TestClient, job_id: str, timeout: float = 3) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "partial", "failed", "cancelled", "interrupted"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def test_stage_metadata_maps_clip_analysis_and_encoding() -> None:
    assert main._stage_metadata("analyzing clip 1 of 3") == ("job.analyzingClip", {"current": 1, "total": 3})
    assert main._stage_metadata("encoding clip 2 of 3") == ("job.encodingClip", {"current": 2, "total": 3})
    assert main._stage_metadata("rendered clip 3 of 3") == ("job.renderedClip", {"current": 3, "total": 3})


def test_highlight_segment_payload_omits_provider_word_timing() -> None:
    segment = TranscriptSegment(
        text="A valid segment",
        startSeconds=10,
        endSeconds=20,
        words=[TranscriptWord(text="outside", startSeconds=9.8, endSeconds=10.2)],
    )

    assert main.highlight_segment_payload(segment) == {
        "text": "A valid segment",
        "startSeconds": 10,
        "endSeconds": 20,
    }


def test_upload_streams_to_project_storage_and_returns_real_metadata(
    isolated_store, monkeypatch, tmp_path: Path
) -> None:
    async def fake_probe(path: str | Path):
        assert Path(path).stat().st_size == 2 * 1024 * 1024
        return {"durationSeconds": 60.0, "width": 1280, "height": 720, "resolution": "1280 x 720"}

    monkeypatch.setattr(main, "probe_media", fake_probe)
    settings = json.dumps({"clipCount": 3, "duration": {"minSeconds": 15, "maxSeconds": 90}})
    with TestClient(main.app) as client:
        response = client.post(
            "/api/projects/upload",
            files={"file": ("recording.mp4", b"x" * (2 * 1024 * 1024), "video/mp4")},
            data={"settings_json": settings},
        )
    assert response.status_code == 201
    project = response.json()
    assert project["durationSeconds"] == 60
    assert Path(project["sourcePath"]).read_bytes() == b"x" * (2 * 1024 * 1024)
    assert (isolated_store.project_dir(project["id"]) / "project.json").is_file()


def test_prepare_endpoint_returns_202_job_and_persists_terminal_result(isolated_store, monkeypatch) -> None:
    project = Project(id="project-job", sourceLabel="fixture.mp4", sourceKind="file", sourcePath="fixture.mp4")
    isolated_store.save_project(project)

    async def fake_prepare(job: Job, current: Project):
        current.status = "transcript_ready"
        isolated_store.save_project(current)
        return "succeeded", {"project": current.model_dump(mode="json")}

    monkeypatch.setattr(main, "process_prepare", fake_prepare)
    with TestClient(main.app) as client:
        response = client.post(f"/api/projects/{project.id}/prepare")
        assert response.status_code == 202
        queued = response.json()
        assert queued["status"] == "queued"
        terminal = wait_for_terminal(client, queued["id"])
    assert terminal["status"] == "succeeded"
    assert terminal["result"]["project"]["status"] == "transcript_ready"
    assert (isolated_store.jobs_dir / f'{queued["id"]}.json').is_file()


def test_youtube_cache_cleanup_preserves_project_data_previews_and_outputs(isolated_store, tmp_path: Path) -> None:
    transcript = Transcript(
        text="archive transcript",
        durationSeconds=30,
        segments=[TranscriptSegment(text="archive transcript", startSeconds=0, endSeconds=2)],
    )
    project = Project(
        id="youtube-cache-project",
        sourceLabel="Downloaded talk",
        sourceKind="youtube",
        sourceUrl="https://youtube.com/watch?v=fixture",
        transcriptReady=True,
        transcriptText=transcript.text,
        transcript=transcript,
        candidates=[Candidate(id="moment-1", startSeconds=0, endSeconds=15, title="Safe moment")],
    )
    isolated_store.save_project(project)
    project_dir = isolated_store.project_dir(project.id)
    source = project_dir / "source.mp4"
    partial = project_dir / "source.f137.mp4.part"
    source.write_bytes(b"source-cache")
    partial.write_bytes(b"partial")
    project.sourcePath = str(source)
    isolated_store.save_project(project)
    preview = project_dir / "previews" / "layout-safe.json"
    preview.parent.mkdir(parents=True)
    preview.write_text("{}", encoding="utf-8")
    rendered = isolated_store.output_root / "finished.mp4"
    rendered.write_bytes(b"rendered")

    with TestClient(main.app) as client:
        inventory = client.get("/api/storage/youtube-cache").json()
        response = client.post("/api/storage/youtube-cache/cleanup", json={"projectIds": [project.id]})

    assert inventory["totalBytes"] == len(b"source-cachepartial")
    assert inventory["entries"][0]["projectId"] == project.id
    assert response.status_code == 200
    assert response.json()["bytesFreed"] == len(b"source-cachepartial")
    assert not source.exists() and not partial.exists()
    assert preview.is_file() and rendered.is_file()
    reloaded = isolated_store.projects[project.id]
    assert reloaded.sourcePath is None
    assert reloaded.transcriptText == "archive transcript"
    assert [candidate.id for candidate in reloaded.candidates] == ["moment-1"]
    assert (project_dir / "project.json").is_file()


def test_youtube_cache_cleanup_skips_active_jobs_and_local_uploads(isolated_store) -> None:
    youtube = Project(id="busy-youtube", sourceLabel="Busy", sourceKind="youtube", sourceUrl="https://youtu.be/fixture")
    isolated_store.save_project(youtube)
    youtube_source = isolated_store.project_dir(youtube.id) / "source.webm"
    youtube_source.write_bytes(b"busy")
    youtube.sourcePath = str(youtube_source)
    isolated_store.save_project(youtube)

    upload = Project(id="local-upload", sourceLabel="upload.mp4", sourceKind="file")
    isolated_store.save_project(upload)
    upload_source = isolated_store.project_dir(upload.id) / "source.mp4"
    upload_source.write_bytes(b"local")
    upload.sourcePath = str(upload_source)
    isolated_store.save_project(upload)

    with TestClient(main.app) as client:
        isolated_store.save_job(Job(id="busy-job", projectId=youtube.id, type="render", status="running"))
        payload = client.post(
            "/api/storage/youtube-cache/cleanup",
            json={"projectIds": [youtube.id, upload.id]},
        ).json()

    assert payload["skippedActiveProjectIds"] == [youtube.id]
    assert payload["failures"][0]["code"] == "NOT_YOUTUBE_SOURCE"
    assert youtube_source.is_file() and upload_source.is_file()


def test_youtube_cache_file_resolution_rejects_project_path_traversal(isolated_store) -> None:
    escaped_dir = isolated_store.projects_dir.parent / "escaped-project"
    escaped_dir.mkdir()
    escaped_source = escaped_dir / "source.mp4.part"
    escaped_source.write_bytes(b"must-stay")
    project = Project(id="../escaped-project", sourceLabel="Unsafe", sourceKind="youtube")

    assert main._youtube_source_files(project) == []
    assert main._remove_youtube_source_files(project) == (0, [])
    assert escaped_source.read_bytes() == b"must-stay"


@pytest.mark.asyncio
async def test_restore_source_reuses_transcript_without_transcribing(isolated_store, monkeypatch) -> None:
    transcript = Transcript(
        text="keep this transcript",
        durationSeconds=30,
        segments=[TranscriptSegment(text="keep this transcript", startSeconds=0, endSeconds=2)],
    )
    project = Project(
        id="restore-only",
        sourceLabel="Restorable",
        sourceKind="youtube",
        sourceUrl="https://youtu.be/fixture",
        transcriptReady=True,
        transcriptText=transcript.text,
        transcript=transcript,
        candidates=[Candidate(id="existing-moment", startSeconds=0, endSeconds=15)],
    )
    isolated_store.save_project(project)

    async def fake_download(_job: Job, current: Project) -> None:
        source = isolated_store.project_dir(current.id) / "source.mp4"
        source.write_bytes(b"restored")
        current.sourcePath = str(source)
        isolated_store.save_project(current)

    async def fake_probe(_path):
        return {"durationSeconds": 30, "width": 1920, "height": 1080, "resolution": "1920 x 1080"}

    async def unexpected(*_, **__):
        raise AssertionError("restore-source must not extract or transcribe audio")

    monkeypatch.setattr(main, "prepare_youtube_source", fake_download)
    monkeypatch.setattr(main, "probe_media", fake_probe)
    monkeypatch.setattr(main, "extract_audio_chunks", unexpected)
    job = Job(id="restore-job", projectId=project.id, type="prepare", request={"restoreSource": True})

    status, result = await main.process_prepare(job, project)

    assert status == "succeeded"
    assert result["project"]["transcriptText"] == "keep this transcript"
    assert result["project"]["status"] == "review_ready"
    assert result["project"]["sourcePath"].endswith("source.mp4")


@pytest.mark.asyncio
async def test_failed_youtube_download_removes_partial_artifacts(isolated_store, monkeypatch) -> None:
    project = Project(
        id="failed-youtube-download",
        sourceLabel="Download",
        sourceKind="youtube",
        sourceUrl="https://youtu.be/fixture",
    )
    isolated_store.save_project(project)
    monkeypatch.setattr(main, "yt_dlp", object())

    async def no_deno():
        return None

    calls = 0
    def fake_youtube_info(_url: str, options: dict[str, object]):
        nonlocal calls
        calls += 1
        if options.get("skip_download"):
            return {"duration": 30, "title": "Fixture"}
        (isolated_store.project_dir(project.id) / "source.f248.webm.part").write_bytes(b"partial")
        (isolated_store.project_dir(project.id) / "source.ytdl").write_bytes(b"state")
        raise RuntimeError("network stopped")

    monkeypatch.setattr(main, "ensure_deno", no_deno)
    monkeypatch.setattr(main, "_youtube_info", fake_youtube_info)
    job = Job(id="failed-download-job", projectId=project.id, type="prepare")

    with pytest.raises(WorkerError, match="YouTube download failed"):
        await main.prepare_youtube_source(job, project)

    assert calls == 2
    assert list(isolated_store.project_dir(project.id).glob("source.*")) == []
    assert isolated_store.projects[project.id].sourcePath is None


def test_cleanup_restore_preview_and_render_api_flow(isolated_store, monkeypatch) -> None:
    transcript = Transcript(
        text="existing archive data",
        durationSeconds=30,
        words=[TranscriptWord(text="existing", startSeconds=0, endSeconds=1)],
        segments=[TranscriptSegment(text="existing archive data", startSeconds=0, endSeconds=2)],
    )
    clip = Candidate(id="flow-clip", startSeconds=0, endSeconds=15, title="Flow clip")
    project = Project(
        id="cleanup-restore-flow",
        sourceLabel="Flow source",
        sourceKind="youtube",
        sourceUrl="https://youtu.be/fixture",
        durationSeconds=30,
        width=1920,
        height=1080,
        resolution="1920 x 1080",
        transcriptReady=True,
        transcriptText=transcript.text,
        transcript=transcript,
        candidates=[clip],
        status="review_ready",
    )
    isolated_store.save_project(project)
    source = isolated_store.project_dir(project.id) / "source.mp4"
    source.write_bytes(b"cached-source")
    project.sourcePath = str(source)
    isolated_store.save_project(project)

    async def fake_restore(job: Job, current: Project):
        assert job.request == {"restoreSource": True}
        restored = isolated_store.project_dir(current.id) / "source.mp4"
        restored.write_bytes(b"restored-source")
        current.sourcePath = str(restored)
        current.status = "review_ready"
        isolated_store.save_project(current)
        return "succeeded", {"project": current.model_dump(mode="json")}

    async def fake_models():
        return Path("yunet.onnx"), Path("silero.onnx")

    def fake_smart_crop(*_args, **_kwargs):
        return main.SmartCropResult(mode="single", track=[], facecams=[])

    async def fake_render(_job: Job, current: Project):
        output = RenderOutput(
            id="flow-output",
            clipId=clip.id,
            fileName="flow.mp4",
            path=str(isolated_store.output_root / "flow.mp4"),
            mediaUrl=f"/api/projects/{current.id}/outputs/flow-output",
            durationSeconds=15,
            status="succeeded",
        )
        current.outputs = [output]
        current.status = "complete"
        isolated_store.save_project(current)
        return "succeeded", {"project": current.model_dump(mode="json"), "outputs": [output.model_dump(mode="json")]}

    monkeypatch.setattr(main, "process_prepare", fake_restore)
    monkeypatch.setattr(main, "ensure_vision_models", fake_models)
    monkeypatch.setattr(main, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(main, "smart_crop_track", fake_smart_crop)
    monkeypatch.setattr(main, "process_render", fake_render)

    with TestClient(main.app) as client:
        cleaned = client.post("/api/storage/youtube-cache/cleanup", json={"projectIds": [project.id]})
        missing_preview = client.post(
            f"/api/projects/{project.id}/layout-preview",
            json={"clipId": clip.id, "startSeconds": 0, "endSeconds": 15, "layout": "smart_portrait"},
        )
        restore_job = client.post(f"/api/projects/{project.id}/restore-source").json()
        restored = wait_for_terminal(client, restore_job["id"])
        preview = client.post(
            f"/api/projects/{project.id}/layout-preview",
            json={"clipId": clip.id, "startSeconds": 0, "endSeconds": 15, "layout": "smart_portrait"},
        )
        render_job = client.post(
            f"/api/projects/{project.id}/render",
            json={"settings": project.settings.model_dump(mode="json"), "clips": [clip.model_dump(mode="json")]},
        ).json()
        rendered = wait_for_terminal(client, render_job["id"])

    assert cleaned.json()["cleanedProjectIds"] == [project.id]
    assert missing_preview.status_code == 409
    assert missing_preview.json()["error"]["code"] == "SOURCE_RESTORE_REQUIRED"
    assert restored["status"] == "succeeded"
    assert restored["result"]["project"]["transcriptText"] == "existing archive data"
    assert preview.status_code == 200
    assert preview.json()["mode"] == "single"
    assert rendered["status"] == "succeeded"
    assert rendered["result"]["outputs"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_prepare_reuses_existing_transcript_without_reprocessing(isolated_store, monkeypatch) -> None:
    transcript = Transcript(
        text="already transcribed",
        language="en",
        durationSeconds=1,
        segments=[TranscriptSegment(text="already transcribed", startSeconds=0, endSeconds=1)],
    )
    project = Project(
        id="prepared-project",
        sourceLabel="source.mp4",
        sourceKind="youtube",
        sourceUrl="https://youtube.example/video",
        transcriptReady=True,
        transcriptText=transcript.text,
        transcript=transcript,
        status="failed",
    )
    isolated_store.save_project(project)

    async def unexpected(*_, **__):
        raise AssertionError("existing transcript should bypass media preparation")

    monkeypatch.setattr(main, "prepare_youtube_source", unexpected)
    monkeypatch.setattr(main, "probe_media", unexpected)
    job = Job(id="prepare-reuse", projectId=project.id, type="prepare")

    status, result = await main.process_prepare(job, project)

    assert status == "succeeded"
    assert result["project"]["transcriptReady"] is True
    assert project.status == "transcript_ready"


def test_cancelled_job_has_retryable_error(isolated_store, monkeypatch) -> None:
    project = Project(id="project-cancel", sourceLabel="fixture.mp4", sourceKind="file", sourcePath="fixture.mp4")
    isolated_store.save_project(project)
    started = asyncio.Event()

    async def slow_prepare(job: Job, current: Project):
        started.set()
        await asyncio.sleep(30)
        return "succeeded", {"project": current.model_dump(mode="json")}

    monkeypatch.setattr(main, "process_prepare", slow_prepare)
    with TestClient(main.app) as client:
        queued = client.post(f"/api/projects/{project.id}/prepare").json()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and client.get(f'/api/jobs/{queued["id"]}').json()["status"] == "queued":
            time.sleep(0.01)
        cancelled = client.delete(f'/api/jobs/{queued["id"]}')
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["error"]["retryable"] is True


def test_consistent_error_envelope(isolated_store) -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/projects/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "PROJECT_NOT_FOUND", "message": "Project not found.", "retryable": False}
    }


def test_project_summaries_are_newest_first_and_omit_transcript(isolated_store) -> None:
    older = Project(id="older", sourceLabel="older.mov", sourceKind="file", transcriptText="private transcript")
    newer = Project(id="newer", sourceLabel="newer.mov", sourceKind="file", candidates=[Candidate(id="newer-clip", startSeconds=1, endSeconds=16, title="Moment", hook="Hook", reason="Reason", score=90, accent="coral")])
    isolated_store.save_project(older)
    isolated_store.save_project(newer)
    older.updatedAt = "2024-01-01T00:00:00+00:00"
    newer.updatedAt = "2025-01-01T00:00:00+00:00"
    with TestClient(main.app) as client:
        response = client.get("/api/projects/summaries")
    assert response.status_code == 200
    summaries = response.json()
    assert [item["id"] for item in summaries] == ["newer", "older"]
    assert summaries[0]["candidateCount"] == 1
    assert "transcriptText" not in summaries[1]


def test_delete_project_preserves_output_root(isolated_store, tmp_path: Path) -> None:
    project = Project(id="delete-me", sourceLabel="delete.mov", sourceKind="file")
    isolated_store.save_project(project)
    output = isolated_store.project_output_dir(project) / "clip.mp4"
    output.write_bytes(b"render")
    with TestClient(main.app) as client:
        deleted = client.delete(f"/api/projects/{project.id}")
    assert deleted.status_code == 204
    assert project.id not in isolated_store.projects
    assert output.exists()


def test_delete_project_rejects_an_active_job(isolated_store) -> None:
    project = Project(id="active-delete", sourceLabel="active.mov", sourceKind="file")
    isolated_store.save_project(project)
    with TestClient(main.app) as client:
        isolated_store.save_job(Job(id="active-job", projectId=project.id, type="prepare", status="running", stage="working", stageKey="job.prepare"))
        response = client.delete(f"/api/projects/{project.id}")
    assert response.status_code == 409
    assert project.id in isolated_store.projects


def test_gateway_access_headers_are_forwarded_and_required_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_URL", "https://gateway.example.test")
    monkeypatch.setenv("CUTTOCLIP_INSTALLATION_TOKEN", "installation-token")
    monkeypatch.setenv("CUTTOCLIP_CF_ACCESS_CLIENT_ID", "client-id.access")
    monkeypatch.setenv("CUTTOCLIP_CF_ACCESS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_REQUIRE_ACCESS", "true")
    assert main.gateway_configuration_error() is None
    assert main.gateway_headers() == {
        "Authorization": "Bearer installation-token",
        "CF-Access-Client-Id": "client-id.access",
        "CF-Access-Client-Secret": "client-secret",
    }

    monkeypatch.delenv("CUTTOCLIP_CF_ACCESS_CLIENT_SECRET")
    error = main.gateway_configuration_error()
    assert error is not None
    assert error.code == "GATEWAY_EDGE_AUTH_INCOMPLETE"


@pytest.mark.asyncio
async def test_transcription_retries_retryable_gateway_error(monkeypatch, tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object], headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.payload = payload
            self.headers = headers or {"content-type": "application/json"}

        def json(self):
            return self.payload

    responses = [
        FakeResponse(
            503,
            {"error": {"code": "gateway_busy", "message": "Busy", "retryable": True}},
            {"content-type": "application/json", "retry-after": "0"},
        ),
        FakeResponse(
            200,
            {
                "text": "hello",
                "language": "en",
                "duration": 1,
                "segments": [{"text": "hello", "start": 0, "end": 1}],
                "words": [{"word": "hello", "start": 0, "end": 1}],
            },
        ),
    ]
    calls = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            nonlocal calls
            calls += 1
            return responses.pop(0)

    audio_path = tmp_path / "chunk.mp3"
    audio_path.write_bytes(b"audio")
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_URL", "https://gateway.example.test")
    monkeypatch.setenv("CUTTOCLIP_INSTALLATION_TOKEN", "installation-token")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: FakeClient())

    transcript = await main.transcribe_audio(AudioChunk(audio_path, 0, 1), "auto")

    assert transcript.text == "hello"
    assert calls == 2


@pytest.mark.asyncio
async def test_transcription_does_not_retry_permanent_gateway_error(monkeypatch, tmp_path: Path) -> None:
    class FakeResponse:
        status_code = 400
        headers = {"content-type": "application/json"}

        def json(self):
            return {"error": {"code": "bad_audio", "message": "Bad audio", "retryable": False}}

    calls = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            nonlocal calls
            calls += 1
            return FakeResponse()

    audio_path = tmp_path / "chunk.mp3"
    audio_path.write_bytes(b"audio")
    monkeypatch.setenv("CUTTOCLIP_GATEWAY_URL", "https://gateway.example.test")
    monkeypatch.setenv("CUTTOCLIP_INSTALLATION_TOKEN", "installation-token")
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **_: FakeClient())

    with pytest.raises(WorkerError, match="Bad audio"):
        await main.transcribe_audio(AudioChunk(audio_path, 0, 1), "auto")

    assert calls == 1


def test_output_endpoint_supports_http_range(isolated_store, tmp_path: Path) -> None:
    rendered = tmp_path / "clip.mp4"
    rendered.write_bytes(b"0123456789")
    output = RenderOutput(
        id="output-1",
        clipId="clip-1",
        fileName="clip.mp4",
        path=str(rendered),
        mediaUrl="/api/projects/project-range/outputs/output-1",
        durationSeconds=15,
        status="succeeded",
    )
    project = Project(id="project-range", sourceLabel="fixture.mp4", sourceKind="file", outputs=[output])
    isolated_store.save_project(project)
    with TestClient(main.app) as client:
        response = client.get(output.mediaUrl, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_source_endpoint_supports_http_range(isolated_store, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"abcdefghij")
    project = Project(id="project-source-range", sourceLabel="source.mp4", sourceKind="file", sourcePath=str(source))
    isolated_store.save_project(project)
    with TestClient(main.app) as client:
        response = client.get(f"/api/projects/{project.id}/source", headers={"Range": "bytes=3-6"})
    assert response.status_code == 206
    assert response.content == b"defg"
    assert response.headers["accept-ranges"] == "bytes"


def test_project_patch_persists_candidate_revision_and_rejects_stale_base(isolated_store) -> None:
    clip = Candidate(id="clip-edit", startSeconds=0, endSeconds=20, title="Before")
    project = Project(id="project-edit", sourceLabel="source.mp4", sourceKind="file", durationSeconds=60, candidates=[clip])
    isolated_store.save_project(project)
    payload = {
        "baseRevision": 0,
        "candidates": [{**clip.model_dump(mode="json"), "title": "After"}],
    }
    with TestClient(main.app) as client:
        updated = client.patch(f"/api/projects/{project.id}", json=payload)
        conflict = client.patch(f"/api/projects/{project.id}", json=payload)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    assert updated.json()["candidates"][0]["revision"] == 1
    assert updated.json()["candidates"][0]["title"] == "After"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PROJECT_REVISION_CONFLICT"


def test_active_project_job_returns_running_job(isolated_store) -> None:
    project = Project(id="project-active", sourceLabel="source.mp4", sourceKind="file")
    job = Job(id="job-active", projectId=project.id, type="prepare", status="running")
    with TestClient(main.app) as client:
        isolated_store.save_project(project)
        isolated_store.save_job(job)
        response = client.get(f"/api/projects/{project.id}/active-job")
    assert response.status_code == 200
    assert response.json()["id"] == job.id


def test_frame_endpoint_caches_ffmpeg_result(isolated_store, monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    project = Project(id="project-frame", sourceLabel="source.mp4", sourceKind="file", sourcePath=str(source), durationSeconds=30)
    isolated_store.save_project(project)
    calls = 0

    async def fake_run_command(*args, **kwargs):
        nonlocal calls
        calls += 1
        Path(args[-1]).write_bytes(b"jpeg")
        return 0, "", ""

    monkeypatch.setattr(main, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(main, "run_command", fake_run_command)
    with TestClient(main.app) as client:
        first = client.get(f"/api/projects/{project.id}/frame?at=5&width=320")
        second = client.get(f"/api/projects/{project.id}/frame?at=5&width=320")
    assert first.status_code == 200
    assert first.content == b"jpeg"
    assert second.status_code == 200
    assert calls == 1


def test_capabilities_advertise_versioned_editor_features() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/system/capabilities")
    assert response.status_code == 200
    assert {
        "project-revision",
        "source-stream",
        "frame-preview",
        "active-job",
        "per-clip-presentation",
        "output-revision",
        "gaming-portrait-facecam",
    }.issubset(set(response.json()["apiFeatures"]))


def test_failed_render_job_preserves_per_clip_result(isolated_store, monkeypatch) -> None:
    words = [TranscriptWord(text="hello", startSeconds=0, endSeconds=1)]
    transcript = Transcript(
        text="hello",
        durationSeconds=30,
        words=words,
        segments=[TranscriptSegment(text="hello", startSeconds=0, endSeconds=1, words=words)],
    )
    clip = Candidate(id="clip-fail", startSeconds=0, endSeconds=15, title="Will fail")
    project = Project(
        id="project-render-fail",
        sourceLabel="fixture.mp4",
        sourceKind="file",
        durationSeconds=30,
        transcriptReady=True,
        transcriptText="hello",
        transcript=transcript,
        candidates=[clip],
    )
    isolated_store.save_project(project)
    failed_output = RenderOutput(
        id="failed-output",
        clipId=clip.id,
        fileName="01-will-fail.mp4",
        path="missing.mp4",
        mediaUrl=f"/api/projects/{project.id}/outputs/failed-output",
        durationSeconds=15,
        status="failed",
        error=ErrorInfo(code="FFMPEG_RENDER_FAILED", message="render failed", retryable=True),
    )

    async def fake_render(job: Job, current: Project):
        current.status = "failed"
        current.outputs = [failed_output]
        isolated_store.save_project(current)
        return "failed", {
            "project": current.model_dump(mode="json"),
            "outputs": [failed_output.model_dump(mode="json")],
        }

    monkeypatch.setattr(main, "process_render", fake_render)
    with TestClient(main.app) as client:
        queued = client.post(
            f"/api/projects/{project.id}/render",
            json={"settings": project.settings.model_dump(mode="json"), "clips": [clip.model_dump(mode="json")]},
        ).json()
        terminal = wait_for_terminal(client, queued["id"])
    assert terminal["status"] == "failed"
    assert terminal["error"]["code"] == "RENDER_FAILED"
    assert terminal["result"]["outputs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_failed_clip_retry_keeps_unaffected_project_candidates(isolated_store, monkeypatch) -> None:
    words = [TranscriptWord(text="hello", startSeconds=0, endSeconds=1)]
    transcript = Transcript(
        text="hello",
        durationSeconds=45,
        words=words,
        segments=[TranscriptSegment(text="hello", startSeconds=0, endSeconds=1, words=words)],
    )
    completed_clip = Candidate(id="clip-ok", startSeconds=0, endSeconds=15, title="Already complete")
    retry_clip = Candidate(id="clip-retry", startSeconds=15, endSeconds=30, title="Retry me")
    prior_output = RenderOutput(
        id="output-ok",
        clipId=completed_clip.id,
        fileName="01-already-complete.mp4",
        path="completed.mp4",
        mediaUrl="/api/projects/project-merge/outputs/output-ok",
        durationSeconds=15,
        status="succeeded",
    )
    project = Project(
        id="project-merge",
        sourceLabel="fixture.mp4",
        sourceKind="file",
        sourcePath="fixture.mp4",
        durationSeconds=45,
        transcriptReady=True,
        transcriptText="hello",
        transcript=transcript,
        candidates=[completed_clip, retry_clip],
        outputs=[prior_output],
    )
    isolated_store.save_project(project)
    job = Job(
        id="retry-job",
        projectId=project.id,
        type="render",
        status="running",
        request={"settings": project.settings.model_dump(mode="json"), "clips": [retry_clip.model_dump(mode="json")]},
    )
    monkeypatch.setattr(main, "ffmpeg_path", lambda: "ffmpeg")

    async def fail_render(*args, **kwargs):
        raise WorkerError("FFMPEG_RENDER_FAILED", "retry failed", retryable=True)

    monkeypatch.setattr(main, "render_one_clip", fail_render)
    terminal, result = await main.process_render(job, project)

    assert terminal == "failed"
    assert [candidate.id for candidate in project.candidates] == ["clip-ok", "clip-retry"]
    assert [output.clipId for output in project.outputs] == ["clip-ok", "clip-retry"]
    assert result["outputs"][0]["clipId"] == "clip-retry"
