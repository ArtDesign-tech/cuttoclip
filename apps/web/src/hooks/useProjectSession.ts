import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorFromUnknown, getProject, patchProject } from "../lib/api";
import { DRAFT_PREFIX, LAST_PROJECT_KEY, LAST_STAGE_KEY } from "../app/storage";
import type { ConnectionState, SaveStatus, Stage, T, WorkflowAction } from "../app/types";
import type { HighlightCandidate, Project, ProjectSettings } from "../types";
import type { TranslationKey } from "../i18n";

type ToastState =
  | { kind: "success"; message: TranslationKey }
  | { kind: "undo"; message: TranslationKey; candidates: HighlightCandidate[] }
  | null;

export function useProjectSession({ project, dispatch, connectionStatus, t, setSettings, upsertSummary }: {
  project: Project | null;
  dispatch: React.Dispatch<WorkflowAction>;
  connectionStatus: ConnectionState["status"];
  t: T;
  setSettings: React.Dispatch<React.SetStateAction<ProjectSettings>>;
  upsertSummary: (project: Project) => void;
}) {
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [renderSelection, setRenderSelection] = useState<Set<string>>(new Set());
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved");
  const [saveVersion, setSaveVersion] = useState(0);
  const [toast, setToast] = useState<ToastState>(null);
  const projectRef = useRef<Project | null>(null);
  const saveInFlightRef = useRef(false);
  const saveVersionRef = useRef(0);
  const savedVersionRef = useRef(0);

  const selectedClip = useMemo(
    () => project?.candidates.find((clip) => clip.id === selectedClipId) ?? project?.candidates[0] ?? null,
    [project, selectedClipId],
  );

  const acceptProject = useCallback((next: Project, stage?: Stage) => {
    projectRef.current = next;
    dispatch({ type: "project", project: next });
    setSettings(next.settings);
    if (!selectedClipId || !next.candidates.some((clip) => clip.id === selectedClipId)) setSelectedClipId(next.candidates[0]?.id ?? null);
    setRenderSelection((current) => {
      const valid = new Set([...current].filter((id) => next.candidates.some((clip) => clip.id === id)));
      return valid.size ? valid : new Set(next.candidates.map((clip) => clip.id));
    });
    window.localStorage.setItem(LAST_PROJECT_KEY, next.id);
    upsertSummary(next);
    if (stage) {
      dispatch({ type: "stage", stage });
      window.localStorage.setItem(LAST_STAGE_KEY, stage);
    }
  }, [selectedClipId, dispatch, setSettings, upsertSummary]);

  const markDirty = useCallback((next: Project) => {
    projectRef.current = next;
    dispatch({ type: "project", project: next });
    const version = saveVersionRef.current + 1;
    saveVersionRef.current = version;
    setSaveVersion(version);
    setSaveStatus(connectionStatus === "online" || connectionStatus === "demo" ? "saving" : "pending");
    window.localStorage.setItem(`${DRAFT_PREFIX}${next.id}`, JSON.stringify({ settings: next.settings, candidates: next.candidates }));
  }, [connectionStatus, dispatch]);

  const flushSave = useCallback(async (): Promise<Project> => {
    if (saveInFlightRef.current) return projectRef.current!;
    const snapshot = projectRef.current;
    if (!snapshot || savedVersionRef.current === saveVersionRef.current) return snapshot!;
    if (connectionStatus !== "online" && connectionStatus !== "demo") {
      setSaveStatus("pending");
      throw new Error(t("save.pending"));
    }
    saveInFlightRef.current = true;
    const version = saveVersionRef.current;
    setSaveStatus("saving");
    try {
      const saved = await patchProject(snapshot);
      savedVersionRef.current = version;
      const current = projectRef.current;
      const merged = current === snapshot ? saved : {
        ...current!, revision: saved.revision,
        candidates: current!.candidates.map((clip) => ({ ...clip, revision: saved.candidates.find((item) => item.id === clip.id)?.revision ?? clip.revision })),
      };
      projectRef.current = merged;
      dispatch({ type: "project", project: merged });
      window.localStorage.removeItem(`${DRAFT_PREFIX}${snapshot.id}`);
      setSaveStatus(savedVersionRef.current === saveVersionRef.current ? "saved" : "saving");
      return merged;
    } catch (error) {
      const normalized = errorFromUnknown(error);
      if (normalized.code.toLowerCase() === "project_revision_conflict" && snapshot) {
        const latest = await getProject(snapshot.id);
        if (window.confirm(t("save.conflict"))) {
          const local = projectRef.current ?? snapshot;
          const rebased = {
            ...local,
            revision: latest.revision,
            candidates: local.candidates.map((clip) => ({ ...clip, revision: latest.candidates.find((item) => item.id === clip.id)?.revision ?? 0 })),
          };
          projectRef.current = rebased;
          dispatch({ type: "project", project: rebased });
          const nextVersion = saveVersionRef.current + 1;
          saveVersionRef.current = nextVersion;
          setSaveVersion(nextVersion);
          setSaveStatus("saving");
          return rebased;
        }
        projectRef.current = latest;
        dispatch({ type: "project", project: latest });
        savedVersionRef.current = saveVersionRef.current;
        setSaveStatus("saved");
        return latest;
      }
      setSaveStatus("failed");
      throw error;
    } finally {
      saveInFlightRef.current = false;
    }
  }, [connectionStatus, t, dispatch]);

  useEffect(() => {
    if (!project || savedVersionRef.current === saveVersion) return;
    const timer = window.setTimeout(() => void flushSave().catch(() => undefined), 600);
    return () => window.clearTimeout(timer);
  }, [flushSave, project, saveVersion, connectionStatus]);

  const updateProject = (transform: (current: Project) => Project) => {
    const current = projectRef.current;
    if (!current) return;
    markDirty(transform(current));
  };

  const updateClip = (id: string, patch: Partial<HighlightCandidate>) => updateProject((current) => ({
    ...current,
    candidates: current.candidates.map((clip) => clip.id === id ? { ...clip, ...patch } : clip),
  }));

  const applyPresentationToAll = () => {
    if (!selectedClip) return;
    updateProject((current) => ({
      ...current,
      settings: { ...current.settings, ...selectedClip.presentation },
      candidates: current.candidates.map((clip) => ({ ...clip, presentation: { ...selectedClip.presentation } })),
    }));
    setToast({ kind: "success", message: "toast.appliedAll" });
  };

  const removeClip = (id: string) => {
    if (!project) return;
    setToast({ kind: "undo", message: "toast.removed", candidates: project.candidates });
    updateProject((current) => ({ ...current, candidates: current.candidates.filter((clip) => clip.id !== id) }));
    setRenderSelection((current) => { const next = new Set(current); next.delete(id); return next; });
    if (selectedClipId === id) setSelectedClipId(project.candidates.find((clip) => clip.id !== id)?.id ?? null);
  };

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.kind === "undo" ? 8_000 : 4_000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const undoChange = () => {
    if (toast?.kind !== "undo") return;
    updateProject((current) => ({ ...current, candidates: toast.candidates }));
    setRenderSelection(new Set(toast.candidates.map((clip) => clip.id)));
    setToast(null);
  };

  const resetSession = () => {
    projectRef.current = null;
    setSelectedClipId(null);
    setRenderSelection(new Set());
    savedVersionRef.current = 0;
    saveVersionRef.current = 0;
    setSaveVersion(0);
    setSaveStatus("saved");
    setToast(null);
  };

  return {
    selectedClipId, setSelectedClipId, selectedClip, renderSelection, setRenderSelection,
    saveStatus, setSaveStatus, saveVersion, setSaveVersion, toast, setToast,
    projectRef, saveVersionRef, savedVersionRef,
    acceptProject, markDirty, flushSave, updateProject, updateClip, applyPresentationToAll, removeClip, undoChange, resetSession,
  };
}

export type UseProjectSession = ReturnType<typeof useProjectSession>;
