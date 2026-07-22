from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import File, Form, FastAPI, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

try:
    import httpx
except ImportError:  # Reported through capabilities and actionable job errors.
    httpx = None

try:
    import yt_dlp
except ImportError:  # Reported through capabilities and actionable job errors.
    yt_dlp = None

try:
    from .captions import ass_filter, build_ass, captions_disabled
    from .errors import WorkerError
    from .media import (
        MAX_SOURCE_DURATION_SECONDS,
        AudioChunk,
        extract_audio_chunks,
        ffmpeg_path,
        ffprobe_path,
        merge_transcripts,
        normalize_transcript,
        probe_media,
        run_command,
    )
    from .models import (
        Candidate,
        ClipPresentation,
        CreateProjectRequest,
        ErrorInfo,
        Job,
        LayoutPreviewPlan,
        LayoutPreviewRequest,
        Project,
        ProjectSummary,
        ProjectPatchRequest,
        ProjectSettings,
        RenderOutput,
        RenderRequest,
        Transcript,
        SourceCacheEntry,
        YoutubeCacheCleanupFailure,
        YoutubeCacheCleanupRequest,
        YoutubeCacheCleanupResult,
        YoutubeCacheInventory,
        utc_now,
    )
    from .model_manager import ensure_vision_models, ensure_yunet_model, vision_status
    from . import providers
    from .runtimes import deno_installed, ensure_deno
    from .storage import ProjectStore, safe_slug
    from .vision import (
        SmartCropResult,
        dual_facecam_filter,
        ffmpeg_crop_commands,
        gaming_facecam_filter,
        gaming_facecam_track,
        layout_preview_plan,
        smart_crop_track,
    )
except ImportError:  # PyInstaller can execute this entrypoint as a script.
    from captions import ass_filter, build_ass, captions_disabled
    from errors import WorkerError
    from media import (
        MAX_SOURCE_DURATION_SECONDS,
        AudioChunk,
        extract_audio_chunks,
        ffmpeg_path,
        ffprobe_path,
        merge_transcripts,
        normalize_transcript,
        probe_media,
        run_command,
    )
    from models import (
        Candidate,
        ClipPresentation,
        CreateProjectRequest,
        ErrorInfo,
        Job,
        LayoutPreviewPlan,
        LayoutPreviewRequest,
        Project,
        ProjectSummary,
        ProjectPatchRequest,
        ProjectSettings,
        RenderOutput,
        RenderRequest,
        Transcript,
        SourceCacheEntry,
        YoutubeCacheCleanupFailure,
        YoutubeCacheCleanupRequest,
        YoutubeCacheCleanupResult,
        YoutubeCacheInventory,
        utc_now,
    )
    from model_manager import ensure_vision_models, ensure_yunet_model, vision_status
    import providers
    from runtimes import deno_installed, ensure_deno
    from storage import ProjectStore, safe_slug
    from vision import (
        SmartCropResult,
        dual_facecam_filter,
        ffmpeg_crop_commands,
        gaming_facecam_filter,
        gaming_facecam_track,
        layout_preview_plan,
        smart_crop_track,
    )


SUPPORTED_UPLOAD_SUFFIXES = {".mp4", ".mov"}
YOUTUBE_PATTERN = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/", re.I)
TERMINAL_JOB_STATUSES = {"succeeded", "partial", "failed", "cancelled", "interrupted"}

store = ProjectStore()
active_tasks: dict[str, asyncio.Task[None]] = {}
active_download_cancels: dict[str, threading.Event] = {}
media_lock: asyncio.Lock | None = None


def _error_body(error: ErrorInfo) -> dict[str, object]:
    return {"error": error.model_dump(mode="json", exclude_none=True)}


def _worker_error_info(error: WorkerError) -> ErrorInfo:
    return ErrorInfo(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        details=error.details,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global media_lock
    store.load()
    store.mark_interrupted_jobs()
    media_lock = asyncio.Lock()
    yield
    tasks = list(active_tasks.values())
    for job_id, task in list(active_tasks.items()):
        if task.done():
            continue
        job = store.jobs.get(job_id)
        if job and job.status in {"queued", "running"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.stageKey = "job.interrupted"
            job.stageParams = {}
            job.completedAt = utc_now()
            job.error = ErrorInfo(
                code="WORKER_STOPPED",
                message="The worker stopped before this job completed. Retry the operation after restart.",
                retryable=True,
            )
            store.save_job(job)
            project = store.projects.get(job.projectId)
            if project:
                project.status = "interrupted"
                store.save_project(project)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="CutToClip Local Worker", version="0.2.0-beta.1", lifespan=lifespan)
_default_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
_extra_cors = [o.strip() for o in os.getenv("CUTTOCLIP_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[*_default_cors_origins, *_extra_cors],
    allow_methods=["*"],
    allow_headers=["*"],
)

TRANSCRIPTION_MAX_ATTEMPTS = 3
TRANSCRIPTION_RETRY_BASE_SECONDS = 1.0
TRANSCRIPTION_RETRY_MAX_SECONDS = 30.0
HIGHLIGHTS_MAX_ATTEMPTS = 3


@app.exception_handler(WorkerError)
async def worker_error_handler(_: Request, error: WorkerError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content=_error_body(_worker_error_info(error)))


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, error: HTTPException) -> JSONResponse:
    if isinstance(error.detail, dict) and "code" in error.detail:
        payload = ErrorInfo.model_validate(error.detail)
    else:
        payload = ErrorInfo(
            code="HTTP_ERROR",
            message=str(error.detail),
            retryable=error.status_code >= 500,
        )
    return JSONResponse(status_code=error.status_code, content=_error_body(payload), headers=error.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    details = json.loads(json.dumps(error.errors(), default=str))
    payload = ErrorInfo(code="VALIDATION_ERROR", message="The request payload is invalid.", details=details)
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=_error_body(payload))


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    payload = ErrorInfo(
        code="INTERNAL_WORKER_ERROR",
        message="The worker encountered an unexpected error.",
        retryable=True,
        details=str(error),
    )
    return JSONResponse(status_code=500, content=_error_body(payload))


def is_public_youtube(source: str) -> bool:
    return bool(YOUTUBE_PATTERN.match(source.strip()))


def gateway_url() -> str:
    return os.getenv("CUTTOCLIP_GATEWAY_URL", "").rstrip("/")


def gateway_headers() -> dict[str, str]:
    token = os.getenv("CUTTOCLIP_INSTALLATION_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    access_client_id = os.getenv("CUTTOCLIP_CF_ACCESS_CLIENT_ID", "").strip()
    access_client_secret = os.getenv("CUTTOCLIP_CF_ACCESS_CLIENT_SECRET", "").strip()
    if access_client_id and access_client_secret:
        headers["CF-Access-Client-Id"] = access_client_id
        headers["CF-Access-Client-Secret"] = access_client_secret
    return headers


def gateway_requires_access() -> bool:
    return os.getenv("CUTTOCLIP_GATEWAY_REQUIRE_ACCESS", "").strip().lower() in {"1", "true", "yes"}


def gateway_access_configured() -> bool:
    client_id = os.getenv("CUTTOCLIP_CF_ACCESS_CLIENT_ID", "").strip()
    client_secret = os.getenv("CUTTOCLIP_CF_ACCESS_CLIENT_SECRET", "").strip()
    return bool(client_id and client_secret)


def gateway_configuration_error() -> WorkerError | None:
    if not gateway_url():
        return WorkerError("GATEWAY_NOT_CONFIGURED", "CUTTOCLIP_GATEWAY_URL is not configured.", status_code=503, retryable=True)
    if not os.getenv("CUTTOCLIP_INSTALLATION_TOKEN", "").strip():
        return WorkerError("GATEWAY_NOT_CONFIGURED", "CUTTOCLIP_INSTALLATION_TOKEN is not configured.", status_code=503, retryable=True)
    if gateway_requires_access() and not gateway_access_configured():
        return WorkerError(
            "GATEWAY_EDGE_AUTH_INCOMPLETE",
            "Cloudflare Access client credentials are required for this gateway.",
            status_code=503,
            retryable=True,
        )
    return None


def get_project(project_id: str) -> Project:
    project = store.projects.get(project_id)
    if project is None:
        raise WorkerError("PROJECT_NOT_FOUND", "Project not found.", status_code=404)
    return project


def get_job(job_id: str) -> Job:
    job = store.jobs.get(job_id)
    if job is None:
        raise WorkerError("JOB_NOT_FOUND", "Job not found.", status_code=404)
    return job


def apply_metadata(project: Project, metadata: dict[str, float | int | str]) -> None:
    project.durationSeconds = float(metadata["durationSeconds"])
    project.width = int(metadata["width"])
    project.height = int(metadata["height"])
    project.resolution = str(metadata["resolution"])


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "local-worker", "version": "0.2.0-beta.1"}


def _provider_capability_report(mode: providers.ProviderMode) -> dict[str, object]:
    """Report provider identity for the UI without ever leaking a secret.

    Only model names and boolean key-presence flags are surfaced — never the key
    values themselves.
    """

    if mode == "byok":
        groq = providers.groq_config()
        gemini = providers.gemini_config()
        groq_count = len(providers.groq_api_keys())
        gemini_count = len(providers.gemini_api_keys())
        return {
            "mode": "byok",
            "transcription": {
                "provider": "groq",
                "model": groq.model,
                "keyPresent": groq_count > 0,
                "keyCount": groq_count,
            },
            "highlights": {
                "provider": "gemini",
                "model": gemini.model,
                "keyPresent": gemini_count > 0,
                "keyCount": gemini_count,
            },
        }
    if mode == "openai":
        groq = providers.groq_config()
        _openai = providers.openrouter_config()
        groq_count = len(providers.groq_api_keys())
        openai_count = len(providers.openrouter_api_keys())
        return {
            "mode": "openai",
            "transcription": {
                "provider": "groq",
                "model": groq.model,
                "keyPresent": groq_count > 0,
                "keyCount": groq_count,
            },
            "highlights": {
                "provider": "9router",
                "model": _openai.model,
                "keyPresent": openai_count > 0,
                "keyCount": openai_count,
            },
        }
    return {
        "mode": "managed",
        "transcription": {"provider": "gateway"},
        "highlights": {"provider": "gateway"},
        "gatewayConfigured": gateway_configuration_error() is None,
    }


@app.get("/api/system/capabilities")
async def capabilities() -> dict[str, object]:
    executable = ffmpeg_path()
    if executable:
        encoders = await detect_encoders(executable)
    else:
        encoders = []
    default_encoder = resolve_encoder(encoders, "auto")
    mode = providers.provider_mode()
    provider_report = _provider_capability_report(mode)
    return {
        "platform": "windows" if os.name == "nt" else os.name,
        "ffmpeg": bool(executable),
        "ffprobe": bool(ffprobe_path()),
        "ytDlp": yt_dlp is not None,
        "deno": deno_installed(),
        "encoders": encoders,
        "defaultEncoder": default_encoder,
        "vision": vision_status(),
        "providerMode": mode,
        "providerConfigured": provider_configuration_error() is None,
        "provider": provider_report,
        "gatewayConfigured": gateway_configuration_error() is None,
        "gatewayEdgeAuthConfigured": gateway_access_configured(),
        "dataRoot": str(store.root),
        "outputRoot": str(store.output_root),
        "maxSourceDurationSeconds": MAX_SOURCE_DURATION_SECONDS,
        "supportedFormats": ["mp4", "mov"],
        "apiFeatures": [
            "project-revision",
            "source-stream",
            "frame-preview",
            "active-job",
            "per-clip-presentation",
            "output-revision",
            "project-library",
            "project-delete",
            "caption-none",
            "smart-crop-yunet-vad",
            "gaming-portrait-facecam",
            "layout-preview-plan",
            "youtube-cache-manager",
        ],
    }


@app.get("/api/projects", response_model=list[Project])
async def list_projects() -> list[Project]:
    return sorted(store.projects.values(), key=lambda project: project.updatedAt, reverse=True)


@app.get("/api/projects/summaries", response_model=list[ProjectSummary])
async def list_project_summaries() -> list[ProjectSummary]:
    return [
        ProjectSummary(
            id=project.id,
            sourceLabel=project.sourceLabel,
            sourceKind=project.sourceKind,
            durationSeconds=project.durationSeconds,
            resolution=project.resolution,
            transcriptReady=project.transcriptReady,
            status=project.status,
            createdAt=project.createdAt,
            updatedAt=project.updatedAt,
            candidateCount=len(project.candidates),
            outputCount=len(project.outputs),
            failedOutputCount=sum(output.status == "failed" for output in project.outputs),
        )
        for project in sorted(store.projects.values(), key=lambda project: project.updatedAt, reverse=True)
    ]


def _project_has_active_job(project_id: str) -> bool:
    return any(job.projectId == project_id and job.status in {"queued", "running"} for job in store.jobs.values())


@app.get("/api/storage/youtube-cache", response_model=YoutubeCacheInventory)
async def youtube_cache_inventory() -> YoutubeCacheInventory:
    entries: list[SourceCacheEntry] = []
    for project in sorted(store.projects.values(), key=lambda item: item.updatedAt, reverse=True):
        files = _youtube_source_files(project)
        size = sum(path.stat().st_size for path in files if path.is_file())
        if not size:
            continue
        entries.append(SourceCacheEntry(
            projectId=project.id,
            sourceLabel=project.sourceLabel,
            sizeBytes=size,
            activeJob=_project_has_active_job(project.id),
        ))
    return YoutubeCacheInventory(totalBytes=sum(entry.sizeBytes for entry in entries), entries=entries)


@app.post("/api/storage/youtube-cache/cleanup", response_model=YoutubeCacheCleanupResult)
async def cleanup_youtube_cache(request: YoutubeCacheCleanupRequest) -> YoutubeCacheCleanupResult:
    cleaned: list[str] = []
    skipped: list[str] = []
    failures: list[YoutubeCacheCleanupFailure] = []
    bytes_freed = 0
    for project_id in dict.fromkeys(request.projectIds):
        project = store.projects.get(project_id)
        if project is None:
            failures.append(YoutubeCacheCleanupFailure(projectId=project_id, code="PROJECT_NOT_FOUND", message="Project not found."))
            continue
        if project.sourceKind != "youtube":
            failures.append(YoutubeCacheCleanupFailure(projectId=project_id, code="NOT_YOUTUBE_SOURCE", message="Only YouTube source caches can be cleaned."))
            continue
        if _project_has_active_job(project_id):
            skipped.append(project_id)
            continue
        freed, file_failures = _remove_youtube_source_files(project)
        if file_failures:
            failures.append(YoutubeCacheCleanupFailure(
                projectId=project_id,
                code="CACHE_DELETE_FAILED",
                message="; ".join(file_failures),
            ))
            continue
        project.sourcePath = None
        store.save_project(project)
        bytes_freed += freed
        cleaned.append(project_id)
    return YoutubeCacheCleanupResult(
        bytesFreed=bytes_freed,
        cleanedProjectIds=cleaned,
        skippedActiveProjectIds=skipped,
        failures=failures,
    )


@app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str) -> None:
    get_project(project_id)
    if any(job.projectId == project_id and job.status in {"queued", "running"} for job in store.jobs.values()):
        raise WorkerError(
            "PROJECT_JOB_ACTIVE",
            "Finish or cancel the active job before deleting this project.",
            status_code=409,
            retryable=False,
        )
    store.delete_project(project_id)


