import type {
  HighlightCandidate,
  Job,
  JobError,
  JobStatus,
  JobType,
  Project,
  ProjectSummary,
  ProjectSettings,
  LayoutPreviewPlan,
  Layout,
  RenderOutput,
  SystemCapabilities,
  WorkerHealth,
  YoutubeCacheCleanupResult,
  YoutubeCacheInventory,
} from "../types";

const workerBase = (import.meta.env.VITE_WORKER_URL ?? "http://127.0.0.1:4317/api").replace(/\/$/, "");
export const isDemoMode = import.meta.env.VITE_DEMO_MODE === "true";

const terminalStatuses = new Set<JobStatus>(["succeeded", "partial", "failed", "cancelled", "interrupted"]);
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
let workerApiFeatures = new Set<string>();

export function supportsWorkerFeature(feature: string): boolean {
  return isDemoMode || workerApiFeatures.has(feature);
}

export class ApiRequestError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly details?: unknown;
  readonly status?: number;

  constructor(error: JobError, status?: number) {
    super(error.message);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.retryable = error.retryable;
    this.details = error.details;
    this.status = status;
  }
}

export function errorFromUnknown(error: unknown): JobError {
  if (error instanceof ApiRequestError) {
    return { code: error.code, message: error.message, retryable: error.retryable, details: error.details };
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return { code: "request_cancelled", message: "The request was cancelled.", retryable: true };
  }
  if (error instanceof Error) {
    return { code: "unexpected_error", message: error.message, retryable: true };
  }
  return { code: "unexpected_error", message: "An unexpected error stopped the workflow.", retryable: true, details: error };
}

