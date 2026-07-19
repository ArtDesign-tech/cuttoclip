import React from "react";
import { Check, Clapperboard, FileVideo, FolderOpen, Menu, PanelLeftClose, PanelLeftOpen, PanelRight, PanelRightClose, Plus, Settings2, Sparkles, WifiOff, X } from "lucide-react";
import type { Project, ProjectSummary } from "../types";
import type { AppView, ConnectionState, LibraryState, ProjectTab, SaveStatus, T, WorkflowState } from "../app/types";
import { formatTime, relativeTime } from "../app/time";
import { isDemoMode } from "../lib/api";

export function AppHeader({ t, view, project, projectTab, saveStatus, detailsOpen, sidebarCollapsed, onToggleDetails, onToggleSidebar, onMenu, menuButtonRef, reopenButtonRef }: { t: T; view: AppView; project: Project | null; projectTab: ProjectTab; saveStatus: SaveStatus; detailsOpen: boolean; sidebarCollapsed: boolean; onToggleDetails: () => void; onToggleSidebar: () => void; onMenu: () => void; menuButtonRef: React.RefObject<HTMLButtonElement | null>; reopenButtonRef: React.RefObject<HTMLButtonElement | null> }) {
  const inProject = view === "project" && Boolean(project);
  const title = inProject && project ? `${project.sourceLabel} / ${t(`project.${projectTab}`)}` : view === "settings" ? t("settings.title") : view === "projects" ? t("sidebar.projects") : t("sidebar.create");
  return <header className={`app-header ${sidebarCollapsed ? "sidebar-reopen-visible" : ""}`}>
    <button ref={menuButtonRef} className="mobile-menu" onClick={onMenu} aria-label={t("sidebar.open")}><Menu size={20} /></button>
    {sidebarCollapsed && <button ref={reopenButtonRef} className="sidebar-reopen" onClick={onToggleSidebar} aria-label={t("sidebar.expand")} title={t("sidebar.expand")}><PanelLeftOpen size={18} /></button>}
    <div className="header-breadcrumb"><span>{inProject ? t("sidebar.projects") : "CUTTOCLIP"}</span><b title={title}>{title}</b></div>
    <div className="header-actions">{inProject && <><span className={`save-state save-${saveStatus}`}>{saveStatus === "saved" && <Check size={13} />}{t(`save.${saveStatus}`)}</span><button className={`header-icon ${detailsOpen ? "active" : ""}`} onClick={onToggleDetails} aria-pressed={detailsOpen} aria-label={detailsOpen ? t("details.close") : t("details.open")} title={detailsOpen ? t("details.close") : t("details.open")}>{detailsOpen ? <PanelRightClose size={18} /> : <PanelRight size={18} />}</button></>}</div>
  </header>;
}

