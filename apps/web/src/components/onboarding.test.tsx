import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OnboardingScreen } from "./onboarding";
import { translate, type Locale, type TranslationKey } from "../i18n";

const t = (key: TranslationKey | string, params?: Record<string, string | number>) => translate("en" as Locale, key, params);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("OnboardingScreen", () => {
  it("shows both provider choices and lets the user skip", async () => {
    const onSkip = vi.fn();
    const user = userEvent.setup();
    render(<OnboardingScreen t={t} onComplete={vi.fn()} onSkip={onSkip} />);

    expect(screen.getByRole("heading", { name: "Choose how CutToClip Beta reaches AI" })).toBeInTheDocument();
    expect(screen.getByText("Managed Beta")).toBeInTheDocument();
    expect(screen.getByText("My API keys")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Set up later" }));
    expect(onSkip).toHaveBeenCalledOnce();
  });

  it("keeps the managed activate button disabled until an invite and privacy consent are provided", async () => {
    const user = userEvent.setup();
    render(<OnboardingScreen t={t} onComplete={vi.fn()} onSkip={vi.fn()} />);

    await user.click(screen.getByText("Managed Beta"));
    const activate = screen.getByRole("button", { name: "Activate" });
    expect(activate).toBeDisabled();

    await user.type(screen.getByLabelText("Invite code"), "tester-invite");
    expect(activate).toBeDisabled(); // still need consent

    await user.click(screen.getByLabelText("I understand and agree"));
    expect(activate).toBeEnabled();
  });

  it("shows the needs-desktop message when activating outside the desktop host", async () => {
    const user = userEvent.setup();
    render(<OnboardingScreen t={t} onComplete={vi.fn()} onSkip={vi.fn()} />);

    await user.click(screen.getByText("Managed Beta"));
    await user.type(screen.getByLabelText("Invite code"), "tester-invite");
    await user.click(screen.getByLabelText("I understand and agree"));
    await user.click(screen.getByRole("button", { name: "Activate" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/desktop app/i);
  });

  it("requires both BYOK keys before saving", async () => {
    const user = userEvent.setup();
    render(<OnboardingScreen t={t} onComplete={vi.fn()} onSkip={vi.fn()} />);

    await user.click(screen.getByText("My API keys"));
    const save = screen.getByRole("button", { name: "Save keys" });
    expect(save).toBeDisabled();

    await user.type(screen.getByLabelText("Groq API key"), "gsk-test");
    await user.click(screen.getByLabelText("I understand and agree"));
    expect(save).toBeDisabled(); // gemini still missing

    await user.type(screen.getByLabelText("Gemini API key"), "gem-test");
    expect(save).toBeEnabled();
  });

  it("offers an optional backup key slot per provider", async () => {
    const user = userEvent.setup();
    const { container } = render(<OnboardingScreen t={t} onComplete={vi.fn()} onSkip={vi.fn()} />);

    await user.click(screen.getByText("My API keys"));
    // Two slots each for Groq and Gemini; the second is the optional backup.
    expect(container.querySelector("#groq-key-1")).not.toBeNull();
    expect(container.querySelector("#gemini-key-1")).not.toBeNull();

    // One key per provider (first slot) is enough to enable saving.
    await user.type(screen.getByLabelText("Groq API key"), "gsk-primary");
    await user.type(screen.getByLabelText("Gemini API key"), "gem-primary");
    await user.click(screen.getByLabelText("I understand and agree"));
    expect(screen.getByRole("button", { name: "Save keys" })).toBeEnabled();
  });
});