@app.get("/api/projects/{project_id}", response_model=Project)
async def read_project(project_id: str) -> Project:
    return get_project(project_id)


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    return candidate.model_dump(mode="json", exclude={"revision"})


@app.patch("/api/projects/{project_id}", response_model=Project)
async def patch_project(project_id: str, request: ProjectPatchRequest) -> Project:
    project = get_project(project_id)
    if request.baseRevision != project.revision:
        raise WorkerError(
            "PROJECT_REVISION_CONFLICT",
            "This project changed in another window.",
            status_code=409,
            retryable=True,
            details={"currentRevision": project.revision},
        )

    changed = False
    if request.settings is not None and request.settings != project.settings:
        project.settings = request.settings
        changed = True
    if request.candidates is not None:
        existing = {candidate.id: candidate for candidate in project.candidates}
        normalized: list[Candidate] = []
        for candidate in request.candidates:
            validate_render_clip(candidate, project, request.settings or project.settings)
            if candidate.presentation is None:
                defaults = request.settings or project.settings
                candidate.presentation = ClipPresentation(layout=defaults.layout, captionPreset=defaults.captionPreset)
            previous = existing.get(candidate.id)
            if previous is None:
                candidate.revision = max(1, candidate.revision)
                changed = True
            elif _candidate_payload(previous) != _candidate_payload(candidate):
                candidate.revision = previous.revision + 1
                changed = True
            else:
                candidate.revision = previous.revision
            normalized.append(candidate)
        if [candidate.id for candidate in normalized] != [candidate.id for candidate in project.candidates]:
            changed = True
        project.candidates = normalized
    if changed:
        project.revision += 1
        store.save_project(project)
    return project


@app.get("/api/projects/{project_id}/active-job", response_model=Job | None)
async def active_project_job(project_id: str) -> Job | None:
    get_project(project_id)
    active = [
        job for job in store.jobs.values()
        if job.projectId == project_id and job.status in {"queued", "running"}
    ]
    return max(active, key=lambda job: job.updatedAt, default=None)


