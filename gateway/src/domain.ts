import { z } from "zod";

export type TranscriptWord = {
  text: string;
  startSeconds: number;
  endSeconds: number;
};

export type TranscriptSegment = TranscriptWord & {
  words: TranscriptWord[];
};

export type NormalizedTranscript = {
  text: string;
  language: string;
  duration: number;
  words: TranscriptWord[];
  segments: TranscriptSegment[];
};

export type HighlightSettings = {
  clipCount: number;
  minDurationSeconds: number;
  maxDurationSeconds: number;
  language: string;
};

export type HighlightRequest = {
  transcript: string;
  segments: TranscriptSegment[];
  sourceDurationSeconds: number;
  settings: HighlightSettings;
};

export type HighlightCandidate = {
  startSeconds: number;
  endSeconds: number;
  title: string;
  hook: string;
  reason: string;
  score: number;
};

export type HighlightWindow = {
  index: number;
  startSeconds: number;
  endSeconds: number;
  segments: TranscriptSegment[];
};

const finiteSeconds = z.number().finite().nonnegative();

const transcriptWordInput = z
  .object({
    text: z.string().optional(),
    word: z.string().optional(),
    startSeconds: finiteSeconds.optional(),
    endSeconds: finiteSeconds.optional(),
    start: finiteSeconds.optional(),
    end: finiteSeconds.optional(),
  })
  .passthrough()
  .transform((value, context): TranscriptWord => {
    const text = (value.text ?? value.word ?? "").trim();
    const startSeconds = value.startSeconds ?? value.start;
    const endSeconds = value.endSeconds ?? value.end;
    if (!text) {
      context.addIssue({ code: "custom", message: "Word text is required." });
    }
    if (startSeconds === undefined || endSeconds === undefined || endSeconds < startSeconds) {
      context.addIssue({ code: "custom", message: "Word timing is invalid." });
    }
    return { text, startSeconds: startSeconds ?? 0, endSeconds: endSeconds ?? 0 };
  });

const transcriptSegmentInput = z
  .object({
    text: z.string().trim().min(1).max(20_000),
    startSeconds: finiteSeconds.optional(),
    endSeconds: finiteSeconds.optional(),
    start: finiteSeconds.optional(),
    end: finiteSeconds.optional(),
    words: z.array(transcriptWordInput).max(20_000).optional(),
  })
  .passthrough()
  .transform((value, context): TranscriptSegment => {
    const startSeconds = value.startSeconds ?? value.start;
    const endSeconds = value.endSeconds ?? value.end;
    if (startSeconds === undefined || endSeconds === undefined || endSeconds <= startSeconds) {
      context.addIssue({ code: "custom", message: "Segment timing is invalid." });
    }
    if (startSeconds !== undefined && endSeconds !== undefined) {
      for (const [index, word] of (value.words ?? []).entries()) {
        if (word.startSeconds < startSeconds - 0.05 || word.endSeconds > endSeconds + 0.05) {
          context.addIssue({
            code: "custom",
            path: ["words", index],
            message: "Word timing falls outside its segment.",
          });
        }
      }
    }
    return {
      text: value.text,
      startSeconds: startSeconds ?? 0,
      endSeconds: endSeconds ?? 0,
      words: value.words ?? [],
    };
  });

const rawTranscriptionSchema = z
  .object({
    text: z.string().trim().min(1).max(5_000_000),
    language: z.string().trim().min(1).max(64).optional(),
    duration: finiteSeconds.optional(),
    words: z.array(transcriptWordInput).max(100_000).optional(),
    segments: z.array(transcriptSegmentInput).min(1).max(100_000),
  })
  .passthrough();

export function normalizeTranscriptionPayload(payload: unknown, requestedLanguage = "auto"): NormalizedTranscript {
  const parsed = rawTranscriptionSchema.parse(payload);
  const words = [...(parsed.words ?? [])].sort(compareTimedItems);
  const segments = parsed.segments
    .map((segment) => {
      const segmentWords = segment.words.length
        ? [...segment.words].sort(compareTimedItems)
        : words.filter((word) => word.endSeconds > segment.startSeconds && word.startSeconds < segment.endSeconds);
      return { ...segment, words: segmentWords };
    })
    .sort(compareTimedItems);

  validateMonotonicTiming(words, "word");
  validateMonotonicTiming(segments, "segment");

  const finalWords = words.length ? words : segments.flatMap((segment) => segment.words).sort(compareTimedItems);
  const lastTimestamp = Math.max(
    finalWords.at(-1)?.endSeconds ?? 0,
    segments.at(-1)?.endSeconds ?? 0,
  );
  return {
    text: parsed.text,
    language: parsed.language ?? (requestedLanguage !== "auto" ? requestedLanguage : "unknown"),
    duration: Math.max(parsed.duration ?? 0, lastTimestamp),
    words: finalWords,
    segments,
  };
}

const highlightSettingsSchema = z
  .object({
    clipCount: z.number().int().min(1).max(10),
    minDurationSeconds: z.number().int().min(15).max(90),
    maxDurationSeconds: z.number().int().min(15).max(90),
    language: z.string().trim().min(1).max(64),
  })
  .superRefine((value, context) => {
    if (value.minDurationSeconds > value.maxDurationSeconds) {
      context.addIssue({
        code: "custom",
        path: ["minDurationSeconds"],
        message: "minDurationSeconds must not exceed maxDurationSeconds.",
      });
    }
  });