function errorFromPayload(payload: unknown, status: number): JobError {
  const record = payload && typeof payload === "object" ? payload as Record<string, unknown> : {};
  const nested = record.error && typeof record.error === "object" ? record.error as Record<string, unknown> : null;
  const detail = record.detail;
  const code = String(nested?.code ?? (typeof record.error === "string" ? record.error : `http_${status}`));
  const message = String(
    nested?.message
      ?? record.message
      ?? (typeof detail === "string" ? detail : "The local worker rejected the request."),
  );
  return {
    code,
    message,
    retryable: typeof nested?.retryable === "boolean" ? nested.retryable : status >= 500 || status === 408 || status === 429,
    details: nested?.details ?? (typeof detail === "string" ? undefined : detail),
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${workerBase}${path}`, init);
  } catch (cause) {
    throw new ApiRequestError({
      code: "worker_unreachable",
      message: `The local worker is not responding at ${workerBase}. Start it, then retry.`,
      retryable: true,
      details: cause instanceof Error ? cause.message : cause,
    });
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try { payload = JSON.parse(text); }
    catch { payload = { message: text }; }
  }
  if (!response.ok) throw new ApiRequestError(errorFromPayload(payload, response.status), response.status);
  return payload as T;
}

function absoluteMediaUrl(mediaUrl: string): string {
  if (!mediaUrl) return "";
  if (/^[a-z][a-z\d+.-]*:/i.test(mediaUrl)) return mediaUrl;
  const workerOrigin = new URL(workerBase, window.location.href).origin;
  if (mediaUrl.startsWith("/")) return new URL(mediaUrl, workerOrigin).toString();
  return `${workerBase}/${mediaUrl.replace(/^\.\//, "")}`;
}

function normalizeOutput(output: RenderOutput, projectId: string): RenderOutput {
  const fallbackUrl = output.status === "succeeded" ? `${workerBase}/projects/${projectId}/outputs/${output.id}` : "";
  return { ...output, clipRevision: output.clipRevision ?? 0, mediaUrl: absoluteMediaUrl(output.mediaUrl || fallbackUrl) };
}

function normalizeProject(project: Project): Project {
  const projectId = project.id;
  return {
    ...project,
    revision: project.revision ?? 0,
    candidates: (project.candidates ?? []).map((candidate) => ({
      ...candidate,
      source: candidate.source ?? "ai",
      revision: candidate.revision ?? 0,
      presentation: candidate.presentation ?? { layout: project.settings.layout, captionPreset: project.settings.captionPreset },
    })),
    outputs: (project.outputs ?? []).map((output) => normalizeOutput(output, projectId)),
  };
}

function normalizeJob(job: Job): Job {
  const normalized = { ...job, stageKey: job.stageKey ?? "job.working", stageParams: job.stageParams ?? {} };
  if (!job.result?.project) return normalized;
  const project = normalizeProject(job.result.project);
  return {
    ...normalized,
    result: {
      ...job.result,
      project,
      outputs: (job.result.outputs ?? project.outputs).map((output) => normalizeOutput(output, job.projectId)),
    },
  };
}

export async function getWorkerHealth(): Promise<WorkerHealth> {
  if (isDemoMode) return { status: "demo", service: "browser-simulation" };
  return request<WorkerHealth>("/health");
}

export async function getSystemCapabilities(): Promise<SystemCapabilities> {
  if (isDemoMode) {
    const capabilities: SystemCapabilities = {
      platform: "browser",
      ffmpeg: false,
      ffprobe: false,
      ytDlp: false,
      encoders: [],
      vision: "simulated",
      providerMode: "managed",
      providerConfigured: true,
      provider: { mode: "managed", transcription: { provider: "gateway" }, highlights: { provider: "gateway" }, gatewayConfigured: true },
      gatewayConfigured: false,
      maxSourceDurationSeconds: 7200,
      supportedFormats: [".mp4", ".mov"],
      apiFeatures: ["project-revision", "source-stream", "frame-preview", "active-job", "per-clip-presentation", "output-revision", "caption-none", "smart-crop-yunet-vad", "gaming-portrait-facecam"],
    };
    workerApiFeatures = new Set(capabilities.apiFeatures);
    return capabilities;
  }
  const capabilities = await request<SystemCapabilities>("/system/capabilities");
  workerApiFeatures = new Set(capabilities.apiFeatures ?? []);
  return capabilities;
}

export async function createProject(
  source: string,
  settings: ProjectSettings,
  localFile?: File | null,
  options: { onUploadProgress?: (progress: number) => void; signal?: AbortSignal } = {},
): Promise<Project> {
  if (isDemoMode) return createDemoProject(source, settings, localFile);

  if (localFile && options.onUploadProgress) {
    return uploadProjectWithProgress(localFile, settings, options);
  }

  let body: BodyInit;
  let path = "/projects";
  const headers: HeadersInit = {};
  if (localFile) {
    const form = new FormData();
    form.append("file", localFile);
    form.append("settings_json", JSON.stringify(settings));
    body = form;
    path += "/upload";
  } else {
    headers["content-type"] = "application/json";
    body = JSON.stringify({ source, settings });
  }
  return normalizeProject(await request<Project>(path, { method: "POST", headers, body }));
}

function uploadProjectWithProgress(
  localFile: File,
  settings: ProjectSettings,
  options: { onUploadProgress?: (progress: number) => void; signal?: AbortSignal },
): Promise<Project> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${workerBase}/projects/upload`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onUploadProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    xhr.onerror = () => reject(new ApiRequestError({ code: "worker_unreachable", message: "The local worker stopped responding during upload.", retryable: true }));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    xhr.onload = () => {
      let payload: unknown = null;
      try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch { payload = { message: xhr.responseText }; }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiRequestError(errorFromPayload(payload, xhr.status), xhr.status));
        return;
      }
      options.onUploadProgress?.(100);
      resolve(normalizeProject(payload as Project));
    };
    const abort = () => xhr.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    const form = new FormData();
    form.append("file", localFile);
    form.append("settings_json", JSON.stringify(settings));
    xhr.send(form);
  });
}

export async function getProject(projectId: string): Promise<Project> {
  if (isDemoMode) {
    const project = demoProjects.get(projectId);
    if (!project) throw new ApiRequestError({ code: "project_not_found", message: "The demo project no longer exists.", retryable: false });
    return project;
  }
  return normalizeProject(await request<Project>(`/projects/${projectId}`));
}

export async function getProjectSummaries(): Promise<ProjectSummary[]> {
  if (isDemoMode) return [...demoProjects.values()]
    .map(toProjectSummary)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  return request<ProjectSummary[]>("/projects/summaries");
}

export async function deleteProject(projectId: string): Promise<void> {
  if (isDemoMode) {
    const active = [...demoJobs.values()].some((job) => job.projectId === projectId && !terminalStatuses.has(job.status));
    if (active) throw new ApiRequestError({ code: "PROJECT_JOB_ACTIVE", message: "Finish or cancel the active job before deleting this project.", retryable: false }, 409);
    demoProjects.delete(projectId);
    for (const [id, job] of demoJobs) if (job.projectId === projectId) demoJobs.delete(id);
    return;
  }
  await request<null>(`/projects/${projectId}`, { method: "DELETE" });
}

export async function patchProject(project: Project): Promise<Project> {
  if (isDemoMode) {
    const next = normalizeProject({ ...project, revision: project.revision + 1, updatedAt: new Date().toISOString() });
    demoProjects.set(next.id, next);
    return next;
  }
  return normalizeProject(await request<Project>(`/projects/${project.id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ baseRevision: project.revision, settings: project.settings, candidates: project.candidates }),
  }));
}

