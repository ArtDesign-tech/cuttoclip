import type { ProjectSettings } from "../types";

export const initialSettings: ProjectSettings = {
  clipCount: 3,
  duration: { minSeconds: 15, maxSeconds: 90 },
  language: "auto",
  layout: "portrait",
  captionPreset: "bold_focus",
  encoder: "auto",
};
