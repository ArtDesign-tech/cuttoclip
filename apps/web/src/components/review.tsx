import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, Check, ChevronDown, Download, Layers3, Pause, Play, Plus, RotateCcw, Scissors, Sparkles, Trash2, X } from "lucide-react";
import type { CaptionPreset, HighlightCandidate, Layout, Project } from "../types";
import type { SaveStatus, T } from "../app/types";
import { isDemoMode, projectFrameUrl, projectSourceUrl, supportsWorkerFeature } from "../lib/api";
import { useLayoutPreview } from "../hooks/useLayoutPreview";
import { OutputPreviewCanvas } from "./output-preview";

const captionOptions = (): [string, string][] => {
  const options: [string, string][] = [["bold_focus", "Bold Focus"], ["clean", "Clean"], ["karaoke", "Karaoke"], ["subtitle_box", "Subtitle Box"]];
  if (supportsWorkerFeature("caption-none")) options.unshift(["none", "No caption"]);
  return options;
};

const layoutOptions = (): [Layout, string][] => {
  // Smart/Gaming portrait (AI face tracking) di-hide sementara: terlalu berat di PC lokal.
  const options: [Layout, string][] = [["portrait", "Portrait — center crop · 9:16"], ["landscape", "Landscape — fit · 16:9"]];
  return options;
};
import { clamp, formatTime, parseTimecode } from "../app/time";

export type ReviewProps = {
  t: T; project: Project; selectedClip: HighlightCandidate | null; selectedClipId: string | null; setSelectedClipId: (id: string) => void;
  renderSelection: Set<string>; setRenderSelection: React.Dispatch<React.SetStateAction<Set<string>>>; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void;
  removeClip: (id: string) => void; applyPresentationToAll: () => void; onRescan: () => void; manualOpen: boolean; setManualOpen: (open: boolean) => void;
  addManual: (clip: HighlightCandidate) => void; onRender: () => void; drawer: "edit" | null; setDrawer: (drawer: "edit" | null) => void; saveStatus: SaveStatus;
  onRestoreSource: () => Promise<Project | null>;
};

