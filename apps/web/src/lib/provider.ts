import { invoke } from "@tauri-apps/api/core";
import type { InstallationIdentity, ProviderActionResult, ProviderMode } from "../types";
import { isDemoMode } from "./api";

/**
 * Provider onboarding/settings actions that persist secrets.
 *
 * Secrets (the Managed installation token, and BYOK Groq/Gemini keys) must be
 * written to the Stronghold vault via Tauri commands (built in track C). Those
 * commands only exist inside the desktop host. In a browser/dev/demo context we
 * MUST NOT fall back to localStorage or any other plaintext store — instead we
 * return ``needsDesktop`` so the UI can tell the user to open the desktop app.
 * No secret ever leaves this module in a non-desktop environment.
 */

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

const NEEDS_DESKTOP: ProviderActionResult = { ok: false, needsDesktop: true };

async function invokeCommand(command: string, args: Record<string, unknown>): Promise<ProviderActionResult> {
  // Demo mode has no real backend and no host; treat it like a non-desktop env.
  if (isDemoMode || !isDesktop()) return NEEDS_DESKTOP;
  try {
    await invoke(command, args);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      needsDesktop: false,
      code: extractCode(error),
      message: extractMessage(error),
    };
  }
}

export function activateManaged(inviteCode: string): Promise<ProviderActionResult> {
  return invokeCommand("activate_managed", { inviteCode });
}

/**
 * Save one or more Groq/Gemini keys. Multiple keys per provider are joined with
 * commas into a single secret; the worker rotates through them on rate-limit or
 * rejection. Blank entries are dropped before saving.
 */
export function saveByokCredentials(groqKeys: string[], geminiKeys: string[]): Promise<ProviderActionResult> {
  const groqKey = groqKeys.map((k) => k.trim()).filter(Boolean).join(",");
  const geminiKey = geminiKeys.map((k) => k.trim()).filter(Boolean).join(",");
  return invokeCommand("save_byok_credentials", { groqKey, geminiKey });
}

export function clearByokCredentials(): Promise<ProviderActionResult> {
  return invokeCommand("clear_byok_credentials", {});
}

export function setProviderMode(mode: ProviderMode): Promise<ProviderActionResult> {
  return invokeCommand("set_provider_mode", { mode });
}

export function restartWorker(): Promise<ProviderActionResult> {
  return invokeCommand("restart_worker", {});
}

/**
 * Managed installation identity from the gateway's ``GET /v1/me``, surfaced by a
 * Tauri command. Returns null outside the desktop host or when not activated.
 */
export async function fetchInstallationIdentity(): Promise<InstallationIdentity | null> {
  if (isDemoMode || !isDesktop()) return null;
  try {
    return await invoke<InstallationIdentity>("installation_identity", {});
  } catch {
    return null;
  }
}

function extractCode(error: unknown): string {
  if (typeof error === "object" && error && "code" in error && typeof (error as { code: unknown }).code === "string") {
    return (error as { code: string }).code;
  }
  return "provider_action_failed";
}

function extractMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error && "message" in error && typeof (error as { message: unknown }).message === "string") {
    return (error as { message: string }).message;
  }
  return "The provider action could not be completed.";
}
