"""Regression test: rebuilding CIMA must preserve Step 05 enrichment columns."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pandas as pd


MODULE_PATH = Path(__file__).resolve().parent.parent / "pipeline" / "01_build_cima.py"


def _load_build_module():
    pipeline_dir = str(MODULE_PATH.parent)
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    spec = importlib.util.spec_from_file_location("build_cima", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextmanager
def _workspace_tmpdir():
    root = Path.cwd() / "results" / "_pytest_build_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_save_cima_preserves_existing_enrichment():
    with _workspace_tmpdir() as tmp_path:
        build = _load_build_module()
        build.BASE = tmp_path / "data" / "schools" / "AR"

        iso = "TST"
        out_dir = build.BASE / iso / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{iso}_total_cima.csv"

        existing = pd.DataFrame(
            [
                {
                    "id_centro": "A1",
                    "nombre_centro": "Existing School",
                    "sector": "Public",
                    "nivel_primaria": 1,
                    "nivel_secbaja": 0,
                    "nivel_secalta": 0,
                    "latitud": 1.1,
                    "longitud": -77.1,
                    "adm0_pcode": iso,
                    "coordinate_source": "original",
                    "coordinate_quality": "flag",
                    "acceptance": "FLAG",
                    "arcgis_score": 92.0,
                }
            ]
        )
        existing.to_csv(out_path, index=False, encoding="utf-8")

        rebuilt = pd.DataFrame(
            [
                {
                    "id_centro": "A1",
                    "nombre_centro": "Existing School",
                    "sector": "Public",
                    "nivel_primaria": 1,
                    "nivel_secbaja": 0,
                    "nivel_secalta": 0,
                    "latitud": 1.2,
                    "longitud": -77.2,
                    "adm0_pcode": iso,
                },
                {
                    "id_centro": "B2",
                    "nombre_centro": "New School",
                    "sector": "Private",
                    "nivel_primaria": 0,
                    "nivel_secbaja": 1,
                    "nivel_secalta": 1,
                    "latitud": 2.0,
                    "longitud": -78.0,
                    "adm0_pcode": iso,
                },
            ]
        )

        build.save_cima(rebuilt, iso)
        saved = pd.read_csv(out_path, dtype={"id_centro": str})

        assert "coordinate_source" in saved.columns
        assert "coordinate_quality" in saved.columns
        assert "acceptance" in saved.columns
        assert "arcgis_score" in saved.columns

        restored_row = saved[saved["id_centro"] == "A1"].iloc[0]
        assert restored_row["coordinate_source"] == "original"
        assert restored_row["coordinate_quality"] == "flag"
        assert restored_row["acceptance"] == "FLAG"
        assert restored_row["arcgis_score"] == 92.0

        new_row = saved[saved["id_centro"] == "B2"].iloc[0]
        assert pd.isna(new_row["coordinate_source"])
        assert pd.isna(new_row["coordinate_quality"])
        assert pd.isna(new_row["acceptance"])
        assert pd.isna(new_row["arcgis_score"])


def test_save_cima_preserves_latitud_geocoded_backup():
    """Step 05 writes latitud_geocoded as a non-base backup of geocoded coords.
    save_cima must preserve it so finalize can promote it back if Step 01
    rebuilds latitud from raw and zeroes/NaNs the geocoded value.
    """
    with _workspace_tmpdir() as tmp_path:
        build = _load_build_module()
        build.BASE = tmp_path / "data" / "schools" / "AR"

        iso = "TST"
        out_dir = build.BASE / iso / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{iso}_total_cima.csv"

        existing = pd.DataFrame(
            [
                {
                    "id_centro": "G1",
                    "nombre_centro": "Geocoded School",
                    "sector": "Public",
                    "nivel_primaria": 1,
                    "nivel_secbaja": 0,
                    "nivel_secalta": 0,
                    "latitud": -34.5,
                    "longitud": -58.4,
                    "adm0_pcode": iso,
                    "coordinate_source": "geocoded",
                    "acceptance": "ACCEPT",
                    "latitud_geocoded": -34.5,
                    "longitud_geocoded": -58.4,
                    "arcgis_score": 97.0,
                }
            ]
        )
        existing.to_csv(out_path, index=False, encoding="utf-8")

        rebuilt = pd.DataFrame(
            [
                {
                    "id_centro": "G1",
                    "nombre_centro": "Geocoded School",
                    "sector": "Public",
                    "nivel_primaria": 1,
                    "nivel_secbaja": 0,
                    "nivel_secalta": 0,
                    "latitud": float("nan"),
                    "longitud": float("nan"),
                    "adm0_pcode": iso,
                }
            ]
        )

        build.save_cima(rebuilt, iso)
        saved = pd.read_csv(out_path, dtype={"id_centro": str})

        row = saved[saved["id_centro"] == "G1"].iloc[0]
        assert row["coordinate_source"] == "geocoded"
        assert row["acceptance"] == "ACCEPT"
        assert row["latitud_geocoded"] == -34.5
        assert row["longitud_geocoded"] == -58.4


def test_dom_process_backfills_invalid_2023_coord_from_2022_row():
    with _workspace_tmpdir() as tmp_path:
        build = _load_build_module()
        build.BASE = tmp_path / "data" / "schools" / "AR"
        build.summary = []
        build.errors = {}

        iso = "DOM"
        raw_dir = build.BASE / iso / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / "RTz-8sq-centros-educativos-de-republica-dominicana-periodo-escolar-2023-2024csv.csv"

        raw = pd.DataFrame(
            [
                {
                    "Regional": "11 - PUERTO PLATA",
                    "Distrito": "1104 - LUPERON",
                    "Centros": "02694 - FRANCISCO EMILIO ORTEGA",
                    "Sector": "PUBLICO",
                    "Nivel": "INICIAL - PRIMARIO",
                    "Coordenadas Latitud": "19.8321,",
                    "Coordenadas Longitud": ",1-71.01",
                    "Matricula": 111,
                    "Planta Fisica": "18033415 - FRANCISCO EMILIO ORTEGA",
                    "Provincia": "PUERTO PLATA",
                    "Municipio": "LUPERÓN",
                    "Año": 20222023,
                },
                {
                    "Regional": "11 - PUERTO PLATA",
                    "Distrito": "1104 - LUPERON",
                    "Centros": "02694 - FRANCISCO EMILIO ORTEGA",
                    "Sector": "PÚBLICO",
                    "Nivel": "INICIAL - PRIMARIO",
                    "Coordenadas Latitud": "18033415,-",
                    "Coordenadas Longitud": "71.009898",
                    "Matricula": 104,
                    "Planta Fisica": "18033415 - FRANCISCO EMILIO ORTEGA",
                    "Provincia": "PUERTO PLATA",
                    "Municipio": "LUPERÓN",
                    "Año": 20232024,
                },
            ]
        )
        raw.to_csv(raw_path, sep=";", index=False, encoding="latin-1")

        build.process_DOM()

        out_path = build.BASE / iso / "processed" / f"{iso}_total_cima.csv"
        saved = pd.read_csv(out_path, dtype={"id_centro": str})
        row = saved[saved["id_centro"] == "02694"].iloc[0]

        assert row["latitud"] == 19.8321
        assert row["longitud"] == -71.01
