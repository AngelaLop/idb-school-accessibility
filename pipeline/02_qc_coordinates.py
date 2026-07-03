"""
QC and gap-filling for school coordinate data across 15 LAC countries.

Task A: Validate reported coordinates against stated admin units (spatial join).
Task B: Geocode schools missing coordinates using address data (Using cascade desing arcGIS - Nominatum - ).

Usage:
    python _qc_coordinates.py              # Run both QC + geocoding
    python _qc_coordinates.py --qc-only    # Run QC validation only (no geopy needed)
"""

import sys
import json
import argparse
import unicodedata
from pathlib import Path

import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point

sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = Path("data/schools/AR")
BOUNDS_DIR = Path("data/bounderys/LAC")
RESULTS = Path("results")
RESULTS.mkdir(exist_ok=True)
GEOCODE_CACHE_PATH = RESULTS / "geocode_cache.json"

# ---------------------------------------------------------------------------
# Known ADM1 name aliases and country bounding boxes — single source of truth
# lives in pipeline/constants.py. Imported here.
# ---------------------------------------------------------------------------
from constants import ADM1_ALIASES, COUNTRY_BBOX, COUNTRY_SCOPE, PIPELINE_ISOS
import qc_core

# ---------------------------------------------------------------------------
# Country address config — one entry per country with address data
# ---------------------------------------------------------------------------
COUNTRY_CONFIG = {
    "ARG": {
        "raw_file": "raw/6831 - Listado de establecimientos con caracteristicas básicas.csv",
        "read_fn": "csv",
        "read_kwargs": {"sep": ";", "encoding": "latin-1", "low_memory": False},
        "id_col": "cueanexo",
        "adm1_col": "provincia",
        "adm2_col": "departamento",
        "locality_col": "localidad",
        "street_col": "ndomicilio",
        "country_name": "Argentina",
        # Code-based match via INDEC provincia code (Phase 1)
        "adm1_code_col": "cod_prov",
        "adm1_code_format": "AR{:03d}",
    },
    "BHS": {
        # Raw has no stable school code column. id_centro is synthesized from
        # the original xlsx row index ("BHS-{:03d}") so step-01 and step-02 align.
        "raw_file": "raw/BHS_Schools_Districts_GeoL_Enviado_especialistapais.xlsx",
        "read_fn": "excel",
        "read_kwargs": {"sheet_name": "All Schools"},
        "id_col": None,
        "id_synth_template": "BHS-{:03d}",
        "adm1_col": "Area Education",
        "adm2_col": None,
        "qc_adm1_col": "Area Education",
        "qc_adm2_col": None,
        "locality_col": "Settlement",
        "street_col": "Address",
        "country_name": "Bahamas",
    },
    "BLZ": {
        "raw_file": "raw/geo_schools Belize.xlsx",
        "read_fn": "excel",
        "read_kwargs": {},
        "id_col": "Code",
        "adm1_col": "Area Administrative",
        "adm2_col": None,
        "locality_col": None,
        "street_col": "Address",
        "country_name": "Belize",
    },
    "BOL": {
        "raw_file": "raw/MinEdu_InstitucionesEducativas_2023.xlsx",
        "read_fn": "excel",
        "read_kwargs": {"skiprows": 7},
        "id_col": "Codigo R.U.E.",
        "adm1_col": "Departamento",
        "adm2_col": "Municipio",
        "qc_adm1_col": "Departamento",
        "qc_adm2_col": None,
        "adm1_code_col": "Departamento Código",
        "adm1_code_format": "BO{:02d}",
        "locality_col": None,
        "street_col": "Dirección",
        "country_name": "Bolivia",
    },
    "BRA": {
        "raw_file": "raw/microdados_censo_escolar_2023/dados/microdados_ed_basica_2023.csv",
        "read_fn": "csv",
        "read_kwargs": {"sep": ";", "encoding": "latin-1", "low_memory": False},
        "usecols": [
            "CO_ENTIDADE", "NO_UF", "SG_UF", "NO_MUNICIPIO",
            "CO_MUNICIPIO", "CO_UF", "DS_ENDERECO", "NU_ENDERECO", "NO_BAIRRO",
        ],
        "id_col": "CO_ENTIDADE",
        "adm1_col": "NO_UF",
        "adm2_col": "NO_MUNICIPIO",
        "locality_col": "NO_BAIRRO",
        "street_col": "DS_ENDERECO",
        "country_name": "Brazil",
        # Code-based match via IBGE UF code (Phase 1)
        "adm1_code_col": "CO_UF",
        "adm1_code_format": "BR{:02d}",
    },
    "COL": {
        "raw_file": "raw/DANE_2023/Carátula única de la sede educativa.CSV",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "latin-1", "low_memory": False},
        "text_replace": {
            "Bogotá, D.C.": "Bogotá D.C.",
            "Archipiélago de San Andrés, Providencia y Santa Catalina":
                "Archipiélago de San Andrés Providencia y Santa Catalina",
            "www,": "www.",
        },
        "id_col": "SEDE_CODIGO",
        "adm1_col": "DEPTO",
        "adm2_col": "MUNI",
        "locality_col": "LOCALIDAD",
        "street_col": "SEDE_DIRECCION",
        "country_name": "Colombia",
        # DANE codes for code-based ADM matching (fallback if name match fails).
        "adm1_code_col": "CODIGOINTERNODEPTO",
        "adm1_code_format": "CO{:02d}",
        "adm2_code_col": "CODIGOINTERNOMUNI",
        "adm2_code_format": "CO{:05d}",
    },
    "CRI": {
        "raw_file": "raw/20250711_MEP_CE_PUBLICOS.xlsx",
        "read_fn": "excel",
        "read_kwargs": {},
        "id_col": "CODPRES",
        "adm1_col": "PROVINCIA",
        "adm2_col": "CANTON",
        "locality_col": "POBLADO",
        "street_col": "DIRECCION",
        "country_name": "Costa Rica",
    },
    "GTM": {
        "raw_file": "raw/sire_2024_filtrado/sire_2024_filtrado.shp",
        "read_fn": "shapefile",
        "read_kwargs": {"encoding": "utf-8"},
        "field_indices": {"id": 0, "adm1": 1, "adm2": 3, "street": 5},
        "id_col": "código",
        "adm1_col": "departamen",
        "adm2_col": "municipio",
        "locality_col": None,
        "street_col": "dirección",
        "country_name": "Guatemala",
    },
    "GUY": {
        "raw_file": "raw/School Data-Mapping.xlsx",
        "read_fn": "excel",
        "read_kwargs": {},
        "id_col": "School_ID",
        "adm1_col": "Region_No",
        "adm2_col": None,
        "locality_col": None,
        "street_col": "Address",
        "country_name": "Guyana",
    },
    "HND": {
        "raw_file": "raw/SIPLIE_nivel nacional.xlsx",
        "read_fn": "excel",
        "read_kwargs": {"sheet_name": "Detalle", "skiprows": 7},
        "id_col": "Código Centro",
        "adm1_col": "Departamento",
        "adm2_col": "Municipio",
        "locality_col": None,
        "street_col": "DireccionCentro",
        "country_name": "Honduras",
    },
    "MEX": {
        "raw_file": "raw/siged_total.csv",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "utf-8", "low_memory": False},
        "id_col": "id_centro",
        "adm1_col": "nombre_entidad",
        "adm2_col": "nombre_municipio",
        "locality_col": "nombre_localidad",
        "street_col": "domicilio_completo",
        "country_name": "Mexico",
        # Code-based match via INEGI entidad code (Phase 1)
        "adm1_code_col": "clave_entidad",
        "adm1_code_format": "MX{:02d}",
    },
    "PER": {
        "raw_file": "raw/Padron.csv",
        "read_fn": "csv",
        "read_kwargs": {"sep": ";", "encoding": "ISO-8859-1", "low_memory": False},
        "id_col": None,  # composite: COD_MOD + ANEXO
        "id_composite": ("COD_MOD", "ANEXO"),
        "adm1_col": "DPTO",
        "adm2_col": "PROV",
        "locality_col": "LOCALIDAD",
        "street_col": "DIRECCION",
        "extra_cols": ["REFERENCIA", "DIST"],
        "country_name": "Peru",
    },
    "PRY": {
        "raw_file": "raw/establecimientos_2023.csv",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "utf-8", "low_memory": False},
        "id_col": "codigo_establecimiento",
        "adm1_col": "nombre_departamento",
        "adm2_col": "nombre_distrito",
        "locality_col": "nombre_barrio_localidad",
        "street_col": "direccion",
        "country_name": "Paraguay",
    },
    "SLV": {
        "raw_file": "raw/SLV_coord_EDU.csv",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "latin-1"},
        "id_col": None,  # first column (CÓDIGO C.E.) has encoding issues
        "adm1_col": "DEPARTAMENTO",
        "adm2_col": "MUNICIPIO",
        "locality_col": None,
        "street_col": None,
        "country_name": "El Salvador",
    },
    "SUR": {
        "raw_file": "raw/Suriname School List_03202024.xlsx",
        "read_fn": "excel",
        "read_kwargs": {},
        "id_col": "School code",
        "adm1_col": "District",
        "adm2_col": "Ressort",
        "locality_col": "Settlement area",
        "street_col": "Address",
        "country_name": "Suriname",
    },
    "URY": {
        # Admin CSV preprocessed from the 3 source shapefiles CEIP/CES/CETP
        # for scope (id_centro, departamento) and enriched with addresses
        # from centros_clean.csv (localidad, paraje, calle, n_de_puerta).
        # Re-run pipeline/00_preprocess_admin.py --only URY when source
        # shapefiles or centros_clean.csv change.
        #
        # adm2_col uses `paraje` as the second-level context for query
        # building. paraje is strictly more granular than localidad: in
        # Montevideo localidad collapses to a single value (MONTEVIDEO)
        # while paraje resolves to 62 barrios (CENTRO, PUNTA CARRETAS,
        # CORDON, etc.); in rural areas paraje typically equals localidad.
        # URY's final_match_level is adm1 (departments) per COUNTRY_SCOPE.
        # BID adm2 polygons for URY are unnamed census sections
        # (`n.a2`...`n.a204`) so qc_adm2_col stays None.
        "raw_file": "raw/URY_admin.csv",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "utf-8"},
        "id_col": "id_centro",
        "adm1_col": "departamento",
        "adm2_col": "paraje",
        "qc_adm1_col": "departamento",
        "qc_adm2_col": None,
        "locality_col": None,
        "street_col": "calle",
        "country_name": "Uruguay",
    },
    "CHL": {
        # Admin CSV preprocessed from raw directorio. The raw column NOM_DEPROV_RBD
        # is the MINEDUC education deprov, which doesn't always align with the
        # political Provincia used in BID's shapefile (e.g. MINEDUC "SANTIAGO SUR"
        # covers the political provincia "Maipo"; "ÑUBLE" covers 3 provincias etc.).
        # We resolve this via COD_PRO_RBD -> ADM2_PCODE (CL0{code}) which gives a
        # 1:1 mapping to BID's 56 Provincias. Preprocessed once to CHL_admin.csv.
        "raw_file": "raw/CHL_admin.csv",
        "read_fn": "csv",
        "read_kwargs": {"encoding": "utf-8"},
        "id_col": "id_centro",
        "adm1_col": "provincia_bid",
        "adm2_col": "comuna",
        "qc_adm1_col": None,
        "qc_adm2_col": "provincia_bid",
        # Code-based ADM1 matching: COD_REG_RBD (1..16) -> BID ADM1_PCODE "CL{:02d}".
        # Names diverge ("AYP" abbreviation vs "Región de Arica y Parinacota"); the
        # numeric code is the stable key.
        "adm1_code_col": "cod_reg_rbd",
        "adm1_code_format": "CL{:02d}",
        "locality_col": None,
        "street_col": None,
        "country_name": "Chile",
        "adm_level": 2,
    },
    "ECU": {
        "raw_file": "raw/2_MINEDUC_RegistrosAdministrativos_2024-2025Inicio.csv",
        "read_fn": "csv",
        "read_kwargs": {"sep": ";", "encoding": "utf-8", "low_memory": False},
        "id_col": "AMIE",
        "adm1_col": "Provincia",
        "adm2_col": "Cantón",
        "locality_col": "Parroquia",
        "locality_code_col": "Cod_Parroquia",
        "street_col": None,
        "country_name": "Ecuador",
        # Code-based match — both levels deterministic via MINEDUC official codes:
        # Cod_Provincia (1..24) -> BID ADM1_PCODE "EC{:02d}",
        # Cod_Cantón (101..2406) -> BID ADM2_PCODE "EC{:04d}". 220/222 raw codes
        # overlap with BID's 224 cantón polygons; 2 raw codes (EC2302, EC9006)
        # are not in BID's lac-level-2 (Santo Domingo de los Tsáchilas
        # cantón split / Galápagos sub-cantón). The ADM2_ALIASES["ECU"]
        # entries are kept as a defensive fallback for rows where Cod_Cantón
        # is missing.
        "adm1_code_col": "Cod_Provincia",
        "adm1_code_format": "EC{:02d}",
        "adm2_code_col": "Cod_Cantón",
        "adm2_code_format": "EC{:04d}",
    },
    "DOM": {
        "raw_file": "raw/RTz-8sq-centros-educativos-de-republica-dominicana-periodo-escolar-2023-2024csv.csv",
        "read_fn": "csv",
        "read_kwargs": {"sep": ";", "encoding": "latin-1", "low_memory": False},
        "id_col": None,
        "id_extract_col": "Centros",   # format "02334 - HERNANDO GORJON"
        "id_extract_regex": r"^(\d+)",
        "adm1_col": "Provincia",
        "adm2_col": "Municipio",
        "qc_adm1_col": None,
        "qc_adm2_col": "Provincia",
        "locality_col": None,
        "street_col": None,
        "country_name": "Dominican Republic",
        # DOM special: raw MINERD reports at Provincia level (32 provinces), but BID's
        # lac-level-1.shp for DOM groups these into 10 Regiones. The 32 provinces are
        # at ADM2 in BID's shapefile ("Provincia Azua", "Distrito Nacional", etc.).
        # Use level 2 so that raw "AZUA" matches polygon "Provincia Azua" via the
        # existing partial-match logic in validate_coordinates().
        "adm_level": 2,
    },
    "PAN": {
        "raw_file": "raw/Marco muestral 19 DE JUNIO 2024.xlsx",
        "read_fn": "excel",
        "read_kwargs": {"skiprows": 3},
        "id_col": "Código SIACE",
        "adm1_col": "Provincia",
        "adm2_col": "Distrito",
        "locality_col": "Corregimiento",
        "street_col": None,
        "country_name": "Panama",
    },
    "JAM": {
        # No raw ministry file — CIMA is built from ISO_total (R pipeline output).
        # All schools treated as Public (EMIS covers government + grant-aided only).
        # spatial_only=True: test 6 checks coordinate containment in parish polygons
        # without a raw ADM1 column to compare against.
        "raw_file": None,
        "adm1_col": None,
        "country_name": "Jamaica",
        "spatial_only": True,
    },
}


