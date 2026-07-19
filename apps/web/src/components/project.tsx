import { ArrowLeft } from "lucide-react";
import type { HighlightCandidate, Project } from "../types";
import type { ProjectTab, SaveStatus, T, WorkflowState } from "../app/types";
import { formatTime } from "../app/time";
import { ReviewScreen } from "./review";
import { ResultsScreen } from "./results";

export function ProjectWorkspace({ t, project, tab, setTab, workflow, renderSelection, setRenderSelection, selectedClip, selectedClipId, setSelectedClipId, updateClip, removeClip, applyPresentationToAll, onRescan, manualOpen, setManualOpen, addManual, onRender, onRetry, onRenderAgain, drawer, setDrawer, saveStatus, onRestoreSource }: {
  t: T; project: Project; tab: ProjectTab; setTab: (tab: ProjectTab) => void; workflow: WorkflowState;
  renderSelection: Set<string>; setRenderSelection: React.Dispatch<React.SetStateAction<Set<string>>>; selectedClip: HighlightCandidate | null; selectedClipId: string | null; setSelectedClipId: (id: string) => void;
  updateClip: (id: string, patch: Partial<HighlightCandidate>) => void; removeClip: (id: string) => void; applyPresentationToAll: () => void; onRescan: () => void; manualOpen: boolean; setManualOpen: (open: boolean) => void;
  addManual: (clip: HighlightCandidate) => void; onRender: () => void; onRetry: () => void; onRenderAgain: () => void; drawer: "edit" | null; setDrawer: (drawer: "edit" | null) => void; saveStatus: SaveStatus;
  onRestoreSource: () => Promise<Project | null>;
}) {
  return <section className="project-workspace">
    <div className="project-tabs content-width" role="tablist" aria-label={t("project.tabs")}>
      {(["summary", "moments", "results"] as ProjectTab[]).map((item) => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>{t(`project.${item}`)}</button>)}
    </div>
    {tab === "summary" && <ProjectSummaryScreen t={t} project={project} workflow={workflow} onMoments={() => setTab("moments")} onResults={() => setTab("results")} />}
    {tab === "moments" && <ReviewScreen t={t} project={project} selectedClip={selectedClip} selectedClipId={selectedClipId} setSelectedClipId={setSelectedClipId} renderSelection={renderSelection} setRenderSelection={setRenderSelection} updateClip={updateClip} removeClip={removeClip} applyPresentationToAll={applyPresentationToAll} onRescan={onRescan} manualOpen={manualOpen} setManualOpen={setManualOpen} addManual={addManual} onRender={onRender} drawer={drawer} setDrawer={setDrawer} saveStatus={saveStatus} onRestoreSource={onRestoreSource} />}
    {tab === "results" && <ResultsScreen t={t} project={project} renderSelection={renderSelection} onEdit={() => setTab("moments")} onRetry={onRetry} onRenderAgain={onRenderAgain} />}
  </section>;
}

function ProjectSummaryScreen({ t, project, workflow, onMoments, onResults }: { t: T; project: Project; workflow: WorkflowState; onMoments: () => void; onResults: () => void }) {
  const cta = project.outputs.length ? { label: t("project.viewResults"), run: onResults } : project.candidates.length ? { label: t("project.reviewMoments"), run: onMoments } : { label: t("project.startAnalysis"), run: onMoments };
  return <section className="project-summary content-width"><header><span className="eyebrow"><span />{t("project.overview")}</span><h1>{project.sourceLabel}</h1><p>{t("project.summaryCopy")}</p></header><div className="summary-grid"><article><small>{t("project.duration")}</small><b>{formatTime(project.durationSeconds)}</b><span>{project.resolution}</span></article><article><small>{t("project.transcript")}</small><b>{project.transcriptReady ? t("project.ready") : t("project.waiting")}</b><span>{workflow.phase ? t(`job.${workflow.phase}`) : t("project.local")}</span></article><article><small>{t("project.moments")}</small><b>{project.candidates.length}</b><span>{t("project.selectedCount", { count: project.candidates.length })}</span></article><article><small>{t("project.renders")}</small><b>{project.outputs.length}</b><span>{project.outputs.filter((output) => output.status === "failed").length ? t("project.failedCount", { count: project.outputs.filter((output) => output.status === "failed").length }) : t("project.local")}</span></article></div><div className="summary-cta"><div><b>{t("project.defaultPreset")}</b><span>{project.settings.layout.replace("_", " ")} · {project.settings.captionPreset.replace("_", " ")}</span></div><button className="primary-button" onClick={cta.run}>{cta.label}<ArrowLeft size={16} /></button></div></section>;
}
