import { describe, expect, it } from "vitest";
import { compositeProgress, initialWorkflow, terminalJob, workflowReducer } from "./workflow";
import type { Job, JobError, Project } from "../types";

const stubProject = (id: string): Project => ({
  id, sourceLabel: id, sourceKind: "file", durationSeconds: 100, resolution: "1280 x 720",
  transcriptReady: false, settings: { clipCount: 1, duration: { minSeconds: 15, maxSeconds: 90 }, language: "auto", layout: "smart_portrait", captionPreset: "bold_focus", encoder: "auto" },
  candidates: [], outputs: [], revision: 0,
});

const stubJob = (status: Job["status"]): Job => ({
  id: "job-1", projectId: "p-1", type: "prepare", status, stage: "", stageKey: "job.working", stageParams: {}, progress: 0, createdAt: "now", updatedAt: "now",
});

describe("workflowReducer", () => {
  it("sets stage and clears any error", () => {
    const errored = { ...initialWorkflow, error: { code: "x", message: "m", retryable: true } as JobError };
    expect(workflowReducer(errored, { type: "stage", stage: "review" })).toMatchObject({ stage: "review", error: null });
  });

  it("replaces project reference", () => {
    const next = stubProject("p-1");
    expect(workflowReducer(initialWorkflow, { type: "project", project: next }).project).toBe(next);
  });

  it("keeps current project when processing omits project", () => {
    const base = { ...initialWorkflow, project: stubProject("p-1") };
    const result = workflowReducer(base, { type: "processing", phase: "analyze" });
    expect(result).toMatchObject({ stage: "processing", phase: "analyze", progress: 0, error: null });
    expect(result.project).toBe(base.project);
  });

  it("overrides project when processing passes one explicitly", () => {
    const base = { ...initialWorkflow, project: stubProject("p-1") };
    const swapped = stubProject("p-2");
    expect(workflowReducer(base, { type: "processing", phase: "prepare", project: swapped, progress: 10 }).project).toBe(swapped);
  });

  it("clears project when processing passes null", () => {
    const base = { ...initialWorkflow, project: stubProject("p-1") };
    expect(workflowReducer(base, { type: "processing", phase: "create", project: null }).project).toBeNull();
  });

  it("records job progress", () => {
    const job = stubJob("running");
    expect(workflowReducer(initialWorkflow, { type: "job", job, progress: 42 })).toMatchObject({ job, progress: 42 });
  });

  it("moves to processing and drops the job on error", () => {
    const base = { ...initialWorkflow, job: stubJob("running") };
    const error: JobError = { code: "boom", message: "failed", retryable: true };
    expect(workflowReducer(base, { type: "error", error, phase: "render" })).toMatchObject({ stage: "processing", phase: "render", error, job: null });
  });

  it("resets to the initial workflow", () => {
    const base = { ...initialWorkflow, stage: "results" as const, progress: 100 };
    expect(workflowReducer(base, { type: "reset" })).toEqual(initialWorkflow);
  });
});

describe("terminalJob", () => {
  it("treats null as non-terminal", () => expect(terminalJob(null)).toBe(false));
  it("marks running as non-terminal", () => expect(terminalJob(stubJob("running"))).toBe(false));
  it.each(["succeeded", "partial", "failed", "cancelled", "interrupted"] as const)("marks %s as terminal", (status) => {
    expect(terminalJob(stubJob(status))).toBe(true);
  });
});

describe("compositeProgress", () => {
  it("scales create into the first tenth", () => expect(compositeProgress("create", 100)).toBe(10));
  it("scales prepare into the 10–75 band", () => expect(compositeProgress("prepare", 100)).toBe(75));
  it("scales analyze into the 75–100 band", () => expect(compositeProgress("analyze", 100)).toBe(100));
  it("passes render through unchanged", () => expect(compositeProgress("render", 64)).toBe(64));
});
