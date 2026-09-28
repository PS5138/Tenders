"""Evaluation: the synthetic data generator, the harness, its metrics and reports.

Importing this package puts the repository root and ``backend/`` on ``sys.path`` so the
harness modules can import ``app`` whether they run under pytest (which already adds
``backend``) or as ``python -m eval.harness`` from the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

for _path in (REPO_ROOT, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

__all__ = ["BACKEND_DIR", "REPO_ROOT"]
