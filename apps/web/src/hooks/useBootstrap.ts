import { useCallback, useEffect, useRef, useState } from "react";
import { isDemoMode } from "../lib/api";
import { getBootstrapStatus, installRuntime, isDesktop, startWorker } from "../lib/provider";

/**
 * First-launch runtime bootstrap for the desktop host.
 *
 * The worker is NOT a bundled sidecar — it's a ~250MB runtime downloaded on
 * first launch, then started as a supervised child process. This hook drives
 * that sequence so the app can go from "just installed" to "worker online":
 *
 *   bootstrap_status -> install_runtime (if not installed) -> start_worker
 *
 * Outside the desktop host (browser/dev/demo) it is a no-op that reports "ready"
 * so the normal connection flow takes over.
 */

export type BootstrapPhase =
  | "idle"        // not a desktop host, or nothing to do
  | "checking"    // reading bootstrap_status
  | "installing"  // downloading + extracting the runtime
  | "starting"    // launching the worker
  | "ready"       // worker started (or no bootstrap needed)
  | "error";

export type BootstrapState = {
  phase: BootstrapPhase;
  error?: string;
};

export function useBootstrap(onReady: () => void) {
  const [state, setState] = useState<BootstrapState>({
    phase: isDemoMode || !isDesktop() ? "ready" : "checking",
  });
  const runningRef = useRef(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const run = useCallback(async () => {
    if (isDemoMode || !isDesktop()) {
      setState({ phase: "ready" });
      return;
    }
    if (runningRef.current) return;
    runningRef.current = true;
    try {
      setState({ phase: "checking" });
      const status = await getBootstrapStatus();
      if (!status) {
        // No descriptor / command failed — let the connection flow surface it.
        setState({ phase: "ready" });
        return;
      }
      if (status.state !== "installed") {
        setState({ phase: "installing" });
        await installRuntime();
      }
      if (!status.workerRunning) {
        setState({ phase: "starting" });
        await startWorker();
      }
      setState({ phase: "ready" });
      onReadyRef.current();
    } catch (error) {
      setState({
        phase: "error",
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      runningRef.current = false;
    }
  }, []);

  useEffect(() => {
    void run();
  }, [run]);

  return { bootstrap: state, retryBootstrap: run };
}
