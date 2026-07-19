import { useCallback, useEffect, useRef, useState } from "react";
import {
  analyzeProject, cancelJob, createProject, errorFromUnknown, getActiveProjectJob, getProject,
  isDemoMode, jobFailure, pollJob, prepareProject, renderProject, restoreProjectSource,
} from "../lib/api";
import { compositeProgress, terminalJob } from "../app/workflow";
import { DRAFT_PREFIX, LAST_PROJECT_KEY, LAST_PROJECT_TAB_PREFIX, LAST_STAGE_KEY } from "../app/storage";
import type { ConnectionState, Phase, ProjectTab, T, WorkflowAction, WorkflowState } from "../app/types";
import type { Job, Project, ProjectSettings } from "../types";
import type { UseProjectSession } from "./useProjectSession";

export function useWorkflowRunner({ workflow, dispatch, connectionStatus, t, session, sourceForm, setLibraryState, setView, setProjectTab, setMobileSidebarOpen, setDetailsOpen }: {
  workflow: WorkflowState;
  dispatch: React.Dispatch<WorkflowAction>;
  connectionStatus: ConnectionState["status"];
  t: T;
  session: UseProjectSession;
  sourceForm: { sourceMode: "youtube" | "file"; source: string; localFile: File | null; settings: ProjectSettings; resetSource: () => void };
  setLibraryState: (state: "idle" | "loading" | "failed") => void;
  setView: (view: "create" | "project") => void;
  setProjectTab: (tab: ProjectTab) => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setDetailsOpen: (open: boolean) => void;
}) {
  const { acceptProject, flushSave, projectRef, saveVersionRef, setSaveVersion, setSaveStatus, setRenderSelection, renderSelection, setToast, resetSession } = session;
  const [uploadProgress, setUploadProgress] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const runRef = useRef(0);
  const uploadAbortRef = useRef<AbortController | null>(null);

  const project = workflow.project;

  const completePolledJob = useCallback(async (initial: Job, phase: Exclude<Phase, "create">, run: number) => {
    const completed = await pollJob(initial, {
      onUpdate: (job) => {
        if (run !== runRef.current) return;
        dispatch({ type: "job", job, progress: compositeProgress(phase, job.progress) });
      },
    });
    if (run !== runRef.current) return null;
    const next = completed.result?.project ?? await getProject(completed.projectId);
    acceptProject(next);
    return { completed, project: next };
  }, [acceptProject, dispatch]);

  const runAnalyze = useCallback(async (base: Project, from: "prepare" | "analyze" = "prepare", existingRun?: number) => {
    const run = existingRun ?? ++runRef.current;
    try {
      let current = base;
      if (from === "prepare") {
        dispatch({ type: "processing", phase: "prepare", project: base, progress: 10 });
        const prepared = await completePolledJob(await prepareProject(base.id), "prepare", run);
        if (!prepared) return;
        const failure = jobFailure(prepared.completed);
        if (failure) { dispatch({ type: "error", error: failure, phase: "prepare" }); return; }
        current = prepared.project;
      }
      dispatch({ type: "processing", phase: "analyze", project: current, progress: 75 });
      const analyzed = await completePolledJob(await analyzeProject(current.id, current.settings), "analyze", run);
      if (!analyzed) return;
      const failure = jobFailure(analyzed.completed);
      if (failure) { dispatch({ type: "error", error: failure, phase: "analyze" }); return; }
      acceptProject(analyzed.project, "review");
      setView("project");
      setProjectTab("moments");
      window.localStorage.setItem(`${LAST_PROJECT_TAB_PREFIX}${analyzed.project.id}`, "moments");
      setRenderSelection(new Set(analyzed.project.candidates.map((clip) => clip.id)));
    } catch (error) {
      if (run !== runRef.current) return;
      dispatch({ type: "error", error: errorFromUnknown(error), phase: from });
    }
  }, [acceptProject, completePolledJob, dispatch, setProjectTab, setRenderSelection, setView]);

  useEffect(() => {
    if (connectionStatus !== "online" && connectionStatus !== "demo") return;
    const projectId = window.localStorage.getItem(LAST_PROJECT_KEY);
    if (!projectId || projectRef.current) return;
    const run = ++runRef.current;
    void (async () => {
      try {
        let restored = await getProject(projectId);
        if (run !== runRef.current) return;
        const draftText = window.localStorage.getItem(`${DRAFT_PREFIX}${restored.id}`);
        if (draftText) {
          try {
            const draft = JSON.parse(draftText) as { settings?: ProjectSettings; candidates?: Project["candidates"] };
            restored = { ...restored, settings: draft.settings ?? restored.settings, candidates: draft.candidates ?? restored.candidates };
            saveVersionRef.current = 1;
            setSaveVersion(1);
            setSaveStatus(connectionStatus === "online" || connectionStatus === "demo" ? "saving" : "pending");
          } catch { window.localStorage.removeItem(`${DRAFT_PREFIX}${restored.id}`); }
        }
        acceptProject(restored);
        setView("project");
        const rememberedTab = window.localStorage.getItem(`${LAST_PROJECT_TAB_PREFIX}${restored.id}`);
        setProjectTab(rememberedTab === "moments" || rememberedTab === "results" ? rememberedTab : "summary");
        const active = await getActiveProjectJob(restored.id);
        if (run !== runRef.current) return;
        if (active) {
          dispatch({ type: "processing", phase: active.type, project: restored, progress: compositeProgress(active.type, active.progress) });
          const resumed = await completePolledJob(active, active.type, run);
          if (!resumed) return;
          const failure = jobFailure(resumed.completed);
          if (failure) { dispatch({ type: "error", error: failure, phase: active.type }); return; }
          if (active.type === "prepare" && active.request?.restoreSource !== true) await runAnalyze(resumed.project, "analyze", run);
          else acceptProject(resumed.project, active.type === "render" ? "results" : "review");
          return;
        }
        const remembered = window.localStorage.getItem(LAST_STAGE_KEY);
        acceptProject(restored, remembered === "results" && restored.outputs.length ? "results" : restored.candidates.length ? "review" : "source");
      } catch {
        window.localStorage.removeItem(LAST_PROJECT_KEY);
      }
    })();
  }, [acceptProject, completePolledJob, connectionStatus, runAnalyze, projectRef, saveVersionRef, setProjectTab, setSaveStatus, setSaveVersion, setView, dispatch]);

  const startAnalysis = async () => {
    const chosenSource = sourceForm.sourceMode === "file" ? sourceForm.localFile?.name ?? "" : sourceForm.source.trim();
    if (!chosenSource) return;
    const run = ++runRef.current;
    dispatch({ type: "processing", phase: "create", project: null, progress: 0 });
    setUploadProgress(0);
    uploadAbortRef.current = new AbortController();
    try {
      const created = await createProject(chosenSource, sourceForm.settings, sourceForm.localFile, {
        signal: uploadAbortRef.current.signal,
        onUploadProgress: (progress) => {
          if (run !== runRef.current) return;
          setUploadProgress(progress);
          dispatch({ type: "processing", phase: "create", progress: compositeProgress("create", progress) });
        },
      });
      if (run !== runRef.current) return;
      acceptProject(created);
      await runAnalyze(created, "prepare", run);
    } catch (error) {
      if (run !== runRef.current) return;
      const normalized = errorFromUnknown(error);
      if (normalized.code === "request_cancelled") dispatch({ type: "stage", stage: "source" });
      else dispatch({ type: "error", error: normalized, phase: "create" });
    } finally {
      uploadAbortRef.current = null;
    }
  };

  const cancelActive = async () => {
    setCancelling(true);
    ++runRef.current;
    try {
      uploadAbortRef.current?.abort();
      if (workflow.job && !terminalJob(workflow.job)) await cancelJob(workflow.job.id);
      dispatch({ type: "stage", stage: project?.candidates.length ? "review" : "source" });
    } finally { setCancelling(false); }
  };

  const restoreSource = useCallback(async (showProcessing = false): Promise<Project | null> => {
    const current = projectRef.current;
    if (!current || current.sourceKind !== "youtube") return current;
    if (!window.confirm(t("confirm.restoreSource"))) return null;
    const run = ++runRef.current;
    try {
      if (showProcessing) dispatch({ type: "processing", phase: "prepare", project: current, progress: 0 });
      const completed = await pollJob(await restoreProjectSource(current.id), {
        onUpdate: (job) => {
          if (!showProcessing || run !== runRef.current) return;
          dispatch({ type: "job", job, progress: compositeProgress("prepare", job.progress) });
        },
      });
      if (run !== runRef.current) return null;
      const failure = jobFailure(completed);
      if (failure) {
        if (showProcessing) dispatch({ type: "error", error: failure, phase: "prepare" });
        else window.alert(failure.message);
        return null;
      }
      const next = completed.result?.project ?? await getProject(current.id);
      acceptProject(next, showProcessing ? "review" : undefined);
      return next;
    } catch (error) {
      const normalized = errorFromUnknown(error);
      if (showProcessing) dispatch({ type: "error", error: normalized, phase: "prepare" });
      else window.alert(normalized.message);
      return null;
    }
  }, [acceptProject, dispatch, projectRef, t]);

  const startRender = async (onlyFailed = false) => {
    if (!project) return;
    try {
      let saved = await flushSave();
      if (!isDemoMode && saved.sourceKind === "youtube" && !saved.sourcePath) {
        const restored = await restoreSource(true);
        if (!restored) return;
        saved = restored;
      }
      const failed = new Set(saved.outputs.filter((output) => output.status === "failed").map((output) => output.clipId));
      const clips = saved.candidates.filter((clip) => onlyFailed ? failed.has(clip.id) : renderSelection.has(clip.id));
      if (!clips.length) return;
      const run = ++runRef.current;
      dispatch({ type: "processing", phase: "render", project: saved, progress: 0 });
      const completed = await completePolledJob(await renderProject(saved, clips), "render", run);
      if (!completed) return;
      acceptProject(completed.project, "results");
      setView("project");
      setProjectTab("results");
      window.localStorage.setItem(`${LAST_PROJECT_TAB_PREFIX}${completed.project.id}`, "results");
      const failure = jobFailure(completed.completed);
      if (failure && !completed.completed.result?.outputs?.length) dispatch({ type: "error", error: failure, phase: "render" });
    } catch (error) {
      dispatch({ type: "error", error: errorFromUnknown(error), phase: "render" });
    }
  };

  const rescan = async () => {
    if (!project || !window.confirm(t("confirm.rescan"))) return;
    const previous = project.candidates;
    try {
      await flushSave();
      await runAnalyze(projectRef.current!, "analyze");
      setToast({ kind: "undo", message: "toast.rescanned", candidates: previous });
    } catch (error) {
      dispatch({ type: "error", error: errorFromUnknown(error), phase: "analyze" });
    }
  };

  const retryLast = () => {
    if (workflow.phase === "create") void startAnalysis();
    else if (project && workflow.phase === "prepare") void runAnalyze(project, "prepare");
    else if (project && workflow.phase === "analyze") void runAnalyze(project, "analyze");
    else if (project && workflow.phase === "render") void startRender();
  };

  const newProject = async () => {
    if (workflow.job && !terminalJob(workflow.job) && !window.confirm(t("confirm.newProject"))) return;
    if (workflow.job && !terminalJob(workflow.job)) await cancelActive();
    ++runRef.current;
    dispatch({ type: "reset" });
    sourceForm.resetSource();
    resetSession();
    window.localStorage.removeItem(LAST_PROJECT_KEY); window.localStorage.removeItem(LAST_STAGE_KEY);
    setView("create");
    setDetailsOpen(false);
    setMobileSidebarOpen(false);
  };

  const openProject = async (projectId: string, requestedTab?: ProjectTab) => {
    try {
      const opened = await getProject(projectId);
      acceptProject(opened, opened.outputs.length ? "results" : opened.candidates.length ? "review" : "source");
      setView("project");
      const remembered = window.localStorage.getItem(`${LAST_PROJECT_TAB_PREFIX}${opened.id}`);
      const tab = requestedTab ?? (remembered === "moments" || remembered === "results" ? remembered : "summary");
      setProjectTab(tab);
      window.localStorage.setItem(`${LAST_PROJECT_TAB_PREFIX}${opened.id}`, tab);
      setMobileSidebarOpen(false);
      const active = await getActiveProjectJob(opened.id);
      if (!active) return;
      const run = ++runRef.current;
      dispatch({ type: "processing", phase: active.type, project: opened, progress: compositeProgress(active.type, active.progress) });
      const resumed = await completePolledJob(active, active.type, run);
      if (!resumed) return;
      const failure = jobFailure(resumed.completed);
      if (failure) { dispatch({ type: "error", error: failure, phase: active.type }); return; }
      if (active.type === "prepare" && active.request?.restoreSource !== true) await runAnalyze(resumed.project, "analyze", run);
      else {
        acceptProject(resumed.project, active.type === "render" ? "results" : "review");
        const resumedTab: ProjectTab = active.type === "render" ? "results" : "moments";
        setProjectTab(resumedTab);
        window.localStorage.setItem(`${LAST_PROJECT_TAB_PREFIX}${resumed.project.id}`, resumedTab);
      }
    } catch {
      setLibraryState("failed");
    }
  };

  return { uploadProgress, cancelling, startAnalysis, cancelActive, startRender, restoreSource: () => restoreSource(false), rescan, retryLast, newProject, openProject };
}

export type UseWorkflowRunner = ReturnType<typeof useWorkflowRunner>;
