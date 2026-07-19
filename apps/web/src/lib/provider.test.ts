import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
});

describe("provider adapter outside the desktop host", () => {
  it("reports needsDesktop and never writes a secret to localStorage", async () => {
    // No __TAURI_INTERNALS__ on window => not desktop.
    const provider = await import("./provider");
    expect(provider.isDesktop()).toBe(false);

    const activate = await provider.activateManaged("invite-secret-123");
    expect(activate).toEqual({ ok: false, needsDesktop: true });

    const save = await provider.saveByokCredentials(["gsk-secret"], ["gemini-secret"]);
    expect(save).toEqual({ ok: false, needsDesktop: true });

    // The critical invariant: no secret leaked into localStorage.
    const dump = JSON.stringify(window.localStorage);
    expect(dump).not.toContain("invite-secret-123");
    expect(dump).not.toContain("gsk-secret");
    expect(dump).not.toContain("gemini-secret");
    expect(window.localStorage.length).toBe(0);
  });

  it("returns null installation identity without a host", async () => {
    const provider = await import("./provider");
    expect(await provider.fetchInstallationIdentity()).toBeNull();
  });
});

describe("provider adapter inside the desktop host", () => {
  it("invokes the Tauri command and reports success", async () => {
    const invoke = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const provider = await import("./provider");
    expect(provider.isDesktop()).toBe(true);

    const result = await provider.saveByokCredentials(["gsk-live", "gsk-backup"], ["gem-live"]);
    expect(result).toEqual({ ok: true });
    // Multiple keys are joined with commas into one secret per provider.
    expect(invoke).toHaveBeenCalledWith("save_byok_credentials", { groqKey: "gsk-live,gsk-backup", geminiKey: "gem-live" });

    vi.doUnmock("@tauri-apps/api/core");
  });

  it("surfaces a structured error when the command rejects", async () => {
    const invoke = vi.fn().mockRejectedValue({ code: "invalid_invite", message: "Invite code is invalid or already used." });
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    vi.doMock("@tauri-apps/api/core", () => ({ invoke }));

    const provider = await import("./provider");
    const result = await provider.activateManaged("bad-code");
    expect(result).toEqual({ ok: false, needsDesktop: false, code: "invalid_invite", message: "Invite code is invalid or already used." });

    vi.doUnmock("@tauri-apps/api/core");
  });
});