export async function getActiveProjectJob(projectId: string): Promise<Job | null> {
  if (isDemoMode) {
    return [...demoJobs.values()].find((job) => job.projectId === projectId && !terminalStatuses.has(job.status)) ?? null;
  }
  const job = await request<Job | null>(`/projects/${projectId}/active-job`);
  return job ? normalizeJob(job) : null;
}

export function projectSourceUrl(projectId: string): string {
  return `${workerBase}/projects/${projectId}/source`;
}

export function projectFrameUrl(projectId: string, at: number, width = 320): string {
  return `${workerBase}/projects/${projectId}/frame?at=${encodeURIComponent(at.toFixed(1))}&width=${width}`;
}

export async function prepareProject(projectId: string): Promise<Job> {
  return startJob(projectId, "prepare");
}

export async function analyzeProject(projectId: string, settings: ProjectSettings): Promise<Job> {
  return startJob(projectId, "analyze", settings);
}

export async function renderProject(project: Project, clips: HighlightCandidate[] = project.candidates): Promise<Job> {
  const renderClips = supportsWorkerFeature("per-clip-presentation")
    ? clips
    : clips.map(({ presentation: _presentation, revision: _revision, ...legacyClip }) => legacyClip);
  return startJob(project.id, "render", { settings: project.settings, clips: renderClips });
}

export async function getLayoutPreview(
  projectId: string,
  clip: Pick<HighlightCandidate, "id" | "startSeconds" | "endSeconds">,
  layout: Extract<Layout, "smart_portrait" | "gaming_portrait">,
  signal?: AbortSignal,
): Promise<LayoutPreviewPlan> {
  if (isDemoMode) throw new ApiRequestError({ code: "demo_preview", message: "Demo mode uses a simulated frame preview.", retryable: false });
  return request<LayoutPreviewPlan>(`/projects/${projectId}/layout-preview`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clipId: clip.id, startSeconds: clip.startSeconds, endSeconds: clip.endSeconds, layout }),
    signal,
  });
}

export async function getYoutubeCache(): Promise<YoutubeCacheInventory> {
  if (isDemoMode) return { totalBytes: 0, entries: [] };
  return request<YoutubeCacheInventory>("/storage/youtube-cache");
}

export async function cleanupYoutubeCache(projectIds: string[]): Promise<YoutubeCacheCleanupResult> {
  if (isDemoMode) return { bytesFreed: 0, cleanedProjectIds: [], skippedActiveProjectIds: [], failures: [] };
  return request<YoutubeCacheCleanupResult>("/storage/youtube-cache/cleanup", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ projectIds }),
  });
}

export async function restoreProjectSource(projectId: string): Promise<Job> {
  if (isDemoMode) return startDemoJob(projectId, "prepare", { restoreSource: true });
  return request<Job>(`/projects/${projectId}/restore-source`, { method: "POST" });
}

async function startJob(projectId: string, type: JobType, payload?: unknown): Promise<Job> {
  if (isDemoMode) return startDemoJob(projectId, type, payload);
  const init: RequestInit = { method: "POST" };
  if (payload !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(payload);
  }
  return normalizeJob(await request<Job>(`/projects/${projectId}/${type}`, init));
}

export async function getJob(jobId: string, signal?: AbortSignal): Promise<Job> {
  if (isDemoMode) return getDemoJob(jobId);
  return normalizeJob(await request<Job>(`/jobs/${jobId}`, { signal }));
}

