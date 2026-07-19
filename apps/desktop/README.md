# CutToClip Desktop

This is the Tauri 2 shell around the React studio in `apps/web`.

The release packaging step places a PyInstaller-built worker at:

```text
apps/desktop/src-tauri/binaries/local-worker-<target-triple>.exe
```

The development Tauri config intentionally omits `externalBin` until that binary exists, so a clean checkout can compile. The release script should add the sidecar entry and `shell:allow-spawn` capability after building the worker; the shell already exposes a `start_worker` command for that release path. During development the web studio can run independently with `npm run dev` from the repository root.
