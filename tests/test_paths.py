"""Tests for `pipeline._paths` configurable roots (QW-5).

Locks the env-var contract: ``IDB_ACCESS_DATA_ROOT`` and
``IDB_ACCESS_RESULTS_ROOT`` override the defaults, and an unset env restores
the historical ``<repo>/data`` and ``<repo>/results`` layout. The pipeline
scripts that import from ``_paths`` therefore behave identically on Angela's
laptop, in a Docker container with ``/data`` mounted, and in a Colab notebook
with Drive under ``/content/drive/...`` — no script-level changes needed when
the runtime layout changes.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fresh_paths(monkeypatch):
    """Reload `pipeline._paths` so each test re-evaluates against current env."""
    monkeypatch.delenv("IDB_ACCESS_DATA_ROOT", raising=False)
    monkeypatch.delenv("IDB_ACCESS_RESULTS_ROOT", raising=False)
    import pipeline._paths as p
    return importlib.reload(p)


def test_default_layout_matches_repo_root(fresh_paths):
    p = fresh_paths
    assert p.DATA_ROOT == (p.REPO_ROOT / "data").resolve()
    assert p.RESULTS_ROOT == (p.REPO_ROOT / "results").resolve()


def test_env_overrides_data_root(monkeypatch, tmp_path):
    custom = tmp_path / "mounted_data"
    custom.mkdir()
    monkeypatch.setenv("IDB_ACCESS_DATA_ROOT", str(custom))
    monkeypatch.delenv("IDB_ACCESS_RESULTS_ROOT", raising=False)
    import pipeline._paths as p
    p = importlib.reload(p)
    assert p.DATA_ROOT == custom.resolve()
    assert p.RESULTS_ROOT == (p.REPO_ROOT / "results").resolve()


def test_env_overrides_results_root(monkeypatch, tmp_path):
    custom = tmp_path / "outputs"
    custom.mkdir()
    monkeypatch.setenv("IDB_ACCESS_RESULTS_ROOT", str(custom))
    monkeypatch.delenv("IDB_ACCESS_DATA_ROOT", raising=False)
    import pipeline._paths as p
    p = importlib.reload(p)
    assert p.RESULTS_ROOT == custom.resolve()
    assert p.DATA_ROOT == (p.REPO_ROOT / "data").resolve()


def test_both_roots_can_override_independently(monkeypatch, tmp_path):
    data_dir = tmp_path / "mounted_data"
    results_dir = tmp_path / "mounted_results"
    data_dir.mkdir()
    results_dir.mkdir()
    monkeypatch.setenv("IDB_ACCESS_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("IDB_ACCESS_RESULTS_ROOT", str(results_dir))
    import pipeline._paths as p
    p = importlib.reload(p)
    assert p.DATA_ROOT == data_dir.resolve()
    assert p.RESULTS_ROOT == results_dir.resolve()
