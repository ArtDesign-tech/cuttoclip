# PyInstaller spec for the CutToClip local worker (onedir).
#
# Entry point is app/__main__.py, which runs uvicorn on CUTTOCLIP_PORT. onedir
# (not onefile) is used so native deps (onnxruntime, opencv) and the bundled
# fonts stay discoverable via __file__ and load fast without per-launch unpack.
#
# Build:  python -m PyInstaller apps/worker/cuttoclip-worker.spec
# Output: dist/local-worker/local-worker(.exe) + its _internal/ payload.

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# onnxruntime ships native DLLs + data that must be collected wholesale.
ort_datas, ort_binaries, ort_hiddenimports = collect_all("onnxruntime")

# yt-dlp loads its hundreds of extractors via importlib, so static analysis
# misses them — collect the whole package or YouTube/URL downloads silently
# break (worker reports ytDlp:false and raises YTDLP_UNAVAILABLE at runtime).
ydl_datas, ydl_binaries, ydl_hiddenimports = collect_all("yt_dlp")

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # Bundle the whole worker package so its relative imports resolve when frozen.
    *collect_submodules("app"),
    *ort_hiddenimports,
    *collect_submodules("numpy"),
    *ydl_hiddenimports,
]

# Bundle the Inter font next to the frozen app so captions.py can find it at
# app/assets/fonts (its __file__-relative path is preserved under _internal).
datas = [
    ("app/assets/fonts", "app/assets/fonts"),
    *ort_datas,
    *ydl_datas,
]

a = Analysis(
    ["run_worker.py"],
    pathex=["."],
    binaries=[*ort_binaries, *ydl_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="local-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="local-worker",
)
