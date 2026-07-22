from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DurationRange(WorkerModel):
    minSeconds: int = Field(15, ge=15, le=90)
    maxSeconds: int = Field(90, ge=15, le=90)

    @model_validator(mode="after")
    def validate_order(self) -> "DurationRange":
        if self.minSeconds > self.maxSeconds:
            raise ValueError("minSeconds must be less than or equal to maxSeconds")
        return self


CaptionPresetName = Literal["none", "clean", "bold_focus", "karaoke", "subtitle_box"]


class ProjectSettings(WorkerModel):
    clipCount: int = Field(3, ge=1, le=10)
    duration: DurationRange = Field(default_factory=DurationRange)
    language: str = Field("auto", min_length=1, max_length=32)
    layout: Literal["portrait", "landscape", "smart_portrait", "gaming_portrait"] = "smart_portrait"
    captionPreset: CaptionPresetName = "bold_focus"
    encoder: Literal["auto", "libx264", "h264_amf", "h264_nvenc", "h264_qsv"] = "auto"


class ClipPresentation(WorkerModel):
    layout: Literal["portrait", "landscape", "smart_portrait", "gaming_portrait"] = "smart_portrait"
    captionPreset: CaptionPresetName = "bold_focus"


class CreateProjectRequest(WorkerModel):
    source: str = Field(min_length=1)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)


class TranscriptWord(WorkerModel):
    text: str = Field(min_length=1)
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_timing(self) -> "TranscriptWord":
        if self.endSeconds < self.startSeconds:
            raise ValueError("word endSeconds must be greater than or equal to startSeconds")
        return self


class TranscriptSegment(WorkerModel):
    text: str = ""
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(ge=0)
    words: list[TranscriptWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timing(self) -> "TranscriptSegment":
        if self.endSeconds < self.startSeconds:
            raise ValueError("segment endSeconds must be greater than or equal to startSeconds")
        return self


class Transcript(WorkerModel):
    text: str = ""
    language: str = "unknown"
    durationSeconds: float = Field(0, ge=0)
    words: list[TranscriptWord] = Field(default_factory=list)
    segments: list[TranscriptSegment] = Field(default_factory=list)


class Candidate(WorkerModel):
    id: str = Field(min_length=1)
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    title: str = Field(default="", max_length=160)
    hook: str = Field(default="", max_length=500)
    reason: str = Field(default="", max_length=1000)
    score: int = Field(default=0, ge=0, le=100)
    accent: str = Field(default="coral", max_length=32)
    source: Literal["ai", "manual"] = "ai"
    presentation: ClipPresentation | None = None
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_duration(self) -> "Candidate":
        duration = self.endSeconds - self.startSeconds
        if duration < 15 or duration > 90:
            raise ValueError("candidate duration must be between 15 and 90 seconds")
        return self


class ErrorInfo(WorkerModel):
    code: str
    message: str
    retryable: bool = False
    details: Any | None = None


class RenderOutput(WorkerModel):
    id: str
    clipId: str
    fileName: str
    path: str
    mediaUrl: str
    durationSeconds: float = Field(ge=0)
    status: Literal["succeeded", "failed"]
    error: ErrorInfo | None = None
    clipRevision: int = Field(default=0, ge=0)


class Project(WorkerModel):
    id: str
    sourceLabel: str
    sourceKind: Literal["youtube", "file"]
    sourceUrl: str | None = None
    sourcePath: str | None = None
    durationSeconds: float = Field(0, ge=0)
    resolution: str = "Unknown"
    width: int = Field(0, ge=0)
    height: int = Field(0, ge=0)
    transcriptReady: bool = False
    transcriptText: str = ""
    transcript: Transcript | None = None
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    candidates: list[Candidate] = Field(default_factory=list)
    outputs: list[RenderOutput] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    status: str = "created"
    createdAt: str = Field(default_factory=utc_now)
    updatedAt: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def migrate_clip_presentation(self) -> "Project":
        fallback = ClipPresentation(layout=self.settings.layout, captionPreset=self.settings.captionPreset)
        for candidate in self.candidates:
            if candidate.presentation is None:
                candidate.presentation = fallback.model_copy()
        return self


class ProjectSummary(WorkerModel):
    """Small project-library record that deliberately omits transcript data."""

    id: str
    sourceLabel: str
    sourceKind: Literal["youtube", "file"]
    durationSeconds: float = Field(0, ge=0)
    resolution: str = "Unknown"
    transcriptReady: bool = False
    status: str = "created"
    createdAt: str
    updatedAt: str
    candidateCount: int = Field(0, ge=0)
    outputCount: int = Field(0, ge=0)
    failedOutputCount: int = Field(0, ge=0)


class ProjectPatchRequest(WorkerModel):
    baseRevision: int = Field(ge=0)
    settings: ProjectSettings | None = None
    candidates: list[Candidate] | None = Field(default=None, max_length=10)


class RenderRequest(WorkerModel):
    settings: ProjectSettings
    clips: list[Candidate] = Field(min_length=1, max_length=10)


class LayoutPreviewRequest(WorkerModel):
    clipId: str = Field(min_length=1)
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)
    layout: Literal["smart_portrait", "gaming_portrait"]

    @model_validator(mode="after")
    def validate_duration(self) -> "LayoutPreviewRequest":
        duration = self.endSeconds - self.startSeconds
        if duration < 15 or duration > 90:
            raise ValueError("preview duration must be between 15 and 90 seconds")
        return self


