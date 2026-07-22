import { useState } from "react";
import { Gauge, Globe2, HardDrive, KeyRound, RefreshCw, Trash2 } from "lucide-react";
import type { Locale } from "../i18n";
import type { ConnectionState, T } from "../app/types";
import type { ProviderReport } from "../types";
import { isDemoMode } from "../lib/api";
import { clearByokCredentials, isDesktop } from "../lib/provider";
import { formatBytes, type UseYoutubeCache } from "../hooks/useYoutubeCache";

function visionLabel(t: T, vision: string | boolean | undefined): string {
  if (vision === "ready") return t("settings.visionReady");
  if (vision === "download-on-first-use") return t("settings.visionDownload");
  if (vision === "unavailable") return t("settings.unavailable");
  return String(vision ?? "—");
}

function ProviderPanel({ t, provider, configured, onManageProvider, onRefresh }: { t: T; provider: ProviderReport; configured: boolean; onManageProvider: () => void; onRefresh: () => void }) {
  const [notice, setNotice] = useState("");
  const isByok = provider.mode === "byok";
  const modeLabel = isByok ? t("settings.providerByok") : t("settings.providerManaged");
  const slot = (label: string, value: string | undefined, keyPresent: boolean | undefined) => (
    <div className="settings-row" key={label}>
      <span>{label}</span>
      <b title={value}>{value || "—"}{keyPresent === undefined ? "" : keyPresent ? ` · ${t("settings.providerKeyPresent")}` : ` · ${t("settings.providerKeyMissing")}`}</b>
    </div>
  );
  const clearKeys = async () => {
    setNotice("");
    const result = await clearByokCredentials();
    if (result.ok) { onRefresh(); return; }
    setNotice(result.needsDesktop ? t("settings.providerNeedsDesktop") : result.message);
  };
  return (
    <section className="panel provider-panel">
      <header><div className="cache-heading-icon"><KeyRound size={20} /></div><div><span>{t("settings.provider")}</span><h2>{modeLabel}</h2></div><strong>{configured ? t("settings.providerConfigured") : t("settings.providerNotConfigured")}</strong></header>
      <div className="settings-row"><span>{t("settings.providerMode")}</span><b>{modeLabel}</b></div>
      {slot(t("settings.providerTranscription"), isByok ? `${provider.transcription.provider} · ${provider.transcription.model ?? "—"}` : provider.transcription.provider, isByok ? provider.transcription.keyPresent : undefined)}
      {slot(t("settings.providerHighlights"), isByok ? `${provider.highlights.provider} · ${provider.highlights.model ?? "—"}` : provider.highlights.provider, isByok ? provider.highlights.keyPresent : undefined)}
      {isDesktop() && <div className="provider-actions">
        <button className="secondary-button" onClick={onManageProvider}>{t("settings.providerManage")}</button>
        {isByok && <button className="secondary-button" onClick={() => void clearKeys()}><Trash2 size={15} />{t("settings.providerClearKeys")}</button>}
      </div>}
      {notice && <div className="inline-error">{notice}</div>}
    </section>
  );
}

