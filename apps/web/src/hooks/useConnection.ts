import { useCallback, useEffect, useState } from "react";
import { errorFromUnknown, getSystemCapabilities, getWorkerHealth, isDemoMode } from "../lib/api";
import type { ConnectionState } from "../app/types";

export function useConnection() {
  const [connection, setConnection] = useState<ConnectionState>({ status: "checking", capabilities: null });

  const loadConnection = useCallback(async () => {
    setConnection((current) => ({ ...current, status: "checking", error: undefined }));
    try {
      const [health, capabilities] = await Promise.all([getWorkerHealth(), getSystemCapabilities()]);
      setConnection({ status: isDemoMode || health.status === "demo" ? "demo" : "online", capabilities });
    } catch (error) {
      setConnection({ status: "offline", capabilities: null, error: errorFromUnknown(error).message });
    }
  }, []);

  useEffect(() => {
    void loadConnection();
    const timer = window.setInterval(() => void loadConnection(), 30_000);
    return () => window.clearInterval(timer);
  }, [loadConnection]);

  return { connection, loadConnection };
}

export type UseConnection = ReturnType<typeof useConnection>;
