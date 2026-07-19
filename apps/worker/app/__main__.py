"""Worker entrypoint that runs Uvicorn on a supervisor-chosen port.

The desktop supervisor picks a free loopback port and passes it as
``CUTTOCLIP_PORT``; the worker binds there so it never collides with another
instance. Falls back to 4317 for standalone/dev use. This module is the
PyInstaller ``onedir`` entry target as well as ``python -m app``.
"""

from __future__ import annotations

import os

import uvicorn

try:
    from .main import app
except ImportError:  # PyInstaller executes this as a top-level script.
    from main import app


def main() -> None:
    port = _port_from_env()
    host = os.getenv("CUTTOCLIP_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _port_from_env() -> int:
    raw = os.getenv("CUTTOCLIP_PORT", "").strip()
    if not raw:
        return 4317
    try:
        port = int(raw)
    except ValueError:
        return 4317
    return port if 1 <= port <= 65535 else 4317


if __name__ == "__main__":
    main()
