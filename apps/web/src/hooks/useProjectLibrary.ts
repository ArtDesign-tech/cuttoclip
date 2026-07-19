import { useCallback, useEffect, useState } from "react";
import { getProjectSummaries } from "../lib/api";
import type { ConnectionState, LibraryState } from "../app/types";
import type { Project, ProjectSummary } from "../types";

export function useProjectLibrary(connectionStatus: ConnectionState["status"]) {
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>([]);
  const [libraryState, setLibraryState] = useState<LibraryState>("idle");
  const [projectSearch, setProjectSearch] = useState("");

  const loadProjectSummaries = useCallback(async () => {
    setLibraryState("loading");
    try {
      setProjectSummaries(await getProjectSummaries());
      setLibraryState("idle");
    } catch {
      setLibraryState("failed");
    }
  }, []);

  useEffect(() => {
    if (connectionStatus === "online" || connectionStatus === "demo") void loadProjectSummaries();
  }, [connectionStatus, loadProjectSummaries]);

  const upsertSummary = useCallback((next: Project) => {
    const summary: ProjectSummary = {
      id: next.id, sourceLabel: next.sourceLabel, sourceKind: next.sourceKind, durationSeconds: next.durationSeconds,
      resolution: next.resolution, transcriptReady: next.transcriptReady, status: next.status ?? "created",
      createdAt: next.createdAt ?? new Date().toISOString(), updatedAt: next.updatedAt ?? new Date().toISOString(),
      candidateCount: next.candidates.length, outputCount: next.outputs.length,
      failedOutputCount: next.outputs.filter((output) => output.status === "failed").length,
    };
    setProjectSummaries((current) => [summary, ...current.filter((item) => item.id !== next.id)].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
  }, []);

  const removeSummary = useCallback((id: string) => {
    setProjectSummaries((current) => current.filter((item) => item.id !== id));
  }, []);

  return { projectSummaries, setProjectSummaries, libraryState, setLibraryState, projectSearch, setProjectSearch, loadProjectSummaries, upsertSummary, removeSummary };
}

export type UseProjectLibrary = ReturnType<typeof useProjectLibrary>;
