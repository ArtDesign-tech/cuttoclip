import { useCallback, useEffect, useRef, useState } from "react";
import { errorFromUnknown, getLayoutPreview, isDemoMode, supportsWorkerFeature } from "../lib/api";
import type { HighlightCandidate, LayoutPreviewPlan, Project } from "../types";

export type LayoutPreviewStatus = "instant" | "analyzing" | "accurate" | "failed" | "source_missing" | "restoring";

export function useLayoutPreview(project: Project, clip: HighlightCandidate, onRestoreSource: () => Promise<Project | null>) {
  const [plan, setPlan] = useState<LayoutPreviewPlan | null>(null);
  const [status, setStatus] = useState<LayoutPreviewStatus>("instant");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const requestRef = useRef(0);
  const aiLayout = clip.presentation.layout === "smart_portrait" || clip.presentation.layout === "gaming_portrait" ? clip.presentation.layout : null;
  const isAiLayout = aiLayout !== null;

  useEffect(() => {
    setPlan(null);
    setError("");
    if (!aiLayout || isDemoMode || !supportsWorkerFeature("layout-preview-plan")) {
      setStatus("instant");
      return;
    }
    if (project.sourceKind === "youtube" && !project.sourcePath) {
      setStatus("source_missing");
      return;
    }
    const controller = new AbortController();
    const requestId = ++requestRef.current;
    setStatus("analyzing");
    const timer = window.setTimeout(() => {
      void getLayoutPreview(project.id, clip, aiLayout, controller.signal)
        .then((next) => {
          if (requestId !== requestRef.current) return;
          setPlan(next);
          setStatus("accurate");
        })
        .catch((reason) => {
          if (controller.signal.aborted || requestId !== requestRef.current) return;
          const normalized = errorFromUnknown(reason);
          setError(normalized.message);
          setStatus(normalized.code === "SOURCE_RESTORE_REQUIRED" ? "source_missing" : "failed");
        });
    }, 450);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [aiLayout, attempt, clip.id, clip.startSeconds, clip.endSeconds, project.id, project.sourceKind, project.sourcePath]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const restore = useCallback(async () => {
    setStatus("restoring");
    setError("");
    const restored = await onRestoreSource();
    if (!restored) {
      setStatus("source_missing");
      return;
    }
    setAttempt((value) => value + 1);
  }, [onRestoreSource]);

  return { plan, status, error, retry, restore, isAiLayout };
}
