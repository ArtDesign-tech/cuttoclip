import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("CutToClip production shell", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders a gated source flow and switches the full interface language from settings", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("worker offline")));
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Turn the long take into the moments that matter." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find my clips" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByRole("heading", { name: "Pengaturan" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("id");
  });

  it("fully hides the desktop sidebar, transfers focus, and persists the preference", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("worker offline")));
    const user = userEvent.setup();
    const { container } = render(<App />);
    const shell = container.querySelector(".app-shell");

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(shell).toHaveClass("sidebar-is-collapsed");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveFocus();
    await waitFor(() => expect(window.localStorage.getItem("cuttoclip:sidebar-collapsed")).toBe("true"));

    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));

    expect(shell).not.toHaveClass("sidebar-is-collapsed");
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveFocus();
    await waitFor(() => expect(window.localStorage.getItem("cuttoclip:sidebar-collapsed")).toBe("false"));
  });
});
