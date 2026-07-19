import multipart from "@fastify/multipart";
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from "fastify";
import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { join } from "node:path";
import { z } from "zod";
import {
  createHighlightWindows,
  normalizeTranscriptionPayload,
  parseHighlightRequest,
  rankAndDedupeCandidates,
  validateWindowCandidates,
  type HighlightRequest,
  type HighlightWindow,
} from "./domain.js";
import { GatewayBusyError, UpstreamLimiter } from "./limiter.js";
import { GatewayStore, hashSecret, type Installation } from "./store.js";

declare module "fastify" {
  interface FastifyRequest {
    installation?: Installation;
  }
}

export type GatewayConfig = {
  nodeEnv: string;
  host: string;
  port: number;
  dbPath: string;
  inviteCodes: string[];
  devBearerToken?: string;
  groqApiKey?: string;
  groqTranscriptionUrl: string;
  groqModel: string;
  aiGatewayUrl?: string;
  aiGatewayApiKey?: string;
  aiGatewayModel?: string;
  upstreamTimeoutMs: number;
  upstreamTotalTimeoutMs: number;
  upstreamRetryCount: number;
  highlightConcurrency: number;
  transcriptionGlobalConcurrency: number;
  highlightGlobalConcurrency: number;
  upstreamQueueLimit: number;
  upstreamQueueTimeoutMs: number;
  highlightWindowSeconds: number;
  highlightOverlapSeconds: number;
  trustedProxyCidrs: string[];
  allowedPublicHost?: string;
  logLevel: "debug" | "info" | "warn" | "error";
};

type ApiErrorPayload = {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details?: unknown;
  };
};

type BuildAppOptions = {
  config?: GatewayConfig;
  fetchImpl?: typeof fetch;
  logger?: boolean;
  store?: GatewayStore;
};

export class ApiError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly details?: unknown,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function loadConfig(environment: NodeJS.ProcessEnv = process.env): GatewayConfig {
  const nodeEnv = cleanOptional(environment.NODE_ENV) ?? "development";
  const production = nodeEnv === "production";
  const strict = production;
  const programData = environment.ProgramData ?? environment.PROGRAMDATA ?? "C:\\ProgramData";
  const config: GatewayConfig = {
    nodeEnv,
    host: cleanOptional(environment.HOST) ?? "127.0.0.1",
    port: parseInteger(environment.PORT, 4_327, 1, 65_535, "PORT", strict),
    dbPath: cleanOptional(environment.GATEWAY_DB_PATH) ?? join(programData, "CutToClip", "Gateway", "gateway.sqlite3"),
    inviteCodes: splitCsv(environment.INVITE_CODES),
    devBearerToken: cleanOptional(environment.GATEWAY_DEV_BEARER_TOKEN),
    groqApiKey: cleanOptional(environment.GROQ_API_KEY),
    groqTranscriptionUrl:
      cleanOptional(environment.GROQ_TRANSCRIPTION_URL) ?? "https://api.groq.com/openai/v1/audio/transcriptions",
    groqModel: cleanOptional(environment.GROQ_TRANSCRIPTION_MODEL) ?? "whisper-large-v3-turbo",
    aiGatewayUrl: cleanOptional(environment.AI_GATEWAY_URL),
    aiGatewayApiKey: cleanOptional(environment.AI_GATEWAY_API_KEY),
    aiGatewayModel: cleanOptional(environment.AI_GATEWAY_MODEL),
    upstreamTimeoutMs: parseInteger(environment.UPSTREAM_TIMEOUT_MS, 50_000, 1_000, 110_000, "UPSTREAM_TIMEOUT_MS", strict),
    upstreamTotalTimeoutMs: parseInteger(
      environment.UPSTREAM_TOTAL_TIMEOUT_MS,
      110_000,
      1_000,
      110_000,
      "UPSTREAM_TOTAL_TIMEOUT_MS",
      strict,
    ),
    upstreamRetryCount: parseInteger(environment.UPSTREAM_RETRY_COUNT, 1, 0, 1, "UPSTREAM_RETRY_COUNT", strict),
    highlightConcurrency: parseInteger(environment.HIGHLIGHT_CONCURRENCY, 2, 1, 4, "HIGHLIGHT_CONCURRENCY", strict),
    transcriptionGlobalConcurrency: parseInteger(
      environment.TRANSCRIPTION_GLOBAL_CONCURRENCY,
      2,
      1,
      8,
      "TRANSCRIPTION_GLOBAL_CONCURRENCY",
      strict,
    ),
    highlightGlobalConcurrency: parseInteger(
      environment.HIGHLIGHT_GLOBAL_CONCURRENCY,
      2,
      1,
      8,
      "HIGHLIGHT_GLOBAL_CONCURRENCY",
      strict,
    ),
    upstreamQueueLimit: parseInteger(environment.UPSTREAM_QUEUE_LIMIT, 8, 0, 100, "UPSTREAM_QUEUE_LIMIT", strict),
    upstreamQueueTimeoutMs: parseInteger(
      environment.UPSTREAM_QUEUE_TIMEOUT_MS,
      30_000,
      1_000,
      120_000,
      "UPSTREAM_QUEUE_TIMEOUT_MS",
      strict,
    ),
    highlightWindowSeconds: 12 * 60,
    highlightOverlapSeconds: 30,
    trustedProxyCidrs: splitCsv(environment.TRUST_PROXY_CIDRS || "127.0.0.1,::1"),
    allowedPublicHost: cleanOptional(environment.ALLOWED_PUBLIC_HOST)?.toLowerCase(),
    logLevel: parseLogLevel(environment.LOG_LEVEL),
  };
  if (config.upstreamTotalTimeoutMs < config.upstreamTimeoutMs) {
    throw new Error("UPSTREAM_TOTAL_TIMEOUT_MS must be greater than or equal to UPSTREAM_TIMEOUT_MS.");
  }
  if (production) validateProductionConfig(config);
  return config;
}

