import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildApp, loadConfig, type GatewayConfig } from "../src/app.js";
import {
  createHighlightWindows,
  normalizeTranscriptionPayload,
  overlapRatio,
  parseHighlightRequest,
  rankAndDedupeCandidates,
  type HighlightCandidate,
  type TranscriptSegment,
} from "../src/domain.js";

function testConfig(overrides: Partial<GatewayConfig> = {}): GatewayConfig {
  return {
    ...loadConfig({}),
    nodeEnv: "test",
    inviteCodes: ["valid-invite"],
    devBearerToken: "development-secret",
    groqApiKey: "groq-test-key",
    aiGatewayUrl: "https://model.test/v1/chat/completions",
    aiGatewayApiKey: "model-test-key",
    aiGatewayModel: "test-model",
    dbPath: ":memory:",
    upstreamRetryCount: 0,
    upstreamTimeoutMs: 2_000,
    upstreamTotalTimeoutMs: 2_000,
    ...overrides,
  };
}

test("normalizes Groq word/segment timing into the public transcript contract", () => {
  const transcript = normalizeTranscriptionPayload({
    text: "Hello world",
    language: "en",
    duration: 2.4,
    words: [
      { word: "Hello", start: 0.1, end: 0.7 },
      { word: "world", start: 0.8, end: 1.3 },
    ],
    segments: [{ text: "Hello world", start: 0.1, end: 1.3 }],
  });

  assert.deepEqual(transcript, {
    text: "Hello world",
    language: "en",
    duration: 2.4,
    words: [
      { text: "Hello", startSeconds: 0.1, endSeconds: 0.7 },
      { text: "world", startSeconds: 0.8, endSeconds: 1.3 },
    ],
    segments: [
      {
        text: "Hello world",
        startSeconds: 0.1,
        endSeconds: 1.3,
        words: [
          { text: "Hello", startSeconds: 0.1, endSeconds: 0.7 },
          { text: "world", startSeconds: 0.8, endSeconds: 1.3 },
        ],
      },
    ],
  });
});

test("accepts canonical timed highlights and the sourceDuration compatibility alias", () => {
  const request = parseHighlightRequest({
    transcript: "A useful thought",
    sourceDuration: 45,
    segments: [{ text: "A useful thought", start: 5, end: 35, words: [] }],
    settings: {
      clipCount: 2,
      minDurationSeconds: 15,
      maxDurationSeconds: 45,
      language: "en",
    },
  });

  assert.equal(request.sourceDurationSeconds, 45);
  assert.deepEqual(request.segments[0], {
    text: "A useful thought",
    startSeconds: 5,
    endSeconds: 35,
    words: [],
  });
});

test("creates 12-minute windows with 30-second overlap and skips silent windows", () => {
  const segments: TranscriptSegment[] = [
    { text: "one", startSeconds: 5, endSeconds: 35, words: [] },
    { text: "two", startSeconds: 700, endSeconds: 740, words: [] },
    { text: "three", startSeconds: 1_400, endSeconds: 1_450, words: [] },
  ];
  const windows = createHighlightWindows(segments, 1_500);
  assert.deepEqual(windows.map(({ startSeconds, endSeconds }) => [startSeconds, endSeconds]), [
    [0, 720],
    [690, 1_410],
    [1_380, 1_500],
  ]);
});

test("deduplicates candidates with more than 50 percent shorter-clip overlap before ranking", () => {
  const candidates: HighlightCandidate[] = [
    { startSeconds: 10, endSeconds: 40, title: "A", hook: "A", reason: "A", score: 80 },
    { startSeconds: 12, endSeconds: 42, title: "A better", hook: "A", reason: "A", score: 95 },
    { startSeconds: 70, endSeconds: 100, title: "B", hook: "B", reason: "B", score: 90 },
  ];
  assert.ok(overlapRatio(candidates[0], candidates[1]) > 0.5);
  const ranked = rankAndDedupeCandidates(candidates, 3);
  assert.deepEqual(ranked.map(({ title, score }) => [title, score]), [
    ["A better", 95],
    ["B", 90],
  ]);
  assert.deepEqual(ranked.map(({ id, source }) => [id, source]), [
    ["clip-01", "ai"],
    ["clip-02", "ai"],
  ]);
});

test("developer bearer token works outside production and all validation errors use the common shape", async (t) => {
  const app = await buildApp({ config: testConfig(), logger: false });
  t.after(() => app.close());

  const response = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: "Bearer development-secret" },
    payload: {},
  });
  assert.equal(response.statusCode, 422);
  assert.deepEqual(Object.keys(response.json().error).sort(), ["code", "details", "message", "retryable"]);
  assert.equal(response.json().error.code, "invalid_highlight_request");
  assert.equal(response.json().error.retryable, false);
});