# ===================================================================
# Helper functions
# ===================================================================

def normalize_name(s):
    """Strip accents, control chars, lowercase, trim — for comparing admin unit names."""
    if pd.isna(s) or s is None:
        return ""
    s = str(s).strip().lower()
    # Decompose unicode, strip combining marks (accents) and control chars
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(
        c for c in nfkd
        if not unicodedata.combining(c) and unicodedata.category(c)[0] != "C"
    )


def dms_to_dd(dms_str):
    """Convert DMS string like '25°17'13.5"S' or '25°17\'13.5"S' to decimal degrees."""
    import re
    if pd.isna(dms_str) or dms_str is None:
        return np.nan
    s = str(dms_str).strip()
    if not s:
        return np.nan
    # Try numeric first
    try:
        return float(s)
    except ValueError:
        pass
    # DMS patterns: 25°17'13.5"S or 25°17'13.5"S (various encodings of degree symbol)
    m = re.match(
        r"(-?\d+)[°\xb0\xba\u00ba]?\s*(\d+)['\u2019]?\s*([\d.]+)?[\"″]?\s*([NSEWnsew])?",
        s,
    )
    if not m:
        return np.nan
    deg = float(m.group(1))
    mins = float(m.group(2))
    secs = float(m.group(3)) if m.group(3) else 0.0
    direction = m.group(4).upper() if m.group(4) else ""
    dd = abs(deg) + mins / 60.0 + secs / 3600.0
    if direction in ("S", "W") or deg < 0:
        dd = -dd
    return dd