export async function buildApp(options: BuildAppOptions = {}): Promise<FastifyInstance> {
  const config = options.config ?? loadConfig();
  const fetchImpl = options.fetchImpl ?? fetch;
  const store = options.store ?? new GatewayStore(config.dbPath);
  const app = Fastify({
    logger: options.logger === false ? false : {
      level: config.logLevel,
      redact: {
        paths: [
          "req.headers.authorization",
          "req.headers.cf-access-client-secret",
          "req.headers.cf-access-client-id",
          "req.body",
        ],
        censor: "[REDACTED]",
      },
    },
    trustProxy: config.trustedProxyCidrs,
  });
  const transcriptionLimiter = new UpstreamLimiter(
    config.transcriptionGlobalConcurrency,
    config.upstreamQueueLimit,
    config.upstreamQueueTimeoutMs,
  );
  const highlightLimiter = new UpstreamLimiter(
    config.highlightGlobalConcurrency,
    config.upstreamQueueLimit,
    config.upstreamQueueTimeoutMs,
  );

  for (const inviteCode of config.inviteCodes) store.seedInvite(inviteCode);

  await app.register(multipart, {
    limits: { fileSize: 25 * 1024 * 1024, files: 1, fields: 4 },
  });

  app.addHook("onClose", async () => {
    store.close();
  });

  app.addHook("onRequest", async (request, reply) => {
    if (config.nodeEnv !== "production" || isLocalHealthRoute(request.url)) return;
    if (request.hostname.toLowerCase() !== config.allowedPublicHost || request.protocol !== "https") {
      return sendError(reply, 421, "invalid_public_origin", "The gateway request must use its configured HTTPS hostname.", false);
    }
  });

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ApiError) {
      return sendError(reply, error.statusCode, error.code, error.message, error.retryable, error.details, error.retryAfterSeconds);
    }
    if (error instanceof GatewayBusyError) {
      return sendError(reply, 503, "gateway_busy", "The gateway is busy. Try again shortly.", true, undefined, 30);
    }
    if (error instanceof z.ZodError) {
      return sendError(reply, 422, "invalid_request", "Request validation failed.", false, zodDetails(error));
    }
    const fastifyError = error as Error & { code?: string; statusCode?: number };
    if (fastifyError.code === "FST_REQ_FILE_TOO_LARGE") {
      return sendError(reply, 413, "audio_file_too_large", "Audio chunk exceeds the 25 MB limit.", false);
    }
    request.log.error({ err: error }, "Unhandled gateway request error");
    return sendError(
      reply,
      fastifyError.statusCode && fastifyError.statusCode < 500 ? fastifyError.statusCode : 500,
      "internal_error",
      "The gateway could not complete the request.",
      true,
    );
  });

  app.setNotFoundHandler((_request, reply) =>
    sendError(reply, 404, "route_not_found", "The requested gateway route does not exist.", false),
  );

  const authenticate = async (request: FastifyRequest, reply: FastifyReply): Promise<Installation | undefined> => {
    const authorization = request.headers.authorization;
    const token = authorization?.match(/^Bearer\s+(.+)$/i)?.[1]?.trim() ?? "";
    if (!token) {
      return sendError(reply, 401, "invalid_installation", "A valid installation bearer token is required.", false);
    }
    if (config.nodeEnv !== "production" && config.devBearerToken && safeTokenEqual(token, config.devBearerToken)) {
      const devInstallation: Installation = { id: "development", label: "development", createdAt: "", lastUsedAt: null, revokedAt: null, uses: 0 };
      request.installation = devInstallation;
      return devInstallation;
    }
    const installation = store.authenticate(hashSecret(token));
    if (!installation) {
      return sendError(reply, 401, "invalid_installation", "A valid installation bearer token is required.", false);
    }
    request.installation = installation;
    return installation;
  };

  const liveness = async () => ({ status: "ok", service: "cuttoclip-gateway" });
  app.get("/livez", liveness);
  app.get("/health", liveness);
  app.get("/readyz", async (_request, reply) => {
    if (!store.ready()) {
      return sendError(reply, 503, "not_ready", "The gateway persistence store is not ready.", true);
    }
    return { status: "ready", service: "cuttoclip-gateway" };
  });

  app.post("/v1/activate", async (request, reply) => {
    const parsed = z.object({ inviteCode: z.string().trim().min(1).max(256) }).safeParse(request.body);
    const inviteCode = parsed.success ? parsed.data.inviteCode : "";
    if (!inviteCode) {
      return sendError(reply, 401, "invalid_invite", "Invite code is invalid or already used.", false);
    }
    const token = randomBytes(32).toString("base64url");
    const installationId = randomUUID();
    const installation = store.consumeInvite(inviteCode, installationId, hashSecret(token));
    if (!installation) {
      return sendError(reply, 401, "invalid_invite", "Invite code is invalid or already used.", false);
    }
    return { installationId: installation.id, token, expiresIn: null };
  });

  app.get("/v1/me", { preHandler: authenticate }, async (request) => {
    // authenticate has already validated the token and populated request.installation.
    // This endpoint reports installation identity only — there is intentionally no
    // quota field, per the product decision to run the Managed Beta without daily caps.
    const installation = request.installation!;
    return {
      installationId: installation.id,
      label: installation.label,
      createdAt: installation.createdAt || null,
      lastUsedAt: installation.lastUsedAt,
    };
  });

  app.post("/v1/transcriptions", { preHandler: authenticate }, async (request) => {
    if (!config.groqApiKey) {
      throw new ApiError(503, "groq_not_configured", "Groq transcription is not configured.", false);
    }
    const controller = requestAbortController(request);
    try {
      return await transcriptionLimiter.run(async () => {
        const part = await request.file();
        if (!part) throw new ApiError(400, "audio_file_required", "A multipart audio file is required.", false);
        const audio = await part.toBuffer();
        if (!audio.length) throw new ApiError(400, "audio_file_empty", "The uploaded audio file is empty.", false);
        const language = transcriptionLanguage(request.query);
        const form = new FormData();
        form.append("file", new Blob([new Uint8Array(audio)], { type: part.mimetype }), part.filename || "audio.mp3");
        form.append("model", config.groqModel);
        form.append("response_format", "verbose_json");
        form.append("timestamp_granularities[]", "word");
        form.append("timestamp_granularities[]", "segment");
        if (language !== "auto") form.append("language", language);
        const payload = await fetchJsonWithRetry(
          fetchImpl,
          config.groqTranscriptionUrl,
          { method: "POST", headers: { Authorization: `Bearer ${config.groqApiKey}` }, body: form },
          config,
          "transcription_upstream_failed",
          "The transcription provider rejected the audio request.",
          controller.signal,
        );
        try {
          return normalizeTranscriptionPayload(payload, language);
        } catch (error) {
          if (error instanceof z.ZodError) {
            throw new ApiError(502, "invalid_transcription_response", "The transcription provider returned an invalid or untimed transcript.", true);
          }
          throw error;
        }
      }, controller.signal);
    } finally {
      controller.abort();
    }
  });

  app.post("/v1/highlights", { preHandler: authenticate }, async (request) => {
    if (!config.aiGatewayUrl || !config.aiGatewayApiKey || !config.aiGatewayModel) {
      throw new ApiError(503, "ai_gateway_not_configured", "The highlight model is not configured.", false);
    }
    let highlightRequest: HighlightRequest;
    try {
      highlightRequest = parseHighlightRequest(request.body);
    } catch (error) {
      if (error instanceof z.ZodError) {
        throw new ApiError(422, "invalid_highlight_request", "Timed transcript segments, source duration, and valid settings are required.", false, zodDetails(error));
      }
      throw error;
    }
    const windows = createHighlightWindows(
      highlightRequest.segments,
      highlightRequest.sourceDurationSeconds,
      config.highlightWindowSeconds,
      config.highlightOverlapSeconds,
    );
    if (!windows.length) throw new ApiError(422, "empty_timed_transcript", "No timed transcript content overlaps the source.", false);

    const controller = requestAbortController(request);
    try {
      const candidateGroups = await mapConcurrent(windows, config.highlightConcurrency, (window) =>
        highlightLimiter.run(
          () => requestWindowHighlights(fetchImpl, config, highlightRequest, window, controller.signal),
          controller.signal,
        ),
      );
      const clips = rankAndDedupeCandidates(candidateGroups.flat(), highlightRequest.settings.clipCount);
      return {
        clips,
        meta: {
          windowsProcessed: windows.length,
          candidatesBeforeDedupe: candidateGroups.reduce((total, group) => total + group.length, 0),
        },
      };
    } catch (error) {
      controller.abort();
      throw error;
    } finally {
      controller.abort();
    }
  });

  return app;
}

