import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { CheckCircle2, X } from "lucide-react";
import { deleteProject, errorFromUnknown, getProject, isDemoMode } from "./lib/api";
import { initialLocale, translate, type Locale, type TranslationKey } from "./i18n";
import type { ProjectSummary } from "./types";
import type { AppView, ProjectTab } from "./app/types";
import { initialWorkflow, workflowReducer } from "./app/workflow";
import { LAST_PROJECT_TAB_PREFIX, LOCALE_KEY, ONBOARDING_SKIPPED_KEY, SIDEBAR_COLLAPSED_KEY } from "./app/storage";
import { useBootstrap } from "./hooks/useBootstrap";
import { useConnection } from "./hooks/useConnection";
import { useProjectLibrary } from "./hooks/useProjectLibrary";
import { useSourceForm } from "./hooks/useSourceForm";
import { useProjectSession } from "./hooks/useProjectSession";
import { useWorkflowRunner } from "./hooks/useWorkflowRunner";
import { useYoutubeCache } from "./hooks/useYoutubeCache";
import { AppHeader, ProjectDetailsPanel, Sidebar } from "./components/shell";
import { SourceScreen } from "./components/source";
import { ProcessingScreen } from "./components/processing";
import { ProjectWorkspace } from "./components/project";
import { ProjectLibrary } from "./components/library";
import { SettingsScreen } from "./components/settings";
import { OnboardingScreen } from "./components/onboarding";

