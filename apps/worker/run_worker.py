"""Frozen-worker entry point.

Keeps ``app`` as a proper Python package so its intra-package relative imports
(``from .main import app``, ``from .captions import ...``) resolve both when
frozen by PyInstaller and when run with ``python run_worker.py``.
"""

from app.__main__ import main

if __name__ == "__main__":
    main()
