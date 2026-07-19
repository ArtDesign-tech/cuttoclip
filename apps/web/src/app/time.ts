export const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

export function formatTime(seconds: number): string {
  const safe = Math.max(0, Math.round(seconds));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60).toString().padStart(2, "0");
  const secs = (safe % 60).toString().padStart(2, "0");
  return hours ? `${hours.toString().padStart(2, "0")}:${minutes}:${secs}` : `${minutes}:${secs}`;
}

export function parseTimecode(value: string): number | null {
  const parts = value.trim().split(":");
  if (parts.length < 1 || parts.length > 3 || parts.some((part) => !/^\d+(?:\.\d+)?$/.test(part))) return null;
  const values = parts.map(Number);
  if (values.some((value) => !Number.isFinite(value))) return null;
  if (parts.length === 1) return values[0];
  if (values.at(-1)! >= 60 || (parts.length === 3 && values[1] >= 60)) return null;
  return parts.length === 2 ? values[0] * 60 + values[1] : values[0] * 3600 + values[1] * 60 + values[2];
}

export function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 1000));
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}
