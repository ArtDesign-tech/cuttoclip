import { invoke } from "@tauri-apps/api/core";
import { ArrowLeft, Check, Download, RotateCcw } from "lucide-react";
import type { HighlightCandidate, Project, RenderOutput } from "../types";
import type { T } from "../app/types";
import { isDemoMode, projectFrameUrl, supportsWorkerFeature } from "../lib/api";
import { formatTime } from "../app/time";
import { SignalMark } from "./common";

export function ResultsScreen({ t, project, renderSelection, onEdit, onRetry, onRenderAgain }: { t: T; project: Project; renderSelection: Set<string>; onEdit: () => void; onRetry: () => void; onRenderAgain: () => void }) {
  const outputs = new Map(project.outputs.map((output) => [output.clipId, output]));
  const failed = project.outputs.some((output) => output.status === "failed");
  return <section className="results-screen content-width"><div className="results-heading"><div><span className="eyebrow"><span />LOCAL OUTPUTS</span><h1>{t("results.title")}</h1><p>{t("results.copy")}</p></div><div className="results-actions"><button className="secondary-button" onClick={onEdit}><ArrowLeft size={16} />{t("results.edit")}</button>{failed && <button className="secondary-button" onClick={onRetry}><RotateCcw size={16} />{t("results.retry")}</button>}<button className="primary-button" onClick={onRenderAgain}><Download size={16} />{t("results.renderAgain")}</button></div></div><div className="results-grid">{project.candidates.map((clip, index) => <ResultCard key={clip.id} t={t} project={project} clip={clip} output={outputs.get(clip.id)} index={index} selected={renderSelection.has(clip.id)} />)}</div></section>;
}

function ResultCard({ t, project, clip, output, index, selected }: { t: T; project: Project; clip: HighlightCandidate; output?: RenderOutput; index: number; selected: boolean }) {
  const stale = Boolean(output && output.clipRevision !== clip.revision);
  const state = !output ? "notRendered" : output.status === "failed" ? "failed" : stale ? "stale" : "ready";
  const canReveal = "__TAURI_INTERNALS__" in window && Boolean(output?.path);
  return <article className={`result-card panel result-${state}`}>
    <div className={`result-media layout-${clip.presentation.layout}`}>{output?.status === "succeeded" && output.mediaUrl ? <video controls preload="metadata" src={output.mediaUrl} /> : !isDemoMode && supportsWorkerFeature("frame-preview") ? <img src={projectFrameUrl(project.id, clip.startSeconds + 1, 640)} alt="" /> : <div className="result-demo"><SignalMark /></div>}<span className="result-number">{String(index + 1).padStart(2, "0")}</span><span className={`result-state state-${state}`}>{state === "ready" && <Check size={12} />}{t(`results.${state}`)}</span></div>
    <div className="result-copy"><h3>{clip.title}</h3><p>{formatTime(clip.endSeconds - clip.startSeconds)} · {clip.presentation.layout.replace("_", " ")}</p>{output?.status === "failed" && <div className="result-error">{typeof output.error === "string" ? output.error : output.error?.message}</div>}<div className="result-footer"><div className="result-links">{output?.status === "succeeded" && output.mediaUrl ? <a href={`${output.mediaUrl}?download=true`}><Download size={14} />{t("results.download")}</a> : <span>{selected ? "Selected" : "Not selected"}</span>}{canReveal && <button onClick={() => void invoke("reveal_output", { path: output!.path })}>{t("results.showFolder")}</button>}</div><small>{output?.path ?? "LOCAL"}</small></div></div>
  </article>;
}