async function requestWindowHighlights(
  fetchImpl: typeof fetch,
  config: GatewayConfig,
  request: HighlightRequest,
  window: HighlightWindow,
  signal: AbortSignal,
) {
  const segments = window.segments.map(({ text, startSeconds, endSeconds }) => ({ text, startSeconds, endSeconds }));
  const prompt = {
    sourceDurationSeconds: request.sourceDurationSeconds,
    window: { startSeconds: window.startSeconds, endSeconds: window.endSeconds },
    settings: request.settings,
    segments,
  };
  const payload = await fetchJsonWithRetry(
    fetchImpl,
    config.aiGatewayUrl!,
    {
      method: "POST",
      headers: { "content-type": "application/json", Authorization: `Bearer ${config.aiGatewayApiKey}` },
      body: JSON.stringify({
        model: config.aiGatewayModel,
        stream: false,
        temperature: 0.2,
        response_format: { type: "json_object" },
        messages: [
          {
            role: "system",
            content: [
              "Select self-contained, compelling clips only from the supplied timed window.",
              "Return one JSON object with a clips array and no prose.",
              "Each clip must contain numeric startSeconds/endSeconds, title, hook, reason, and an integer score from 0 to 100.",
              "Every clip must stay inside the supplied window and requested duration range. Use absolute source timestamps.",
              "Return at most the requested clipCount candidates, or an empty array when no honest candidate exists.",
            ].join(" "),
          },
          { role: "user", content: JSON.stringify(prompt) },
        ],
      }),
    },
    config,
    "highlight_upstream_failed",
    "The highlight model could not process a transcript window.",
    signal,
  );
  let envelope: unknown;
  try {
    envelope = extractHighlightEnvelope(payload);
  } catch {
    throw new ApiError(502, "invalid_highlight_response", "The highlight model returned malformed JSON.", true, { windowIndex: window.index });
  }
  try {
    return validateWindowCandidates(envelope, window, request);
  } catch (error) {
    if (error instanceof z.ZodError) {
      throw new ApiError(502, "invalid_highlight_response", "The highlight model returned invalid candidate data or timestamps.", true, { windowIndex: window.index });
    }
    throw error;
  }
}

