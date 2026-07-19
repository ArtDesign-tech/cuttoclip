import type { Job, JobError, Project, SystemCapabilities } from "../types";
import type { TranslationKey } from "../i18n";

export type Stage = "source" | "processing" | "review" | "results";
export type Phase = "create" | "prepare" | "analyze" | "render";
export type AppView = "create" | "projects" | "project" | "settings";
export type ProjectTab = "summary" | "moments" | "results";
export type SaveStatus = "saved" | "saving" | "pending" | "failed";

export type WorkflowState = {
  stage: Stage;
  phase: Phase | null;
  project: Project | null;
  job: Job | null;
  error: JobError | null;
  progress: number;
};

export type WorkflowAction =
  | { type: "stage"; stage: Stage }
  | { type: "project"; project: Project | null }
  | { type: "processing"; phase: Phase; project?: Project | null; progress?: number }
  | { type: "job"; job: Job; progress: number }
  | { type: "error"; error: JobError; phase: Phase }
  | { type: "reset" };

export type ConnectionState = { status: "checking" | "online" | "offline" | "demo"; capabilities: SystemCapabilities | null; error?: string };

export type LibraryState = "idle" | "loading" | "failed";

export type T = (key: TranslationKey | string, params?: Record<string, string | number>) => string;
