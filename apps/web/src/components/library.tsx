import { useState } from "react";
import { FolderOpen, RefreshCw, Trash2 } from "lucide-react";
import type { ProjectSummary } from "../types";
import type { ConnectionState, LibraryState as LibraryStatus, T } from "../app/types";
import { isDemoMode, projectFrameUrl, supportsWorkerFeature } from "../lib/api";
import { formatTime } from "../app/time";
import { SignalMark } from "./common";

export function ProjectLibrary({ t, summaries, state, search, setSearch, connection, onRetry, onOpen, onDelete }: { t: T; summaries: ProjectSummary[]; state: LibraryStatus; search: string; setSearch: (value: string) => void; connection: ConnectionState; onRetry: () => void; onOpen: (id: string) => void; onDelete: (project: ProjectSummary) => void }) {
  const visible = summaries.filter((project) => project.sourceLabel.toLowerCase().includes(search.toLowerCase()));
  if (connection.status === "offline") return <section className="library-screen content-width"><LibraryState t={t} title={t("library.offline")} copy={t("library.offlineCopy")} /></section>;
  if (state === "loading") return <section className="library-screen content-width"><LibraryState t={t} title={t("library.loading")} copy={t("library.loadingCopy")} /></section>;
  if (state === "failed") return <section className="library-screen content-width"><LibraryState t={t} title={t("library.failed")} copy={t("library.failedCopy")} action={onRetry} /></section>;
  return <section className="library-screen content-width"><header className="library-heading"><div><span className="eyebrow"><span />{t("library.eyebrow")}</span><h1>{t("library.title")}</h1><p>{t("library.copy")}</p></div><label className="library-search"><span className="visually-hidden">{t("library.search")}</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("library.search")} /></label></header>{visible.length ? <div className="project-card-grid">{visible.map((item) => <article key={item.id} className="project-card"><div className="project-card-signal"><ProjectThumb item={item} /></div><div className="project-card-copy"><span>{item.sourceKind === "youtube" ? "YOUTUBE" : "LOCAL VIDEO"}</span><h2 title={item.sourceLabel}>{item.sourceLabel}</h2><p>{formatTime(item.durationSeconds)} · {item.resolution}</p><div><small>{item.candidateCount} {t("project.moments")} · {item.outputCount} {t("project.renders")}</small><small>{new Date(item.updatedAt).toLocaleDateString()}</small></div><footer><button className="secondary-button" onClick={() => onOpen(item.id)}>{t("library.open")}</button><button className="icon-danger" onClick={() => onDelete(item)} aria-label={t("library.delete", { name: item.sourceLabel })}><Trash2 size={16} /></button></footer></div></article>)}</div> : <LibraryState t={t} title={search ? t("library.noSearch") : t("library.empty")} copy={search ? t("library.noSearchCopy") : t("library.emptyCopy")} />}</section>;
}

function LibraryState({ t, title, copy, action }: { t: T; title: string; copy: string; action?: () => void }) { return <div className="library-state"><FolderOpen size={28} /><h2>{title}</h2><p>{copy}</p>{action && <button className="secondary-button" onClick={action}><RefreshCw size={15} />{t("library.retry")}</button>}</div>; }

function ProjectThumb({ item }: { item: ProjectSummary }) {
  const [failed, setFailed] = useState(false);
  if (isDemoMode || !supportsWorkerFeature("frame-preview") || failed) return <SignalMark />;
  const at = Math.min(item.durationSeconds, Math.max(1, item.durationSeconds * 0.1));
  return <img src={projectFrameUrl(item.id, at, 640)} alt="" loading="lazy" onError={() => setFailed(true)} />;
}