function extractHighlightEnvelope(payload: unknown): unknown {
  if (isRecord(payload) && Array.isArray(payload.clips)) return payload;
  if (!isRecord(payload) || !Array.isArray(payload.choices) || !payload.choices.length) {
    throw new Error("OpenAI-compatible choices are missing.");
  }
  const choice = payload.choices[0];
  if (!isRecord(choice) || !isRecord(choice.message)) throw new Error("Assistant message is missing.");
  const content = choice.message.content;
  const text = typeof content === "string"
    ? content
    : Array.isArray(content)
      ? content.map((part) => (isRecord(part) && typeof part.text === "string" ? part.text : "")).join("")
      : "";
  if (!text.trim()) throw new Error("Assistant content is empty.");
  return JSON.parse(text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "")) as unknown;
}

async function fetchJsonWithRetry(
  fetchImpl: typeof fetch,
  url: string,
  init: RequestInit,
  config: GatewayConfig,
  errorCode: string,
  errorMessage: string,
  outerSignal: AbortSignal,
): Promise<unknown> {
  const startedAt = Date.now();
  for (let attempt = 0; attempt <= config.upstreamRetryCount; attempt += 1) {
    if (outerSignal.aborted) throw new ApiError(499, "request_aborted", "The client disconnected.", false);
    const remainingMs = config.upstreamTotalTimeoutMs - (Date.now() - startedAt);
    if (remainingMs <= 0) throw new ApiError(502, errorCode, "The upstream provider exceeded the gateway deadline.", true);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.min(config.upstreamTimeoutMs, remainingMs));
    const signal = AbortSignal.any([outerSignal, controller.signal]);
    try {
      const response = await fetchImpl(url, { ...init, signal });
      const payload = await readJsonResponse(response);
      if (response.ok) return payload;
      const retryable = isRetryableStatus(response.status);
      const upstreamError = new ApiError(
        response.status === 429 ? 429 : 502,
        errorCode,
        errorMessage,
        retryable,
        { upstreamStatus: response.status },
      );
      if (!retryable || attempt === config.upstreamRetryCount) throw upstreamError;
      await waitForRetry(response.headers.get("retry-after"), startedAt, config, outerSignal, attempt);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (outerSignal.aborted) throw new ApiError(499, "request_aborted", "The client disconnected.", false);
      if (error instanceof Error && error.name === "AbortError") {
        throw new ApiError(502, errorCode, "The upstream provider timed out.", true);
      }
      if (attempt === config.upstreamRetryCount) {
        throw new ApiError(502, errorCode, "The upstream provider could not be reached.", true);
      }
      await waitForRetry(null, startedAt, config, outerSignal, attempt);
    } finally {
      clearTimeout(timeout);
    }
  }
  throw new ApiError(502, errorCode, errorMessage, true);
}

