import { useCallback, useEffect, useMemo, useState } from "react";
import type { ConnectionState, T } from "../app/types";
import { cleanupYoutubeCache, errorFromUnknown, getYoutubeCache, isDemoMode } from "../lib/api";
import type { YoutubeCacheInventory } from "../types";

const emptyInventory: YoutubeCacheInventory = { totalBytes: 0, entries: [] };

export function useYoutubeCache(connectionStatus: ConnectionState["status"], t: T, onCleaned: (projectIds: string[]) => Promise<void>) {
  const [inventory, setInventory] = useState<YoutubeCacheInventory>(emptyInventory);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (isDemoMode || connectionStatus !== "online") {
      setInventory(emptyInventory);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const next = await getYoutubeCache();
      setInventory(next);
      setSelected((current) => new Set([...current].filter((id) => next.entries.some((entry) => entry.projectId === id && !entry.activeJob))));
    } catch (reason) {
      setError(errorFromUnknown(reason).message);
    } finally { setLoading(false); }
  }, [connectionStatus]);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectable = useMemo(() => inventory.entries.filter((entry) => !entry.activeJob), [inventory.entries]);
  const selectedBytes = useMemo(() => inventory.entries.filter((entry) => selected.has(entry.projectId)).reduce((total, entry) => total + entry.sizeBytes, 0), [inventory.entries, selected]);
  const allSelected = selectable.length > 0 && selectable.every((entry) => selected.has(entry.projectId));
  const toggle = (projectId: string) => setSelected((current) => { const next = new Set(current); if (next.has(projectId)) next.delete(projectId); else next.add(projectId); return next; });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map((entry) => entry.projectId)));

  const clean = useCallback(async () => {
    const ids = [...selected];
    if (!ids.length || !window.confirm(t("settings.cacheConfirm", { count: ids.length, size: formatBytes(selectedBytes) }))) return;
    setCleaning(true);
    setError("");
    try {
      const result = await cleanupYoutubeCache(ids);
      if (result.failures.length) setError(result.failures.map((failure) => failure.message).join(" "));
      await onCleaned(result.cleanedProjectIds);
      setSelected(new Set());
      await refresh();
    } catch (reason) {
      setError(errorFromUnknown(reason).message);
    } finally { setCleaning(false); }
  }, [onCleaned, refresh, selected, selectedBytes, t]);

  return { inventory, selected, selectable, selectedBytes, allSelected, loading, cleaning, error, refresh, toggle, toggleAll, clean };
}

export type UseYoutubeCache = ReturnType<typeof useYoutubeCache>;

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) { value /= 1024; unit = units[index]; }
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${unit}`;
}