def load_boundaries(level=1):
    """Load ADM{level} boundary polygons via pyshp (handles latin-1 encoding).

    Returns GeoDataFrame with columns ADM0_PCODE, geometry, and when present
    the normalized admin-name helper `adm{level}_norm`. Level 0 is used only
    for territory containment (island / remote-territory classification).
    """
    import shapefile as shp_lib
    from shapely.geometry import shape as shp_shape

    shp_path = BOUNDS_DIR / f"level {level}" / f"lac-level-{level}.shp"
    sf = shp_lib.Reader(str(shp_path), encoding="latin-1")
    fields = [f[0] for f in sf.fields[1:]]
    records, geoms = [], []
    for i, rec in enumerate(sf.iterRecords()):
        records.append(dict(zip(fields, rec)))
        geoms.append(shp_shape(sf.shape(i).__geo_interface__))

    gdf = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
    name_col = f"ADM{level}_EN"
    norm_col = f"adm{level}_norm"
    if name_col in gdf.columns:
        gdf[norm_col] = gdf[name_col].apply(normalize_name)
    return gdf


def load_cima(iso):
    """Read the CIMA CSV for a country."""
    path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, dtype={"id_centro": str})
    df["id_centro"] = df["id_centro"].astype(str).str.strip()
    return df


def extract_addresses(iso, cfg):
    """Read raw file, return DataFrame with id_centro + address columns."""
    if cfg.get("skip") or cfg.get("raw_file") is None:
        return None

    raw_path = BASE / iso / cfg["raw_file"]
    if not raw_path.exists():
        print(f"    WARNING: raw file not found: {raw_path}")
        return None

    # --- Read raw data ---
    if cfg["read_fn"] == "csv":
        kw = dict(cfg["read_kwargs"])
        if "usecols" in cfg:
            kw["usecols"] = cfg["usecols"]
        # COL: Caratula has comma-bearing values like "Bogotá, D.C." that the
        # default parser treats as bad lines and silently skips, dropping ~2,500
        # schools. Step-01 fixes this with text replacements before reading;
        # mirror that here so step-02 sees the same row set.
        text_replace = cfg.get("text_replace")
        if text_replace:
            from io import StringIO
            text = raw_path.read_text(encoding=kw.get("encoding", "utf-8"))
            for src, tgt in text_replace.items():
                text = text.replace(src, tgt)
            # Drop bad-line tolerances since the cleaned text parses cleanly.
            kw_clean = {k: v for k, v in kw.items()
                        if k not in ("engine", "on_bad_lines")}
            df = pd.read_csv(StringIO(text), **kw_clean)
        else:
            # Try multiple encodings; pick the one with fewest garbage chars
            cfg_enc = kw.get("encoding", "utf-8")
            best_df, best_bad = None, float("inf")
            for enc in dict.fromkeys(["utf-8", cfg_enc, "latin-1"]):
                try:
                    kw_try = {**kw, "encoding": enc}
                    trial = pd.read_csv(raw_path, **kw_try)
                    # Count C1 control chars (0x80-0x9F) — sign of wrong encoding
                    sample = trial.head(100).to_string()
                    bad = sum(1 for c in sample if 0x80 <= ord(c) <= 0x9F)
                    if bad < best_bad:
                        best_df, best_bad = trial, bad
                    if bad == 0:
                        break
                except (UnicodeDecodeError, pd.errors.ParserError):
                    continue
            df = best_df if best_df is not None else pd.read_csv(raw_path, **kw)
    elif cfg["read_fn"] == "excel":
        df = pd.read_excel(raw_path, **cfg["read_kwargs"])
    elif cfg["read_fn"] == "shapefile":
        import shapefile as shp_lib
        sf = shp_lib.Reader(str(raw_path), **cfg["read_kwargs"])
        flds = [f[0] for f in sf.fields[1:]]
        idx = cfg["field_indices"]
        rows = []
        for rec in sf.iterRecords():
            r = list(rec)
            rows.append({
                "id_centro": str(r[idx["id"]]),
                "raw_adm1": str(r[idx["adm1"]]),
                "raw_adm2": str(r[idx["adm2"]]) if "adm2" in idx else "",
                "qc_raw_adm1": str(r[idx["adm1"]]),
                "qc_raw_adm2": str(r[idx["adm2"]]) if "adm2" in idx else "",
                "raw_street": str(r[idx["street"]]) if "street" in idx else "",
            })
        result = pd.DataFrame(rows)
        result["raw_locality"] = ""
        result["raw_locality_code"] = ""
        result["raw_adm1_code"] = ""
        result["id_centro"] = result["id_centro"].astype(str).str.strip()
        return result
    else:
        return None

    # --- Build id_centro ---
    if cfg.get("id_synth_template"):
        # BHS: raw has no stable school code; synthesize from xlsx row index
        # using the same template step-01 uses ("BHS-{:03d}"). The merge in
        # finalize_cima_evidence aligns by id_centro, so the (filtered) CIMA
        # and the (unfiltered) addr_df line up on the K-12 subset.
        df["id_centro"] = pd.Series(df.index).apply(
            lambda i: cfg["id_synth_template"].format(int(i))
        )
    elif cfg.get("id_composite"):
        cols = cfg["id_composite"]
        # Clean BOM from PER column names
        df.columns = [c.replace("ï»¿", "").replace("\ufeff", "").strip() for c in df.columns]
        df["id_centro"] = df[cols[0]].astype(str) + "-" + df[cols[1]].astype(str)
    elif cfg.get("id_extract_col"):
        # DOM: id embedded in a text column, extract via regex (e.g. "02334 - HERNANDO GORJON")
        src_col = cfg["id_extract_col"]
        if src_col not in df.columns:
            src_col = next((c for c in df.columns if normalize_name(c) == normalize_name(src_col)), None)
        if src_col is None:
            print(f"    WARNING: id_extract_col not found in {iso}")
            return None
        pattern = cfg.get("id_extract_regex", r"^(\d+)")
        df["id_centro"] = df[src_col].astype(str).str.extract(pattern)[0].str.strip()
    elif cfg["id_col"] is None:
        # SLV: first column has encoding issues
        df["id_centro"] = df.iloc[:, 0].astype(str).str.strip()
    else:
        col = cfg["id_col"]
        # Handle possible column name encoding issues
        if col not in df.columns:
            col = next((c for c in df.columns if normalize_name(c) == normalize_name(col)), None)
        if col is None:
            print(f"    WARNING: id_col not found in {iso}")
            return None
        df["id_centro"] = df[col].astype(str).str.strip()

    # --- Extract address columns ---
    def safe_col(name):
        if name is None:
            return None
        if name in df.columns:
            return name
        # Fuzzy match for encoding issues
        match = next((c for c in df.columns if normalize_name(c) == normalize_name(name)), None)
        return match

    adm1_c = safe_col(cfg["adm1_col"])
    adm2_c = safe_col(cfg["adm2_col"])
    qc_adm1_c = safe_col(cfg.get("qc_adm1_col", cfg["adm1_col"]))
    qc_adm2_c = safe_col(cfg.get("qc_adm2_col", cfg["adm2_col"]))
    loc_c = safe_col(cfg.get("locality_col"))
    loc_code_c = safe_col(cfg.get("locality_code_col"))
    street_c = safe_col(cfg.get("street_col"))
    code1_c = safe_col(cfg.get("adm1_code_col"))
    code2_c = safe_col(cfg.get("adm2_code_col"))

    result = pd.DataFrame({"id_centro": df["id_centro"]})
    result["raw_adm1"] = df[adm1_c].astype(str).str.strip() if adm1_c else ""
    result["raw_adm2"] = df[adm2_c].astype(str).str.strip() if adm2_c else ""
    result["qc_raw_adm1"] = df[qc_adm1_c].astype(str).str.strip() if qc_adm1_c else ""
    result["qc_raw_adm2"] = df[qc_adm2_c].astype(str).str.strip() if qc_adm2_c else ""
    result["raw_locality"] = df[loc_c].astype(str).str.strip() if loc_c else ""
    result["raw_locality_code"] = df[loc_code_c].astype(str).str.strip() if loc_code_c else ""
    result["raw_street"] = df[street_c].astype(str).str.strip() if street_c else ""

    # Code-based admin match (Phase 1 — preferred over name matching when available).
    # Stable, encoding-safe, and immune to naming variations. The format string
    # (e.g. "AR{:03d}") converts the numeric raw code to BID's ADM1_PCODE scheme.
    if code1_c and cfg.get("adm1_code_format"):
        fmt = cfg["adm1_code_format"]
        def _fmt(v):
            if pd.isna(v) or str(v).strip() == "":
                return ""
            try:
                return fmt.format(int(float(str(v).strip())))
            except (ValueError, TypeError):
                return ""
        result["raw_adm1_code"] = df[code1_c].apply(_fmt)
    else:
        result["raw_adm1_code"] = ""

    # Same code-based path at ADM2. Used by ECU (Cod_Cantón -> EC{:04d}); other
    # countries can opt in by adding adm2_code_col + adm2_code_format to their
    # config without touching qc_core.
    if code2_c and cfg.get("adm2_code_format"):
        fmt2 = cfg["adm2_code_format"]
        def _fmt2(v):
            if pd.isna(v) or str(v).strip() == "":
                return ""
            try:
                return fmt2.format(int(float(str(v).strip())))
            except (ValueError, TypeError):
                return ""
        result["raw_adm2_code"] = df[code2_c].apply(_fmt2)
    else:
        result["raw_adm2_code"] = ""

    # Extra cols (PER has REFERENCIA, DIST)
    for ec in cfg.get("extra_cols", []):
        ec_found = safe_col(ec)
        if ec_found:
            result[f"raw_{ec.lower()}"] = df[ec_found].astype(str).str.strip()

    # Clean "nan" strings
    for c in result.columns:
        if c != "id_centro":
            result[c] = result[c].replace({"nan": "", "None": "", "none": ""})

    # Deduplicate by id_centro (take first occurrence)
    result = result.drop_duplicates(subset="id_centro", keep="first")
    return result