export function ReviewScreen(props: ReviewProps) {
  const { t, project, selectedClip, selectedClipId, setSelectedClipId, renderSelection, setRenderSelection, updateClip, removeClip, applyPresentationToAll, onRescan, manualOpen, setManualOpen, addManual, onRender, drawer, setDrawer, saveStatus, onRestoreSource } = props;
  const toggle = (id: string) => setRenderSelection((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const allSelected = renderSelection.size === project.candidates.length && project.candidates.length > 0;
  return <section className="review-screen">
    <main className="clip-gallery content-width">
      <header className="gallery-heading">
        <div><span className="eyebrow"><span />{t("review.transcript")}</span><h1>{project.candidates.length} {t("review.moments")}</h1><p>{project.transcript?.words.length.toLocaleString() ?? "0"} words · {renderSelection.size} {t("review.selected")}</p></div>
        <div className="gallery-actions"><button className="secondary-button" onClick={() => setRenderSelection(allSelected ? new Set() : new Set(project.candidates.map((clip) => clip.id)))}>{allSelected ? t("review.clear") : t("review.selectAll")}</button><button className="secondary-button" onClick={onRescan}><RotateCcw size={14} />{t("review.rescan")}</button><button className="secondary-button" disabled={project.candidates.length >= 10} onClick={() => setManualOpen(true)}><Plus size={15} />{t("review.addManual")}</button></div>
      </header>
      <div className="clip-card-grid">{project.candidates.map((clip, index) => <MomentCard key={clip.id} t={t} project={project} clip={clip} index={index} checked={renderSelection.has(clip.id)} onEdit={() => { setSelectedClipId(clip.id); setDrawer("edit"); }} onCheck={() => toggle(clip.id)} />)}</div>
    </main>
    <div className="render-bar"><div><b>{renderSelection.size} {t("review.selected")}</b><span>MP4 · H.264/AAC · local output</span></div><button className="primary-button" disabled={!renderSelection.size} onClick={onRender}>{t("review.render", { count: renderSelection.size })}<Download size={17} /></button></div>
    {drawer === "edit" && selectedClip && <ClipEditDialog t={t} project={project} clip={selectedClip} saveStatus={saveStatus} updateClip={updateClip} onApplyAll={applyPresentationToAll} onRemove={() => { removeClip(selectedClip.id); setDrawer(null); }} onClose={() => setDrawer(null)} onRestoreSource={onRestoreSource} />}
    {manualOpen && <ManualMomentModal t={t} project={project} onAdd={addManual} onClose={() => setManualOpen(false)} />}
  </section>;
}

function MomentCard({ t, project, clip, index, checked, onEdit, onCheck }: { t: T; project: Project; clip: HighlightCandidate; index: number; checked: boolean; onEdit: () => void; onCheck: () => void }) {
  const thumbAt = Math.min(project.durationSeconds, clip.startSeconds + Math.min(1, (clip.endSeconds - clip.startSeconds) / 2));
  const showFrame = !isDemoMode && supportsWorkerFeature("frame-preview");
  // Without real frames, derive a distinct hue per timestamp so cards are
  // scannable at a glance instead of sharing one generic Signal Map.
  const hue = Math.round((clip.startSeconds * 53) % 360);
  const preset = `${clip.presentation.layout.replace("_", " ")} · ${clip.presentation.captionPreset.replace("_", " ")}`;
  return <article className={`moment-card ${checked ? "is-selected" : ""}`}>
    <label className="render-check"><input type="checkbox" checked={checked} onChange={onCheck} aria-label={`${t("review.selected")}: ${clip.title}`} /><span><Check size={12} /></span></label>
    <div className="moment-thumb">
      {showFrame ? <img src={projectFrameUrl(project.id, thumbAt)} alt="" loading="lazy" /> : <div className="card-demo" style={{ "--seed": hue } as React.CSSProperties}><span className="card-demo-time">{formatTime(clip.startSeconds)}</span></div>}
      <span className="moment-duration">{formatTime(clip.endSeconds - clip.startSeconds)}</span>
      <div className="card-signal" aria-hidden="true">{Array.from({ length: 20 }, (_, bar) => <i key={bar} style={{ height: `${20 + ((bar * 31 + index * 17) % 75)}%` }} />)}</div>
    </div>
    <div className="moment-copy">
      <h3>{clip.title}</h3>
      <p>{clip.hook}</p>
      <div className="card-meta">
        <span className="meta-time">{formatTime(clip.startSeconds)} — {formatTime(clip.endSeconds)}</span>
        {clip.source === "ai" ? <span className="meta-score">AI {clip.score}</span> : <span className="meta-score meta-manual">Manual</span>}
        <span className="meta-preset">{preset}</span>
      </div>
      <button className="edit-clip-button" onClick={onEdit}><Scissors size={15} />{t("review.drawerEdit")}</button>
    </div>
  </article>;
}

function ClipEditDialog({ t, project, clip, saveStatus, updateClip, onApplyAll, onRemove, onClose, onRestoreSource }: { t: T; project: Project; clip: HighlightCandidate; saveStatus: SaveStatus; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void; onApplyAll: () => void; onRemove: () => void; onClose: () => void; onRestoreSource: () => Promise<Project | null> }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const saveStatusRef = useRef(saveStatus);
  onCloseRef.current = onClose;
  saveStatusRef.current = saveStatus;

  // Confirm before discarding the editor if the latest save failed.
  const requestClose = useCallback(() => {
    if (saveStatusRef.current === "failed" && !window.confirm(t("review.closeUnsaved"))) return;
    onCloseRef.current();
  }, [t]);
  const requestCloseRef = useRef(requestClose);
  requestCloseRef.current = requestClose;

  // Focus trap + focus restore: keep Tab inside the dialog and return focus to
  // the "Edit clip" button (the element that opened it) once it closes.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const getFocusable = () => {
      const root = dialogRef.current;
      if (!root) return [] as HTMLElement[];
      return Array.from(root.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        .filter((el) => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement);
    };
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); requestCloseRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = getFocusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const within = Boolean(active && items.includes(active));
      if (event.shiftKey) {
        if (!within || active === first) { event.preventDefault(); last.focus(); }
      } else if (!within || active === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previouslyFocused?.focus?.();
    };
  }, []);

  const saveLabel = saveStatus === "saving" ? t("save.saving") : saveStatus === "failed" ? t("save.failed") : saveStatus === "pending" ? t("review.savePending") : t("save.saved");
  return createPortal(<div className="clip-editor-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) requestClose(); }}>
    <section className="clip-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="clip-editor-title" tabIndex={-1} ref={dialogRef}>
      <header>
        <div><span className="panel-kicker">{clip.source === "ai" ? `AI PICK · ${clip.score}` : "MANUAL CUT"}</span><h2 id="clip-editor-title">{clip.title}</h2></div>
        <div className="editor-header-actions">
          <span className={`editor-save save-${saveStatus}`} aria-live="polite">{saveStatus === "saved" && <Check size={13} />}{saveLabel}</span>
          <button className="editor-done" onClick={requestClose}>{t("review.done")}</button>
          <button className="editor-close" onClick={requestClose} aria-label={t("common.close")}><X size={20} /></button>
        </div>
      </header>
      <div className="clip-editor-body"><div className="clip-editor-preview"><MediaEditor t={t} project={project} clip={clip} updateClip={updateClip} onRestoreSource={onRestoreSource} /></div><aside className="clip-editor-settings"><div className="workspace-heading"><div><span>{t("review.inspector")}</span><b>{formatTime(clip.endSeconds - clip.startSeconds)}</b></div></div><ClipInspector t={t} project={project} clip={clip} updateClip={updateClip} onApplyAll={onApplyAll} onRemove={onRemove} /></aside></div>
    </section>
  </div>, document.body);
}

