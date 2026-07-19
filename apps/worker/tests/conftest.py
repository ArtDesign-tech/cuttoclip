from __future__ import annotations

import sys
from pathlib import Path

import pytest


WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app import main
    from app.storage import ProjectStore

    replacement = ProjectStore(tmp_path / "data", tmp_path / "videos")
    monkeypatch.setattr(main, "store", replacement)
    replacement.load()
    return replacement

