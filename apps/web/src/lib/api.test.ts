import { afterEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../types";

const project: Project = {
  id: "project-1",
  sourceLabel: "source.mp4",
  sourceKind: "file",
  durationSeconds: 120,
  resolution: "1280 x 720",
  transcriptReady: true,
  settings: { clipCount: 1, duration: { minSeconds: 15, maxSeconds: 90 }, language: "id", layout: "smart_portrait", captionPreset: "bold_focus", encoder: "auto" },
  candidates: [{ id: "clip-1", startSeconds: 10, endSeconds: 40, title: "Clip", hook: "Hook", reason: "Reason", score: 90, accent: "coral", source: "ai", presentation: { layout: "portrait", captionPreset: "karaoke" }, revision: 3 }],
  outputs: [],
  revision: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("render API compatibility", () => {
  it("removes new candidate fields when an older worker reports no API features", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ platform: "windows", ffmpeg: true, ffprobe: true, ytDlp: true, encoders: [], vision: false, gatewayConfigured: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", projectId: project.id, type: "render", status: "queued", stage: "queued", progress: 0, createdAt: "now", updatedAt: "now" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");

    await api.getSystemCapabilities();
    await api.renderProject(project);

    const init = fetchMock.mock.calls[1][1] as RequestInit;
    const payload = JSON.parse(String(init.body));
    expect(payload.clips[0]).not.toHaveProperty("presentation");
    expect(payload.clips[0]).not.toHaveProperty("revision");
    expect(payload.clips[0]).toMatchObject({ id: "clip-1", startSeconds: 10, endSeconds: 40, title: "Clip" });
  });

  it("keeps per-clip presentation for a current worker", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ platform: "windows", ffmpeg: true, ffprobe: true, ytDlp: true, encoders: [], vision: false, gatewayConfigured: true, apiFeatures: ["per-clip-presentation"] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "job-1", projectId: project.id, type: "render", status: "queued", stage: "queued", progress: 0, createdAt: "now", updatedAt: "now" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");

    await api.getSystemCapabilities();
    await api.renderProject(project);

    const init = fetchMock.mock.calls[1][1] as RequestInit;
    const payload = JSON.parse(String(init.body));
    expect(payload.clips[0].presentation).toEqual({ layout: "portrait", captionPreset: "karaoke" });
    expect(payload.clips[0].revision).toBe(3);
  });

  it("reads compact project summaries and deletes a project through the library API", async () => {
    const summaries = [{ id: "project-1", sourceLabel: "source.mp4", sourceKind: "file", durationSeconds: 120, resolution: "1280 x 720", transcriptReady: true, status: "complete", createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-02T00:00:00Z", candidateCount: 3, outputCount: 2, failedOutputCount: 1 }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(summaries), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = await import("./api");

    await expect(api.getProjectSummaries()).resolves.toEqual(summaries);
    await expect(api.deleteProject("project-1")).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0][0]).toContain("/projects/summaries");
    expect(fetchMock.mock.calls[1][0]).toContain("/projects/project-1");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
  });
});