function MediaEditor({ t, project, clip, updateClip, onRestoreSource }: { t: T; project: Project; clip: HighlightCandidate; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void; onRestoreSource: () => Promise<Project | null> }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(clip.startSeconds);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = clip.startSeconds;
    setCurrentTime(clip.startSeconds);
    setPlaying(false);
  }, [clip.id, clip.startSeconds]);
  const onTime = () => {
    const video = videoRef.current; if (!video) return;
    if (video.currentTime >= clip.endSeconds) video.currentTime = clip.startSeconds;
    setCurrentTime(video.currentTime);
  };
  const togglePlay = async () => {
    const video = videoRef.current; if (!video) return;
    if (video.paused) { if (video.currentTime < clip.startSeconds || video.currentTime >= clip.endSeconds) video.currentTime = clip.startSeconds; await video.play(); setPlaying(true); }
    else { video.pause(); setPlaying(false); }
  };
  const activeWords = project.transcript?.words.filter((word) => word.startSeconds >= clip.startSeconds && word.endSeconds <= clip.endSeconds) ?? [];
  const activeIndex = activeWords.findIndex((word) => currentTime >= word.startSeconds && currentTime <= word.endSeconds + .12);
  // Group words into fixed cues (mirrors the worker's caption grouping) instead
  // of a sliding window. Within a cue the words are constant, so only the
  // highlight moves — the line layout stays put instead of reflowing on every
  // word. This also makes the preview match the rendered .ass output.
  const captionWords = useMemo(() => {
    const limit = clip.presentation.layout === "landscape" ? 10 : 6;
    type Word = (typeof activeWords)[number];
    const cues: Word[][] = [];
    let current: Word[] = [];
    for (const word of activeWords) {
      if (current.length && word.startSeconds - current[current.length - 1].endSeconds > 1.2) { cues.push(current); current = []; }
      current.push(word);
      if (current.length >= limit || (current.length >= 3 && /[.!?]["']?$/.test(word.text))) { cues.push(current); current = []; }
    }
    if (current.length) cues.push(current);
    if (activeIndex < 0) return cues[0] ?? [];
    return cues.find((cue) => cue.includes(activeWords[activeIndex])) ?? cues[0] ?? [];
  }, [activeWords, activeIndex, clip.presentation.layout]);
  const preview = useLayoutPreview(project, clip, onRestoreSource);
  const showVideo = !isDemoMode && supportsWorkerFeature("source-stream") && Boolean(project.sourcePath);
  const statusKey = preview.status === "analyzing" ? "review.previewAnalyzing" : preview.status === "accurate" ? "review.previewAccurate" : preview.status === "failed" ? "review.previewFailed" : preview.status === "source_missing" ? "review.previewSourceMissing" : preview.status === "restoring" ? "review.previewRestoring" : "review.previewInstant";
  return <>
    <div className="media-heading"><div><span className="panel-kicker">{t("review.preview")}</span><h2>{clip.title}</h2></div><span>{formatTime(clip.startSeconds)} — {formatTime(clip.endSeconds)}</span></div>
    <div className={`video-stage layout-${clip.presentation.layout} preview-${preview.status}`}>
      <OutputPreviewCanvas clip={clip} sourceUrl={projectSourceUrl(project.id)} showVideo={showVideo} plan={preview.plan} videoRef={videoRef} onTimeUpdate={onTime} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onLoadedMetadata={() => { if (videoRef.current) videoRef.current.currentTime = clip.startSeconds; }} />
      <span className="frame-badge">{clip.presentation.layout.replaceAll("_", " ")} · {clip.presentation.layout === "landscape" ? "16:9" : "9:16"}</span>
      {clip.presentation.captionPreset !== "none" && <div className={`live-caption caption-${clip.presentation.captionPreset}`}>{captionWords.length ? captionWords.map((word, index) => <span key={`${word.startSeconds}-${index}`} className={currentTime >= word.startSeconds && currentTime <= word.endSeconds + .12 ? "active" : ""}>{word.text}</span>) : <span>{clip.hook}</span>}</div>}
      <button className="player-button" onClick={togglePlay} aria-label={playing ? t("review.pause") : t("review.play")}>{playing ? <Pause size={22} fill="currentColor" /> : <Play size={22} fill="currentColor" />}</button>
    </div>
    <div className={`preview-status status-${preview.status}`} role="status"><Sparkles size={14} /><span><b>{t(statusKey)}</b>{preview.error && <small>{preview.error}</small>}</span>{preview.status === "failed" && <button onClick={preview.retry}>{t("common.retry")}</button>}{preview.status === "source_missing" && <button onClick={() => void preview.restore()}>{t("review.restoreSource")}</button>}</div>
    <button className="play-row" onClick={togglePlay}>{playing ? <Pause size={15} /> : <Play size={15} />}{playing ? t("review.pause") : t("review.play")}<span>{formatTime(currentTime)} / {formatTime(clip.endSeconds)}</span></button>
    <SignalTimeline t={t} project={project} clip={clip} currentTime={currentTime} onSeek={(time) => { if (videoRef.current) videoRef.current.currentTime = time; setCurrentTime(time); }} updateClip={updateClip} />
  </>;
}

function SignalTimeline({ t, project, clip, currentTime, onSeek, updateClip }: { t: T; project: Project; clip: HighlightCandidate; currentTime: number; onSeek: (time: number) => void; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void }) {
  const buckets = useMemo(() => {
    const count = 90; const values = Array.from({ length: count }, () => 0);
    for (const word of project.transcript?.words ?? []) values[Math.min(count - 1, Math.floor((word.startSeconds / Math.max(1, project.durationSeconds)) * count))] += Math.max(1, word.text.length / 5);
    const max = Math.max(1, ...values); return values.map((value) => 18 + (value / max) * 82);
  }, [project.durationSeconds, project.transcript?.words]);
  const start = (clip.startSeconds / project.durationSeconds) * 100;
  const end = (clip.endSeconds / project.durationSeconds) * 100;
  const playhead = (currentTime / project.durationSeconds) * 100;
  return <div className="signal-timeline">
    <div className="timeline-heading"><span>{t("review.signal")}</span><b>{formatTime(clip.endSeconds - clip.startSeconds)}</b></div>
    <div className="density-track" onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); onSeek((clamp(event.clientX - rect.left, 0, rect.width) / rect.width) * project.durationSeconds); }}>
      <div className="density-bars">{buckets.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
      <div className="selected-range" style={{ left: `${start}%`, width: `${end - start}%` }} />
      <div className="playhead" style={{ left: `${playhead}%` }} />
    </div>
    <div className="timeline-range-inputs"><input type="range" min={0} max={project.durationSeconds} step={.1} value={clip.startSeconds} onChange={(event) => updateClip(clip.id, { startSeconds: Math.min(Number(event.target.value), clip.endSeconds - project.settings.duration.minSeconds) })} aria-label={t("review.in")} /><input type="range" min={0} max={project.durationSeconds} step={.1} value={clip.endSeconds} onChange={(event) => updateClip(clip.id, { endSeconds: Math.max(Number(event.target.value), clip.startSeconds + project.settings.duration.minSeconds) })} aria-label={t("review.out")} /></div>
    <div className="timeline-scale"><span>00:00</span><span>{formatTime(project.durationSeconds)}</span></div>
  </div>;
}

