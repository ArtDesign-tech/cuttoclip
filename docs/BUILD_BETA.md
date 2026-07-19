# Building CutToClip Beta (Windows)

End-to-end steps to produce the tester installer. Run on a Windows machine with
the build prerequisites; the desktop build cannot be produced in a CI/sandbox
without MSVC.

## Prerequisites

- **Rust + MSVC toolchain** (Visual Studio Build Tools with the C++ workload) —
  required by `tauri build`.
- **Node 24.x** and repo dependencies: `npm.cmd install`.
- **Python 3.11** with worker deps:
  `python -m pip install -r apps\worker\requirements.txt`.
- **Third-party binaries** placed under `tools/` (not committed):
  - `tools\ffmpeg\ffmpeg.exe`, `tools\ffmpeg\ffprobe.exe`
  - `tools\deno\deno.exe`

## 1. Build the runtime ZIP

Freezes the worker (PyInstaller onedir), assembles the layout the supervisor
expects (`worker/`, `ffmpeg/`, `deno/`), zips it, and writes the SHA-256 into
`runtime-lock.json`.

```powershell
.\scripts\build-runtime.ps1
```

Output: `release-artifacts\CutToClip-Runtime-v0.2.0-beta.1-windows-x64.zip` (+ `.sha256`).

## 2. Publish the runtime and pin its URL

1. Create a GitHub Release and upload the runtime ZIP.
2. Copy the asset's download URL into the `url` field of
   `apps\desktop\src-tauri\runtime-lock.json`. (`sha256`/`version` are already
   filled in by step 1.)

The app refuses to install a runtime when `url` is empty, so this step is required.

## 3. (Optional) Embed default API keys

For BYOK testers who should not enter their own keys, copy the template and fill
it in — the file is gitignored:

```powershell
Copy-Item scripts\build-beta.local.example.ps1 scripts\build-beta.local.ps1
# edit scripts\build-beta.local.ps1 with your Groq/Gemini keys
```

> Embedded keys can be extracted from the installer. Use only throwaway/temp
> keys you are willing to rotate.

**Turnkey behavior:** when both a Groq and a Gemini key are embedded, a fresh
install defaults to **BYOK mode with no gateway** — the worker calls Groq/Gemini
directly with the embedded keys, onboarding is skipped, and the operator gateway
is never contacted (you don't need to host it). A tester can still override the
keys or switch modes later in Settings; once they've made a choice, that choice
is honored over the embedded default.

## 4. Build the installer

```powershell
.\scripts\build-beta.ps1
```

This loads `build-beta.local.ps1` if present (embedding keys at compile time),
runs `tauri build` with the Beta config, and copies the result to
`release-artifacts\CutToClip-Beta-v0.2.0-beta.1-x64-setup.exe` (+ `.sha256`).

## 5. Distribute

Share the installer + its `.sha256`. The first Beta is **unsigned**, so Windows
SmartScreen will warn on first run ("More info" → "Run anyway"). Tell testers to
expect this.

## What the tester experiences

1. Install and launch → onboarding (choose Managed invite or BYOK keys; skipped
   if keys are embedded and a provider mode is preset).
2. App downloads the runtime ZIP from the Release, verifies SHA-256, extracts it
   to `%LOCALAPPDATA%\CutToClip Beta\runtime\<version>`.
3. Worker starts on a private port; the Create Clip screen appears.
4. Local video / YouTube → transcription → AI Moments → preview → render to
   `Videos\CutToClip Beta\<project>`.

## Notes / current limitations

- `restart_worker` accepts an optional `job_active` flag; wire the active-job
  check into the UI before exposing a provider switch mid-job.
- `package-worker.ps1` (the older `--onefile` sidecar packager) is superseded by
  `build-runtime.ps1` for the Beta; keep it only if you still build the Demo.