test("transcription endpoint normalizes the configured provider response", async (t) => {
  let forwardedLanguage: FormDataEntryValue | null = null;
  const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
    assert.ok(init?.body instanceof FormData);
    forwardedLanguage = init.body.get("language");
    return new Response(JSON.stringify({
      text: "Halo dunia",
      language: "id",
      duration: 1.5,
      words: [
        { word: "Halo", start: 0, end: 0.5 },
        { word: "dunia", start: 0.6, end: 1.2 },
      ],
      segments: [{ text: "Halo dunia", start: 0, end: 1.2 }],
    }), { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
  const app = await buildApp({ config: testConfig(), fetchImpl, logger: false });
  t.after(() => app.close());
  const boundary = "cuttoclip-test-boundary";
  const multipartBody = Buffer.from([
    `--${boundary}`,
    'Content-Disposition: form-data; name="file"; filename="chunk.mp3"',
    "Content-Type: audio/mpeg",
    "",
    "test-audio",
    `--${boundary}--`,
    "",
  ].join("\r\n"));

  const response = await app.inject({
    method: "POST",
    url: "/v1/transcriptions?language=id",
    headers: {
      authorization: "Bearer development-secret",
      "content-type": `multipart/form-data; boundary=${boundary}`,
      "content-length": String(multipartBody.length),
    },
    payload: multipartBody,
  });

  assert.equal(response.statusCode, 200);
  assert.equal(forwardedLanguage, "id");
  assert.deepEqual(response.json(), {
    text: "Halo dunia",
    language: "id",
    duration: 1.5,
    words: [
      { text: "Halo", startSeconds: 0, endSeconds: 0.5 },
      { text: "dunia", startSeconds: 0.6, endSeconds: 1.2 },
    ],
    segments: [{
      text: "Halo dunia",
      startSeconds: 0,
      endSeconds: 1.2,
      words: [
        { text: "Halo", startSeconds: 0, endSeconds: 0.5 },
        { text: "dunia", startSeconds: 0.6, endSeconds: 1.2 },
      ],
    }],
  });
});

test("developer bearer token is rejected in production while activation-issued tokens remain valid", async (t) => {
  const app = await buildApp({ config: testConfig({ nodeEnv: "production", allowedPublicHost: "gateway.test" }), logger: false });
  t.after(() => app.close());

  const rejected = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: "Bearer development-secret", host: "gateway.test", "x-forwarded-proto": "https" },
    payload: {},
  });
  assert.equal(rejected.statusCode, 401);
  assert.deepEqual(rejected.json(), {
    error: {
      code: "invalid_installation",
      message: "A valid installation bearer token is required.",
      retryable: false,
    },
  });

  const activation = await app.inject({
    method: "POST",
    url: "/v1/activate",
    headers: { host: "gateway.test", "x-forwarded-proto": "https" },
    payload: { inviteCode: "valid-invite" },
  });
  assert.equal(activation.statusCode, 200);
  const token = activation.json().token as string;
  const accepted = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: `Bearer ${token}`, host: "gateway.test", "x-forwarded-proto": "https" },
    payload: {},
  });
  assert.equal(accepted.statusCode, 422);
});