# ===================================================================
# Task A: Coordinate validation via spatial join
# ===================================================================

def validate_coordinates(cima, addr_df, boundaries, iso, adm_level=1, spatial_only=False):
    """
    For schools with coords + admin info, check if coords fall in stated admin unit.

    Args:
        adm_level: 1 to match against ADM1 polygons (default), 2 for ADM2.
                   DOM uses level 2 because BID's ADM1 for DOM is at region level
                   while the raw MINERD data reports at province level.

    Returns a DataFrame with QC results per school.
    """
    bbox = COUNTRY_BBOX.get(iso)
    if bbox is None:
        return pd.DataFrame()
    lat_min, lat_max, lon_min, lon_max = bbox

    # Ensure numeric coordinates (handle DMS strings like PRY)
    cima = cima.copy()
    lat_numeric = pd.to_numeric(cima["latitud"], errors="coerce")
    if lat_numeric.notna().sum() == 0 and cima["latitud"].notna().sum() > 0:
        # All non-null values failed numeric parse — try DMS conversion
        cima["latitud"] = cima["latitud"].apply(dms_to_dd)
        cima["longitud"] = cima["longitud"].apply(dms_to_dd)
    else:
        cima["latitud"] = lat_numeric
        cima["longitud"] = pd.to_numeric(cima["longitud"], errors="coerce")

    # Treat (0, 0) as missing — common placeholder for no-data
    zero_mask = (cima["latitud"] == 0) & (cima["longitud"] == 0)
    cima.loc[zero_mask, ["latitud", "longitud"]] = np.nan

    # Filter to schools with valid coordinates
    has_coords = cima["latitud"].notna() & cima["longitud"].notna()
    df = cima[has_coords].copy()
    if df.empty:
        return pd.DataFrame()

    # --- Step 1: Bounding box pre-check ---
    lat_ok = df["latitud"].between(lat_min, lat_max)
    lon_ok = df["longitud"].between(lon_min, lon_max)
    df["in_bounds"] = lat_ok & lon_ok

    # --- Step 2: Detect likely swapped lat/lon ---
    # If lon is in lat range and lat is in lon range → likely swapped
    lat_in_lon = df["latitud"].between(lon_min, lon_max)
    lon_in_lat = df["longitud"].between(lat_min, lat_max)
    df["likely_swapped"] = (~df["in_bounds"]) & lat_in_lon & lon_in_lat

    # --- Step 3: Merge with address data ---
    if addr_df is not None and not addr_df.empty:
        addr_cols = ["id_centro", "raw_adm1", "raw_adm2"]
        if "raw_adm1_code" in addr_df.columns:
            addr_cols.append("raw_adm1_code")
        # Drop any overlapping columns from `df` so the merge doesn't produce
        # _x/_y suffixes (matches the qc_core.finalize_cima_evidence guard).
        overlap = [c for c in addr_cols if c != "id_centro" and c in df.columns]
        if overlap:
            df = df.drop(columns=overlap)
        df = df.merge(
            addr_df[addr_cols],
            on="id_centro", how="left",
        )
    else:
        df["raw_adm1"] = ""
        df["raw_adm2"] = ""

    if "raw_adm1_code" not in df.columns:
        df["raw_adm1_code"] = ""
    df["raw_adm1_code"] = df["raw_adm1_code"].fillna("")
    df["raw_adm1_norm"] = df["raw_adm1"].apply(normalize_name)

    # --- Step 4: Spatial join (only for in-bounds points) ---
    in_bounds_df = df[df["in_bounds"]].copy()
    if in_bounds_df.empty:
        df["polygon_adm1"] = ""
        df["polygon_adm1_norm"] = ""
    else:
        # Filter boundaries to this country
        country_bounds = boundaries[boundaries["ADM0_PCODE"] == iso].copy()
        if country_bounds.empty:
            df["polygon_adm1"] = ""
            df["polygon_adm1_norm"] = ""
        else:
            geometry = [
                Point(lon, lat)
                for lon, lat in zip(in_bounds_df["longitud"], in_bounds_df["latitud"])
            ]
            gdf = gpd.GeoDataFrame(
                in_bounds_df[["id_centro"]],
                geometry=geometry,
                crs="EPSG:4326",
            )
            joined = gpd.sjoin(gdf, country_bounds, how="left", predicate="within")
            # Take first match per school (some boundary overlaps)
            joined = joined.drop_duplicates(subset="id_centro", keep="first")
            name_col = f"ADM{adm_level}_EN"
            norm_col = f"adm{adm_level}_norm"
            pcode_col = f"ADM{adm_level}_PCODE"
            joined = joined[["id_centro", name_col, norm_col, pcode_col]].rename(
                columns={
                    name_col: "polygon_adm1",
                    norm_col: "polygon_adm1_norm",
                    pcode_col: "polygon_adm1_pcode",
                }
            )
            df = df.merge(joined, on="id_centro", how="left")

    # Fill missing polygon columns
    for c in ["polygon_adm1", "polygon_adm1_norm", "polygon_adm1_pcode"]:
        if c not in df.columns:
            df[c] = ""
    df["polygon_adm1"] = df["polygon_adm1"].fillna("")
    df["polygon_adm1_norm"] = df["polygon_adm1_norm"].fillna("")
    df["polygon_adm1_pcode"] = df["polygon_adm1_pcode"].fillna("")

    # --- Step 5: Assign QC status ---
    # Preference order:
    #   1. If raw_adm1_code present AND polygon_adm1_pcode present → compare codes (stable)
    #   2. Else fall back to normalized-name match with aliases + partial match
    aliases = ADM1_ALIASES.get(iso, {})

    def assign_status(row):
        if row["likely_swapped"]:
            return "LIKELY_SWAPPED"
        if not row["in_bounds"]:
            return "OUT_OF_BOUNDS"
        if row["polygon_adm1_norm"] == "":
            return "NO_POLYGON"

        # Code-based match (preferred when raw has admin code)
        raw_code = row.get("raw_adm1_code", "")
        poly_code = row.get("polygon_adm1_pcode", "")
        if raw_code and poly_code:
            return "MATCH" if raw_code == poly_code else "MISMATCH"

        # Spatial-only mode (no raw ADM1 column): MATCH = coordinate within a valid polygon
        if spatial_only:
            return "MATCH"

        # Name-based fallback
        if row["raw_adm1_norm"] == "":
            return "NO_RAW_ADM"
        raw_n = row["raw_adm1_norm"]
        poly_n = row["polygon_adm1_norm"]
        # Apply known aliases
        raw_n = aliases.get(raw_n, raw_n)
        if raw_n == poly_n:
            return "MATCH"
        # Partial match: check if one contains the other
        if raw_n in poly_n or poly_n in raw_n:
            return "MATCH"
        return "MISMATCH"

    df["qc_status"] = df.apply(assign_status, axis=1)

    # Build output — include code columns for auditability (Phase 1 code-based match)
    out_cols = [
        "id_centro", "nombre_centro", "latitud", "longitud",
        "raw_adm1", "raw_adm2", "polygon_adm1", "qc_status",
    ]
    # Add code columns if present (from code-based matching countries)
    for c in ["raw_adm1_code", "polygon_adm1_pcode"]:
        if c in df.columns:
            out_cols.append(c)
    result = df[out_cols].copy()
    result.insert(0, "iso", iso)
    return result