@app.post("/api/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(request: CreateProjectRequest) -> Project:
    source = request.source.strip()
    if source.startswith(("http://", "https://")):
        if not is_public_youtube(source):
            raise WorkerError(
                "UNSUPPORTED_SOURCE_URL",
                "Only public YouTube URLs are supported.",
                status_code=400,
            )
        project = Project(
            id=str(uuid.uuid4()),
            sourceLabel="YouTube source",
            sourceKind="youtube",
            sourceUrl=source,
            settings=request.settings,
        )
        store.project_dir(project.id)
        return store.save_project(project)

    path = Path(source).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise WorkerError("SOURCE_NOT_FOUND", "The selected local video does not exist.", status_code=400)
    if path.suffix.lower() not in SUPPORTED_UPLOAD_SUFFIXES:
        raise WorkerError("UNSUPPORTED_MEDIA_TYPE", "Only MP4 and MOV files are supported.", status_code=400)
    metadata = await probe_media(path)
    project = Project(
        id=str(uuid.uuid4()),
        sourceLabel=path.name,
        sourceKind="file",
        sourcePath=str(path),
        settings=request.settings,
    )
    apply_metadata(project, metadata)
    store.project_dir(project.id)
    return store.save_project(project)


@app.post("/api/projects/upload", response_model=Project, status_code=status.HTTP_201_CREATED)
async def upload_project(file: UploadFile = File(...), settings_json: str = Form(...)) -> Project:
    try:
        settings = ProjectSettings.model_validate_json(settings_json)
    except ValidationError as error:
        raise WorkerError(
            "INVALID_PROJECT_SETTINGS",
            "Project settings are invalid.",
            status_code=422,
            details=error.errors(include_url=False),
        ) from error
    original_name = Path(file.filename or "source.mp4").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise WorkerError("UNSUPPORTED_MEDIA_TYPE", "Only MP4 and MOV files are supported.", status_code=400)

    project_id = str(uuid.uuid4())
    project_dir = store.project_dir(project_id)
    source_path = project_dir / f"source{suffix}"
    temporary = project_dir / f".{source_path.name}.uploading"
    try:
        with temporary.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                target.write(chunk)
        os.replace(temporary, source_path)
        metadata = await probe_media(source_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        source_path.unlink(missing_ok=True)
        if project_dir.exists() and not any(project_dir.iterdir()):
            project_dir.rmdir()
        raise
    finally:
        await file.close()

    project = Project(
        id=project_id,
        sourceLabel=original_name,
        sourceKind="file",
        sourcePath=str(source_path),
        settings=settings,
    )
    apply_metadata(project, metadata)
    return store.save_project(project)


def create_job(project: Project, job_type: Literal["prepare", "analyze", "render"], payload: dict[str, Any] | None) -> Job:
    active = next(
        (
            job
            for job in store.jobs.values()
            if job.projectId == project.id and job.status in {"queued", "running"}
        ),
        None,
    )
    if active:
        raise WorkerError(
            "PROJECT_JOB_ACTIVE",
            "This project already has an active job.",
            status_code=409,
            retryable=True,
            details={"jobId": active.id},
        )
    job = Job(id=str(uuid.uuid4()), projectId=project.id, type=job_type, request=payload)
    project.status = "queued"
    store.save_project(project)
    store.save_job(job)
    task = asyncio.create_task(execute_job(job.id), name=f"cuttoclip-{job.type}-{job.id}")
    active_tasks[job.id] = task
    task.add_done_callback(lambda _: active_tasks.pop(job.id, None))
    return job


@app.post("/api/projects/{project_id}/prepare", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
async def prepare_project(project_id: str) -> Job:
    return create_job(get_project(project_id), "prepare", None)


@app.post("/api/projects/{project_id}/restore-source", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
async def restore_project_source(project_id: str) -> Job:
    project = get_project(project_id)
    if project.sourceKind != "youtube":
        raise WorkerError("SOURCE_NOT_RESTORABLE", "Only YouTube sources can be downloaded again.", status_code=422)
    return create_job(project, "prepare", {"restoreSource": True})


@app.post("/api/projects/{project_id}/analyze", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
async def analyze_project(project_id: str, settings: ProjectSettings) -> Job:
    return create_job(get_project(project_id), "analyze", settings.model_dump(mode="json"))


@app.post("/api/projects/{project_id}/render", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
async def render_project(project_id: str, request: RenderRequest) -> Job:
    project = get_project(project_id)
    if project.sourceKind == "youtube" and (not project.sourcePath or not Path(project.sourcePath).is_file()):
        raise WorkerError("SOURCE_RESTORE_REQUIRED", "Download the YouTube source again before rendering.", status_code=409, retryable=True)
    return create_job(project, "render", request.model_dump(mode="json"))


@app.get("/api/jobs/{job_id}", response_model=Job)
async def read_job(job_id: str) -> Job:
    return get_job(job_id)


@app.delete("/api/jobs/{job_id}", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    job = get_job(job_id)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.status = "cancelled"
    job.stage = "cancelled"
    job.stageKey = "job.cancelled"
    job.stageParams = {}
    job.completedAt = utc_now()
    job.error = ErrorInfo(
        code="JOB_CANCELLED",
        message="The job was cancelled. Start the operation again to retry it.",
        retryable=True,
    )
    store.save_job(job)
    project = store.projects.get(job.projectId)
    if project:
        project.status = "cancelled"
        store.save_project(project)
    task = active_tasks.get(job.id)
    if task and not task.done():
        download_cancel = active_download_cancels.get(job.id)
        if download_cancel is not None:
            download_cancel.set()
        else:
            task.cancel()
    return job


@app.post("/api/jobs/{job_id}/retry", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
async def retry_job(job_id: str) -> Job:
    previous = get_job(job_id)
    if previous.status not in {"failed", "partial", "cancelled", "interrupted"}:
        raise WorkerError("JOB_NOT_RETRYABLE", "Only terminal unsuccessful jobs can be retried.", status_code=409)
    payload = previous.request
    if previous.type == "render" and previous.status == "partial" and payload:
        failed_clip_ids = {
            str(item.get("clipId"))
            for item in (previous.result or {}).get("outputs", [])
            if isinstance(item, dict) and item.get("status") == "failed"
        }
        payload = {
            **payload,
            "clips": [
                clip
                for clip in payload.get("clips", [])
                if isinstance(clip, dict) and str(clip.get("id")) in failed_clip_ids
            ],
        }
        if not payload["clips"]:
            raise WorkerError("NO_FAILED_CLIPS", "The partial job has no failed clips to retry.", status_code=409)
    return create_job(get_project(previous.projectId), previous.type, payload)


def _stage_metadata(stage: str) -> tuple[str, dict[str, str | int | float]]:
    exact = {
        "checking YouTube metadata": "job.checkingYoutube",
        "downloading YouTube source": "job.downloadingYoutube",
        "reading source metadata": "job.readingSource",
        "transcribing audio chunks": "job.transcribing",
        "merging transcript timestamps": "job.mergingTranscript",
        "validating timed transcript": "job.validatingTranscript",
        "scanning transcript windows": "job.scanningTranscript",
        "validating and de-duplicating moments": "job.rankingMoments",
        "building timed captions": "job.buildingCaptions",
    }
    if stage in exact:
        return exact[stage], {}
    match = re.match(r"extracting audio chunk (\d+) of (\d+)", stage)
    if match:
        return "job.extractingAudio", {"current": int(match.group(1)), "total": int(match.group(2))}
    match = re.match(r"transcribing audio chunk (\d+) of (\d+)", stage)
    if match:
        return "job.transcribingChunk", {"current": int(match.group(1)), "total": int(match.group(2))}
    match = re.match(r"analyzing clip (\d+) of (\d+)", stage)
    if match:
        return "job.analyzingClip", {"current": int(match.group(1)), "total": int(match.group(2))}
    match = re.match(r"encoding clip (\d+) of (\d+)", stage)
    if match:
        return "job.encodingClip", {"current": int(match.group(1)), "total": int(match.group(2))}
    match = re.match(r"rendered clip (\d+) of (\d+)", stage)
    if match:
        return "job.renderedClip", {"current": int(match.group(1)), "total": int(match.group(2))}
    match = re.match(r"clip (\d+) failed; continuing", stage)
    if match:
        return "job.clipFailed", {"current": int(match.group(1))}
    return "job.working", {"detail": stage}


async def set_job_progress(job: Job, progress: int, stage: str) -> None:
    if job.status == "cancelled":
        raise asyncio.CancelledError
    job.progress = max(job.progress, min(99, progress))
    job.stage = stage
    job.stageKey, job.stageParams = _stage_metadata(stage)
    store.save_job(job)


async def execute_job(job_id: str) -> None:
    job = store.jobs.get(job_id)
    if not job or job.status != "queued":
        return
    global media_lock
    lock = media_lock
    if lock is None:
        lock = media_lock = asyncio.Lock()
    try:
        async with lock:
            if job.status == "cancelled":
                return
            project = get_project(job.projectId)
            job.status = "running"
            job.stage = "starting"
            job.stageKey = "job.starting"
            job.stageParams = {}
            job.startedAt = utc_now()
            store.save_job(job)
            project.status = {"prepare": "preparing", "analyze": "analyzing", "render": "rendering"}[job.type]
            store.save_project(project)

            if job.type == "prepare":
                terminal_status, result = await process_prepare(job, project)
            elif job.type == "analyze":
                terminal_status, result = await process_analyze(job, project)
            else:
                terminal_status, result = await process_render(job, project)

            job.status = terminal_status
            job.stage = "complete" if terminal_status == "succeeded" else terminal_status
            job.stageKey = f"job.{job.stage}"
            job.stageParams = {}
            job.progress = 100
            job.result = result
            job.completedAt = utc_now()
            if terminal_status == "partial":
                failed_count = len([item for item in result.get("outputs", []) if item.get("status") == "failed"])
                job.error = ErrorInfo(
                    code="RENDER_PARTIAL",
                    message=f"{failed_count} clip(s) failed to render; successful outputs were preserved.",
                    retryable=True,
                )
            elif terminal_status == "failed" and job.type == "render":
                failed_count = len([item for item in result.get("outputs", []) if item.get("status") == "failed"])
                job.error = ErrorInfo(
                    code="RENDER_FAILED",
                    message=f"All {failed_count} clip(s) failed to render.",
                    retryable=True,
                )
            store.save_job(job)
    except asyncio.CancelledError:
        cancelled_project = store.projects.get(job.projectId)
        if cancelled_project and cancelled_project.sourceKind == "youtube" and (
            not cancelled_project.sourcePath or not Path(cancelled_project.sourcePath).is_file()
        ):
            _remove_youtube_source_files(cancelled_project)
            cancelled_project.sourcePath = None
            store.save_project(cancelled_project)
        if job.status not in {"cancelled", "interrupted"}:
            job.status = "cancelled"
            job.stage = "cancelled"
            job.stageKey = "job.cancelled"
            job.stageParams = {}
            job.completedAt = utc_now()
            job.error = ErrorInfo(code="JOB_CANCELLED", message="The job was cancelled.", retryable=True)
            store.save_job(job)
        project = store.projects.get(job.projectId)
        if project and project.status in {"preparing", "analyzing", "rendering", "queued"}:
            project.status = "cancelled"
            store.save_project(project)
    except WorkerError as error:
        job.status = "failed"
        job.stage = "failed"
        job.stageKey = "job.failed"
        job.stageParams = {}
        job.completedAt = utc_now()
        job.error = _worker_error_info(error)
        store.save_job(job)
        project = store.projects.get(job.projectId)
        if project:
            project.status = "failed"
            store.save_project(project)
    except Exception as error:  # Keep background task failures observable through the API.
        job.status = "failed"
        job.stage = "failed"
        job.stageKey = "job.failed"
        job.stageParams = {}
        job.completedAt = utc_now()
        job.error = ErrorInfo(
            code="INTERNAL_WORKER_ERROR",
            message="The worker encountered an unexpected error.",
            retryable=True,
            details=str(error),
        )
        store.save_job(job)
        project = store.projects.get(job.projectId)
        if project:
            project.status = "failed"
            store.save_project(project)


def _bundled_ffmpeg_location() -> str | None:
    """Directory holding the bundled ffmpeg/ffprobe, for yt-dlp's muxing step.

    yt-dlp searches PATH for ffmpeg on its own; on a clean tester PC (no global
    ffmpeg) the bv*+ba merge would fail, so we point it at the bundled binary.
    """
    executable = ffmpeg_path()
    if executable:
        return str(Path(executable).parent)
    return None


def _youtube_info(url: str, options: dict[str, object]) -> dict[str, object]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    with yt_dlp.YoutubeDL(options) as downloader:
        return downloader.extract_info(url, download=not bool(options.get("skip_download"))) or {}


def _youtube_source_files(project: Project) -> list[Path]:
    """Return only top-level yt-dlp source artifacts owned by this project."""
    if project.sourceKind != "youtube":
        return []
    projects_root = store.projects_dir.resolve()
    project_dir = (projects_root / project.id).resolve()
    if project_dir.parent != projects_root or not project_dir.is_dir():
        return []
    files: list[Path] = []
    for candidate in project_dir.glob("source.*"):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.parent != project_dir or not resolved.is_file():
            continue
        files.append(resolved)
    return files


def _remove_youtube_source_files(project: Project, *, keep: Path | None = None) -> tuple[int, list[str]]:
    freed = 0
    failures: list[str] = []
    keep_resolved = keep.resolve() if keep else None
    for path in _youtube_source_files(project):
        if keep_resolved is not None and path == keep_resolved:
            continue
        try:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            freed += size
        except OSError as error:
            failures.append(f"{path.name}: {error}")
    return freed, failures


async def prepare_youtube_source(job: Job, project: Project) -> None:
    if yt_dlp is None:
        raise WorkerError("YTDLP_UNAVAILABLE", "yt-dlp is not installed in the worker.", status_code=503, retryable=True)
    if not project.sourceUrl:
        raise WorkerError("SOURCE_URL_MISSING", "The YouTube project has no source URL.")
    cancel_event = threading.Event()
    active_download_cancels[job.id] = cancel_event
    try:
        await set_job_progress(job, 3, "preparing Deno runtime")
        deno = None
        try:
            deno = await ensure_deno()
        except Exception as error:
            print(f"[deno] ensure failed, continuing without Deno: {error}", flush=True)
        await set_job_progress(job, 4, "checking YouTube metadata")
        inspect_options: dict[str, object] = {
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        if deno:
            inspect_options["js_runtimes"] = {"deno": {"path": deno}}
        try:
            metadata = await asyncio.to_thread(_youtube_info, project.sourceUrl, inspect_options)
        except Exception as error:
            if cancel_event.is_set():
                raise asyncio.CancelledError from error
            raise WorkerError(
                "YOUTUBE_METADATA_FAILED",
                "Could not read metadata for this public YouTube URL.",
                status_code=502,
                retryable=True,
                details=str(error),
            ) from error
        if cancel_event.is_set():
            raise asyncio.CancelledError
        duration = float(metadata.get("duration") or 0)
        if duration > MAX_SOURCE_DURATION_SECONDS + 0.01:
            raise WorkerError(
                "SOURCE_TOO_LONG",
                "Sources longer than 2 hours are not supported.",
                status_code=422,
                details={"durationSeconds": duration, "maximumSeconds": MAX_SOURCE_DURATION_SECONDS},
            )

        await set_job_progress(job, 9, "downloading YouTube source")
        project_dir = store.project_dir(project.id)

        def stop_cancelled_download(_: dict[str, object]) -> None:
            if cancel_event.is_set():
                raise RuntimeError("YouTube download cancelled")

        options: dict[str, object] = {
            "format": "bv*[height<=1080]+ba/b[height<=1080]",
            "merge_output_format": "mp4",
            "outtmpl": str(project_dir / "source.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [stop_cancelled_download],
        }
        ffmpeg_location = _bundled_ffmpeg_location()
        if ffmpeg_location:
            options["ffmpeg_location"] = ffmpeg_location
        if deno:
            options["js_runtimes"] = {"deno": {"path": deno}}
        # Clear stale fragments from a previous interrupted download before yt-dlp
        # starts, but never touch previews, crop plans, project metadata, or outputs.
        _remove_youtube_source_files(project)
        try:
            downloaded = await asyncio.to_thread(_youtube_info, project.sourceUrl, options)
        except Exception as error:
            _remove_youtube_source_files(project)
            project.sourcePath = None
            store.save_project(project)
            if cancel_event.is_set():
                raise asyncio.CancelledError from error
            raise WorkerError(
                "YOUTUBE_DOWNLOAD_FAILED",
                "YouTube download failed.",
                status_code=502,
                retryable=True,
                details=str(error),
            ) from error
        if cancel_event.is_set():
            _remove_youtube_source_files(project)
            project.sourcePath = None
            store.save_project(project)
            raise asyncio.CancelledError
        candidates = [
            item
            for item in project_dir.glob("source.*")
            if item.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} and item.is_file()
        ]
        media = max(candidates, key=lambda item: item.stat().st_size, default=None)
        if media is None:
            _remove_youtube_source_files(project)
            project.sourcePath = None
            store.save_project(project)
            raise WorkerError("YOUTUBE_OUTPUT_MISSING", "yt-dlp did not produce a playable video.", retryable=True)
        project.sourcePath = str(media)
        project.sourceLabel = str(downloaded.get("title") or metadata.get("title") or project.sourceLabel)
        _remove_youtube_source_files(project, keep=media)
        store.save_project(project)
    finally:
        active_download_cancels.pop(job.id, None)


async def process_prepare(job: Job, project: Project) -> tuple[Literal["succeeded"], dict[str, Any]]:
    restore_only = bool((job.request or {}).get("restoreSource"))
    if restore_only:
        if project.sourceKind != "youtube":
            raise WorkerError("SOURCE_NOT_RESTORABLE", "Only YouTube sources can be downloaded again.", status_code=422)
        if not project.sourcePath or not Path(project.sourcePath).is_file():
            await prepare_youtube_source(job, project)
        if not project.sourcePath or not Path(project.sourcePath).is_file():
            raise WorkerError("SOURCE_NOT_FOUND", "The YouTube source could not be restored.", status_code=400, retryable=True)
        await set_job_progress(job, 92, "reading source metadata")
        apply_metadata(project, await probe_media(project.sourcePath))
        project.status = "review_ready" if project.candidates else "transcript_ready"
        store.save_project(project)
        return "succeeded", {"project": project.model_dump(mode="json")}
    if project.transcriptReady and project.transcript is not None:
        project.status = "transcript_ready"
        store.save_project(project)
        return "succeeded", {"project": project.model_dump(mode="json")}
    if project.sourceKind == "youtube" and (not project.sourcePath or not Path(project.sourcePath).is_file()):
        await prepare_youtube_source(job, project)
    if not project.sourcePath or not Path(project.sourcePath).is_file():
        raise WorkerError("SOURCE_NOT_FOUND", "The local source file is no longer available.", status_code=400)

    await set_job_progress(job, 18, "reading source metadata")
    metadata = await probe_media(project.sourcePath)
    apply_metadata(project, metadata)
    store.save_project(project)

    async def extraction_progress(current: int, total: int) -> None:
        await set_job_progress(job, 20 + round((current / max(1, total)) * 25), f"extracting audio chunk {current} of {total}")

    chunks = await extract_audio_chunks(
        project.sourcePath,
        store.project_dir(project.id),
        project.durationSeconds,
        extraction_progress,
    )
    await set_job_progress(job, 46, "transcribing audio chunks")
    transcribed: list[tuple[float, Transcript]] = []
    completed = 0

    async def transcribe_chunk(chunk: AudioChunk) -> tuple[float, Transcript]:
        nonlocal completed
        transcript = await transcribe_audio(chunk, project.settings.language)
        completed += 1
        await set_job_progress(
            job,
            46 + round((completed / max(1, len(chunks))) * 43),
            f"transcribed audio chunk {completed} of {len(chunks)}",
        )
        return chunk.offset_seconds, transcript

    # Submit chunks sequentially. Parallel uploads can make one long request
    # consume the gateway's upstream slots while another is rate-limited; a
    # single transient rejection should not fail an otherwise valid project.
    for chunk in chunks:
        transcribed.append(await transcribe_chunk(chunk))
    await set_job_progress(job, 92, "merging transcript timestamps")
    transcript = merge_transcripts(transcribed, project.durationSeconds)
    if not transcript.text or not transcript.segments:
        raise WorkerError(
            "TRANSCRIPT_EMPTY",
            "The transcription service returned no timed speech segments.",
            status_code=502,
            retryable=True,
        )
    project.transcript = transcript
    project.transcriptText = transcript.text
    project.transcriptReady = True
    project.status = "transcript_ready"
    store.save_project(project)
    return "succeeded", {"project": project.model_dump(mode="json")}


def provider_configuration_error() -> WorkerError | None:
    mode = providers.provider_mode()
    if mode == "byok":
        return providers.byok_configuration_error()
    if mode == "openai":
        # Transcription always goes through Groq; highlights through 9router.
        if not providers.groq_api_keys():
            return WorkerError(
                "BYOK_GROQ_KEY_MISSING",
                "A Groq API key is required for transcription in any non-managed mode.",
                status_code=503,
                retryable=False,
            )
        return providers.openai_highlights_configuration_error()
    return gateway_configuration_error()


async def transcribe_audio(chunk: AudioChunk, language: str) -> Transcript:
    if httpx is None:
        raise WorkerError("HTTP_CLIENT_UNAVAILABLE", "httpx is not installed in the worker.", status_code=503, retryable=True)
    mode = providers.provider_mode()
    if mode in ("byok", "openai"):
        return await transcribe_audio_byok(chunk, language)
    return await transcribe_audio_managed(chunk, language)


async def transcribe_audio_managed(chunk: AudioChunk, language: str) -> Transcript:
    configuration_error = gateway_configuration_error()
    if configuration_error:
        raise configuration_error
    params = {} if language == "auto" else {"language": language}
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        for attempt in range(TRANSCRIPTION_MAX_ATTEMPTS):
            response = None
            try:
                # Reopen the chunk for every attempt so multipart encoding starts
                # from byte zero after a failed upload.
                with chunk.path.open("rb") as audio:
                    response = await client.post(
                        f"{gateway_url()}/v1/transcriptions",
                        headers=gateway_headers(),
                        params=params,
                        files={"file": (chunk.path.name, audio, "audio/mpeg")},
                    )
            except httpx.HTTPError as error:
                gateway_error = WorkerError(
                    "TRANSCRIPTION_UNREACHABLE",
                    "The transcription gateway could not be reached.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                )
                if attempt + 1 >= TRANSCRIPTION_MAX_ATTEMPTS:
                    raise gateway_error from error
                await asyncio.sleep(_transcription_retry_delay(None, attempt))
                continue

            if response.status_code >= 400:
                gateway_error = gateway_response_error(
                    response,
                    "TRANSCRIPTION_FAILED",
                    "The transcription gateway rejected an audio chunk.",
                )
                if not gateway_error.retryable or attempt + 1 >= TRANSCRIPTION_MAX_ATTEMPTS:
                    raise gateway_error
                await asyncio.sleep(_transcription_retry_delay(response, attempt))
                continue

            try:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("response is not a JSON object")
                return normalize_transcript(payload, chunk.duration_seconds)
            except (ValueError, TypeError, ValidationError) as error:
                raise WorkerError(
                    "TRANSCRIPTION_RESPONSE_INVALID",
                    "The transcription gateway returned an invalid payload.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                ) from error

    raise WorkerError(
        "TRANSCRIPTION_FAILED",
        "The transcription gateway exhausted all retry attempts.",
        status_code=502,
        retryable=True,
    )


async def transcribe_audio_byok(chunk: AudioChunk, language: str) -> Transcript:
    configuration_error = providers.byok_configuration_error()
    if configuration_error:
        raise configuration_error
    keys = providers.groq_api_keys()
    data: dict[str, Any] = {
        "response_format": "verbose_json",
        # httpx encodes a list value as repeated form fields, matching Groq's
        # OpenAI-compatible ``timestamp_granularities[]`` word+segment request.
        "timestamp_granularities[]": ["word", "segment"],
    }
    if language != "auto":
        data["language"] = language
    last_rotate_error: WorkerError | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        for key in keys:
            config = providers.groq_config(key)
            request_data = {**data, "model": config.model}
            rotate = False
            for attempt in range(TRANSCRIPTION_MAX_ATTEMPTS):
                try:
                    with chunk.path.open("rb") as audio:
                        response = await client.post(
                            config.transcription_url,
                            headers=providers.groq_headers(key),
                            data=request_data,
                            files={"file": (chunk.path.name, audio, "audio/mpeg")},
                        )
                except httpx.HTTPError as error:
                    if attempt + 1 >= TRANSCRIPTION_MAX_ATTEMPTS:
                        raise WorkerError(
                            "TRANSCRIPTION_UNREACHABLE",
                            "Groq could not be reached for transcription.",
                            status_code=502,
                            retryable=True,
                            details=str(error),
                        ) from error
                    await asyncio.sleep(_transcription_retry_delay(None, attempt))
                    continue

                if providers.is_key_permanent_reject(response.status_code):
                    # 401/402: the key is invalid or out of quota. Retrying it is
                    # futile — fall through to the next key immediately.
                    last_rotate_error = gateway_response_error(
                        response, "TRANSCRIPTION_FAILED", "Groq rejected the API key."
                    )
                    rotate = True
                    break

                if providers.is_key_transient_status(response.status_code):
                    # 403/429: a valid key momentarily failing. Retry on the same
                    # key with backoff; only rotate once its retries are spent.
                    last_rotate_error = gateway_response_error(
                        response, "TRANSCRIPTION_FAILED", "Groq temporarily rejected the API key."
                    )
                    if attempt + 1 >= TRANSCRIPTION_MAX_ATTEMPTS:
                        rotate = True
                        break
                    await asyncio.sleep(_transcription_retry_delay(response, attempt))
                    continue

                if response.status_code >= 400:
                    groq_error = gateway_response_error(
                        response,
                        "TRANSCRIPTION_FAILED",
                        "Groq rejected an audio chunk.",
                    )
                    if not groq_error.retryable or attempt + 1 >= TRANSCRIPTION_MAX_ATTEMPTS:
                        raise groq_error
                    await asyncio.sleep(_transcription_retry_delay(response, attempt))
                    continue

                try:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise TypeError("response is not a JSON object")
                    return normalize_transcript(payload, chunk.duration_seconds)
                except (ValueError, TypeError, ValidationError) as error:
                    raise WorkerError(
                        "TRANSCRIPTION_RESPONSE_INVALID",
                        "Groq returned an invalid transcription payload.",
                        status_code=502,
                        retryable=True,
                        details=str(error),
                    ) from error
            if not rotate:
                break

    if last_rotate_error is not None:
        raise WorkerError(
            "TRANSCRIPTION_KEYS_EXHAUSTED",
            "Every configured Groq API key was rate-limited or rejected.",
            status_code=502,
            retryable=True,
            details=last_rotate_error.details,
        )
    raise WorkerError(
        "TRANSCRIPTION_FAILED",
        "Groq transcription exhausted all retry attempts.",
        status_code=502,
        retryable=True,
    )


def _transcription_retry_delay(response: Any | None, attempt: int) -> float:
    if response is not None:
        raw_retry_after = response.headers.get("retry-after")
        if raw_retry_after:
            try:
                return min(TRANSCRIPTION_RETRY_MAX_SECONDS, max(0.0, float(raw_retry_after)))
            except ValueError:
                pass
    return min(TRANSCRIPTION_RETRY_MAX_SECONDS, TRANSCRIPTION_RETRY_BASE_SECONDS * (2**attempt))


def gateway_response_error(response: Any, fallback_code: str, fallback_message: str) -> WorkerError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    upstream = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(upstream, dict):
        code = str(upstream.get("code") or fallback_code)
        message = str(upstream.get("message") or fallback_message)
        details = upstream.get("details")
        retryable = bool(upstream.get("retryable", response.status_code == 429 or response.status_code >= 500))
    else:
        code = str(upstream or fallback_code)
        message = str(payload.get("message") or fallback_message) if isinstance(payload, dict) else fallback_message
        details = payload if payload else {
            "gatewayStatus": response.status_code,
            "contentType": response.headers.get("content-type"),
        }
        retryable = response.status_code == 429 or response.status_code >= 500
    return WorkerError(code, message, status_code=502, retryable=retryable, details=details)


async def request_highlights(project: Project, settings: ProjectSettings) -> list[Candidate]:
    if httpx is None:
        raise WorkerError("HTTP_CLIENT_UNAVAILABLE", "httpx is not installed in the worker.", status_code=503, retryable=True)
    mode = providers.provider_mode()
    if mode == "byok":
        return await request_highlights_byok(project, settings)
    if mode == "openai":
        return await request_highlights_openai(project, settings)
    return await request_highlights_managed(project, settings)


async def request_highlights_managed(project: Project, settings: ProjectSettings) -> list[Candidate]:
    configuration_error = gateway_configuration_error()
    if configuration_error:
        raise configuration_error
    transcript = project.transcript
    if transcript is None or not transcript.segments:
        raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before analyzing highlights.", status_code=409)
    request_payload = {
        "transcript": transcript.text,
        "segments": [highlight_segment_payload(segment) for segment in transcript.segments],
        "sourceDurationSeconds": project.durationSeconds,
        "settings": {
            "clipCount": settings.clipCount,
            "minDurationSeconds": settings.duration.minSeconds,
            "maxDurationSeconds": settings.duration.maxSeconds,
            "language": settings.language,
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        for attempt in range(HIGHLIGHTS_MAX_ATTEMPTS):
            response = None
            try:
                response = await client.post(
                    f"{gateway_url()}/v1/highlights",
                    headers=gateway_headers(),
                    json=request_payload,
                )
            except httpx.HTTPError as error:
                gateway_error = WorkerError(
                    "HIGHLIGHTS_UNREACHABLE",
                    "The highlight gateway could not be reached.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                )
                if attempt + 1 >= HIGHLIGHTS_MAX_ATTEMPTS:
                    raise gateway_error from error
                await asyncio.sleep(_transcription_retry_delay(None, attempt))
                continue

            if response.status_code >= 400:
                gateway_error = gateway_response_error(response, "HIGHLIGHTS_FAILED", "Highlight analysis failed.")
                if not gateway_error.retryable or attempt + 1 >= HIGHLIGHTS_MAX_ATTEMPTS:
                    raise gateway_error
                await asyncio.sleep(_transcription_retry_delay(response, attempt))
                continue

            try:
                payload = response.json()
                clips = payload.get("clips") if isinstance(payload, dict) else None
                if not isinstance(clips, list) or not clips:
                    raise ValueError("clips must be a non-empty array")
                candidates: list[Candidate] = []
                for index, raw in enumerate(clips):
                    if not isinstance(raw, dict):
                        raise ValueError(f"clip {index + 1} is not an object")
                    candidate = Candidate.model_validate(
                        {**raw, "id": raw.get("id") or f"clip-{index + 1:02d}", "source": "ai"}
                    )
                    duration = candidate.endSeconds - candidate.startSeconds
                    if candidate.endSeconds > project.durationSeconds + 0.05:
                        raise ValueError(f"clip {candidate.id} exceeds source duration")
                    if duration < settings.duration.minSeconds or duration > settings.duration.maxSeconds:
                        raise ValueError(f"clip {candidate.id} is outside the requested duration range")
                    candidates.append(candidate)
                kept: list[Candidate] = []
                for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
                    duplicate = any(overlap_ratio(candidate, existing) > 0.5 for existing in kept)
                    if not duplicate:
                        kept.append(candidate)
                    if len(kept) >= settings.clipCount:
                        break
                if not kept:
                    raise WorkerError("NO_VALID_HIGHLIGHTS", "No valid highlight candidates were found.", retryable=True)
                return kept
            except (ValueError, TypeError, ValidationError) as error:
                gateway_error = WorkerError(
                    "HIGHLIGHTS_RESPONSE_INVALID",
                    "The highlight gateway returned invalid clip candidates.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                )
                if attempt + 1 >= HIGHLIGHTS_MAX_ATTEMPTS:
                    raise gateway_error from error
                await asyncio.sleep(_transcription_retry_delay(response if isinstance(response, object) else None, attempt))
                continue

    raise WorkerError(
        "HIGHLIGHTS_FAILED",
        "Highlight analysis exhausted all retry attempts.",
        status_code=502,
        retryable=True,
    )


async def request_highlights_byok(project: Project, settings: ProjectSettings) -> list[Candidate]:
    configuration_error = providers.byok_configuration_error()
    if configuration_error:
        raise configuration_error
    transcript = project.transcript
    if transcript is None or not transcript.segments:
        raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before analyzing highlights.", status_code=409)
    keys = providers.gemini_api_keys()
    windows = providers.create_highlight_windows(transcript.segments, project.durationSeconds)
    if not windows:
        raise WorkerError(
            "EMPTY_TIMED_TRANSCRIPT",
            "No timed transcript content overlaps the source.",
            status_code=422,
        )
    settings_payload = {
        "clipCount": settings.clipCount,
        "minDurationSeconds": settings.duration.minSeconds,
        "maxDurationSeconds": settings.duration.maxSeconds,
        "language": settings.language,
    }
    candidates: list[Candidate] = []
    # Rotate keys across windows: once a key is rate-limited/rejected we advance
    # to the next one and never fall back to the dead key for later windows.
    key_index = 0
    last_rotate_error: WorkerError | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        for window in windows:
            request_body = providers.build_gemini_highlight_request(
                window, project.durationSeconds, settings_payload
            )
            response = None
            while key_index < len(keys):
                config = providers.gemini_config(keys[key_index])
                # Retry a transient failure (403 just-created key not yet
                # propagated, 429 brief rate limit) on the SAME key with backoff
                # before giving up on it. A permanent reject (401/402) rotates
                # immediately — retrying a bad key only wastes time.
                rotate = False
                for attempt in range(HIGHLIGHTS_MAX_ATTEMPTS):
                    try:
                        response = await client.post(
                            config.generate_content_url,
                            headers={
                                "content-type": "application/json",
                                "x-goog-api-key": keys[key_index],
                            },
                            json=request_body,
                        )
                    except httpx.HTTPError as error:
                        raise WorkerError(
                            "HIGHLIGHTS_UNREACHABLE",
                            "Gemini could not be reached for AI Moments.",
                            status_code=502,
                            retryable=True,
                            details=str(error),
                        ) from error
                    if providers.is_key_permanent_reject(response.status_code):
                        last_rotate_error = gateway_response_error(
                            response, "HIGHLIGHTS_FAILED", "Gemini rejected the API key."
                        )
                        rotate = True
                        break
                    if providers.is_key_transient_status(response.status_code):
                        last_rotate_error = gateway_response_error(
                            response, "HIGHLIGHTS_FAILED", "Gemini temporarily rejected the API key."
                        )
                        if attempt + 1 >= HIGHLIGHTS_MAX_ATTEMPTS:
                            # Same-key retries spent; rotate to the next key.
                            rotate = True
                            break
                        await asyncio.sleep(_transcription_retry_delay(response, attempt))
                        response = None
                        continue
                    break
                if rotate:
                    key_index += 1
                    response = None
                    continue
                break
            if response is None:
                raise WorkerError(
                    "HIGHLIGHTS_KEYS_EXHAUSTED",
                    "Every configured Gemini API key was rate-limited or rejected.",
                    status_code=502,
                    retryable=True,
                    details=last_rotate_error.details if last_rotate_error else None,
                )
            if response.status_code >= 400:
                raise gateway_response_error(response, "HIGHLIGHTS_FAILED", "Gemini highlight analysis failed.")
            try:
                clips = providers.extract_gemini_clips(response.json())
            except (ValueError, TypeError) as error:
                raise WorkerError(
                    "HIGHLIGHTS_RESPONSE_INVALID",
                    "Gemini returned malformed highlight output.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                ) from error
            candidates.extend(
                providers.validate_window_candidates(
                    clips,
                    window,
                    project.durationSeconds,
                    settings.duration.minSeconds,
                    settings.duration.maxSeconds,
                )
            )
    kept = providers.rank_and_dedupe_candidates(candidates, settings.clipCount)
    if not kept:
        raise WorkerError("NO_VALID_HIGHLIGHTS", "No valid highlight candidates were found.", retryable=True)
    return kept


async def request_highlights_openai(project: Project, settings: ProjectSettings) -> list[Candidate]:
    """Run highlight detection through an OpenAI-compatible endpoint (9router)."""
    configuration_error = providers.openai_highlights_configuration_error()
    if configuration_error:
        raise configuration_error
    transcript = project.transcript
    if transcript is None or not transcript.segments:
        raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before analyzing highlights.", status_code=409)
    keys = providers.openrouter_api_keys()
    windows = providers.create_highlight_windows(transcript.segments, project.durationSeconds)
    if not windows:
        raise WorkerError(
            "EMPTY_TIMED_TRANSCRIPT",
            "No timed transcript content overlaps the source.",
            status_code=422,
        )
    settings_payload = {
        "clipCount": settings.clipCount,
        "minDurationSeconds": settings.duration.minSeconds,
        "maxDurationSeconds": settings.duration.maxSeconds,
        "language": settings.language,
    }
    candidates: list[Candidate] = []
    key_index = 0
    last_rotate_error: WorkerError | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
        for window in windows:
            config = providers.openrouter_config(keys[key_index] if key_index < len(keys) else "")
            request_body = providers.build_openai_highlight_request(
                window, project.durationSeconds, settings_payload
            )
            request_body["model"] = config.model
            response = None
            while key_index < len(keys):
                config = providers.openrouter_config(keys[key_index])
                request_body["model"] = config.model
                rotate = False
                for attempt in range(HIGHLIGHTS_MAX_ATTEMPTS):
                    try:
                        response = await client.post(
                            config.chat_completions_url,
                            headers=providers.openai_headers(keys[key_index]),
                            json=request_body,
                        )
                    except httpx.HTTPError as error:
                        raise WorkerError(
                            "HIGHLIGHTS_UNREACHABLE",
                            "The 9router endpoint could not be reached for AI Moments.",
                            status_code=502,
                            retryable=True,
                            details=str(error),
                        ) from error
                    if providers.is_key_permanent_reject(response.status_code):
                        last_rotate_error = gateway_response_error(
                            response, "HIGHLIGHTS_FAILED", "9router rejected the API key."
                        )
                        rotate = True
                        break
                    if providers.is_key_transient_status(response.status_code):
                        last_rotate_error = gateway_response_error(
                            response, "HIGHLIGHTS_FAILED", "9router temporarily rejected the API key."
                        )
                        if attempt + 1 >= HIGHLIGHTS_MAX_ATTEMPTS:
                            rotate = True
                            break
                        await asyncio.sleep(_transcription_retry_delay(response, attempt))
                        response = None
                        continue
                    break
                if rotate:
                    key_index += 1
                    response = None
                    continue
                break
            if response is None:
                raise WorkerError(
                    "HIGHLIGHTS_KEYS_EXHAUSTED",
                    "Every configured 9router API key was rate-limited or rejected.",
                    status_code=502,
                    retryable=True,
                    details=last_rotate_error.details if last_rotate_error else None,
                )
            if response.status_code >= 400:
                raise gateway_response_error(response, "HIGHLIGHTS_FAILED", "9router highlight analysis failed.")
            try:
                clips = providers.extract_openai_clips(response.json())
            except (ValueError, TypeError) as error:
                raise WorkerError(
                    "HIGHLIGHTS_RESPONSE_INVALID",
                    "9router returned malformed highlight output.",
                    status_code=502,
                    retryable=True,
                    details=str(error),
                ) from error
            candidates.extend(
                providers.validate_window_candidates(
                    clips,
                    window,
                    project.durationSeconds,
                    settings.duration.minSeconds,
                    settings.duration.maxSeconds,
                )
            )
    kept = providers.rank_and_dedupe_candidates(candidates, settings.clipCount)
    if not kept:
        raise WorkerError("NO_VALID_HIGHLIGHTS", "No valid highlight candidates were found.", retryable=True)
    return kept


def overlap_ratio(left: Candidate, right: Candidate) -> float:
    overlap = max(0.0, min(left.endSeconds, right.endSeconds) - max(left.startSeconds, right.startSeconds))
    shortest = min(left.endSeconds - left.startSeconds, right.endSeconds - right.startSeconds)
    return overlap / shortest if shortest > 0 else 0.0


async def process_analyze(job: Job, project: Project) -> tuple[Literal["succeeded"], dict[str, Any]]:
    try:
        settings = ProjectSettings.model_validate(job.request or {})
    except ValidationError as error:
        raise WorkerError("INVALID_JOB_PAYLOAD", "The persisted analyze settings are invalid.", details=str(error)) from error
    project.settings = settings
    store.save_project(project)
    await set_job_progress(job, 12, "validating timed transcript")
    if not project.transcriptReady or project.transcript is None:
        raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before analyzing highlights.", status_code=409)
    await set_job_progress(job, 28, "scanning transcript windows")
    candidates = await request_highlights(project, settings)
    await set_job_progress(job, 88, "validating and de-duplicating moments")
    manual = [candidate for candidate in project.candidates if candidate.source == "manual"]
    presentation = ClipPresentation(layout=settings.layout, captionPreset=settings.captionPreset)
    for candidate in candidates:
        if candidate.presentation is None:
            candidate.presentation = presentation.model_copy()
    project.candidates = candidates[: max(0, 10 - len(manual))] + manual
    project.revision += 1
    project.status = "review_ready"
    store.save_project(project)
    return "succeeded", {"project": project.model_dump(mode="json")}


def validate_render_clip(clip: Candidate, project: Project, settings: ProjectSettings) -> None:
    duration = clip.endSeconds - clip.startSeconds
    if clip.startSeconds < 0 or clip.endSeconds > project.durationSeconds + 0.05:
        raise WorkerError(
            "CLIP_OUT_OF_BOUNDS",
            f"Clip {clip.id} must stay within the source duration.",
            status_code=422,
            details={"sourceDurationSeconds": project.durationSeconds},
        )
    if duration < settings.duration.minSeconds or duration > settings.duration.maxSeconds:
        raise WorkerError(
            "CLIP_DURATION_INVALID",
            f"Clip {clip.id} must be between {settings.duration.minSeconds} and {settings.duration.maxSeconds} seconds.",
            status_code=422,
        )


def highlight_segment_payload(segment: TranscriptSegment) -> dict[str, str | float]:
    return {
        "text": segment.text,
        "startSeconds": segment.startSeconds,
        "endSeconds": segment.endSeconds,
    }


HW_ENCODERS = ("h264_amf", "h264_nvenc", "h264_qsv")

ENCODER_PARAMS: dict[str, list[str]] = {
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p"],
    "h264_amf": ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "quality", "-qp_i", "22", "-qp_p", "22", "-pix_fmt", "yuv420p"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22", "-rc", "vbr", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22", "-pix_fmt", "nv12"],
}

_encoder_cache: list[str] | None = None


async def detect_encoders(executable: str) -> list[str]:
    global _encoder_cache
    if _encoder_cache is not None:
        return _encoder_cache

    def _run() -> list[str]:
        import subprocess
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 15}
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000
            result = subprocess.run([executable, "-hide_banner", "-encoders"], **kwargs)
            output = result.stdout + result.stderr
        except Exception:
            return []
        available: list[str] = []
        for name in ENCODER_PARAMS:
            if name in output:
                available.append(name)
        return available

    _encoder_cache = await asyncio.to_thread(_run)
    return _encoder_cache


def resolve_encoder(available: list[str], desired: str) -> str:
    if desired in available:
        return desired
    if desired == "auto":
        for hw in HW_ENCODERS:
            if hw in available:
                return hw
    return "libx264"


def output_filter(layout: str, command_file_name: str | None = None) -> str:
    if layout == "smart_portrait" and command_file_name:
        escaped = command_file_name.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        return (
            f"scale=-2:1920,sendcmd=f='{escaped}',"
            "crop@smart=1080:1920:x='(iw-ow)/2':y='(ih-oh)/2'"
        )
    if layout == "landscape":
        return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"


def _clip_progress(index: int, total: int, fraction: float) -> int:
    # Map a within-clip fraction into the 5..95 render band, so per-clip sub-steps
    # advance the bar instead of leaving it parked between clip completions.
    base = 5 + ((index - 1) / total) * 90
    top = 5 + (index / total) * 90
    return round(base + (top - base) * fraction)


async def render_one_clip(
    project: Project,
    clip: Candidate,
    settings: ProjectSettings,
    index: int,
    output_dir: Path,
    *,
    job: Job | None = None,
    total: int = 1,
) -> RenderOutput:
    executable = ffmpeg_path()
    if not executable:
        raise WorkerError("FFMPEG_UNAVAILABLE", "FFmpeg is required to render clips.", status_code=503, retryable=True)
    if not project.sourcePath or not Path(project.sourcePath).is_file():
        raise WorkerError("SOURCE_NOT_FOUND", "The project source video is unavailable.", status_code=400)

    presentation = clip.presentation or ClipPresentation(layout=settings.layout, captionPreset=settings.captionPreset)
    project_dir = store.project_dir(project.id)

    # Analyze before captions: the detected crop mode decides the render graph.
    smart_result = None
    gaming_facecams: list[dict[str, int]] = []
    if presentation.layout == "smart_portrait" and project.width > project.height:
        if job is not None:
            await set_job_progress(job, _clip_progress(index, total, 0.05), f"analyzing clip {index} of {total}")
        yunet_path, silero_path = await ensure_vision_models()
        smart_result = await asyncio.to_thread(
            smart_crop_track,
            project.sourcePath,
            clip.startSeconds,
            clip.endSeconds,
            yunet_path,
            silero_path,
            executable,
        )
    elif presentation.layout == "gaming_portrait" and project.width > project.height:
        if job is not None:
            await set_job_progress(job, _clip_progress(index, total, 0.05), f"analyzing clip {index} of {total}")
        yunet_path = await ensure_yunet_model()
        gaming_facecams = await asyncio.to_thread(
            gaming_facecam_track,
            project.sourcePath,
            clip.startSeconds,
            clip.endSeconds,
            yunet_path,
        )
    dual = smart_result is not None and smart_result.mode == "dual"

    caption_expression: str | None = None
    if not captions_disabled(presentation.captionPreset):
        if project.transcript is None:
            raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before rendering captions.", status_code=409)
        captions_dir = project_dir / "captions"
        caption_path = captions_dir / f"{safe_slug(clip.id, f'clip-{index:02d}')}.ass"
        build_ass(
            project.transcript,
            clip.startSeconds,
            clip.endSeconds,
            presentation.captionPreset,
            presentation.layout,
            caption_path,
            dual,
        )
        fonts_dir = Path(__file__).resolve().parent / "assets" / "fonts"
        caption_expression = ass_filter(caption_path, fonts_dir if fonts_dir.exists() else None)

    command_file_name: str | None = None
    if smart_result is not None and smart_result.mode == "single" and smart_result.track:
        command_path = project_dir / f"crop-{safe_slug(clip.id, f'clip-{index:02d}')}.commands"
        command_path.write_text(ffmpeg_crop_commands(smart_result.track), encoding="utf-8")
        command_file_name = command_path.name

    title = safe_slug(clip.title, f"clip-{index:02d}")
    file_name = f"{index:02d}-{title}.mp4"
    output_path = output_dir / file_name
    temporary = output_path.with_suffix(".partial.mp4")
    temporary.unlink(missing_ok=True)
    duration = clip.endSeconds - clip.startSeconds
    command = [
        executable,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{clip.startSeconds:.3f}",
        "-i",
        project.sourcePath,
        "-t",
        f"{duration:.3f}",
    ]
    if dual:
        graph = dual_facecam_filter(smart_result.facecams)
        video_label = "[stacked]"
        if caption_expression:
            graph += f";[stacked]{caption_expression}[vout]"
            video_label = "[vout]"
        command += ["-filter_complex", graph, "-map", video_label, "-map", "0:a:0?"]
    elif gaming_facecams:
        graph = gaming_facecam_filter(gaming_facecams)
        video_label = "[stacked]"
        if caption_expression:
            graph += f";[stacked]{caption_expression}[vout]"
            video_label = "[vout]"
        command += ["-filter_complex", graph, "-map", video_label, "-map", "0:a:0?"]
    else:
        video_filter = ",".join(
            expression
            for expression in [output_filter(presentation.layout, command_file_name), caption_expression]
            if expression
        )
        command += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", video_filter]
    command += ENCODER_PARAMS["libx264"]
    command += [
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(temporary),
    ]

    available_encoders = await detect_encoders(executable)
    encoder = resolve_encoder(available_encoders, settings.encoder)
    if encoder != "libx264":
        encoder_command = command.copy()
        encoder_params = ENCODER_PARAMS[encoder]
        codec_idx = encoder_command.index("-c:v") if "-c:v" in encoder_command else -1
        if codec_idx >= 0:
            encoder_command[codec_idx : codec_idx + len(ENCODER_PARAMS["libx264"])] = encoder_params
        try:
            if job is not None:
                await set_job_progress(job, _clip_progress(index, total, 0.6), f"encoding clip {index} of {total} ({encoder})")
            code, _, stderr = await run_command(*encoder_command, cwd=str(project_dir))
            if code == 0 and temporary.exists():
                os.replace(temporary, output_path)
                output_id = str(uuid.uuid4())
                try:
                    rendered_metadata = await probe_media(output_path)
                    rendered_duration = float(rendered_metadata["durationSeconds"])
                except WorkerError:
                    rendered_duration = duration
                return RenderOutput(
                    id=output_id,
                    clipId=clip.id,
                    fileName=file_name,
                    path=str(output_path),
                    mediaUrl=f"/api/projects/{project.id}/outputs/{output_id}",
                    durationSeconds=rendered_duration,
                    status="succeeded",
                    clipRevision=clip.revision,
                )
            temporary.unlink(missing_ok=True)
        except Exception:
            temporary.unlink(missing_ok=True)

    if job is not None:
        await set_job_progress(job, _clip_progress(index, total, 0.6), f"encoding clip {index} of {total}")
    code, _, stderr = await run_command(*command, cwd=str(project_dir))
    if code != 0 or not temporary.exists():
        temporary.unlink(missing_ok=True)
        raise WorkerError(
            "FFMPEG_RENDER_FAILED",
            f"FFmpeg could not render clip {clip.id}.",
            retryable=True,
            details=stderr[-1200:] or None,
        )
    os.replace(temporary, output_path)
    output_id = str(uuid.uuid4())
    try:
        rendered_metadata = await probe_media(output_path)
        rendered_duration = float(rendered_metadata["durationSeconds"])
    except WorkerError:
        rendered_duration = duration
    return RenderOutput(
        id=output_id,
        clipId=clip.id,
        fileName=file_name,
        path=str(output_path),
        mediaUrl=f"/api/projects/{project.id}/outputs/{output_id}",
        durationSeconds=rendered_duration,
        status="succeeded",
        clipRevision=clip.revision,
    )


async def process_render(
    job: Job,
    project: Project,
) -> tuple[Literal["succeeded", "partial", "failed"], dict[str, Any]]:
    try:
        request = RenderRequest.model_validate(job.request or {})
    except ValidationError as error:
        raise WorkerError("INVALID_JOB_PAYLOAD", "The persisted render request is invalid.", details=str(error)) from error
    for clip in request.clips:
        validate_render_clip(clip, project, request.settings)
    if not ffmpeg_path():
        raise WorkerError("FFMPEG_UNAVAILABLE", "FFmpeg is required to render clips.", status_code=503, retryable=True)
    if not project.transcriptReady or project.transcript is None:
        raise WorkerError("TRANSCRIPT_REQUIRED", "Prepare the project before rendering captions.", status_code=409)

    project.settings = request.settings
    incoming = {clip.id: clip for clip in request.clips}
    merged_candidates = [incoming.pop(existing.id, existing) for existing in project.candidates]
    merged_candidates.extend(incoming.values())
    project.candidates = merged_candidates
    store.save_project(project)
    output_dir = store.project_output_dir(project)
    await set_job_progress(job, 5, "building timed captions")
    outputs: list[RenderOutput] = []
    for index, clip in enumerate(request.clips, start=1):
        try:
            output = await render_one_clip(project, clip, request.settings, index, output_dir)
        except asyncio.CancelledError:
            raise
        except WorkerError as error:
            failed_id = str(uuid.uuid4())
            output = RenderOutput(
                id=failed_id,
                clipId=clip.id,
                fileName=f"{index:02d}-{safe_slug(clip.title, f'clip-{index:02d}')}.mp4",
                path=str(output_dir / f"{index:02d}-{safe_slug(clip.title, f'clip-{index:02d}')}.mp4"),
                mediaUrl=f"/api/projects/{project.id}/outputs/{failed_id}",
                durationSeconds=clip.endSeconds - clip.startSeconds,
                status="failed",
                error=_worker_error_info(error),
                clipRevision=clip.revision,
            )
        outputs.append(output)
        project.outputs = [existing for existing in project.outputs if existing.clipId != clip.id]
        project.outputs.append(output)
        store.save_project(project)
        await set_job_progress(
            job,
            5 + round((index / len(request.clips)) * 90),
            f"rendered clip {index} of {len(request.clips)}" if output.status == "succeeded" else f"clip {index} failed; continuing",
        )

    success_count = len([output for output in outputs if output.status == "succeeded"])
    failure_count = len(outputs) - success_count
    if success_count == len(outputs):
        terminal: Literal["succeeded", "partial", "failed"] = "succeeded"
        project.status = "complete"
    elif success_count:
        terminal = "partial"
        project.status = "partial"
    else:
        terminal = "failed"
        project.status = "failed"
    store.save_project(project)
    result = {
        "project": project.model_dump(mode="json"),
        "outputs": [output.model_dump(mode="json") for output in outputs],
    }
    return terminal, result


@app.get("/api/projects/{project_id}/outputs", response_model=list[RenderOutput])
async def list_outputs(project_id: str) -> list[RenderOutput]:
    return get_project(project_id).outputs


def _range_bounds(header: str | None, size: int) -> tuple[int, int, bool]:
    if not header:
        return 0, max(0, size - 1), False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        raise WorkerError("INVALID_RANGE", "The requested byte range is invalid.", status_code=416)
    start_text, end_text = match.groups()
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise WorkerError("INVALID_RANGE", "The requested byte range is invalid.", status_code=416)
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    if start >= size or start > end:
        raise WorkerError("RANGE_NOT_SATISFIABLE", "The requested byte range is outside the file.", status_code=416)
    return start, min(end, size - 1), True


def _file_iterator(path: Path, start: int, length: int, chunk_size: int = 1024 * 1024):
    with path.open("rb") as source:
        source.seek(start)
        remaining = length
        while remaining > 0:
            block = source.read(min(chunk_size, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def _stream_local_file(path: Path, request: Request, file_name: str) -> StreamingResponse:
    if not path.is_file():
        raise WorkerError("SOURCE_FILE_MISSING", "The source video is no longer available.", status_code=404)
    size = path.stat().st_size
    start, end, partial = _range_bounds(request.headers.get("range"), size)
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{Path(file_name).name}"',
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _file_iterator(path, start, length),
        status_code=206 if partial else 200,
        media_type=mimetypes.guess_type(path.name)[0] or "video/mp4",
        headers=headers,
    )


@app.post("/api/projects/{project_id}/layout-preview", response_model=LayoutPreviewPlan)
async def project_layout_preview(project_id: str, request: LayoutPreviewRequest) -> LayoutPreviewPlan:
    project = get_project(project_id)
    if not project.sourcePath or not Path(project.sourcePath).is_file():
        code = "SOURCE_RESTORE_REQUIRED" if project.sourceKind == "youtube" else "SOURCE_NOT_READY"
        message = "Download the YouTube source again to build an accurate preview." if project.sourceKind == "youtube" else "The source video is unavailable."
        raise WorkerError(code, message, status_code=409, retryable=project.sourceKind == "youtube")
    if project.durationSeconds and request.endSeconds > project.durationSeconds + 0.01:
        raise WorkerError("PREVIEW_RANGE_INVALID", "The preview range is outside the source video.", status_code=422)

    source = Path(project.sourcePath)
    source_stat = source.stat()
    signature = json.dumps({
        "source": str(source),
        "size": source_stat.st_size,
        "mtime": source_stat.st_mtime_ns,
        "start": round(request.startSeconds, 3),
        "end": round(request.endSeconds, 3),
        "layout": request.layout,
        "vision": "yunet-2026may-silero-v1",
    }, sort_keys=True)
    cache_key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
    preview_dir = store.project_dir(project.id) / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    cache_path = preview_dir / f"layout-{cache_key}.json"
    if cache_path.is_file():
        try:
            return LayoutPreviewPlan.model_validate_json(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache_path.unlink(missing_ok=True)

    width = project.width
    height = project.height
    if width <= 0 or height <= 0:
        metadata = await probe_media(source)
        apply_metadata(project, metadata)
        store.save_project(project)
        width, height = project.width, project.height

    if width <= height:
        analysis: SmartCropResult | list[dict[str, int]] = SmartCropResult(mode="single", track=[], facecams=[])
    elif request.layout == "smart_portrait":
        yunet_path, silero_path = await ensure_vision_models()
        executable = ffmpeg_path()
        if not executable:
            raise WorkerError("FFMPEG_UNAVAILABLE", "FFmpeg is required for Smart Portrait preview.", status_code=503, retryable=True)
        analysis = await asyncio.to_thread(
            smart_crop_track,
            source,
            request.startSeconds,
            request.endSeconds,
            yunet_path,
            silero_path,
            executable,
        )
    else:
        yunet_path = await ensure_yunet_model()
        analysis = await asyncio.to_thread(
            gaming_facecam_track,
            source,
            request.startSeconds,
            request.endSeconds,
            yunet_path,
        )

    plan = LayoutPreviewPlan.model_validate(layout_preview_plan(request.layout, width, height, analysis, cache_key))
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    os.replace(temporary, cache_path)
    return plan


@app.get("/api/projects/{project_id}/source")
async def stream_source(project_id: str, request: Request) -> StreamingResponse:
    project = get_project(project_id)
    if not project.sourcePath:
        raise WorkerError("SOURCE_NOT_READY", "The source video is still being prepared.", status_code=409, retryable=True)
    return _stream_local_file(Path(project.sourcePath), request, project.sourceLabel)


@app.get("/api/projects/{project_id}/frame")
async def project_frame(
    project_id: str,
    at: float = Query(0, ge=0),
    width: int = Query(320, ge=160, le=1280),
) -> FileResponse:
    project = get_project(project_id)
    if not project.sourcePath or not Path(project.sourcePath).is_file():
        raise WorkerError("SOURCE_NOT_READY", "The source video is still being prepared.", status_code=409, retryable=True)
    if project.durationSeconds and at > project.durationSeconds:
        raise WorkerError("FRAME_TIME_OUT_OF_RANGE", "The preview time is outside the source video.", status_code=422)
    executable = ffmpeg_path()
    if not executable:
        raise WorkerError("FFMPEG_UNAVAILABLE", "FFmpeg is required to create preview frames.", status_code=503, retryable=True)
    preview_dir = store.project_dir(project.id) / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    frame_path = preview_dir / f"frame-{round(at * 10):010d}-{width}.jpg"
    if not frame_path.is_file():
        temporary = frame_path.with_suffix(".partial.jpg")
        code, _, stderr = await run_command(
            executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            project.sourcePath,
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-q:v",
            "3",
            str(temporary),
            cwd=str(preview_dir),
        )
        if code != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            raise WorkerError("FRAME_EXTRACTION_FAILED", "The preview frame could not be created.", retryable=True, details=stderr[-1200:] or None)
        os.replace(temporary, frame_path)
    return FileResponse(frame_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/projects/{project_id}/outputs/{output_id}")
async def stream_output(
    project_id: str,
    output_id: str,
    request: Request,
    download: bool = Query(False),
) -> StreamingResponse:
    project = get_project(project_id)
    output = next((item for item in project.outputs if item.id == output_id), None)
    if output is None:
        raise WorkerError("OUTPUT_NOT_FOUND", "Rendered output not found.", status_code=404)
    if output.status != "succeeded":
        raise WorkerError("OUTPUT_UNAVAILABLE", "This clip did not render successfully.", status_code=409, retryable=True)
    path = Path(output.path)
    if not path.is_file():
        raise WorkerError("OUTPUT_FILE_MISSING", "The rendered file no longer exists.", status_code=404)
    response = _stream_local_file(path, request, output.fileName)
    response.headers["Content-Disposition"] = f'{"attachment" if download else "inline"}; filename="{output.fileName}"'
    return response


_web_dist = os.getenv("CUTTOCLIP_WEB_DIST", "").strip()
if _web_dist and Path(_web_dist).is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_web_dist, html=True), name="spa")