test("highlight endpoint processes long transcripts window-by-window and returns globally ranked clips", async (t) => {
  const calls: Array<{ stream: boolean; messages: Array<{ content: string }> }> = [];
  const fetchImpl = (async (_url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { stream: boolean; messages: Array<{ content: string }> };
    calls.push(body);
    const prompt = JSON.parse(body.messages[1].content) as { window: { startSeconds: number; endSeconds: number } };
    const start = prompt.window.startSeconds + 10;
    const scores: Record<number, number> = { 0: 80, 690: 95, 1_380: 90 };
    const content = JSON.stringify({
      clips: [{
        startSeconds: start,
        endSeconds: start + 30,
        title: `Window ${prompt.window.startSeconds}`,
        hook: "A complete thought",
        reason: "Clear payoff",
        score: scores[prompt.window.startSeconds],
      }],
    });
    return new Response(JSON.stringify({ choices: [{ message: { content } }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  const app = await buildApp({ config: testConfig(), fetchImpl, logger: false });
  t.after(() => app.close());

  const response = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: "Bearer development-secret" },
    payload: {
      transcript: "Long transcript",
      sourceDurationSeconds: 1_500,
      segments: [
        { text: "first idea", startSeconds: 5, endSeconds: 100 },
        { text: "second idea", startSeconds: 700, endSeconds: 800 },
        { text: "third idea", startSeconds: 1_390, endSeconds: 1_480 },
      ],
      settings: { clipCount: 3, minDurationSeconds: 15, maxDurationSeconds: 90, language: "en" },
    },
  });

  assert.equal(response.statusCode, 200);
  assert.equal(calls.length, 3);
  assert.ok(calls.every((call) => call.stream === false));
  const payload = response.json();
  assert.deepEqual(payload.clips.map((clip: { title: string }) => clip.title), ["Window 690", "Window 1380", "Window 0"]);
  assert.equal(payload.meta.windowsProcessed, 3);
});

test("malformed model JSON becomes an explicit retryable structured error", async (t) => {
  const fetchImpl = (async () => new Response(JSON.stringify({ choices: [{ message: { content: "not-json" } }] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  })) as typeof fetch;
  const app = await buildApp({ config: testConfig(), fetchImpl, logger: false });
  t.after(() => app.close());

  const response = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: "Bearer development-secret" },
    payload: {
      sourceDurationSeconds: 60,
      segments: [{ text: "A complete idea", startSeconds: 0, endSeconds: 60 }],
      settings: { clipCount: 1, minDurationSeconds: 15, maxDurationSeconds: 60, language: "en" },
    },
  });

  assert.equal(response.statusCode, 502);
  assert.equal(response.json().error.code, "invalid_highlight_response");
  assert.equal(response.json().error.retryable, true);
});

test("upstream rate limits preserve retryability in the common error contract", async (t) => {
  const fetchImpl = (async () => new Response(JSON.stringify({ error: { message: "slow down" } }), {
    status: 429,
    headers: { "content-type": "application/json" },
  })) as typeof fetch;
  const app = await buildApp({ config: testConfig(), fetchImpl, logger: false });
  t.after(() => app.close());

  const response = await app.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { authorization: "Bearer development-secret" },
    payload: {
      sourceDurationSeconds: 60,
      segments: [{ text: "A complete idea", startSeconds: 0, endSeconds: 60 }],
      settings: { clipCount: 1, minDurationSeconds: 15, maxDurationSeconds: 60, language: "en" },
    },
  });
  assert.equal(response.statusCode, 429);
  assert.deepEqual(response.json().error, {
    code: "highlight_upstream_failed",
    message: "The highlight model could not process a transcript window.",
    retryable: true,
    details: { upstreamStatus: 429 },
  });
});

test("production configuration fails fast for missing secrets, dev auth, and invalid numeric settings", () => {
  assert.throws(() => loadConfig({ NODE_ENV: "production" }), /GROQ_API_KEY/);
  assert.throws(() => loadConfig({
    NODE_ENV: "production",
    HOST: "127.0.0.1",
    GROQ_API_KEY: "groq",
    AI_GATEWAY_URL: "https://model.test/v1/chat/completions",
    AI_GATEWAY_API_KEY: "key",
    AI_GATEWAY_MODEL: "model",
    ALLOWED_PUBLIC_HOST: "gateway.test",
    GATEWAY_DEV_BEARER_TOKEN: "forbidden",
  }), /GATEWAY_DEV_BEARER_TOKEN/);
  assert.throws(() => loadConfig({
    NODE_ENV: "production",
    HOST: "127.0.0.1",
    GROQ_API_KEY: "groq",
    AI_GATEWAY_URL: "https://model.test/v1/chat/completions",
    AI_GATEWAY_API_KEY: "key",
    AI_GATEWAY_MODEL: "model",
    ALLOWED_PUBLIC_HOST: "gateway.test",
    UPSTREAM_TIMEOUT_MS: "not-a-number",
  }), /UPSTREAM_TIMEOUT_MS/);
});

test("persistent activation survives restart and never reopens a consumed invite", async (t) => {
  const directory = mkdtempSync(join(tmpdir(), "cuttoclip-gateway-"));
  const dbPath = join(directory, "gateway.sqlite3");
  const config = testConfig({ nodeEnv: "production", dbPath, allowedPublicHost: "gateway.test" });
  const headers = { host: "gateway.test", "x-forwarded-proto": "https" };
  const first = await buildApp({ config, logger: false });
  const activation = await first.inject({ method: "POST", url: "/v1/activate", headers, payload: { inviteCode: "valid-invite" } });
  assert.equal(activation.statusCode, 200);
  const token = activation.json().token as string;
  await first.close();

  const restarted = await buildApp({ config, logger: false });
  t.after(async () => {
    await restarted.close();
    rmSync(directory, { recursive: true, force: true });
  });
  const accepted = await restarted.inject({
    method: "POST",
    url: "/v1/highlights",
    headers: { ...headers, authorization: `Bearer ${token}` },
    payload: {},
  });
  assert.equal(accepted.statusCode, 422);
  const reused = await restarted.inject({ method: "POST", url: "/v1/activate", headers, payload: { inviteCode: "valid-invite" } });
  assert.equal(reused.statusCode, 401);
});

