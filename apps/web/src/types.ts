export type Layout = "portrait" | "landscape" | "smart_portrait" | "gaming_portrait";
export type CaptionPreset = "none" | "clean" | "bold_focus" | "karaoke" | "subtitle_box";
export type CandidateSource = "ai" | "manual";

export type ClipPresentation = {
  layout: Layout;
  captionPreset: CaptionPreset;
};

export type TranscriptWord = {
  text: string;
  startSeconds: number;
  endSeconds: number;
};

export type TranscriptSegment = {
  text: string;
  startSeconds: number;
  endSeconds: number;
  words: TranscriptWord[];
};

export type Transcript = {
  text: string;
  language: string;
  durationSeconds: number;
  words: TranscriptWord[];
  segments: TranscriptSegment[];
};

export type HighlightCandidate = {
  id: string;
  startSeconds: number;
  endSeconds: number;
  title: string;
  hook: string;
  reason: string;
  score: number;
  accent: string;
  source: CandidateSource;
  presentation: ClipPresentation;
  revision: number;
};

export type ProjectSettings = {
  clipCount: number;
  duration: { minSeconds: number; maxSeconds: number };
  language: "auto" | string;
  layout: Layout;
  captionPreset: CaptionPreset;
  encoder: "auto" | "libx264" | "h264_amf" | "h264_nvenc" | "h264_qsv";
};

export type RenderOutput = {
  id: string;
  clipId: string;
  fileName: string;
  path: string;
  mediaUrl: string;
  durationSeconds: number;
  status: "succeeded" | "failed";
  error?: JobError | string | null;
  clipRevision: number;
};

export type Project = {
  id: string;
  sourceLabel: string;
  sourceKind: "youtube" | "file";
  sourceUrl?: string | null;
  sourcePath?: string | null;
  durationSeconds: number;
  resolution: string;
  width?: number;
  height?: number;
  transcriptReady: boolean;
  transcriptText?: string;
  transcript?: Transcript | null;
  settings: ProjectSettings;
  candidates: HighlightCandidate[];
  outputs: RenderOutput[];
  status?: string;
  createdAt?: string;
  updatedAt?: string;
  revision: number;
};

export type ProjectSummary = {
  id: string;
  sourceLabel: string;
  sourceKind: "youtube" | "file";
  durationSeconds: number;
  resolution: string;
  transcriptReady: boolean;
  status: string;
  createdAt: string;
  updatedAt: string;
  candidateCount: number;
  outputCount: number;
  failedOutputCount: number;
};

export type JobType = "prepare" | "analyze" | "render";
export type JobStatus = "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled" | "interrupted";

export type JobError = {
  code: string;
  message: string;
  retryable: boolean;
  details?: unknown;
};

export type JobResult = {
  project: Project;
  outputs?: RenderOutput[];
};

export type Job = {
  id: string;
  projectId: string;
  type: JobType;
  status: JobStatus;
  stage: string;
  stageKey: string;
  stageParams: Record<string, string | number>;
  progress: number;
  request?: Record<string, unknown> | null;
  error?: JobError | null;
  result?: JobResult | null;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  completedAt?: string | null;
};

export type WorkerHealth = {
  status: string;
  service?: string;
};

export type BootstrapStatus = {
  state: "not-installed" | "installed" | "outdated";
  installedVersion?: string | null;
  requiredVersion?: string | null;
  runtimeDir?: string | null;
  workerRunning: boolean;
  workerBaseUrl?: string | null;
};

export type ProviderMode = "managed" | "byok";

export type ProviderSlot = {
  provider: string;
  model?: string;
  keyPresent?: boolean;
};

export type ProviderReport = {
  mode: ProviderMode;
  transcription: ProviderSlot;
  highlights: ProviderSlot;
  gatewayConfigured?: boolean;
};

export type SystemCapabilities = {
  platform: string;
  ffmpeg: boolean;
  ffprobe: boolean;
  ytDlp: boolean;
  encoders: string[];
  defaultEncoder?: string;
  vision: string | boolean;
  providerMode?: ProviderMode;
  providerConfigured?: boolean;
  provider?: ProviderReport;
  gatewayConfigured: boolean;
  gatewayEdgeAuthConfigured?: boolean;
  dataRoot?: string;
  outputRoot?: string;
  maxSourceDurationSeconds?: number;
  supportedFormats?: string[];
  apiFeatures?: string[];
};

export type InstallationIdentity = {
  installationId: string;
  label: string;
  createdAt: string | null;
  lastUsedAt: string | null;
};

/**
 * Result of a provider action (activate invite, save/clear BYOK keys, switch
 * mode). ``needsDesktop`` is returned when running outside the Tauri host, where
 * secrets cannot be persisted to the Stronghold vault — the UI shows an
 * actionable "open the desktop app" message and never writes a secret anywhere.
 */
export type ProviderActionResult =
  | { ok: true }
  | { ok: false; needsDesktop: true }
  | { ok: false; needsDesktop?: false; code: string; message: string };

export type NormalizedRect = { x: number; y: number; width: number; height: number };
export type LayoutPreviewLayer = { source: NormalizedRect; destination: NormalizedRect };
export type LayoutPreviewKeyframe = { atSeconds: number; layers: LayoutPreviewLayer[] };
export type LayoutPreviewPlan = {
  layout: "smart_portrait" | "gaming_portrait";
  mode: "single" | "dual" | "gaming_single" | "gaming_dual";
  canvasWidth: number;
  canvasHeight: number;
  sourceWidth: number;
  sourceHeight: number;
  keyframes: LayoutPreviewKeyframe[];
  cacheKey: string;
};

export type SourceCacheEntry = { projectId: string; sourceLabel: string; sizeBytes: number; activeJob: boolean };
export type YoutubeCacheInventory = { totalBytes: number; entries: SourceCacheEntry[] };
export type YoutubeCacheCleanupResult = {
  bytesFreed: number;
  cleanedProjectIds: string[];
  skippedActiveProjectIds: string[];
  failures: Array<{ projectId: string; code: string; message: string }>;
};