# ===================================================================
# Task A2: Duplicate coordinates check
# ===================================================================

def check_duplicate_coordinates(cima, addr_df, iso):
    """
    Three checks:
    1. Schools sharing exact same coordinates (any count >= 2).
    2. Schools sharing coords but with DIFFERENT addresses (suspicious).
    3. Schools at clusters of >=5 schools at the same coord (strong centroid signal).

    Returns: (dup_df, n_with_coords, n_dup_all, n_dup_diff_addr, n_dup_ge5)
    """
    cima = cima.copy()
    cima["latitud"] = pd.to_numeric(cima["latitud"], errors="coerce")
    cima["longitud"] = pd.to_numeric(cima["longitud"], errors="coerce")

    has_coords = cima["latitud"].notna() & cima["longitud"].notna()
    df = cima[has_coords].copy()
    if df.empty:
        return pd.DataFrame(), 0, 0, 0, 0

    # Merge address info
    has_addr = False
    if addr_df is not None and not addr_df.empty:
        addr_cols = ["id_centro", "raw_adm1", "raw_adm2", "raw_locality", "raw_street"]
        available = [c for c in addr_cols if c in addr_df.columns]
        df = df.merge(addr_df[available], on="id_centro", how="left")
        has_addr = "raw_street" in df.columns or "raw_adm2" in df.columns

    # Round coords to 5 decimals (~1m precision)
    df["lat_r"] = df["latitud"].round(5)
    df["lon_r"] = df["longitud"].round(5)

    # Groups with >1 school at same location
    coord_counts = df.groupby(["lat_r", "lon_r"]).size().reset_index(name="n_schools")
    dup_coords = coord_counts[coord_counts["n_schools"] > 1]

    if dup_coords.empty:
        return pd.DataFrame(), len(df), 0, 0, 0

    dup_df = df.merge(dup_coords[["lat_r", "lon_r", "n_schools"]], on=["lat_r", "lon_r"])
    n_dup_all = len(dup_df)

    # --- Check 2: same coords, different address ---
    n_dup_diff_addr = 0
    dup_df["diff_addr"] = False
    if has_addr:
        # Build a comparable address string per school
        addr_parts = []
        for c in ["raw_street", "raw_adm2", "raw_locality"]:
            if c in dup_df.columns:
                addr_parts.append(dup_df[c].fillna("").astype(str).str.strip().str.lower())
        if addr_parts:
            dup_df["_addr_key"] = addr_parts[0]
            for p in addr_parts[1:]:
                dup_df["_addr_key"] = dup_df["_addr_key"] + "|" + p

            # For each coord group, check if there are >1 distinct addresses
            addr_variety = dup_df.groupby(["lat_r", "lon_r"])["_addr_key"].nunique().reset_index(
                name="n_distinct_addr"
            )
            diff_addr_locs = addr_variety[addr_variety["n_distinct_addr"] > 1][["lat_r", "lon_r"]]

            if not diff_addr_locs.empty:
                dup_df = dup_df.merge(diff_addr_locs, on=["lat_r", "lon_r"], how="left", indicator=True)
                dup_df["diff_addr"] = dup_df["_merge"] == "both"
                dup_df.drop(columns=["_merge"], inplace=True)
                n_dup_diff_addr = dup_df["diff_addr"].sum()

            dup_df.drop(columns=["_addr_key"], inplace=True)

    # --- Check 3: clusters of >=5 schools at same coord (strong centroid signal) ---
    # Independent of address data — works for countries without raw addresses.
    big_clusters = coord_counts[coord_counts["n_schools"] >= 5][["lat_r", "lon_r"]]
    if not big_clusters.empty:
        n_dup_ge5 = int(df.merge(big_clusters, on=["lat_r", "lon_r"], how="inner").shape[0])
    else:
        n_dup_ge5 = 0

    # Build output
    out_cols = ["id_centro", "nombre_centro", "latitud", "longitud",
                "n_schools", "diff_addr"]
    for c in ["raw_adm1", "raw_adm2", "raw_locality", "raw_street"]:
        if c in dup_df.columns:
            out_cols.append(c)
    result = dup_df[out_cols].copy()
    result.insert(0, "iso", iso)

    return result, len(df), n_dup_all, n_dup_diff_addr, n_dup_ge5


# ===================================================================
# Task B: Geocode missing coordinates
# ===================================================================

def geocode_missing(cima, addr_df, iso, cfg, geocoder, cache):
    """
    For schools without coordinates, attempt geocoding via Nominatim.

    Returns DataFrame with geocoded results.
    """
    if addr_df is None or addr_df.empty:
        return pd.DataFrame()

    # Filter to schools missing coordinates
    missing = cima[cima["latitud"].isna() | cima["longitud"].isna()].copy()
    if missing.empty:
        return pd.DataFrame()

    # Merge with address data
    missing = missing.merge(addr_df, on="id_centro", how="left")
    country = cfg["country_name"]

    results = []
    for _, row in missing.iterrows():
        street = row.get("raw_street", "")
        locality = row.get("raw_locality", "")
        adm2 = row.get("raw_adm2", "")
        adm1 = row.get("raw_adm1", "")

        # Build queries from most to least specific
        queries = []
        if street:
            queries.append((f"{street}, {adm2}, {adm1}, {country}", "STREET"))
        if locality:
            queries.append((f"{locality}, {adm2}, {adm1}, {country}", "LOCALITY"))
        if adm2:
            queries.append((f"{adm2}, {adm1}, {country}", "ADM2"))
        # Skip ADM1-only (too coarse — project rule: no centroids)

        if not queries:
            results.append({
                "iso": iso,
                "id_centro": row["id_centro"],
                "nombre_centro": row.get("nombre_centro", ""),
                "geocoded_lat": np.nan,
                "geocoded_lon": np.nan,
                "geocode_query": "",
                "geocode_level": "SKIPPED_NO_ADDRESS",
                "nominatim_display": "",
            })
            continue

        geocoded = False
        for query, level in queries:
            query_clean = query.strip(", ")
            # Check cache first
            if query_clean in cache:
                cached = cache[query_clean]
                if cached is not None:
                    results.append({
                        "iso": iso,
                        "id_centro": row["id_centro"],
                        "nombre_centro": row.get("nombre_centro", ""),
                        "geocoded_lat": cached["lat"],
                        "geocoded_lon": cached["lon"],
                        "geocode_query": query_clean,
                        "geocode_level": f"SUCCESS_{level}",
                        "nominatim_display": cached.get("display", ""),
                    })
                    geocoded = True
                    break
                else:
                    continue  # cached failure, try next level

            # Query Nominatim
            if geocoder is None:
                continue
            try:
                location = geocoder.geocode(query_clean)
                if location:
                    cache[query_clean] = {
                        "lat": location.latitude,
                        "lon": location.longitude,
                        "display": location.address,
                    }
                    results.append({
                        "iso": iso,
                        "id_centro": row["id_centro"],
                        "nombre_centro": row.get("nombre_centro", ""),
                        "geocoded_lat": location.latitude,
                        "geocoded_lon": location.longitude,
                        "geocode_query": query_clean,
                        "geocode_level": f"SUCCESS_{level}",
                        "nominatim_display": location.address,
                    })
                    geocoded = True
                    break
                else:
                    cache[query_clean] = None  # mark as failed
            except Exception as e:
                print(f"      Geocode error for {row['id_centro']}: {e}")
                cache[query_clean] = None

        if not geocoded:
            results.append({
                "iso": iso,
                "id_centro": row["id_centro"],
                "nombre_centro": row.get("nombre_centro", ""),
                "geocoded_lat": np.nan,
                "geocoded_lon": np.nan,
                "geocode_query": queries[0][0] if queries else "",
                "geocode_level": "FAILED",
                "nominatim_display": "",
            })

    return pd.DataFrame(results)