async function waitForRetry(
  retryAfter: string | null,
  startedAt: number,
  config: GatewayConfig,
  signal: AbortSignal,
  attempt: number,
) {
  const fallbackMs = 500 * (attempt + 1) + Math.floor(Math.random() * 250);
  const requestedMs = retryAfterToMs(retryAfter) ?? fallbackMs;
  const remainingMs = config.upstreamTotalTimeoutMs - (Date.now() - startedAt);
  if (requestedMs >= remainingMs) throw new ApiError(502, "upstream_deadline_exceeded", "The upstream provider exceeded the gateway deadline.", true);
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, requestedMs);
    const onAbort = () => {
      clearTimeout(timer);
      const error = new Error("Request aborted");
      error.name = "AbortError";
      reject(error);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) {
    throw new ApiError(502, "empty_upstream_response", "The upstream provider returned an empty response.", true, { upstreamStatus: response.status });
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError(502, "malformed_upstream_response", "The upstream provider returned malformed JSON.", true, { upstreamStatus: response.status });
  }
}

async function mapConcurrent<T, R>(items: T[], concurrency: number, operation: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  const worker = async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await operation(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => worker()));
  return results;
}

function sendError(
  reply: FastifyReply,
  statusCode: number,
  code: string,
  message: string,
  retryable: boolean,
  details?: unknown,
  retryAfterSeconds?: number,
) {
  if (retryAfterSeconds) reply.header("Retry-After", String(retryAfterSeconds));
  const payload: ApiErrorPayload = {
    error: { code, message, retryable, ...(details === undefined ? {} : { details }) },
  };
  return reply.code(statusCode).send(payload);
}