test("production routes require the configured HTTPS hostname while readiness stays local", async (t) => {
  const app = await buildApp({ config: testConfig({ nodeEnv: "production", allowedPublicHost: "gateway.test" }), logger: false });
  t.after(() => app.close());
  const rejected = await app.inject({ method: "POST", url: "/v1/activate", payload: { inviteCode: "valid-invite" } });
  assert.equal(rejected.statusCode, 421);
  const ready = await app.inject({ method: "GET", url: "/readyz" });
  assert.equal(ready.statusCode, 200);
  assert.equal(ready.json().status, "ready");
});

test("global upstream queue rejects overflow with Retry-After", async (t) => {
  let release!: () => void;
  let started!: () => void;
  const startedPromise = new Promise<void>((resolve) => { started = resolve; });
  const heldResponse = new Promise<Response>((resolve) => { release = () => resolve(new Response(JSON.stringify({
    text: "ok", language: "en", duration: 1,
    words: [{ word: "ok", start: 0, end: 1 }], segments: [{ text: "ok", start: 0, end: 1 }],
  }), { status: 200, headers: { "content-type": "application/json" } })); });
  const fetchImpl = (async () => {
    started();
    return heldResponse;
  }) as typeof fetch;
  const app = await buildApp({
    config: testConfig({ transcriptionGlobalConcurrency: 1, upstreamQueueLimit: 0 }),
    fetchImpl,
    logger: false,
  });
  t.after(() => app.close());
  const body = multipartFixture();
  const first = app.inject({ method: "POST", url: "/v1/transcriptions", headers: body.headers, payload: body.payload });
  await startedPromise;
  const overflow = await app.inject({ method: "POST", url: "/v1/transcriptions", headers: body.headers, payload: body.payload });
  assert.equal(overflow.statusCode, 503);
  assert.equal(overflow.headers["retry-after"], "30");
  assert.equal(overflow.json().error.code, "gateway_busy");
  release();
  assert.equal((await first).statusCode, 200);
});

test("GET /v1/me rejects a request without a token", async (t) => {
  const app = await buildApp({ config: testConfig(), logger: false });
  t.after(() => app.close());
  const response = await app.inject({ method: "GET", url: "/v1/me" });
  assert.equal(response.statusCode, 401);
  assert.equal(response.json().error.code, "invalid_installation");
});

test("GET /v1/me returns installation identity with no quota field", async (t) => {
  const app = await buildApp({ config: testConfig(), logger: false });
  t.after(() => app.close());

  const activation = await app.inject({
    method: "POST",
    url: "/v1/activate",
    payload: { inviteCode: "valid-invite" },
  });
  assert.equal(activation.statusCode, 200);
  const token = activation.json().token as string;

  const me = await app.inject({
    method: "GET",
    url: "/v1/me",
    headers: { authorization: `Bearer ${token}` },
  });
  assert.equal(me.statusCode, 200);
  const body = me.json();
  assert.equal(typeof body.installationId, "string");
  // Seeded invites default to the "development" label (see seedInvite).
  assert.equal(body.label, "development");
  assert.equal(typeof body.createdAt, "string");
  // No quota surfaced: the Managed Beta runs without daily caps.
  assert.equal("quota" in body, false);
  assert.equal("remaining" in body, false);
  assert.equal("dailyQuota" in body, false);
});

test("GET /v1/me accepts the developer bearer token outside production", async (t) => {
  const app = await buildApp({ config: testConfig(), logger: false });
  t.after(() => app.close());
  const me = await app.inject({
    method: "GET",
    url: "/v1/me",
    headers: { authorization: "Bearer development-secret" },
  });
  assert.equal(me.statusCode, 200);
  assert.equal(me.json().installationId, "development");
});

function multipartFixture() {
  const boundary = "cuttoclip-queue-boundary";
  const payload = Buffer.from([
    `--${boundary}`,
    'Content-Disposition: form-data; name="file"; filename="chunk.mp3"',
    "Content-Type: audio/mpeg",
    "",
    "test-audio",
    `--${boundary}--`,
    "",
  ].join("\r\n"));
  return {
    headers: {
      authorization: "Bearer development-secret",
      "content-type": `multipart/form-data; boundary=${boundary}`,
      "content-length": String(payload.length),
    },
    payload,
  };
}