# ===================================================================
# Main
# ===================================================================

def upsert_by_iso(new_df: pd.DataFrame, out_path: Path, iso_col: str = "iso") -> pd.DataFrame:
    """Write `new_df` to `out_path`, replacing rows for ISOs present in new_df
    while preserving rows for ISOs not in this batch.

    Fixes the legacy overwrite bug where running `--countries XXX` would
    silently drop the data for every other country from the global report.
    """
    if iso_col not in new_df.columns:
        raise ValueError(f"upsert_by_iso: '{iso_col}' missing from new_df columns")

    if not out_path.exists():
        new_df.to_csv(out_path, index=False, encoding="utf-8")
        return new_df

    try:
        existing = pd.read_csv(out_path, dtype={"id_centro": str}, low_memory=False)
    except Exception as e:
        print(f"    WARNING: could not read {out_path.name} for upsert ({e}); overwriting")
        new_df.to_csv(out_path, index=False, encoding="utf-8")
        return new_df

    if iso_col not in existing.columns:
        # Schema drift — overwrite (caller is responsible for downstream impact)
        new_df.to_csv(out_path, index=False, encoding="utf-8")
        return new_df

    incoming_isos = set(new_df[iso_col].unique())
    kept = existing[~existing[iso_col].isin(incoming_isos)].copy()
    merged = pd.concat([kept, new_df], ignore_index=True)

    # Align columns: union, preserving existing-then-new column order
    all_cols = list(existing.columns) + [c for c in new_df.columns if c not in existing.columns]
    merged = merged.reindex(columns=all_cols)

    merged.to_csv(out_path, index=False, encoding="utf-8")
    return merged


def run_finalize(countries, boundaries_by_level):
    """Step 02 finalize — schema v2.

    For each country: load CIMA + raw addresses, call
    `qc_core.finalize_cima_evidence`, write the enriched CIMA back to disk.
    After enrichment, also compute the per-country target buckets that step-04
    will process (via `qc_core.compute_geocode_targets`) and persist them to
    `results/dashboard/dashboard_geocode_targets.csv` so the dashboard can show
    the step-03 → step-04 funnel without recomputing.

    Idempotent: re-running on its own output produces the same CIMA.
    `coordinate_quality` is rederived from evidence each time, so legacy v1
    labels (BRA/COL/HND/MEX/PAN/SUR) are migrated automatically without a
    separate script.

    Side reports (`qc_coordinate_summary.csv`, `qc_coordinate_report.csv`)
    are NOT regenerated here — those still come from the prepass loop above.
    They will be folded in once Step 05 stops depending on them.
    """
    print("\n" + "=" * 65)
    print("STEP 02 FINALIZE — schema v2 enrichment")
    print("=" * 65)

    finalize_summary = []
    targets_summary = []
    candidates_rows: list[dict] = []
    qc_baseline_rows: list[dict] = []
    for iso in countries:
        scope = COUNTRY_SCOPE.get(iso)
        if scope is None:
            print(f"  {iso}: skipped (no COUNTRY_SCOPE entry)")
            continue
        if not scope.get("pipeline_enabled", True):
            print(f"  {iso}: skipped (pipeline_enabled=False)")
            continue

        cima = load_cima(iso)
        if cima is None or cima.empty:
            print(f"  {iso}: no CIMA file — skipped")
            continue

        cfg = COUNTRY_CONFIG.get(iso)
        addr = None
        if cfg and not cfg.get("skip"):
            try:
                addr = extract_addresses(iso, cfg)
            except Exception as e:
                print(f"  {iso}: address extraction failed ({e}) — continuing without addresses")

        try:
            enriched = qc_core.finalize_cima_evidence(
                cima=cima, addr_df=addr,
                boundaries_by_level=boundaries_by_level,
                iso=iso, scope=scope,
            )
        except Exception as e:
            print(f"  {iso}: FAILED finalize: {e}")
            continue

        # Write back — preserve column ordering: base SCHEMA first, then enriched.
        out_path = BASE / iso / "processed" / f"{iso}_total_cima.csv"
        enriched.to_csv(out_path, index=False, encoding="utf-8")

        # Quick distribution print
        vc = enriched["coordinate_quality"].value_counts(dropna=False).to_dict()
        n = len(enriched)
        print(f"  {iso}: {n:,} schools | {dict(sorted(vc.items(), key=lambda x: -x[1]))}")
        finalize_summary.append({
            "iso": iso, "total": n, "final_match_level": scope["final_match_level"],
            **{f"q_{k}": v for k, v in vc.items()},
        })

        # --- Step-04 target discovery (consumed by the dashboard funnel) ---
        # The funnel shows PRE-geocoding candidates: schools that still need
        # something from step-04. Rows already filled by Phase B (Step 05
        # geocoder or centroid cascade) are excluded from the pending pool —
        # they have a non-original `coordinate_source` and the dashboard counts
        # them separately as "already processed".
        #
        # Sector breakdown: cluster detection MUST run on the full pending CIMA
        # (filtering by sector first would re-cluster on a subset and miss
        # mixed-sector clusters). So we run compute_geocode_targets once and
        # then intersect each bucket (set of id_centro) with the sector mask
        # to get total / public / private counts. 3 rows written per ISO.
        try:
            src = (
                enriched.get("coordinate_source", pd.Series([""] * len(enriched)))
                .fillna("").astype(str)
            )
            pending_mask = src.isin(["", "original"])
            # Reset index — `detect_clusters_exact` aligns by `df.index` and a
            # subset with non-contiguous indices breaks the cluster-size join.
            pending_cima = enriched[pending_mask].copy().reset_index(drop=True)

            targets = qc_core.compute_geocode_targets(
                cima=pending_cima, addr_df=addr, qc_evidence=pending_cima,
            )

            # Sector-aware id sets over the pending pool
            pending_sector = (
                pending_cima.get("sector", pd.Series([""] * len(pending_cima)))
                .fillna("").astype(str)
            )
            pending_ids_by_sector = {
                "total": set(pending_cima["id_centro"].astype(str)),
                "public": set(pending_cima.loc[pending_sector == "Public", "id_centro"].astype(str)),
                "private": set(pending_cima.loc[pending_sector == "Private", "id_centro"].astype(str)),
            }

            # Already-counts (geocoded / cascade) sliced by sector on the FULL CIMA.
            enriched_sector = (
                enriched.get("sector", pd.Series([""] * len(enriched)))
                .fillna("").astype(str)
            )
            sector_filters = {
                "total": pd.Series(True, index=enriched.index),
                "public": enriched_sector == "Public",
                "private": enriched_sector == "Private",
            }
            already_by_sector = {
                name: {
                    "geocoded": int(((src == "geocoded") & mask).sum()),
                    "cascade": int(((src == "centroid_cascade") & mask).sum()),
                }
                for name, mask in sector_filters.items()
            }

            for sector_name, sector_ids in pending_ids_by_sector.items():
                n_zeros_sec = len(targets["zeros"] & sector_ids)
                n_missing_sec = len((targets["missing"] | targets["zeros"]) & sector_ids)
                targets_summary.append({
                    "iso": iso,
                    "sector": sector_name,
                    "n_pending_missing": n_missing_sec,
                    "n_pending_zeros": n_zeros_sec,
                    "n_pending_out_of_bounds": len(targets["out_of_bounds"] & sector_ids),
                    "n_pending_mismatches": len(targets["mismatches"] & sector_ids),
                    "n_pending_centroids": len(targets["centroids"] & sector_ids),
                    "n_pending_dup_addr": len(targets["dup_addr"] & sector_ids),
                    "n_already_geocoded": already_by_sector[sector_name]["geocoded"],
                    "n_already_cascade": already_by_sector[sector_name]["cascade"],
                    "centroids_address_filtered": bool(targets["centroids_address_filtered"]),
                })

            # --- Per-school candidates ledger (consumed by export_dashboard_data
            # to filter the step-04 ledger to step-03 funnel members only) ---
            # Funnel = pending step-03 buckets (missing/zeros/oob/mismatches/
            # centroids — explicitly NOT dup_addr) UNION schools step-04 already
            # filled (coordinate_source ∈ {geocoded, centroid_cascade}). The
            # already-fixed schools were funnel candidates before recovery, so
            # they belong in the universe; their geocoder ledger rows reconcile
            # the step-03 funnel and the step-04 universe.
            candidate_ids_pending = (
                targets["missing"] | targets["zeros"] | targets["out_of_bounds"]
                | targets["mismatches"] | targets["centroids"]
            )
            already_fixed_ids = set(
                enriched.loc[
                    src.isin(["geocoded", "centroid_cascade"]),
                    "id_centro",
                ].astype(str)
            )
            funnel_ids = candidate_ids_pending | already_fixed_ids

            id_to_src = dict(zip(enriched["id_centro"].astype(str), src))
            id_to_sector = dict(
                zip(enriched["id_centro"].astype(str), enriched_sector)
            )

            def _funnel_status(sid: str) -> str:
                s = id_to_src.get(sid, "")
                if s == "geocoded":
                    return "already_geocoded"
                if s == "centroid_cascade":
                    return "already_cascade"
                if sid in targets["missing"] or sid in targets["zeros"]:
                    return "pending_missing"
                if sid in targets["out_of_bounds"]:
                    return "pending_out_of_bounds"
                if sid in targets["mismatches"]:
                    return "pending_mismatches"
                if sid in targets["centroids"]:
                    return "pending_centroids"
                return "unknown"

            for sid in sorted(funnel_ids):
                candidates_rows.append({
                    "iso": iso,
                    "id_centro": sid,
                    "sector": id_to_sector.get(sid, ""),
                    "funnel_status": _funnel_status(sid),
                })
        except Exception as e:
            print(f"  {iso}: WARNING — compute_geocode_targets failed ({e})")

        # --- Dashboard QC matrix baseline (3 sectors × 2 modes per country) ---
        try:
            qc_baseline_rows.extend(
                qc_core.compute_qc_baseline(cima=enriched, iso=iso, scope=scope)
            )
        except Exception as e:
            print(f"  {iso}: WARNING — compute_qc_baseline failed ({e})")

    if finalize_summary:
        sum_df = pd.DataFrame(finalize_summary)
        out = RESULTS / "qc_finalize_summary.csv"
        merged = upsert_by_iso(sum_df, out)
        print(f"\n  {out}: {len(merged)} countries (this batch contributed {len(sum_df)})")

    if targets_summary:
        targets_df = pd.DataFrame(targets_summary)
        targets_out = RESULTS / "dashboard" / "dashboard_geocode_targets.csv"
        targets_out.parent.mkdir(parents=True, exist_ok=True)
        merged = upsert_by_iso(targets_df, targets_out)
        print(
            f"  {targets_out}: {len(merged)} countries "
            f"(this batch contributed {len(targets_df)})"
        )

    if candidates_rows:
        candidates_df = pd.DataFrame(candidates_rows)
        candidates_out = RESULTS / "dashboard" / "dashboard_step03_candidates.csv"
        candidates_out.parent.mkdir(parents=True, exist_ok=True)
        merged_cands = upsert_by_iso(candidates_df, candidates_out)
        print(
            f"  {candidates_out}: {len(merged_cands)} candidate-rows "
            f"({merged_cands['iso'].nunique()} countries; this batch contributed {len(candidates_df)})"
        )

    if qc_baseline_rows:
        baseline_df = pd.DataFrame(qc_baseline_rows)
        baseline_out = RESULTS / "dashboard" / "dashboard_qc_baseline.csv"
        baseline_out.parent.mkdir(parents=True, exist_ok=True)
        # Upsert per (iso, mode, sector) — drop existing batch rows then append.
        if baseline_out.exists():
            existing = pd.read_csv(baseline_out)
            keep = ~existing["iso"].isin(baseline_df["iso"].unique())
            merged_baseline = pd.concat([existing[keep], baseline_df], ignore_index=True)
        else:
            merged_baseline = baseline_df
        merged_baseline = merged_baseline.sort_values(
            ["iso", "mode", "sector"]
        ).reset_index(drop=True)
        merged_baseline.to_csv(baseline_out, index=False, encoding="utf-8")
        n_countries = merged_baseline["iso"].nunique()
        print(
            f"  {baseline_out}: {n_countries} countries × 6 rows = {len(merged_baseline)} "
            f"(this batch contributed {len(baseline_df)})"
        )


