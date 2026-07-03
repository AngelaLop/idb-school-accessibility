"""Configurable data and results roots for the pipeline (QW-5).

Background
----------
Every pipeline script used to derive its paths from
``ROOT = Path(__file__).resolve().parent.parent``, i.e., the repo root checked
out on Angela's machine. That binds the scripts to one filesystem layout —
fine for local runs, broken for Docker (where the data volume lives at
``/data``) and Colab (Drive mounted under ``/content/drive/...``).

This module exposes three roots with env-var override and a backwards-
compatible default. Set ``IDB_ACCESS_DATA_ROOT`` and / or
``IDB_ACCESS_RESULTS_ROOT`` to override; leave them unset to preserve the
historical ``<repo>/data`` and ``<repo>/results`` behaviour. Pipeline scripts
continue to behave exactly as before when no env var is exported.

Usage in pipeline scripts (sibling import, same pattern as ``constants``)::

    from _paths import DATA_ROOT, RESULTS_ROOT
    GRID_DIR = DATA_ROOT / "population" / "WorldPop" / "processed"
    OUT_DIR  = RESULTS_ROOT / "accessibility"

Tests and external consumers go through the package::

    from pipeline._paths import DATA_ROOT, RESULTS_ROOT, REPO_ROOT

Values are evaluated at import time. To override in a long-running process
(e.g. a notebook reusing the same kernel after exporting env vars), reload
the module: ``importlib.reload(pipeline._paths)``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _resolve(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    return Path(raw).resolve() if raw else default.resolve()


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = _resolve("IDB_ACCESS_DATA_ROOT", REPO_ROOT / "data")
RESULTS_ROOT = _resolve("IDB_ACCESS_RESULTS_ROOT", REPO_ROOT / "results")

__all__ = ["REPO_ROOT", "DATA_ROOT", "RESULTS_ROOT"]
