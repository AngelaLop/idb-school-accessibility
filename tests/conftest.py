"""Shared fixtures for CIMA pipeline tests."""

import pandas as pd
import pytest
from pathlib import Path

from pipeline.constants import (
    ALL_ISOS,
    ANALYSIS_ISOS,
    PIPELINE_ISOS,
    COUNTRY_BBOX,
    REQUIRED_COLUMNS,
)

CIMA_DIR = Path("data/schools/AR")
RESULTS_DIR = Path("results")

__all__ = [
    "ALL_ISOS",
    "ANALYSIS_ISOS",
    "CIMA_DIR",
    "COUNTRY_BBOX",
    "PIPELINE_ISOS",
    "REQUIRED_COLUMNS",
    "RESULTS_DIR",
    "_cima_path",
]


def _cima_path(iso):
    return CIMA_DIR / iso / "processed" / f"{iso}_total_cima.csv"


@pytest.fixture(scope="session")
def all_cima():
    """Load all CIMA files into a dict {iso: DataFrame}."""
    data = {}
    for iso in ANALYSIS_ISOS:
        path = _cima_path(iso)
        if path.exists():
            data[iso] = pd.read_csv(path, dtype={"id_centro": str})
    return data


def pytest_generate_tests(metafunc):
    """Parametrize tests that use 'iso' fixture by all available CIMA files."""
    if "iso" in metafunc.fixturenames:
        available = [iso for iso in ANALYSIS_ISOS if _cima_path(iso).exists()]
        metafunc.parametrize("iso", available)