const highlightRequestInput = z
  .object({
    transcript: z.string().max(5_000_000).optional(),
    segments: z.array(transcriptSegmentInput).min(1).max(100_000),
    sourceDurationSeconds: finiteSeconds.positive().max(7_200).optional(),
    sourceDuration: finiteSeconds.positive().max(7_200).optional(),
    settings: highlightSettingsSchema,
  })
  .superRefine((value, context) => {
    if (value.sourceDurationSeconds === undefined && value.sourceDuration === undefined) {
      context.addIssue({ code: "custom", path: ["sourceDurationSeconds"], message: "Source duration is required." });
      return;
    }
    const sourceDurationSeconds = value.sourceDurationSeconds ?? value.sourceDuration ?? 0;
    for (const [index, segment] of value.segments.entries()) {
      if (segment.endSeconds > sourceDurationSeconds + 0.001) {
        context.addIssue({
          code: "custom",
          path: ["segments", index, "endSeconds"],
          message: "Segment exceeds the source duration.",
        });
      }
    }
  })
  .transform((value): HighlightRequest => ({
    transcript: value.transcript?.trim() || value.segments.map((segment) => segment.text).join(" "),
    segments: [...value.segments].sort(compareTimedItems),
    sourceDurationSeconds: value.sourceDurationSeconds ?? value.sourceDuration ?? 0,
    settings: value.settings,
  }));

export function parseHighlightRequest(payload: unknown): HighlightRequest {
  return highlightRequestInput.parse(payload);
}

export const highlightEnvelopeSchema = z.object({
  clips: z
    .array(
      z.object({
        startSeconds: finiteSeconds,
        endSeconds: finiteSeconds,
        title: z.string().trim().min(1).max(160),
        hook: z.string().trim().min(1).max(500),
        reason: z.string().trim().min(1).max(500),
        score: z.number().int().min(0).max(100),
      }),
    )
    .max(50),
});

export function createHighlightWindows(
  segments: TranscriptSegment[],
  sourceDurationSeconds: number,
  windowSeconds = 12 * 60,
  overlapSeconds = 30,
): HighlightWindow[] {
  if (windowSeconds <= 0 || overlapSeconds < 0 || overlapSeconds >= windowSeconds) {
    throw new Error("Highlight window configuration is invalid.");
  }
  const windows: HighlightWindow[] = [];
  const stepSeconds = windowSeconds - overlapSeconds;
  for (let startSeconds = 0, index = 0; startSeconds < sourceDurationSeconds; startSeconds += stepSeconds, index += 1) {
    const endSeconds = Math.min(sourceDurationSeconds, startSeconds + windowSeconds);
    const windowSegments = segments.filter(
      (segment) => segment.endSeconds > startSeconds && segment.startSeconds < endSeconds,
    );
    if (windowSegments.length) {
      windows.push({ index, startSeconds, endSeconds, segments: windowSegments });
    }
    if (endSeconds === sourceDurationSeconds) break;
  }
  return windows;
}

export function validateWindowCandidates(
  payload: unknown,
  window: HighlightWindow,
  request: HighlightRequest,
): HighlightCandidate[] {
  const envelope = highlightEnvelopeSchema.parse(payload);
  return envelope.clips.map((clip, index) => {
    const duration = clip.endSeconds - clip.startSeconds;
    const withinWindow = clip.startSeconds >= window.startSeconds - 0.001 && clip.endSeconds <= window.endSeconds + 0.001;
    const withinSource = clip.endSeconds <= request.sourceDurationSeconds + 0.001;
    if (
      clip.endSeconds <= clip.startSeconds ||
      duration < request.settings.minDurationSeconds - 0.001 ||
      duration > request.settings.maxDurationSeconds + 0.001 ||
      !withinWindow ||
      !withinSource
    ) {
      throw new z.ZodError([
        {
          code: "custom",
          path: ["clips", index],
          message: "Candidate timing is outside the source/window or requested duration range.",
        },
      ]);
    }
    return clip;
  });
}

export function overlapRatio(left: HighlightCandidate, right: HighlightCandidate): number {
  const overlap = Math.max(0, Math.min(left.endSeconds, right.endSeconds) - Math.max(left.startSeconds, right.startSeconds));
  const shorterDuration = Math.min(left.endSeconds - left.startSeconds, right.endSeconds - right.startSeconds);
  return shorterDuration > 0 ? overlap / shorterDuration : 0;
}

export function rankAndDedupeCandidates(candidates: HighlightCandidate[], clipCount: number) {
  const ranked = [...candidates].sort((left, right) => right.score - left.score || left.startSeconds - right.startSeconds);
  const selected: HighlightCandidate[] = [];
  for (const candidate of ranked) {
    if (selected.some((existing) => overlapRatio(existing, candidate) > 0.5)) continue;
    selected.push(candidate);
    if (selected.length === clipCount) break;
  }
  const accents = ["coral", "mint", "violet"] as const;
  return selected.map((candidate, index) => ({
    id: `clip-${String(index + 1).padStart(2, "0")}`,
    ...candidate,
    source: "ai" as const,
    accent: accents[index % accents.length],
  }));
}

function compareTimedItems(left: TranscriptWord, right: TranscriptWord) {
  return left.startSeconds - right.startSeconds || left.endSeconds - right.endSeconds;
}

function validateMonotonicTiming(items: TranscriptWord[], label: string) {
  for (let index = 1; index < items.length; index += 1) {
    if (items[index].startSeconds + 0.001 < items[index - 1].startSeconds) {
      throw new z.ZodError([
        { code: "custom", path: [label, index], message: `${label} timing must be monotonic.` },
      ]);
    }
  }
}