export default function App() {
  const [workflow, dispatch] = useReducer(workflowReducer, initialWorkflow);
  const [view, setView] = useState<AppView>("create");
  const [projectTab, setProjectTab] = useState<ProjectTab>("summary");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true");
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [locale, setLocale] = useState<Locale>(initialLocale);
  const [manualOpen, setManualOpen] = useState(false);
  const [drawer, setDrawer] = useState<"edit" | null>(null);
  const [onboardingSkipped, setOnboardingSkipped] = useState(() => window.localStorage.getItem(ONBOARDING_SKIPPED_KEY) === "true");
  const [forceOnboarding, setForceOnboarding] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarCollapseButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarReopenButtonRef = useRef<HTMLButtonElement>(null);
  const t = useCallback((key: TranslationKey | string, params?: Record<string, string | number>) => translate(locale, key, params), [locale]);

  const { connection, loadConnection } = useConnection();
  const { bootstrap, retryBootstrap } = useBootstrap(loadConnection);
  const library = useProjectLibrary(connection.status);
  const sourceForm = useSourceForm();
  const session = useProjectSession({ project: workflow.project, dispatch, connectionStatus: connection.status, t, setSettings: sourceForm.setSettings, upsertSummary: library.upsertSummary });
  const runner = useWorkflowRunner({
    workflow, dispatch, connectionStatus: connection.status, t, session,
    sourceForm: { sourceMode: sourceForm.sourceMode, source: sourceForm.source, localFile: sourceForm.localFile, settings: sourceForm.settings, resetSource: sourceForm.resetSource },
    setLibraryState: library.setLibraryState, setView, setProjectTab, setMobileSidebarOpen, setDetailsOpen,
  });
  const handleCacheCleaned = useCallback(async (projectIds: string[]) => {
    const activeId = session.projectRef.current?.id;
    if (!activeId || !projectIds.includes(activeId)) return;
    session.acceptProject(await getProject(activeId));
  }, [session.acceptProject, session.projectRef]);
  const youtubeCache = useYoutubeCache(connection.status, t, handleCacheCleaned);

  const project = workflow.project;

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = "CutToClip";
    window.localStorage.setItem(LOCALE_KEY, locale);
  }, [locale]);

  useEffect(() => window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed)), [sidebarCollapsed]);

  useEffect(() => {
    if (!mobileSidebarOpen) return;
    const closeDrawer = () => {
      setMobileSidebarOpen(false);
      window.setTimeout(() => menuButtonRef.current?.focus(), 0);
    };
    const sidebar = document.querySelector<HTMLElement>(".app-sidebar");
    const getFocusable = () => sidebar
      ? Array.from(sidebar.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter((el) => el.offsetWidth > 0 || el.offsetHeight > 0)
      : [];
    // Defer initial focus until the drawer content is actually focusable: the
    // sidebar's visibility transition leaves children briefly `visibility:hidden`,
    // which silently drops focus(). Retry across frames until it lands.
    let rafId = 0;
    let attempts = 0;
    const focusFirst = () => {
      const target = getFocusable()[0];
      if (target) { target.focus(); if (document.activeElement === target) return; }
      if (++attempts < 40) rafId = window.requestAnimationFrame(focusFirst);
    };
    rafId = window.requestAnimationFrame(focusFirst);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeDrawer(); return; }
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
    // Closing on a breakpoint change keeps the drawer from lingering over content
    // when the layout crosses between mobile and desktop.
    const media = window.matchMedia("(max-width: 900px)");
    media.addEventListener("change", closeDrawer);
    return () => {
      window.cancelAnimationFrame(rafId);
      document.removeEventListener("keydown", onKey, true);
      media.removeEventListener("change", closeDrawer);
    };
  }, [mobileSidebarOpen]);

  const selectProjectTab = (tab: ProjectTab) => {
    if (!project) return;
    setProjectTab(tab);
    window.localStorage.setItem(`${LAST_PROJECT_TAB_PREFIX}${project.id}`, tab);
  };

  const openSidebarSession = (projectId: string, tab: ProjectTab) => {
    void runner.openProject(projectId, tab);
    setMobileSidebarOpen(false);
  };

  const removeProject = async (summary: ProjectSummary) => {
    if (!window.confirm(t("library.deleteConfirm", { name: summary.sourceLabel }))) return;
    try {
      await deleteProject(summary.id);
      if (project?.id === summary.id) await runner.newProject();
      library.removeSummary(summary.id);
    } catch (error) {
      window.alert(errorFromUnknown(error).message);
    }
  };

  const navigate = (next: AppView) => {
    if (next === "create") { void runner.newProject(); return; }
    setDetailsOpen(false);
    setView(next);
    setMobileSidebarOpen(false);
  };

  const switchLocale = () => setLocale((current) => current === "en" ? "id" : "en");
  const sourceReady = sourceForm.sourceReady;
  // Prefer the provider-aware flag; fall back to the legacy gatewayConfigured for
  // an older worker that predates provider modes.
  const cap = connection.capabilities;
  const providerConfigured = cap ? (cap.providerConfigured ?? cap.gatewayConfigured) : false;
  const capabilityReady = isDemoMode || (connection.status === "online" && Boolean(cap?.ffmpeg) && providerConfigured && (sourceForm.sourceMode !== "youtube" || Boolean(cap?.ytDlp)));
  // First-launch gate: once the worker is reachable and reports the provider is
  // NOT configured, show onboarding — unless the user chose "set up later".
  // forceOnboarding lets the Settings "Manage provider" action reopen the flow
  // even when a provider is already configured.
  const showOnboarding = !isDemoMode && connection.status === "online" && cap != null && ((!providerConfigured && !onboardingSkipped) || forceOnboarding);
  const completeOnboarding = () => { setForceOnboarding(false); setOnboardingSkipped(false); window.localStorage.removeItem(ONBOARDING_SKIPPED_KEY); void loadConnection(); };
  const skipOnboarding = () => { setForceOnboarding(false); setOnboardingSkipped(true); window.localStorage.setItem(ONBOARDING_SKIPPED_KEY, "true"); };
  const manageProvider = () => { setForceOnboarding(true); };
  const showProjectDetails = view === "project" && Boolean(project);

  // Desktop first launch: the worker runtime (~250MB) is downloaded and started
  // before anything else can connect. Show a dedicated screen while that runs,
  // and stay on it if it fails so the user isn't dropped into a dead app.
  if (bootstrap.phase !== "ready" && bootstrap.phase !== "idle") {
    const isError = bootstrap.phase === "error";
    return (
      <div className="app-shell onboarding-shell">
        <main className="app-main stage-onboarding">
          <div className="bootstrap-screen" role="status" aria-live="polite">
            {!isError && <div className="bootstrap-spinner" aria-hidden="true" />}
            <h1 className="bootstrap-title">{t(`bootstrap.${bootstrap.phase}`)}</h1>
            {bootstrap.phase === "installing" && <p className="bootstrap-copy">{t("bootstrap.installingCopy")}</p>}
            {isError && (
              <>
                <p className="bootstrap-copy bootstrap-error">{bootstrap.error}</p>
                <button className="primary-button" onClick={() => void retryBootstrap()}>{t("bootstrap.retry")}</button>
              </>
            )}
          </div>
        </main>
      </div>
    );
  }

  if (showOnboarding) {
    return (
      <div className="app-shell onboarding-shell">
        <main className="app-main stage-onboarding">
          <OnboardingScreen t={t} onComplete={completeOnboarding} onSkip={skipOnboarding} />
        </main>
      </div>
    );
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-is-collapsed" : ""} ${showProjectDetails && detailsOpen ? "details-are-open" : ""} ${mobileSidebarOpen ? "mobile-drawer-open" : ""}`}>
      {mobileSidebarOpen && <button className="sidebar-backdrop" aria-label={t("common.close")} onClick={() => { setMobileSidebarOpen(false); window.setTimeout(() => menuButtonRef.current?.focus(), 0); }} />}
      <Sidebar t={t} view={view} project={project} summaries={library.projectSummaries} libraryState={library.libraryState} mobileOpen={mobileSidebarOpen} connection={connection} collapseButtonRef={sidebarCollapseButtonRef}
        onNew={() => void runner.newProject()} onProjects={() => navigate("projects")} onOpenProject={(id) => openSidebarSession(id, "summary")}
        onSettings={() => navigate("settings")} onCollapse={() => { setSidebarCollapsed(true); window.setTimeout(() => sidebarReopenButtonRef.current?.focus(), 0); }} onClose={() => { setMobileSidebarOpen(false); window.setTimeout(() => menuButtonRef.current?.focus(), 0); }} />
      <section className="app-surface">
        <AppHeader
          t={t} view={view} project={project} projectTab={projectTab} saveStatus={session.saveStatus} detailsOpen={detailsOpen} sidebarCollapsed={sidebarCollapsed} reopenButtonRef={sidebarReopenButtonRef}
          onToggleDetails={() => setDetailsOpen((current) => !current)} onToggleSidebar={() => { setSidebarCollapsed(false); window.setTimeout(() => sidebarCollapseButtonRef.current?.focus(), 0); }} onMenu={() => setMobileSidebarOpen(true)} menuButtonRef={menuButtonRef}
        />
        <main className={`app-main stage-${workflow.stage}`}>
        {view === "create" && workflow.stage === "source" && (
          <SourceScreen
            t={t} mode={sourceForm.sourceMode} setMode={(mode) => { sourceForm.setSourceMode(mode); sourceForm.setSourceError(""); }} source={sourceForm.source} setSource={sourceForm.setSource}
            localFile={sourceForm.localFile} sourceError={sourceForm.sourceError} settings={sourceForm.settings} setSettings={sourceForm.setSettings} fileInputRef={sourceForm.fileInputRef}
            chooseFile={() => sourceForm.fileInputRef.current?.click()} onFile={(file) => { sourceForm.setLocalFile(file); sourceForm.setSourceError(""); }}
            onPaste={async () => { try { sourceForm.setSource((await navigator.clipboard.readText()).trim()); } catch { sourceForm.setSourceError("Clipboard access is unavailable."); } }}
            ready={sourceReady && capabilityReady} capabilityReady={capabilityReady} connection={connection} onStart={() => void runner.startAnalysis()}
          />
        )}
        {workflow.stage === "processing" && (
          <ProcessingScreen t={t} phase={workflow.phase} job={workflow.job} progress={workflow.progress} uploadProgress={runner.uploadProgress} error={workflow.error} cancelling={runner.cancelling} onCancel={() => void runner.cancelActive()} onRetry={runner.retryLast} onBack={() => dispatch({ type: "stage", stage: project?.candidates.length ? "review" : "source" })} />
        )}
        {view === "project" && workflow.stage !== "processing" && project && (
          <ProjectWorkspace t={t} project={project} tab={projectTab} setTab={selectProjectTab} workflow={workflow}
            renderSelection={session.renderSelection} setRenderSelection={session.setRenderSelection} selectedClip={session.selectedClip} selectedClipId={session.selectedClipId} setSelectedClipId={session.setSelectedClipId}
            updateClip={session.updateClip} removeClip={session.removeClip} applyPresentationToAll={session.applyPresentationToAll} onRescan={() => void runner.rescan()} manualOpen={manualOpen} setManualOpen={setManualOpen}
            addManual={(clip) => { session.updateProject((current) => ({ ...current, candidates: [...current.candidates, clip] })); session.setSelectedClipId(clip.id); session.setRenderSelection((current) => new Set([...current, clip.id])); setManualOpen(false); }}
            onRender={() => void runner.startRender()} onRetry={() => void runner.startRender(true)} onRenderAgain={() => void runner.startRender()} drawer={drawer} setDrawer={setDrawer} saveStatus={session.saveStatus} onRestoreSource={runner.restoreSource} />
        )}
        {view === "projects" && <ProjectLibrary t={t} summaries={library.projectSummaries} state={library.libraryState} search={library.projectSearch} setSearch={library.setProjectSearch} connection={connection} onRetry={() => void library.loadProjectSummaries()} onOpen={(id) => void runner.openProject(id)} onDelete={(summary) => void removeProject(summary)} />}
        {view === "settings" && <SettingsScreen t={t} locale={locale} connection={connection} cache={youtubeCache} onLocale={switchLocale} onRefresh={() => { void loadConnection(); void youtubeCache.refresh(); }} onManageProvider={manageProvider} />}
        </main>
      </section>
      {showProjectDetails && project && <ProjectDetailsPanel t={t} project={project} workflow={workflow} open={detailsOpen} onClose={() => setDetailsOpen(false)} onOpenMoments={() => selectProjectTab("moments")} onOpenResults={() => selectProjectTab("results")} />}
      {session.toast && <div className={`app-toast is-${session.toast.kind}`} role="status"><span className="app-toast-message">{session.toast.kind === "success" && <CheckCircle2 size={17} aria-hidden="true" />}{t(session.toast.message)}</span>{session.toast.kind === "undo" && <button onClick={session.undoChange}>{t("common.undo")}</button>}<button aria-label={t("common.close")} onClick={() => session.setToast(null)}><X size={16} /></button></div>}
    </div>
  );
}
