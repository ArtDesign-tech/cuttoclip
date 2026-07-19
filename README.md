# CutToClip

CutToClip is a local-first AI clipping studio for Windows. A React studio talks to a local FastAPI worker for media processing, while a small Fastify gateway keeps AI credentials off the user's machine. Source video stays local; compressed audio chunks are sent to the configured transcription service.

## What works

- Local MP4/MOV upload and public YouTube intake, up to two hours.
- Timestamped transcription in 20-minute audio chunks with overlap merging.
- AI highlight selection, editable trim points, and manually added moments.
- Portrait, landscape, and face-following Smart portrait render modes.
- Clean, Bold Focus, Karaoke, and Subtitle Box captions burned into MP4 output.
- Persistent projects and pollable prepare/analyze/render jobs with real progress, cancellation, retryable errors, and partial-render results.
- Output gallery backed by files in `Videos\CutToClip\<project>`.

## Install

Install Node dependencies from PowerShell:

```powershell
npm.cmd install
```

Create the Python environment and install the worker plus test dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps\worker\requirements.txt -r apps\worker\requirements-dev.txt
```

FFmpeg and FFprobe must be available on `PATH`. yt-dlp is installed by the worker requirements and is required only for YouTube sources.

## Configure the closed-beta gateway

The public gateway is a production-only closed beta. It uses SQLite-backed installation tokens, a Cloudflare Access service token, and live Groq/9router credentials that must never enter source control.

Create the ignored gateway environment file:

```powershell
Copy-Item gateway\.env.example gateway\.env
```

Set the real values in `gateway\.env`:

- `ALLOWED_PUBLIC_HOST`: the future hostname, for example `gateway.example.com`.
- `GROQ_API_KEY`: used for timestamped Whisper transcription.
- `AI_GATEWAY_URL`, `AI_GATEWAY_API_KEY`, and `AI_GATEWAY_MODEL`: the exact 9router OpenAI-compatible Chat Completions endpoint and model.

Production intentionally rejects `GATEWAY_DEV_BEARER_TOKEN` and `INVITE_CODES`. Create one-time invites only through the local admin command after building the gateway:

```powershell
npm.cmd run build:gateway
npm.cmd run admin:gateway -- invite create --label tester-01 --expires-hours 168
```

The gateway writes only hashed invite and installation tokens to `%ProgramData%\CutToClip\Gateway\gateway.sqlite3`.

## Run the real pipeline

The Cloudflare hostname, domain purchase, Tunnel, Access application, service token, WAF rule, and provider spend caps are operator-side prerequisites. See [the tunnel runbook](docs/CLOUDFLARE_TUNNEL.md).

```powershell
# Terminal 1: manually run the production gateway
.\scripts\start-gateway.ps1
```

```powershell
# Terminal 2: one-time tester activation, then start the local media worker
.\scripts\onboard-tester.ps1 -GatewayUrl "https://gateway.example.com" -AccessClientId "<cloudflare-client-id>"
.\scripts\start-worker.ps1
```

```powershell
# Terminal 3: web studio
npm.cmd run dev
```

Open `http://localhost:5173`. The studio reports live worker, FFmpeg, yt-dlp, and gateway capabilities instead of assuming they are online.

## Explicit UI demo mode

The real client never substitutes demo data after an API failure. To work on the UI without a worker, opt in before starting Vite:

```powershell
$env:VITE_DEMO_MODE = "true"
npm.cmd run dev
```

The app displays a visible Demo mode badge while this adapter is active.

## Verify

```powershell
.\.venv\Scripts\Activate.ps1
npm.cmd run check
npm.cmd run build
cargo check --offline --manifest-path apps\desktop\src-tauri\Cargo.toml
```

The Tauri shell remains compile-ready, but worker sidecar packaging and the Windows installer are intentionally the next milestone. See `apps\desktop\README.md` for the current packaging hook.