function ClipInspector({ t, project, clip, updateClip, onApplyAll, onRemove }: { t: T; project: Project; clip: HighlightCandidate; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void; onApplyAll: () => void; onRemove: () => void }) {
  return <div className="clip-inspector">
    <label className="inspector-field"><span>{t("review.title")}</span><input value={clip.title} onChange={(event) => updateClip(clip.id, { title: event.target.value })} /></label>
    <label className="inspector-field"><span>{t("review.hook")}</span><textarea rows={3} value={clip.hook} onChange={(event) => updateClip(clip.id, { hook: event.target.value })} /></label>
    <TrimInputs t={t} project={project} clip={clip} updateClip={updateClip} />
    <InspectorSelect label={t("review.frame")} value={clip.presentation.layout} options={layoutOptions()} onChange={(value) => updateClip(clip.id, { presentation: { ...clip.presentation, layout: value as Layout } })} />
    <InspectorSelect label={t("review.caption")} value={clip.presentation.captionPreset} options={captionOptions()} onChange={(value) => updateClip(clip.id, { presentation: { ...clip.presentation, captionPreset: value as CaptionPreset } })} />
    <button className="apply-all" onClick={onApplyAll}><Layers3 size={15} />{t("review.applyAll")}</button>
    <details className="reason"><summary>{t("review.reason")}</summary><p>{clip.reason}</p></details>
    <button className="danger-button" onClick={onRemove}><Trash2 size={15} />{t("review.remove")}</button>
  </div>;
}