function requestAbortController(request: FastifyRequest) {
  const controller = new AbortController();
  request.raw.once("aborted", () => controller.abort());
  request.raw.once("close", () => {
    if (!request.raw.complete) controller.abort();
  });
  return controller;
}

function transcriptionLanguage(query: unknown) {
  const parsed = z.object({ language: z.string().trim().min(2).max(16).optional() }).safeParse(query);
  if (!parsed.success) throw new ApiError(422, "invalid_language", "The transcription language query is invalid.", false, zodDetails(parsed.error));
  return parsed.data.language ?? "auto";
}

function validateProductionConfig(config: GatewayConfig) {
  const missing = [
    ["GROQ_API_KEY", config.groqApiKey],
    ["AI_GATEWAY_URL", config.aiGatewayUrl],
    ["AI_GATEWAY_API_KEY", config.aiGatewayApiKey],
    ["AI_GATEWAY_MODEL", config.aiGatewayModel],
    ["ALLOWED_PUBLIC_HOST", config.allowedPublicHost],
    ["GATEWAY_DB_PATH", config.dbPath],
  ].filter(([, value]) => !value || isPlaceholder(value)).map(([name]) => name);
  if (missing.length) throw new Error(`Production gateway configuration is missing: ${missing.join(", ")}.`);
  if (config.devBearerToken || config.inviteCodes.length) {
    throw new Error("GATEWAY_DEV_BEARER_TOKEN and INVITE_CODES must be empty in production.");
  }
  if (!isLoopbackHost(config.host)) throw new Error("HOST must be a loopback address in production.");
  for (const [name, value] of [["GROQ_TRANSCRIPTION_URL", config.groqTranscriptionUrl], ["AI_GATEWAY_URL", config.aiGatewayUrl!] as const]) {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:") throw new Error(`${name} must use HTTPS in production.`);
  }
}

function zodDetails(error: z.ZodError) {
  return { issues: error.issues.map(({ code, path, message }) => ({ code, path, message })) };
}

function safeTokenEqual(left: string, right: string) {
  const leftHash = Buffer.from(createHash("sha256").update(left).digest("hex"), "hex");
  const rightHash = Buffer.from(createHash("sha256").update(right).digest("hex"), "hex");
  return timingSafeEqual(leftHash, rightHash);
}

function isRetryableStatus(status: number) {
  return status === 408 || status === 425 || status === 429 || status === 500 || status === 502 || status === 503 || status === 504;
}

function retryAfterToMs(value: string | null) {
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.round(seconds * 1_000);
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
}

function isLocalHealthRoute(url: string) {
  const path = url.split("?", 1)[0];
  return path === "/livez" || path === "/readyz" || path === "/health";
}

function isLoopbackHost(value: string) {
  return value === "127.0.0.1" || value === "::1" || value.toLowerCase() === "localhost";
}

function isPlaceholder(value: string) {
  return /replace-with|<[^>]+>|your[_ -]?|changeme/i.test(value);
}

function splitCsv(value: string | undefined) {
  return (value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

function cleanOptional(value: string | undefined) {
  const cleaned = value?.trim();
  return cleaned || undefined;
}

function parseInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
  name: string,
  strict: boolean,
) {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  if (Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum) return parsed;
  if (strict) throw new Error(`${name} must be an integer between ${minimum} and ${maximum}.`);
  return fallback;
}

function parseLogLevel(value: string | undefined): GatewayConfig["logLevel"] {
  return value === "debug" || value === "warn" || value === "error" ? value : "info";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