export async function cancelJob(jobId: string): Promise<Job> {
  if (isDemoMode) return cancelDemoJob(jobId);
  return normalizeJob(await request<Job>(`/jobs/${jobId}`, { method: "DELETE" }));
}

export async function retryJob(jobId: string): Promise<Job> {
  if (isDemoMode) {
    const previous = demoJobs.get(jobId);
    if (!previous) throw new ApiRequestError({ code: "job_not_found", message: "The demo job no longer exists.", retryable: false });
    return startDemoJob(previous.projectId, previous.type, demoJobPayloads.get(jobId));
  }
  return normalizeJob(await request<Job>(`/jobs/${jobId}/retry`, { method: "POST" }));
}

export async function pollJob(
  initialJob: Job,
  options: { onUpdate?: (job: Job) => void; signal?: AbortSignal; intervalMs?: number } = {},
): Promise<Job> {
  let job = initialJob;
  options.onUpdate?.(job);
  while (!terminalStatuses.has(job.status)) {
    await wait(options.intervalMs ?? 650);
    if (options.signal?.aborted) throw new DOMException("Polling cancelled", "AbortError");
    job = await getJob(job.id, options.signal);
    options.onUpdate?.(job);
  }
  return job;
}

export function jobFailure(job: Job): JobError | null {
  if (job.status === "succeeded" || job.status === "partial") return null;
  if (job.error) return job.error;
  if (job.status === "cancelled") return { code: "job_cancelled", message: "The job was cancelled. No source files were removed.", retryable: true };
  if (job.status === "interrupted") return { code: "job_interrupted", message: "The worker restarted before this job finished.", retryable: true };
  return { code: "job_failed", message: `${job.type} stopped before it completed.`, retryable: true };
}

const demoProjects = new Map<string, Project>();
const demoJobs = new Map<string, Job>();
const demoJobPayloads = new Map<string, unknown>();

const demoCandidates: HighlightCandidate[] = [
  { id: "clip-01", startSeconds: 412, endSeconds: 458, title: "The uncomfortable growth loop", hook: "Most creators stop right before the useful part.", reason: "Clear tension, concrete payoff, and a strong opening sentence.", score: 96, accent: "coral", source: "ai", revision: 0, presentation: { layout: "smart_portrait", captionPreset: "bold_focus" } },
  { id: "clip-02", startSeconds: 781, endSeconds: 836, title: "Ship the tiny version", hook: "Your first version should feel almost embarrassingly small.", reason: "Self-contained idea with a memorable contrast and clean ending.", score: 91, accent: "mint", source: "ai", revision: 0, presentation: { layout: "smart_portrait", captionPreset: "bold_focus" } },
  { id: "clip-03", startSeconds: 1094, endSeconds: 1145, title: "A better creative brief", hook: "The brief is not paperwork; it is your first edit.", reason: "Strong quotable line and an actionable takeaway.", score: 88, accent: "violet", source: "ai", revision: 0, presentation: { layout: "smart_portrait", captionPreset: "bold_focus" } },
];

function toProjectSummary(project: Project): ProjectSummary {
  return {
    id: project.id, sourceLabel: project.sourceLabel, sourceKind: project.sourceKind,
    durationSeconds: project.durationSeconds, resolution: project.resolution,
    transcriptReady: project.transcriptReady, status: project.status ?? "created",
    createdAt: project.createdAt ?? new Date().toISOString(), updatedAt: project.updatedAt ?? new Date().toISOString(),
    candidateCount: project.candidates.length, outputCount: project.outputs.length,
    failedOutputCount: project.outputs.filter((output) => output.status === "failed").length,
  };
}