export function SettingsScreen({ t, locale, connection, cache, onLocale, onRefresh, onManageProvider }: { t: T; locale: Locale; connection: ConnectionState; cache: UseYoutubeCache; onLocale: () => void; onRefresh: () => void; onManageProvider: () => void }) {
  const cap = connection.capabilities;
  const desktop = isDesktop();
  // On the shared web build the tester should see only Language. The AI provider
  // panel, system-status rows, and absolute paths are operator/desktop concerns,
  // so gate them all behind the desktop host.
  const rows: Array<[string, string]> = [];
  if (desktop) {
    rows.push(
      [t("settings.worker"), connection.status],
      ["FFmpeg", cap?.ffmpeg ? t("project.ready") : t("settings.unavailable")],
      ["FFprobe", cap?.ffprobe ? t("project.ready") : t("settings.unavailable")],
      ["yt-dlp", cap?.ytDlp ? t("project.ready") : t("settings.unavailable")],
      [t("settings.gateway"), cap?.gatewayConfigured ? t("project.ready") : t("settings.unavailable")],
      [t("settings.vision"), visionLabel(t, cap?.vision)],
      [t("settings.encoder"), cap?.encoders.join(", ") || "—"],
      [t("settings.defaultEncoder"), cap?.defaultEncoder ? settingsEncoderLabel(cap.defaultEncoder) : "—"],
      [t("settings.formats"), cap?.supportedFormats?.join(", ") || "—"],
      [t("settings.dataRoot"), cap?.dataRoot ?? "—"],
      [t("settings.outputRoot"), cap?.outputRoot ?? "—"],
    );
  }
  return <section className="settings-screen content-width"><header><span className="eyebrow"><span />{t("sidebar.settings")}</span><h1>{t("settings.title")}</h1><p>{t("settings.copy")}</p></header>
    {desktop && cap?.provider && <ProviderPanel t={t} provider={cap.provider} configured={Boolean(cap.providerConfigured)} onManageProvider={onManageProvider} onRefresh={onRefresh} />}
    <div className="settings-layout"><section className="panel"><div className="settings-row"><span>{t("settings.language")}</span><button className="secondary-button" onClick={onLocale}><Globe2 size={15} />{locale.toUpperCase()}</button></div>{rows.map(([label, value]) => <div key={label} className="settings-row"><span>{label}</span><b title={value}>{value}</b></div>)}</section>{desktop && <aside className="panel settings-note"><Gauge size={22} /><b>{t("settings.renderInfo")}</b><p>{t("settings.renderInfoCopy")}</p>{isDemoMode && <div className="demo-storage-note"><HardDrive size={15} /><span>{t("settings.cacheDemo")}</span></div>}<button className="secondary-button" onClick={onRefresh}><RefreshCw size={15} />{t("settings.recheck")}</button></aside>}</div>
    {desktop && !isDemoMode && <section className="panel cache-manager">
      <header><div className="cache-heading-icon"><HardDrive size={20} /></div><div><span>{t("settings.cacheEyebrow")}</span><h2>{t("settings.cacheTitle")}</h2><p>{t("settings.cacheCopy")}</p></div><strong>{formatBytes(cache.inventory.totalBytes)}</strong></header>
      <>
        <div className="cache-toolbar"><label><input type="checkbox" checked={cache.allSelected} disabled={!cache.selectable.length} onChange={cache.toggleAll} />{t("settings.cacheSelectAll")}</label><button onClick={() => void cache.refresh()} disabled={cache.loading}><RefreshCw size={14} />{t("settings.cacheRefresh")}</button></div>
        <div className="cache-list">
          {cache.loading ? <div className="cache-empty">{t("settings.cacheLoading")}</div> : cache.inventory.entries.length ? cache.inventory.entries.map((entry) => <label key={entry.projectId} className={entry.activeJob ? "cache-entry is-busy" : "cache-entry"}><input type="checkbox" checked={cache.selected.has(entry.projectId)} disabled={entry.activeJob} onChange={() => cache.toggle(entry.projectId)} /><span><b>{entry.sourceLabel}</b><small>{entry.activeJob ? t("settings.cacheActive") : t("settings.cacheReady")}</small></span><strong>{formatBytes(entry.sizeBytes)}</strong></label>) : <div className="cache-empty">{t("settings.cacheEmpty")}</div>}
        </div>
        {cache.error && <div className="inline-error">{cache.error}</div>}
        <footer><span>{t("settings.cacheSelected", { count: cache.selected.size, size: formatBytes(cache.selectedBytes) })}</span><button className="cache-clean" disabled={!cache.selected.size || cache.cleaning} onClick={() => void cache.clean()}><Trash2 size={15} />{cache.cleaning ? t("settings.cacheCleaning") : t("settings.cacheClean")}</button></footer>
      </>
     </section>}
  </section>;
}

function settingsEncoderLabel(enc: string): string {
  const labels: Record<string, string> = { libx264: "CPU (x264)", h264_amf: "AMD AMF", h264_nvenc: "NVIDIA NVENC", h264_qsv: "Intel QSV" };
  return labels[enc] ?? enc;
}
