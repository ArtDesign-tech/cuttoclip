import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("bilingual source, editor, tablet drawers, and results stay production-ready", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 920 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Turn the long take into the moments that matter." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open details" })).toHaveCount(0);
  const sourceA11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(sourceA11y.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);

  await page.getByRole("textbox", { name: /Public YouTube URL/ }).fill("https://youtube.com/watch?v=demo");
  await page.getByRole("button", { name: "Find my clips" }).click();
  await expect(page.locator(".clip-card-grid h3", { hasText: "The uncomfortable growth loop" })).toBeVisible({ timeout: 12_000 });
  await expect(page.getByRole("button", { name: "Render 3 clips" })).toBeVisible();
  const momentsA11y = await new AxeBuilder({ page }).include(".clip-card-grid").withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(momentsA11y.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);
  const cardMetaBounds = await page.locator(".clip-card-grid .card-meta").first().boundingBox();
  const editButtonBounds = await page.getByRole("button", { name: "Edit clip" }).first().boundingBox();
  expect(cardMetaBounds).not.toBeNull();
  expect(editButtonBounds).not.toBeNull();
  expect(editButtonBounds!.y - (cardMetaBounds!.y + cardMetaBounds!.height)).toBeGreaterThanOrEqual(12);
  await page.getByRole("button", { name: "Edit clip" }).first().click();
  await expect(page.locator("body > .clip-editor-backdrop")).toHaveCount(1);
  const editorBounds = await page.getByRole("dialog", { name: "The uncomfortable growth loop" }).boundingBox();
  expect(editorBounds).not.toBeNull();
  expect(editorBounds!.x).toBeGreaterThanOrEqual(0);
  expect(editorBounds!.x + editorBounds!.width).toBeLessThanOrEqual(1440);
  // Save assurance: an explicit status and a Done action live inside the editor.
  await expect(page.getByRole("dialog").locator(".editor-save")).toHaveText(/Saved|Saving/);
  await expect(page.getByRole("dialog").getByRole("button", { name: "Done" })).toBeVisible();
  const portraitStage = page.getByRole("dialog").locator(".video-stage");
  const portraitBounds = await portraitStage.boundingBox();
  expect(portraitBounds).not.toBeNull();
  expect(portraitBounds!.width / portraitBounds!.height).toBeCloseTo(9 / 16, 2);
  await expect(page.getByRole("dialog").getByRole("status")).toHaveText("Instant preview");
  await page.getByRole("dialog").getByLabel("Frame").selectOption("landscape");
  const landscapeBounds = await portraitStage.boundingBox();
  expect(landscapeBounds).not.toBeNull();
  expect(landscapeBounds!.width / landscapeBounds!.height).toBeCloseTo(16 / 9, 2);
  await page.getByRole("dialog").getByLabel("Frame").selectOption("smart_portrait");
  // Focus stays trapped inside the dialog.
  await expect.poll(async () => page.evaluate(() => document.querySelector(".clip-editor-dialog")?.contains(document.activeElement) ?? false)).toBe(true);
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.querySelector(".clip-editor-dialog")?.contains(document.activeElement) ?? false)).toBe(true);
  // Done closes the editor and returns focus to the originating Edit clip button.
  await page.getByRole("dialog").getByRole("button", { name: "Done" }).click();
  await expect(page.locator("body > .clip-editor-backdrop")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit clip" }).first()).toBeFocused();
  await page.getByRole("button", { name: "Open details" }).click();
  await expect(page.getByRole("complementary", { name: "Project details" })).toBeVisible();
  await expect.poll(async () => Math.round((await page.locator(".app-surface").boundingBox())?.width ?? 0)).toBe(848);
  await page.getByRole("complementary", { name: "Project details" }).getByRole("button", { name: "Close details" }).click();
  await page.getByRole("button", { name: /^All projects/ }).click();
  await expect(page.getByRole("heading", { name: "My projects" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open details" })).toHaveCount(0);
  await page.locator(".sidebar-project-row").first().click();
  await page.getByRole("tab", { name: "AI moments" }).click();
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.getByRole("button", { name: "Expand sidebar" }).click();

  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.locator(".clip-card-grid .moment-card")).toHaveCount(3);
  await page.getByRole("button", { name: "Edit clip" }).first().click();
  await expect(page.getByRole("dialog", { name: "The uncomfortable growth loop" })).toBeVisible();
  await page.getByRole("dialog").getByLabel("Frame").selectOption("landscape");
  await page.getByRole("dialog").getByLabel("Caption").selectOption("karaoke");
  await page.getByRole("dialog").getByRole("button", { name: "Apply style to all clips" }).click();
  const toast = page.locator(".app-toast");
  await expect(toast).toHaveText("Style applied to all clips");
  await expect(page.getByRole("dialog", { name: "The uncomfortable growth loop" })).toBeVisible();
  await toast.getByRole("button", { name: "Close" }).click();
  await expect(toast).toHaveCount(0);
  await page.getByRole("dialog").getByRole("button", { name: "Apply style to all clips" }).click();
  await expect(toast).toHaveText("Style applied to all clips");
  await expect(toast).toHaveCount(0, { timeout: 5_000 });
  await expect(page.getByRole("dialog", { name: "The uncomfortable growth loop" })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await expect(page.locator(".clip-card-grid .card-meta span:last-child").filter({ hasText: "landscape · karaoke" })).toHaveCount(3);

  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "YouTube source cache" })).toHaveCount(0);
  await expect(page.getByText("CutToClip Demo does not download or retain video files.")).toBeVisible();
  await page.getByRole("button", { name: "EN", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Pengaturan" })).toBeVisible();
  expect(await page.locator("html").getAttribute("lang")).toBe("id");

  await page.getByRole("button", { name: "Buka navigasi" }).click();
  await page.locator(".sidebar-project-row").first().click();
  await page.getByRole("tab", { name: "AI Moments" }).click();
  await page.getByRole("button", { name: "Render 3 clip" }).click();
  await expect(page.getByRole("heading", { name: "Clip Anda siap" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".result-state").filter({ hasText: "Siap" })).toHaveCount(3);
});

test("layered sidebar reveals smoothly, persists, and becomes an accessible mobile drawer", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const shell = page.locator(".app-shell");
  const sidebar = page.locator(".app-sidebar");
  const surface = page.locator(".app-surface");

  await expect.poll(async () => Math.round((await surface.boundingBox())?.x ?? -1)).toBe(260);
  const layers = await page.evaluate(() => ({
    sidebar: Number.parseInt(getComputedStyle(document.querySelector<HTMLElement>(".app-sidebar")!).zIndex, 10),
    surface: Number.parseInt(getComputedStyle(document.querySelector<HTMLElement>(".app-surface")!).zIndex, 10),
  }));
  expect(layers.surface).toBeGreaterThan(layers.sidebar);

  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(shell).toHaveClass(/sidebar-is-collapsed/);
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeFocused();
  await expect.poll(async () => Math.round((await surface.boundingBox())?.x ?? -1)).toBe(0);
  await expect(sidebar).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("cuttoclip:sidebar-collapsed"))).toBe("true");

  await page.reload();
  await expect(shell).toHaveClass(/sidebar-is-collapsed/);
  await expect.poll(async () => Math.round((await surface.boundingBox())?.x ?? -1)).toBe(0);
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeFocused();
  await expect.poll(async () => Math.round((await surface.boundingBox())?.x ?? -1)).toBe(260);

  await page.emulateMedia({ reducedMotion: "reduce" });
  const reducedDurations = await surface.evaluate((element) => getComputedStyle(element).transitionDuration.split(",").map((duration) => Number.parseFloat(duration)));
  expect(reducedDurations.every((duration) => duration <= 0.001)).toBe(true);

  await page.setViewportSize({ width: 900, height: 900 });
  await expect.poll(async () => Math.round((await surface.boundingBox())?.x ?? -1)).toBe(0);
  await expect(surface).toHaveCSS("border-top-left-radius", "0px");
  const menuButton = page.getByRole("button", { name: "Open navigation" });
  await menuButton.click();
  await expect(sidebar).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sidebar).toBeHidden();
  await expect(menuButton).toBeFocused();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(async () => Math.round((await surface.boundingBox())?.width ?? -1)).toBe(390);
  await menuButton.click();
  await expect(sidebar).toBeVisible();
  // Background scroll is locked and focus is trapped while the drawer is open.
  await expect(shell).toHaveClass(/mobile-drawer-open/);
  await expect(page.locator(".app-main")).toHaveCSS("overflow-y", "hidden");
  await expect.poll(async () => page.evaluate(() => document.querySelector(".app-sidebar")?.contains(document.activeElement) ?? false)).toBe(true);
  await sidebar.getByRole("button", { name: "Close" }).click();
  await expect(menuButton).toBeFocused();
  await expect(shell).not.toHaveClass(/mobile-drawer-open/);
});
