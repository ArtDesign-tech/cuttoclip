import { useRef, useState } from "react";
import { initialSettings } from "../app/settings";
import type { ProjectSettings } from "../types";

/** True when the string is a public YouTube watch/short/embed/live URL. */
export function isValidYouTubeUrl(raw: string): boolean {
  const value = raw.trim();
  if (!value) return false;
  let url: URL;
  try { url = new URL(value); } catch { return false; }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  const host = url.hostname.replace(/^www\./, "").toLowerCase();
  if (host === "youtu.be") return url.pathname.replace(/\/+$/, "").length > 1;
  if (host === "youtube.com" || host === "m.youtube.com" || host === "music.youtube.com") {
    if (url.pathname === "/watch") return Boolean(url.searchParams.get("v"));
    return /^\/(shorts|embed|live|v)\/[\w-]+/.test(url.pathname);
  }
  return false;
}

/** True when the file is an MP4 or MOV video CutToClip can process. */
export function isSupportedVideoFile(file: File | null): boolean {
  if (!file) return false;
  const name = file.name.toLowerCase();
  if (name.endsWith(".mp4") || name.endsWith(".mov")) return true;
  return file.type === "video/mp4" || file.type === "video/quicktime";
}

export function useSourceForm() {
  const [sourceMode, setSourceMode] = useState<"youtube" | "file">("youtube");
  const [source, setSource] = useState("");
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [sourceError, setSourceError] = useState("");
  const [settings, setSettings] = useState<ProjectSettings>(initialSettings);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // A source is only "ready" when it is present AND valid, so the CTA never
  // enables (or claims readiness) for an unusable URL or unsupported file.
  const sourceReady = sourceMode === "file" ? isSupportedVideoFile(localFile) : isValidYouTubeUrl(source);

  const resetSource = () => { setSource(""); setLocalFile(null); setSourceError(""); };

  return { sourceMode, setSourceMode, source, setSource, localFile, setLocalFile, sourceError, setSourceError, settings, setSettings, fileInputRef, sourceReady, resetSource };
}

export type UseSourceForm = ReturnType<typeof useSourceForm>;
