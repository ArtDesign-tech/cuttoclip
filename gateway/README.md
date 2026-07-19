# CutToClip gateway

The gateway keeps provider credentials away from the local worker. Audio chunks are sent to the configured Groq transcription endpoint; only timed transcript windows are sent to the configured OpenAI-compatible highlight endpoint.

## Closed-beta production model

The gateway binds only to loopback and is intended to be published through Cloudflare Tunnel. In production it rejects shared development bearer tokens and plaintext environment invites. Installation tokens and one-use invite state are persisted in SQLite, so restart does not invalidate testers or reopen an invite.

Copy `.env.example` to `.env`, fill in the provider settings, then build and run:

```powershell
npm.cmd run test --workspace @cuttoclip/gateway
npm.cmd run build --workspace @cuttoclip/gateway
npm.cmd run admin --workspace @cuttoclip/gateway -- invite create --label tester-01 --expires-hours 168
npm.cmd run start --workspace @cuttoclip/gateway
```

The admin CLI prints an invite once. Share it over a secure channel. `installation list` and `installation revoke --id <uuid>` are local-only operations; there is no public admin endpoint.

`/livez` and `/health` report process liveness. `/readyz` verifies configuration and SQLite without making a billable provider request.

## HTTP contract

Authenticated routes use `Authorization: Bearer <token>`. Every error has the same shape:

```json
{
  "error": {
    "code": "invalid_highlight_request",
    "message": "Timed transcript segments, source duration, and valid settings are required.",
    "retryable": false,
    "details": {}
  }
}
```

`POST /v1/transcriptions?language=auto` accepts one multipart `file` up to 25 MB and returns:

```json
{
  "text": "Full transcript",
  "language": "en",
  "duration": 42.5,
  "words": [{ "text": "Full", "startSeconds": 0.1, "endSeconds": 0.4 }],
  "segments": [{
    "text": "Full transcript",
    "startSeconds": 0.1,
    "endSeconds": 1.2,
    "words": [{ "text": "Full", "startSeconds": 0.1, "endSeconds": 0.4 }]
  }]
}
```

`POST /v1/highlights` accepts:

```json
{
  "transcript": "Full transcript (optional when segments contain all text)",
  "sourceDurationSeconds": 1500,
  "segments": [{ "text": "Timed sentence", "startSeconds": 12.1, "endSeconds": 16.8, "words": [] }],
  "settings": {
    "clipCount": 3,
    "minDurationSeconds": 15,
    "maxDurationSeconds": 90,
    "language": "auto"
  }
}
```

It returns `{ "clips": [...], "meta": { ... } }`. Each clip contains `id`, `startSeconds`, `endSeconds`, `title`, `hook`, `reason`, integer `score`, `source: "ai"`, and `accent`. For long sources, the gateway scans 12-minute windows with 30-second overlap, validates model timestamps and durations, removes candidates whose overlap exceeds 50% of the shorter clip, and returns the globally highest scores. `sourceDuration` is accepted as a compatibility alias for `sourceDurationSeconds`.

## Load and secrecy controls

- Global upstream concurrency is configured independently for transcriptions and highlight windows; queue overflow returns `503 gateway_busy` with `Retry-After`.
- Upstream retries cover transient connection failures and `408/425/429/500/502/503/504` only. Total upstream time stays below Cloudflare's default proxy deadline.
- Logs redact gateway bearer tokens, Cloudflare Access credentials, request bodies, and provider secrets. Do not use verbose HTTP clients against the live gateway.
