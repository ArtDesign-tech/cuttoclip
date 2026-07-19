import React from "react";
import { AlertCircle, CheckCircle2, FileVideo, Layers3, Link2, Sparkles, Upload, WandSparkles, WifiOff } from "lucide-react";
import type { ProjectSettings } from "../types";
import type { ConnectionState, T } from "../app/types";
import { SignalMark } from "./common";
import { isSupportedVideoFile, isValidYouTubeUrl } from "../hooks/useSourceForm";
import { isDemoMode } from "../lib/api";

type ReadinessTone = "ready" | "blocked" | "neutral";
type Readiness = { tone: ReadinessTone; messageKey: string; detail?: string };

/**
 * Readiness reflects the form, not just the worker. The worker/gateway status
 * lives in the shell; here we tell the user exactly what stands between them and
 * the CTA so "ready" is never shown while the source is still empty or invalid.
 */
function resolveReadiness(mode: "youtube" | "file", source: string, localFile: File | null, capabilityReady: boolean, connection: ConnectionState): Readiness {
  if (!capabilityReady) {
    return { tone: "blocked", messageKey: "source.systemUnavailable", detail: connection.error || undefined };
  }
  const hasSource = mode === "youtube" ? Boolean(source.trim()) : Boolean(localFile);
  if (!hasSource) return { tone: "neutral", messageKey: "source.needsSource" };
  const valid = mode === "youtube" ? isValidYouTubeUrl(source) : isSupportedVideoFile(localFile);
  if (!valid) return { tone: "blocked", messageKey: mode === "youtube" ? "source.invalidUrl" : "source.invalidFile" };
  return { tone: "ready", messageKey: "source.readyForm" };
}

export function SourceScreen({ t, mode, setMode, source, setSource, localFile, sourceError, settings, setSettings, fileInputRef, chooseFile, onFile, onPaste, ready, capabilityReady, connection, onStart }: {
  t: T; mode: "youtube" | "file"; setMode: (mode: "youtube" | "file") => void; source: string; setSource: (value: string) => void;
  localFile: File | null; sourceError: string; settings: ProjectSettings; setSettings: React.Dispatch<React.SetStateAction<ProjectSettings>>;
  fileInputRef: React.RefObject<HTMLInputElement | null>; chooseFile: () => void; onFile: (file: File) => void; onPaste: () => void; ready: boolean; capabilityReady: boolean; connection: ConnectionState; onStart: () => void;
}) {
  const update = (patch: Partial<ProjectSettings>) => setSettings((current) => ({ ...current, ...patch }));
  const readiness = resolveReadiness(mode, source, localFile, capabilityReady, connection);
  const ReadyIcon = readiness.tone === "ready" ? CheckCircle2 : readiness.tone === "blocked" ? (capabilityReady ? AlertCircle : WifiOff) : Sparkles;
  return <section className="source-screen content-width">
    <div className="source-hero">
      <div><span className="eyebrow"><span />{t("source.eyebrow")}</span><h1>{t("source.title")}</h1><p>{t("source.copy")}</p></div>
      <SignalMark />
    </div>
    <div className="source-workspace">
      <section className="panel source-panel">
        <div className="segmented"><button className={mode === "youtube" ? "active" : ""} onClick={() => setMode("youtube")}><Link2 size={16} />{t("source.youtube")}</button><button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}><FileVideo size={16} />{t("source.file")}</button></div>
        {mode === "youtube" ? <label className="field-label"><span>{t("source.urlLabel")}</span><div className="url-input"><Link2 size={17} /><input value={source} onChange={(event) => setSource(event.target.value)} placeholder={t("source.urlPlaceholder")} aria-invalid={Boolean(sourceError)} /><button onClick={onPaste}>{t("source.paste")}</button></div></label> : <button className={`file-drop ${localFile ? "has-file" : ""}`} onClick={chooseFile}><span className="file-icon">{localFile ? <CheckCircle2 size={24} /> : <Upload size={24} />}</span><span><b>{localFile?.name ?? t("source.choose")}</b><small>{localFile ? `${(localFile.size / 1024 / 1024).toFixed(1)} MB · ${t("source.change")}` : t("source.dropHint")}</small></span></button>}
        <input ref={fileInputRef} className="visually-hidden" type="file" accept="video/mp4,video/quicktime,.mp4,.mov" aria-label={t("source.choose")} onChange={(event) => { const file = event.target.files?.[0]; if (file) onFile(file); }} />
        {sourceError && <div className="inline-error"><AlertCircle size={15} />{sourceError}</div>}
        <div className="privacy-note"><Layers3 size={15} /><span>{t(isDemoMode ? "source.demoPrivate" : "source.private")}</span></div>
      </section>
      <section className="panel brief-panel">
        <div className="panel-heading"><span>{t("source.brief")}</span><Sparkles size={16} /></div>
        <SettingRow label={t("source.count")}><div className="stepper"><button onClick={() => update({ clipCount: Math.max(1, settings.clipCount - 1) })} aria-label="−">−</button><b>{settings.clipCount}</b><button onClick={() => update({ clipCount: Math.min(10, settings.clipCount + 1) })} aria-label="+">+</button></div></SettingRow>
        <SettingRow label={t("source.duration")}><select aria-label={t("source.duration")} value={settings.duration.maxSeconds} onChange={(event) => update({ duration: { ...settings.duration, maxSeconds: Number(event.target.value) } })}><option value={30}>15–30 sec</option><option value={60}>15–60 sec</option><option value={90}>15–90 sec</option></select></SettingRow>
        <SettingRow label={t("source.language")}><select aria-label={t("source.language")} value={settings.language} onChange={(event) => update({ language: event.target.value })}><option value="auto">{t("source.auto")}</option><option value="id">Bahasa Indonesia</option><option value="en">English</option><option value="ms">Bahasa Melayu</option></select></SettingRow>
      </section>
    </div>
    <div className={`source-launch ${readiness.tone === "ready" ? "is-ready" : ""}`}>
      <div className={`form-readiness readiness-${readiness.tone}`} role="status"><ReadyIcon size={18} /><span><b>{t(readiness.messageKey)}</b>{readiness.detail && <small>{readiness.detail}</small>}</span></div>
      <button className="primary-button" disabled={!ready} onClick={onStart}>{t("source.start")}<WandSparkles size={18} /></button>
    </div>
  </section>;
}

function SettingRow({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="setting-row"><label>{label}</label>{children}</div>;
}