function TrimInputs({ t, project, clip, updateClip }: { t: T; project: Project; clip: HighlightCandidate; updateClip: (id: string, patch: Partial<HighlightCandidate>) => void }) {
  const [start, setStart] = useState(formatTime(clip.startSeconds)); const [end, setEnd] = useState(formatTime(clip.endSeconds)); const [error, setError] = useState("");
  useEffect(() => { setStart(formatTime(clip.startSeconds)); setEnd(formatTime(clip.endSeconds)); setError(""); }, [clip.id, clip.startSeconds, clip.endSeconds]);
  const commit = (kind: "start" | "end") => { const value = parseTimecode(kind === "start" ? start : end); if (value === null) { setError("Use MM:SS or HH:MM:SS."); return; } const nextStart = kind === "start" ? value : clip.startSeconds; const nextEnd = kind === "end" ? value : clip.endSeconds; if (nextStart < 0 || nextEnd > project.durationSeconds || nextEnd - nextStart < project.settings.duration.minSeconds || nextEnd - nextStart > project.settings.duration.maxSeconds) { setError(`${project.settings.duration.minSeconds}–${project.settings.duration.maxSeconds} sec`); return; } setError(""); updateClip(clip.id, kind === "start" ? { startSeconds: value } : { endSeconds: value }); };
  return <div className="trim-inputs"><label><span>{t("review.in")}</span><input value={start} onChange={(event) => setStart(event.target.value)} onBlur={() => commit("start")} /></label><span><Scissors size={13} />{formatTime(clip.endSeconds - clip.startSeconds)}</span><label><span>{t("review.out")}</span><input value={end} onChange={(event) => setEnd(event.target.value)} onBlur={() => commit("end")} /></label>{error && <small>{error}</small>}</div>;
}

function InspectorSelect({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (value: string) => void }) {
  return <label className="inspector-select"><span>{label}</span><div><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><ChevronDown size={15} /></div></label>;
}

function ManualMomentModal({ t, project, onAdd, onClose }: { t: T; project: Project; onAdd: (clip: HighlightCandidate) => void; onClose: () => void }) {
  const [start, setStart] = useState("00:00"); const [end, setEnd] = useState(formatTime(Math.min(project.settings.duration.maxSeconds, project.durationSeconds))); const [title, setTitle] = useState(""); const [hook, setHook] = useState(""); const [error, setError] = useState("");
  const submit = (event: React.FormEvent) => { event.preventDefault(); const startSeconds = parseTimecode(start); const endSeconds = parseTimecode(end); if (startSeconds === null || endSeconds === null || endSeconds > project.durationSeconds || endSeconds - startSeconds < project.settings.duration.minSeconds || endSeconds - startSeconds > project.settings.duration.maxSeconds) { setError(`${project.settings.duration.minSeconds}–${project.settings.duration.maxSeconds} sec`); return; } onAdd({ id: crypto.randomUUID(), startSeconds, endSeconds, title: title.trim() || `Manual cut ${project.candidates.length + 1}`, hook: hook.trim(), reason: "Added manually", score: 0, accent: "coral", source: "manual", revision: 0, presentation: { layout: project.settings.layout, captionPreset: project.settings.captionPreset } }); };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><form className="modal panel" onSubmit={submit}><div className="modal-heading"><div><span className="panel-kicker">MANUAL CUT</span><h2>{t("manual.title")}</h2></div><button type="button" onClick={onClose} aria-label={t("common.close")}><X size={18} /></button></div><div className="modal-time"><label><span>{t("review.in")}</span><input value={start} onChange={(event) => setStart(event.target.value)} /></label><label><span>{t("review.out")}</span><input value={end} onChange={(event) => setEnd(event.target.value)} /></label></div><label className="inspector-field"><span>{t("manual.name")}</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="inspector-field"><span>{t("manual.hook")}</span><textarea rows={3} value={hook} onChange={(event) => setHook(event.target.value)} /></label>{error && <div className="inline-error"><AlertCircle size={14} />{error}</div>}<button className="primary-button" type="submit"><Plus size={16} />{t("manual.add")}</button></form></div>;
}