export function Sidebar({ t, view, project, summaries, libraryState, mobileOpen, connection, onNew, onProjects, onOpenProject, onSettings, onCollapse, onClose, collapseButtonRef }: { t: T; view: AppView; project: Project | null; summaries: ProjectSummary[]; libraryState: LibraryState; mobileOpen: boolean; connection: ConnectionState; onNew: () => void; onProjects: () => void; onOpenProject: (projectId: string) => void; onSettings: () => void; onCollapse: () => void; onClose: () => void; collapseButtonRef: React.RefObject<HTMLButtonElement | null> }) {
  const connectionKey = connection.status === "demo" ? "status.demo" : connection.status === "online" ? "status.online" : connection.status === "offline" ? "status.offline" : "status.checking";
  const recentProjects = summaries.slice(0, 6);
  const projectList = <div className="sidebar-project-list">{libraryState === "loading" ? <span className="sidebar-list-state">{t("library.loading")}</span> : recentProjects.map((item) => {
    const active = project?.id === item.id;
    return <button key={item.id} className={`sidebar-project-row ${active && view === "project" ? "active" : ""}`} onClick={() => onOpenProject(item.id)} aria-current={active && view === "project" ? "page" : undefined}><span className="sidebar-project-icon"><FileVideo size={14} /><i className={`project-status-dot status-${item.status}`} /></span><span><b title={item.sourceLabel}>{item.sourceLabel}</b><small>{t("sidebar.projectMeta", { age: relativeTime(item.updatedAt), moments: item.candidateCount, renders: item.outputCount })}</small></span></button>;
  })}{libraryState === "idle" && !summaries.length && <div className="sidebar-empty"><Sparkles size={16} /><div><b>{t("sidebar.emptyTitle")}</b><span>{t("sidebar.emptyCopy")}</span></div></div>}{libraryState === "failed" && <div className="sidebar-empty is-error"><WifiOff size={16} /><div><b>{t("library.failed")}</b><span>{t("library.failedCopy")}</span></div></div>}</div>;
  return <aside className={`app-sidebar ${mobileOpen ? "is-open" : ""}`} aria-label={t("sidebar.navigation")}>
    <div className="sidebar-top"><div className="sidebar-brand"><span className="sidebar-brand-mark"><Clapperboard size={17} /></span><span><b>cut<span>to</span>clip</b><small>{t(isDemoMode ? "app.demoStudio" : "app.localStudio")}</small></span></div><button className="sidebar-close" onClick={onClose} aria-label={t("common.close")}><X size={18} /></button><button ref={collapseButtonRef} className="sidebar-collapse sidebar-top-collapse" onClick={onCollapse} aria-label={t("sidebar.collapse")} title={t("sidebar.collapse")}><PanelLeftClose size={17} /></button></div>
    <button className="sidebar-new" onClick={onNew} title={t("app.newProject")} aria-current={view === "create" ? "page" : undefined}><Plus size={17} /><span>{t("app.newProject")}</span><kbd>N</kbd></button>
    <div className="sidebar-workspace"><span className="sidebar-section-label">{t("sidebar.workspace")}</span><nav className="sidebar-primary"><button className={view === "projects" ? "active" : ""} onClick={onProjects} aria-current={view === "projects" ? "page" : undefined}><FolderOpen size={17} /><span>{t("sidebar.allProjects")}</span><small>{summaries.length}</small></button></nav></div>
    <section className="sidebar-recent" aria-labelledby="recent-projects-title"><header><span id="recent-projects-title" className="sidebar-section-label">{t("sidebar.recent")}</span>{summaries.length > 0 && <button onClick={onProjects}>{t("sidebar.viewAll")}</button>}</header>{projectList}</section>
    <div className="sidebar-footer"><div className={`sidebar-worker worker-${connection.status}`} title={connection.error}><i /><span><b>{t(connectionKey)}</b><small>{t("sidebar.systemStatus")}</small></span></div><button className={view === "settings" ? "sidebar-settings active" : "sidebar-settings"} onClick={onSettings}><Settings2 size={17} /><span>{t("sidebar.settings")}</span></button></div>
  </aside>;
}

export function ProjectDetailsPanel({ t, project, workflow, open, onClose, onOpenMoments, onOpenResults }: { t: T; project: Project; workflow: WorkflowState; open: boolean; onClose: () => void; onOpenMoments: () => void; onOpenResults: () => void }) {
  const failed = project.outputs.filter((output) => output.status === "failed").length;
  return <aside className={`project-details ${open ? "is-open" : ""}`} aria-hidden={!open} aria-label={t("details.title")}>
    <header><div><span>{t("details.eyebrow")}</span><h2>{t("details.title")}</h2></div><button onClick={onClose} aria-label={t("details.close")}><X size={18} /></button></header>
    <div className="details-body"><section><span>{t("details.source")}</span><b title={project.sourceLabel}>{project.sourceLabel}</b><small>{formatTime(project.durationSeconds)} · {project.resolution}</small></section><section><span>{t("details.status")}</span><b>{workflow.phase ? t(`job.${workflow.phase}`) : project.status ?? t("project.local")}</b><small>{project.transcriptReady ? t("project.ready") : t("project.waiting")}</small></section><section className="details-metrics"><button onClick={onOpenMoments}><small>{t("project.moments")}</small><b>{project.candidates.length}</b><span>{t("details.open")}</span></button><button onClick={onOpenResults}><small>{t("project.renders")}</small><b>{project.outputs.length}</b><span>{failed ? t("project.failedCount", { count: failed }) : t("details.open")}</span></button></section><section><span>{t("project.defaultPreset")}</span><b>{project.settings.layout.replace("_", " ")}</b><small>{project.settings.captionPreset.replace("_", " ")}</small></section></div>
  </aside>;
}