async function createDemoProject(source: string, settings: ProjectSettings, localFile?: File | null): Promise<Project> {
  await wait(350);
  const id = crypto.randomUUID();
  const isYoutube = !localFile && (source.includes("youtube") || source.includes("youtu.be"));
  const project: Project = {
    id,
    sourceLabel: localFile?.name ?? (isYoutube ? "Creator field guide — YouTube" : source),
    sourceKind: isYoutube ? "youtube" : "file",
    durationSeconds: 1748,
    resolution: "2560 × 1440",
    width: 2560,
    height: 1440,
    transcriptReady: false,
    settings,
    candidates: [],
    outputs: [],
    revision: 0,
    status: "created",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  demoProjects.set(id, project);
  return project;
}

function startDemoJob(projectId: string, type: JobType, payload?: unknown): Job {
  const now = new Date().toISOString();
  const job: Job = {
    id: crypto.randomUUID(), projectId, type, status: "queued", stage: "Waiting for the local media slot", progress: 0,
    stageKey: "job.queued", stageParams: {},
    createdAt: now, updatedAt: now,
  };
  demoJobs.set(job.id, job);
  demoJobPayloads.set(job.id, payload);
  return job;
}

function getDemoJob(jobId: string): Job {
  const current = demoJobs.get(jobId);
  if (!current) throw new ApiRequestError({ code: "job_not_found", message: "The demo job no longer exists.", retryable: false });
  if (terminalStatuses.has(current.status)) return current;
  const elapsed = Date.now() - Date.parse(current.createdAt);
  const duration = current.type === "render" ? 1900 : current.type === "analyze" ? 1500 : 1200;
  const progress = Math.min(100, Math.round((elapsed / duration) * 100));
  const stages: Record<JobType, string> = {
    prepare: progress < 38 ? "Reading source metadata" : progress < 82 ? "Transcribing audio chunks" : "Indexing word timings",
    analyze: progress < 55 ? "Scanning transcript windows" : "Ranking and de-duplicating moments",
    render: progress < 35 ? "Building timed captions" : progress < 82 ? "Rendering clips with FFmpeg" : "Finalizing MP4 outputs",
  };
  if (progress < 100) {
    const running: Job = { ...current, status: "running", stage: stages[current.type], stageKey: `job.${current.type}`, stageParams: {}, progress, startedAt: current.startedAt ?? new Date().toISOString(), updatedAt: new Date().toISOString() };
    demoJobs.set(jobId, running);
    return running;
  }

  const project = demoProjects.get(current.projectId);
  if (!project) throw new ApiRequestError({ code: "project_not_found", message: "The demo project no longer exists.", retryable: false });
  let updated = { ...project, updatedAt: new Date().toISOString() };
  let outputs: RenderOutput[] | undefined;
  if (current.type === "prepare") {
    updated = { ...updated, transcriptReady: true, status: "transcript_ready" };
  } else if (current.type === "analyze") {
    const settings = demoJobPayloads.get(jobId) as ProjectSettings;
    updated = { ...updated, settings, transcriptReady: true, candidates: demoCandidates.slice(0, settings.clipCount), status: "review_ready" };
  } else {
    const renderPayload = demoJobPayloads.get(jobId) as { settings: ProjectSettings; clips: HighlightCandidate[] };
    outputs = renderPayload.clips.map((clip, index) => ({
      id: `demo-output-${index + 1}`, clipId: clip.id, fileName: `clip-${index + 1}.mp4`,
      path: "Simulation only - no video file created", mediaUrl: "",
      durationSeconds: clip.endSeconds - clip.startSeconds, status: "succeeded" as const, clipRevision: clip.revision,
    }));
    updated = { ...updated, settings: renderPayload.settings, candidates: renderPayload.clips, outputs, status: "complete" };
  }
  demoProjects.set(updated.id, updated);
  const done: Job = {
    ...current, status: "succeeded", stage: "Complete", stageKey: "job.complete", stageParams: {}, progress: 100,
    result: { project: updated, ...(outputs ? { outputs } : {}) },
    startedAt: current.startedAt ?? current.createdAt, completedAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
  };
  demoJobs.set(jobId, done);
  return done;
}

function cancelDemoJob(jobId: string): Job {
  const current = demoJobs.get(jobId);
  if (!current) throw new ApiRequestError({ code: "job_not_found", message: "The demo job no longer exists.", retryable: false });
  if (terminalStatuses.has(current.status)) return current;
  const cancelled: Job = { ...current, status: "cancelled", stage: "Cancelled", stageKey: "job.cancelled", stageParams: {}, error: { code: "job_cancelled", message: "The demo job was cancelled.", retryable: true }, completedAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
  demoJobs.set(jobId, cancelled);
  return cancelled;
}