def main():
    parser = argparse.ArgumentParser(description="QC and geocode school coordinates")
    parser.add_argument("--qc-only", action="store_true", help="Run QC validation only")
    parser.add_argument("--countries", nargs="*", help="Process specific countries (ISO codes)")
    parser.add_argument(
        "--mode",
        choices=["prepass", "finalize"],
        default="finalize",
        help=(
            "prepass: legacy behaviour only — produce side reports for Step 05 "
            "compatibility, do NOT enrich CIMA. "
            "finalize (default): legacy reports + write schema-v2 columns "
            "(coordinate_quality, coordinate_quality_reason, qc_scope_class, "
            "include_in_spatial_indicators, qc_*, adm_pcodes) back to each "
            "CIMA via qc_core.finalize_cima_evidence."
        ),
    )
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help=(
            "Only valid with --mode finalize. Skip the legacy prepass/report/geocoding "
            "loop and run qc_core.finalize_cima_evidence directly on the selected CIMA files."
        ),
    )
    args = parser.parse_args()
    if args.finalize_only and args.mode != "finalize":
        parser.error("--finalize-only requires --mode finalize")

    print("=" * 65)
    print("Coordinate QC & Gap-Filling")
    print("=" * 65)

    # --- Load boundaries ---
    # ADM1 is the default; ADM2 is only loaded for countries where BID's ADM1
    # granularity doesn't match the raw data (e.g. DOM: BID uses 10 regiones at
    # ADM1, raw MINERD reports 32 provincias which live at ADM2 in BID's shapefile).
    print("\nLoading admin boundaries...")
    boundaries_by_level = {
        0: load_boundaries(level=0),
        1: load_boundaries(level=1),
        2: load_boundaries(level=2),
    }
    print(f"  ADM0: {len(boundaries_by_level[0])} polygons")
    print(f"  ADM1: {len(boundaries_by_level[1])} polygons")
    print(f"  ADM2: {len(boundaries_by_level[2])} polygons")

    # --- Load geocode cache ---
    cache = {}
    if GEOCODE_CACHE_PATH.exists():
        with open(GEOCODE_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  Geocode cache: {len(cache)} entries loaded")

    # --- Init geocoder ---
    geocoder = None
    if not args.qc_only:
        try:
            from geopy.geocoders import Nominatim
            from geopy.extra.rate_limiter import RateLimiter
            _nom = Nominatim(user_agent="idb_school_accessibility_platform_qc")
            geocoder = RateLimiter(_nom.geocode, min_delay_seconds=1.1)
            print("  Nominatim geocoder ready")
        except ImportError:
            print("  WARNING: geopy not installed — geocoding disabled")
            print("  Install with: uv pip install geopy")

    # --- Select countries ---
    # Default: iterate over the full 23-country operational pipeline universe.
    # Countries with config run tests 6+7; countries without config still run
    # test 7 (dup coord clusters) on their CIMA.
    countries = args.countries if args.countries else PIPELINE_ISOS

    if args.finalize_only:
        print("\nSkipping legacy prepass/export loop (--finalize-only).")
        run_finalize(countries, boundaries_by_level)
        print("\nDone.")
        return

    # --- Process ---
    all_qc = []
    all_dups = []
    all_geocoded = []
    summary_rows = []

    print()
    for iso in countries:
        cfg = COUNTRY_CONFIG.get(iso)
        country_name = cfg["country_name"] if cfg else iso
        print(f"  {iso} ({country_name})")

        # Load CIMA (required for any test)
        cima = load_cima(iso)
        if cima is None or cima.empty:
            print(f"    No CIMA file found — skipping")
            continue

        total = len(cima)
        has_coords = (cima["latitud"].notna() & cima["longitud"].notna()).sum()
        missing_coords = total - has_coords
        print(f"    Schools: {total:,} total, {has_coords:,} with coords, {missing_coords:,} missing")

        # Extract addresses if config available and not skipped
        addr = None
        if cfg and not cfg.get("skip"):
            try:
                addr = extract_addresses(iso, cfg)
                if addr is not None:
                    print(f"    Addresses: {len(addr):,} extracted from raw")
            except Exception as e:
                print(f"    WARNING: address extraction failed: {e}")
                addr = None

        # Initialize summary row — every country gets a row (test 7 always runs)
        row = {
            "iso": iso,
            "total": total,
            "with_coords": int(has_coords),
            "missing_coords": int(missing_coords),
            "qc_checked": 0,
            "match": 0, "mismatch": 0, "out_of_bounds": 0,
            "likely_swapped": 0, "no_polygon": 0, "no_raw_adm": 0,
            "match_rate_pct": None,
        }

        # --- Test 6: ADM1 match (requires config + adm1_col, OR spatial_only mode) ---
        can_run_test6 = cfg is not None and not cfg.get("skip") and (
            cfg.get("adm1_col") or cfg.get("spatial_only")
        )
        if can_run_test6:
            # Countries can specify adm_level=2 to match against finer polygons.
            # DOM does this because its raw data is at provincia level but BID's
            # ADM1 shapefile groups DOM provinces into 10 regiones.
            adm_level = cfg.get("adm_level", 1)
            bounds_gdf = boundaries_by_level[adm_level]
            qc = validate_coordinates(cima, addr, bounds_gdf, iso,
                                      adm_level=adm_level,
                                      spatial_only=cfg.get("spatial_only", False))
            if not qc.empty:
                all_qc.append(qc)
                match = (qc["qc_status"] == "MATCH").sum()
                mismatch = (qc["qc_status"] == "MISMATCH").sum()
                oob = (qc["qc_status"] == "OUT_OF_BOUNDS").sum()
                swapped = (qc["qc_status"] == "LIKELY_SWAPPED").sum()
                no_poly = (qc["qc_status"] == "NO_POLYGON").sum()
                no_adm = (qc["qc_status"] == "NO_RAW_ADM").sum()
                checked = len(qc)
                match_rate = match / max(checked - no_adm, 1) * 100

                print(f"    Test 6 ADM1: {match:,} match, {mismatch:,} mismatch, "
                      f"{oob:,} out-of-bounds, {swapped:,} swapped, "
                      f"{no_poly:,} no-polygon, {no_adm:,} no-adm | "
                      f"rate={match_rate:.1f}%")

                row.update({
                    "qc_checked": int(checked),
                    "match": int(match),
                    "mismatch": int(mismatch),
                    "out_of_bounds": int(oob),
                    "likely_swapped": int(swapped),
                    "no_polygon": int(no_poly),
                    "no_raw_adm": int(no_adm),
                    "match_rate_pct": round(match_rate, 1),
                })
        else:
            print(f"    Test 6 ADM1: skipped (no raw ADM1 config)")

        # --- Test 7: Duplicate coordinates (>=5 cluster) — ALWAYS runs ---
        dup_result, n_with_coords, n_dup_all, n_dup_diff, n_dup_ge5 = check_duplicate_coordinates(cima, addr, iso)
        if not dup_result.empty:
            all_dups.append(dup_result)
        dup_all_pct = n_dup_all / max(n_with_coords, 1) * 100
        dup_diff_pct = n_dup_diff / max(n_with_coords, 1) * 100
        dup_ge5_pct = n_dup_ge5 / max(n_with_coords, 1) * 100
        print(f"    Test 7 Dup: {n_dup_all:,} same coord ({dup_all_pct:.1f}%), "
              f"{n_dup_diff:,} diff addr ({dup_diff_pct:.1f}%), "
              f"{n_dup_ge5:,} in >=5 cluster ({dup_ge5_pct:.1f}%)")

        row["dup_coord_schools"] = int(n_dup_all)
        row["dup_coord_pct"] = round(dup_all_pct, 1)
        row["dup_diff_addr"] = int(n_dup_diff)
        row["dup_diff_addr_pct"] = round(dup_diff_pct, 1)
        row["dup_ge5_schools"] = int(n_dup_ge5)
        row["dup_ge5_pct"] = round(dup_ge5_pct, 1)

        summary_rows.append(row)

        # --- Task B: Geocode missing ---
        if not args.qc_only and missing_coords > 0 and cfg:
            geo = geocode_missing(cima, addr, iso, cfg, geocoder, cache)
            if not geo.empty:
                all_geocoded.append(geo)
                success = geo["geocode_level"].str.startswith("SUCCESS").sum()
                failed = (geo["geocode_level"] == "FAILED").sum()
                skipped = geo["geocode_level"].str.startswith("SKIPPED").sum()
                print(f"    Geocoded: {success:,} success, {failed:,} failed, {skipped:,} skipped")

                # Save cache periodically
                with open(GEOCODE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)

    # --- Export reports ---
    print("\n" + "=" * 65)
    print("EXPORTING REPORTS")
    print("=" * 65)

    if all_qc:
        qc_df = pd.concat(all_qc, ignore_index=True)
        merged = upsert_by_iso(qc_df, RESULTS / "qc_coordinate_report.csv")
        print(f"  {RESULTS / 'qc_coordinate_report.csv'}: {len(merged):,} rows (this batch: {len(qc_df):,})")

    if summary_rows:
        sum_df = pd.DataFrame(summary_rows)
        merged = upsert_by_iso(sum_df, RESULTS / "qc_coordinate_summary.csv")
        print(f"  {RESULTS / 'qc_coordinate_summary.csv'}: {len(merged)} countries (this batch: {len(sum_df)})")

    if all_dups:
        dup_df = pd.concat(all_dups, ignore_index=True)
        merged = upsert_by_iso(dup_df, RESULTS / "qc_duplicate_coordinates.csv")
        print(f"  {RESULTS / 'qc_duplicate_coordinates.csv'}: {len(merged):,} rows (this batch: {len(dup_df):,})")

    if all_geocoded:
        geo_df = pd.concat(all_geocoded, ignore_index=True)
        merged = upsert_by_iso(geo_df, RESULTS / "geocoded_coordinates.csv")
        print(f"  {RESULTS / 'geocoded_coordinates.csv'}: {len(merged):,} rows (this batch: {len(geo_df):,})")

    # Save final cache
    with open(GEOCODE_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"  {GEOCODE_CACHE_PATH}: {len(cache)} entries")

    # --- Print summary table ---
    if summary_rows:
        print("\n" + "=" * 65)
        print("QC SUMMARY")
        print("=" * 65)
        print(f"{'ISO':<5} {'Total':>8} {'Match%':>7} {'Mis':>5} {'OOB':>5} "
              f"{'DupAll':>8} {'DifAddr':>8} {'>=5':>8}")
        print("-" * 55)
        for s in summary_rows:
            da = s.get('dup_coord_schools', 0)
            da_p = s.get('dup_coord_pct', 0.0)
            dd = s.get('dup_diff_addr', 0)
            dd_p = s.get('dup_diff_addr_pct', 0.0)
            ge5 = s.get('dup_ge5_schools', 0)
            ge5_p = s.get('dup_ge5_pct', 0.0)
            mr = s.get('match_rate_pct')
            mr_str = f"{mr:>6.1f}%" if mr is not None else "    —  "
            print(f"{s['iso']:<5} {s['total']:>8,} {mr_str} "
                  f"{s['mismatch']:>5,} {s['out_of_bounds']:>5,} "
                  f"{da:>5,}({da_p:>2.0f}%) {dd:>5,}({dd_p:>2.0f}%) "
                  f"{ge5:>5,}({ge5_p:>2.0f}%)")

    # --- Schema v2 finalize (default) ---
    if args.mode == "finalize":
        run_finalize(countries, boundaries_by_level)
    else:
        print(f"\n  Skipping finalize (--mode={args.mode}). Side reports still produced for Step 05 compat.")

    print("\nDone.")


if __name__ == "__main__":
    main()