class NormalizedRect(WorkerModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class LayoutPreviewLayer(WorkerModel):
    source: NormalizedRect
    destination: NormalizedRect


class LayoutPreviewKeyframe(WorkerModel):
    atSeconds: float = Field(ge=0)
    layers: list[LayoutPreviewLayer] = Field(min_length=1, max_length=3)


class LayoutPreviewPlan(WorkerModel):
    layout: Literal["smart_portrait", "gaming_portrait"]
    mode: Literal["single", "dual", "gaming_single", "gaming_dual"]
    canvasWidth: int = 1080
    canvasHeight: int = 1920
    sourceWidth: int = Field(gt=0)
    sourceHeight: int = Field(gt=0)
    keyframes: list[LayoutPreviewKeyframe] = Field(min_length=1)
    cacheKey: str = Field(min_length=1)


class SourceCacheEntry(WorkerModel):
    projectId: str
    sourceLabel: str
    sizeBytes: int = Field(ge=0)
    activeJob: bool = False


class YoutubeCacheInventory(WorkerModel):
    totalBytes: int = Field(ge=0)
    entries: list[SourceCacheEntry] = Field(default_factory=list)


class YoutubeCacheCleanupRequest(WorkerModel):
    projectIds: list[str] = Field(min_length=1, max_length=100)


class YoutubeCacheCleanupFailure(WorkerModel):
    projectId: str
    code: str
    message: str


class YoutubeCacheCleanupResult(WorkerModel):
    bytesFreed: int = Field(ge=0)
    cleanedProjectIds: list[str] = Field(default_factory=list)
    skippedActiveProjectIds: list[str] = Field(default_factory=list)
    failures: list[YoutubeCacheCleanupFailure] = Field(default_factory=list)


class Job(WorkerModel):
    id: str
    projectId: str
    type: Literal["prepare", "analyze", "render"]
    status: Literal[
        "queued",
        "running",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "interrupted",
    ] = "queued"
    stage: str = "queued"
    stageKey: str = "job.queued"
    stageParams: dict[str, str | int | float] = Field(default_factory=dict)
    progress: int = Field(0, ge=0, le=100)
    error: ErrorInfo | None = None
    result: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    createdAt: str = Field(default_factory=utc_now)
    updatedAt: str = Field(default_factory=utc_now)
    startedAt: str | None = None
    completedAt: str | None = None
