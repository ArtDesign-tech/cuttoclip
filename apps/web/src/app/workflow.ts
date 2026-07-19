import type { Job } from "../types";
import type { Phase, WorkflowAction, WorkflowState } from "./types";

export const initialWorkflow: WorkflowState = { stage: "source", phase: null, project: null, job: null, error: null, progress: 0 };

export function workflowReducer(state: WorkflowState, action: WorkflowAction): WorkflowState {
  switch (action.type) {
    case "stage": return { ...state, stage: action.stage, error: null };
    case "project": return { ...state, project: action.project };
    case "processing": return { ...state, stage: "processing", phase: action.phase, project: action.project === undefined ? state.project : action.project, error: null, progress: action.progress ?? 0 };
    case "job": return { ...state, job: action.job, progress: action.progress };
    case "error": return { ...state, stage: "processing", phase: action.phase, error: action.error, job: null };
    case "reset": return initialWorkflow;
  }
}

export const terminalJob = (job: Job | null) => Boolean(job && ["succeeded", "partial", "failed", "cancelled", "interrupted"].includes(job.status));

export function compositeProgress(phase: Phase, progress: number): number {
  if (phase === "create") return progress * 0.1;
  if (phase === "prepare") return 10 + progress * 0.65;
  if (phase === "analyze") return 75 + progress * 0.25;
  return progress;
}
