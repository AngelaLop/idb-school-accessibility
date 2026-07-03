"""
Build {ISO}_total_cima.csv from RAW ministry files for all countries.

Key difference from v1: reads from raw data (not the already-filtered processed CSV/GeoJSON),
so that BOTH public AND private K-12 institutions are included.
Adds a 'sector' column: "Public" or "Private".

K-12 definition: nivel_primaria OR nivel_secbaja OR nivel_secalta (excluding initial/preschool-only).
"""

import sys
import os
import json
import re
import traceback
from io import StringIO
from pathlib import Path

import urllib.request

import pandas as pd
import numpy as np
import geopandas as gpd
import shapefile as shp_lib

from constants import COUNTRY_SCOPE, PIPELINE_ISOS, SCHEMA

sys.stdout.reconfigure(encoding='utf-8')

BASE = Path("data/schools/AR")
RESULTS = Path("results")
RESULTS.mkdir(exist_ok=True)

errors = {}
summary = []


def save_cima(df, iso):
    """Ensure base schema, preserve existing enrichment columns, save to processed/."""
    out_dir = BASE / iso / 'processed'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{iso}_total_cima.csv'

    base_df = df.copy()
    for col in SCHEMA:
        if col not in base_df.columns:
            base_df[col] = np.nan
    base_df = base_df[SCHEMA].copy()

    # Step 05 and later stages enrich CIMA with additional audit columns
    # (e.g. coordinate_quality, acceptance, geocoded alternatives). If Step 01
    # is rerun after geocoding, preserve any existing non-base columns by
    # joining them back on id_centro instead of collapsing the file to SCHEMA.
    if out_path.exists():
        try:
            existing = pd.read_csv(out_path, dtype={"id_centro": str}, low_memory=False)
            extra_cols = [c for c in existing.columns if c not in SCHEMA]
            if extra_cols:
                existing["id_centro"] = existing["id_centro"].astype(str).str.strip()
                base_df["id_centro"] = base_df["id_centro"].astype(str).str.strip()
                extras = existing[["id_centro"] + extra_cols].drop_duplicates(
                    subset="id_centro", keep="first"
                )
                base_df = base_df.merge(extras, on="id_centro", how="left")
        except Exception as e:
            print(f"    WARNING: could not preserve existing enrichment for {iso}: {e}")

    base_df.to_csv(out_path, index=False, encoding='utf-8')
    return out_path


def record(iso, df, note=''):
    total = len(df)
    pub = (df['sector'] == 'Public').sum() if 'sector' in df else 0
    prv = (df['sector'] == 'Private').sum() if 'sector' in df else 0
    geo = (df['latitud'].notna() & df['longitud'].notna()).sum() if 'latitud' in df else 0
    scope = COUNTRY_SCOPE.get(iso, {})
    summary.append({
        'iso': iso,
        'pipeline_enabled': scope.get('pipeline_enabled', True),
        'analysis_included': scope.get('analysis_included', True),
        'final_match_level': scope.get('final_match_level'),
        'validation_tier': scope.get('validation_tier'),
        'data_status': scope.get('data_status'),
        'total_k12': total,
        'public': pub,
        'private': prv,
        'georef': geo,
        'note': note
    })


# ==============================================================================
# ARGENTINA
# ==============================================================================
def process_ARG():
    iso = 'ARG'
    try:
        raw = BASE / iso / 'raw' / '6831 - Listado de establecimientos con caracteristicas básicas.csv'
        df = pd.read_csv(raw, sep=';', encoding='latin-1', low_memory=False)
        # Restrict to sector ∈ {1, 2} = Estatal + Privado. Sector=3 (gestión
        # comunitaria/social/indígena, ~628 rows raw, 55 K-12) queda excluido
        # para alinear con el consultor (ARG_script.qmd:23 filtra sector==1).
        # CIMA conserva las privadas (sector=2) porque el universo publicado en
        # el dashboard es público + privado. Decisión metodológica heredada —
        # abierta a revisión con el consultor (ver step01_consultor_questions).
        df = df[df['sector'].isin([1, 2]) & (df['comun'] == 'X')].copy()

        df['nivel_primaria'] = (df['comun_primaria'].fillna('') == 'X').astype(int)
        df['nivel_secbaja']  = ((df['comun_cb'].fillna('') == 'X') |
                                (df['comun_ambos'].fillna('') == 'X')).astype(int)
        df['nivel_secalta']  = ((df['comun_co'].fillna('') == 'X') |
                                (df['comun_ambos'].fillna('') == 'X')).astype(int)

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        df['sector'] = df['sector'].map({1: 'Public', 2: 'Private'}).fillna('Unknown')
        df['id_centro'] = df['cueanexo'].astype(str)
        # column is 'nombre' in the 6831 file
        nombre_col = next((c for c in df.columns if c.lower() in ('nombre', 'nombre_est', 'nombre_establecimiento')), None)
        df['nombre_centro'] = df[nombre_col].astype(str) if nombre_col else ''
        df['adm0_pcode'] = iso
        df['latitud'] = pd.to_numeric(df['latitud'], errors='coerce')
        df['longitud'] = pd.to_numeric(df['longitud'], errors='coerce')

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={( df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")


# ==============================================================================
# BAHAMAS
# ==============================================================================
def process_BHS():
    iso = 'BHS'
    try:
        # Authoritative source: MoE xlsx received from BID country specialist 2026-05-11.
        # The companion shapefile (bhs_schools_shp/) is a POI dataset that mixes
        # schools with non-schools (libraries, etc.) and is NOT used here.
        raw = BASE / iso / 'raw' / 'BHS_Schools_Districts_GeoL_Enviado_especialistapais.xlsx'
        df = pd.read_excel(raw, sheet_name='All Schools')
        df['_raw_row'] = df.index  # preserve original row number for stable id_centro

        # Drop the "Virtual School Bahamas" row: tagged Island=Virtual / Settlement=Virtual,
        # no physical location, not analytically meaningful as a spatial point.
        df = df[~df['Island'].astype(str).str.strip().str.lower().eq('virtual')].copy()

        # K-12 filter (Bahamas MoE Type taxonomy):
        #   Primary    -> primaria (BID K-1 to grade 6)
        #   Secondary  -> secbaja + secalta (Bahamas Secondary spans grades 7-12,
        #                 mirrors the BLZ/BRB pattern where Secondary covers both
        #                 BID cycles).
        #   All Age    -> primaria + secbaja + secalta (covers full K-12 in single
        #                 building, common in family/out-island settlements).
        #   Pre-primary, Special -> excluded per BID K-12 definition.
        type_col = 'Type'
        is_primary  = df[type_col].fillna('').str.strip().str.lower() == 'primary'
        is_secondary = df[type_col].fillna('').str.strip().str.lower() == 'secondary'
        is_all_age  = df[type_col].fillna('').str.strip().str.lower() == 'all age'

        df['nivel_primaria'] = (is_primary  | is_all_age).astype(int)
        df['nivel_secbaja']  = (is_secondary | is_all_age).astype(int)
        df['nivel_secalta']  = (is_secondary | is_all_age).astype(int)

        df = df[(df['nivel_primaria'] == 1) |
                (df['nivel_secbaja']  == 1) |
                (df['nivel_secalta']  == 1)].copy()

        # Sector: Bahamas Government -> Public, "Private" -> Private, NaN -> Unknown.
        ownership_map = {'Bahamas Government': 'Public', 'Private': 'Private'}
        df['sector'] = df['Ownership'].map(ownership_map).fillna('Unknown')

        # id_centro: raw file has no stable school code column. Use the original
        # xlsx row index prefixed by ISO so IDs stay stable across re-runs and
        # debuggable against the source spreadsheet.
        df['id_centro'] = df['_raw_row'].apply(lambda i: f'BHS-{int(i):03d}')
        df['nombre_centro'] = df['Name'].astype(str).str.strip()
        df['latitud']  = pd.to_numeric(df['Latitude'],  errors='coerce')
        df['longitud'] = pd.to_numeric(df['Longitude'], errors='coerce')
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='xlsx from BID country specialist; ADM1 spatial-join in step-02')
        print(f"  {iso}: {len(df):,} total (Public+Private from MoE xlsx)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# BELIZE
# ==============================================================================
def process_BLZ():
    iso = 'BLZ'
    try:
        raw = BASE / iso / 'raw' / 'geo_schools Belize.xlsx'
        df = pd.read_excel(raw)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        edu_col = next((c for c in df.columns if 'level' in c or 'tipo' in c or 'educ' in c), None)
        lat_col = next((c for c in df.columns if 'lat' in c), None)
        lon_col = next((c for c in df.columns if 'lon' in c or 'lng' in c), None)
        name_col = next((c for c in df.columns if 'name' in c or 'nombre' in c), None)
        id_col = next((c for c in df.columns if 'code' in c or 'id' in c or 'codigo' in c), None)

        if edu_col:
            # Per BID Diccionario oficial: "Primary en BLZ no se puede distinguir entre
            # Elementary y Middle School" → nivel_primaria = nivel_secbaja (ambos Primary).
            # Secondary aplica para nivel_secalta.
            is_primary = df[edu_col].fillna('').str.contains('Primary|Primaria', case=False)
            is_secondary = df[edu_col].fillna('').str.contains('Secondary|Secundaria', case=False)
            df['nivel_primaria'] = is_primary.astype(int)
            df['nivel_secbaja']  = is_primary.astype(int)   # Primary cubre ambos ciclos
            df['nivel_secalta']  = is_secondary.astype(int)
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja']  = 1
            df['nivel_secalta']  = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()
        df['sector'] = 'Public'
        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='No private schools in file')
        print(f"  {iso}: {len(df):,} total (all Public - no private in raw file)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")


# ==============================================================================
# BOLIVIA
# ==============================================================================
def process_BOL():
    iso = 'BOL'
    try:
        raw = BASE / iso / 'raw' / 'MinEdu_InstitucionesEducativas_2023.xlsx'
        df = pd.read_excel(raw, skiprows=7)
        df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]

        sub_col = next((c for c in df.columns if 'sub_sist' in c), None)
        dep_col = next((c for c in df.columns if 'depend' in c), None)
        niv_col = next((c for c in df.columns if 'nivel_auto' in c or 'nivel' in c), None)
        id_col  = next((c for c in df.columns if 'codigo' in c or 'rue' in c or 'cod_r' in c), None)
        name_col = next((c for c in df.columns if 'institucion' in c or 'nombre' in c), None)
        lat_col = next((c for c in df.columns if c in ('cord_x', 'latitud', 'lat')), None)
        lon_col = next((c for c in df.columns if c in ('cord_y', 'longitud', 'lon')), None)

        if sub_col:
            df = df[df[sub_col] == 'Regular'].copy()

        df['nivel_primaria'] = df[niv_col].fillna('').str.contains('Primaria', case=False).astype(int) if niv_col else 0
        df['nivel_secbaja']  = df[niv_col].fillna('').str.contains('Secundaria', case=False).astype(int) if niv_col else 0
        df['nivel_secalta']  = df['nivel_secbaja']

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1)].copy()

        # Sector
        if dep_col:
            df['sector'] = df[dep_col].apply(
                lambda x: 'Private' if str(x).strip().lower() == 'privada' else 'Public')
        else:
            df['sector'] = 'Public'

        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''

        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan

        # Bounds check for Bolivia
        if lat_col:
            df.loc[(df['latitud'] < -23) | (df['latitud'] > 8), 'latitud'] = np.nan
        if lon_col:
            df.loc[(df['longitud'] < -70) | (df['longitud'] > -57), 'longitud'] = np.nan

        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# BRAZIL — coordinate recovery helpers
# ==============================================================================

def _download_geobr_schools(cache_path):
    """Download INEP schools 2023 GeoPackage (same file geobr R/Python wraps), cache locally."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        print(f"    Using cached {cache_path.name} ({cache_path.stat().st_size / 1e6:.0f} MB)")
    else:
        urls = [
            "https://www.ipea.gov.br/geobr/data_gpkg/schools/2023/schools_2023.gpkg",
            "https://github.com/ipeaGIT/geobr/releases/download/v1.7.0/schools_2023.gpkg",
        ]
        for url in urls:
            try:
                print(f"    Downloading {url} ...")
                urllib.request.urlretrieve(url, cache_path)
                break
            except Exception as e:
                print(f"    Failed ({e}), trying next mirror...")
                continue
        else:
            raise ConnectionError("Could not download geobr schools from any mirror")

    gdf = gpd.read_file(cache_path)
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    has_geom = ~gdf.geometry.is_empty
    result = gdf.loc[has_geom, ['code_school']].copy()
    result['latitud'] = gdf.loc[has_geom].geometry.y
    result['longitud'] = gdf.loc[has_geom].geometry.x
    # Drop (0, 0) coordinates
    mask_zero = (result['latitud'] == 0) & (result['longitud'] == 0)
    result.loc[mask_zero, ['latitud', 'longitud']] = np.nan
    result = result.dropna(subset=['latitud', 'longitud'])
    result['code_school'] = result['code_school'].astype(str)
    result = result.drop_duplicates(subset='code_school', keep='first')
    return result


def _load_bra_coord_edu(path):
    """Load BRA_coord_EDU.csv (MEC/Educação Conectada), return DataFrame with coords."""
    df = pd.read_csv(path, sep=';', encoding='utf-8-sig',
                     usecols=['Código INEP', 'Latitude', 'Longitude'])
    df['id_centro'] = df['Código INEP'].astype(str)
    for col in ['Latitude', 'Longitude']:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.', regex=False), errors='coerce')
    df = df.rename(columns={'Latitude': 'latitud', 'Longitude': 'longitud'})
    df = df.dropna(subset=['latitud', 'longitud'])
    return df[['id_centro', 'latitud', 'longitud']].drop_duplicates(subset='id_centro', keep='first')


def _load_bra_geojson_coords(path):
    """Load coords from BRA_total.geojson properties."""
    gdf = gpd.read_file(path)
    cols_needed = ['id_centro', 'latitud', 'longitud']
    if not all(c in gdf.columns for c in cols_needed):
        # Try extracting from geometry if lat/lon not in properties
        if 'id_centro' in gdf.columns and gdf.geometry is not None:
            result = gdf[['id_centro']].copy()
            has_geom = ~(gdf.geometry.is_empty | gdf.geometry.isna())
            result['latitud'] = np.nan
            result['longitud'] = np.nan
            result.loc[has_geom, 'latitud'] = gdf.loc[has_geom].geometry.y
            result.loc[has_geom, 'longitud'] = gdf.loc[has_geom].geometry.x
        else:
            return pd.DataFrame(columns=['id_centro', 'latitud', 'longitud'])
    else:
        result = gdf[cols_needed].copy()
    result['id_centro'] = result['id_centro'].astype(str)
    result = result.dropna(subset=['latitud', 'longitud'])
    return result.drop_duplicates(subset='id_centro', keep='first')


# ==============================================================================
# BRAZIL
# ==============================================================================
def process_BRA():
    iso = 'BRA'
    try:
        raw = BASE / iso / 'raw' / 'microdados_censo_escolar_2023' / 'dados' / 'microdados_ed_basica_2023.csv'
        usecols = ['CO_ENTIDADE', 'NO_ENTIDADE', 'TP_DEPENDENCIA',
                   'TP_SITUACAO_FUNCIONAMENTO', 'IN_FUND_AI', 'IN_FUND_AF', 'IN_MED']
        # BRA microdatos uses latin-1 and has no geo columns directly
        df = pd.read_csv(raw, sep=';', encoding='latin-1', usecols=usecols, low_memory=False)

        df = df[df['TP_SITUACAO_FUNCIONAMENTO'] == 1].copy()
        df['nivel_primaria'] = df['IN_FUND_AI'].fillna(0).astype(int)
        df['nivel_secbaja']  = df['IN_FUND_AF'].fillna(0).astype(int)
        df['nivel_secalta']  = df['IN_MED'].fillna(0).astype(int)

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        df['sector'] = df['TP_DEPENDENCIA'].apply(
            lambda x: 'Private' if x == 4 else 'Public')
        df['id_centro'] = df['CO_ENTIDADE'].astype(str)
        df['nombre_centro'] = df['NO_ENTIDADE'].astype(str)
        df['latitud'] = np.nan
        df['longitud'] = np.nan
        df['adm0_pcode'] = iso

        # ---- Coordinate recovery cascade ----
        n_total = len(df)

        # Source 1: INEP GeoPackage (geobr) — ALL schools, public + private
        print(f"    Recovering coordinates ...")
        geobr_cache = BASE / iso / 'raw' / 'schools_2023.gpkg'
        try:
            geobr_df = _download_geobr_schools(geobr_cache)
            merged = df[['id_centro']].merge(
                geobr_df, left_on='id_centro', right_on='code_school', how='left',
                suffixes=('', '_geobr'))
            mask = merged['latitud_geobr'].notna() if 'latitud_geobr' in merged.columns else merged['latitud'].notna()
            lat_col = 'latitud_geobr' if 'latitud_geobr' in merged.columns else 'latitud'
            lon_col = 'longitud_geobr' if 'longitud_geobr' in merged.columns else 'longitud'
            df.loc[mask.values, 'latitud'] = merged.loc[mask, lat_col].values
            df.loc[mask.values, 'longitud'] = merged.loc[mask, lon_col].values
            n_geobr = mask.sum()
            print(f"    geobr: {n_geobr:,} coords recovered")
        except Exception as e:
            n_geobr = 0
            print(f"    geobr: FAILED - {e}")

        # Source 2: BRA_coord_EDU.csv (MEC/Educação Conectada)
        still_missing = df['latitud'].isna()
        if still_missing.any():
            edu_path = BASE / iso / '08. Brasil' / 'BRA_coord_EDU.csv'
            if edu_path.exists():
                try:
                    edu_df = _load_bra_coord_edu(edu_path)
                    idx_missing = df.index[still_missing]
                    matched = df.loc[idx_missing, ['id_centro']].merge(
                        edu_df, on='id_centro', how='left')
                    mask2 = matched['latitud'].notna()
                    df.loc[idx_missing[mask2.values], 'latitud'] = matched.loc[mask2, 'latitud'].values
                    df.loc[idx_missing[mask2.values], 'longitud'] = matched.loc[mask2, 'longitud'].values
                    n_edu = mask2.sum()
                    print(f"    BRA_coord_EDU: {n_edu:,} coords recovered")
                except Exception as e:
                    print(f"    BRA_coord_EDU: FAILED - {e}")

        # Source 3: BRA_total.geojson (R pipeline, public only)
        still_missing = df['latitud'].isna()
        if still_missing.any():
            geojson_path = BASE / iso / 'processed' / 'BRA_total.geojson'
            if geojson_path.exists():
                try:
                    geo_df = _load_bra_geojson_coords(geojson_path)
                    idx_missing = df.index[still_missing]
                    matched = df.loc[idx_missing, ['id_centro']].merge(
                        geo_df, on='id_centro', how='left')
                    mask3 = matched['latitud'].notna()
                    df.loc[idx_missing[mask3.values], 'latitud'] = matched.loc[mask3, 'latitud'].values
                    df.loc[idx_missing[mask3.values], 'longitud'] = matched.loc[mask3, 'longitud'].values
                    n_geojson = mask3.sum()
                    print(f"    BRA_total.geojson: {n_geojson:,} coords recovered")
                except Exception as e:
                    print(f"    BRA_total.geojson: FAILED - {e}")

        # Summary
        n_georef = df['latitud'].notna().sum()
        n_missing = n_total - n_georef
        print(f"    --- BRA coords: {n_georef:,}/{n_total:,} ({100*n_georef/n_total:.1f}%) | missing: {n_missing:,} ---")

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# BARBADOS
# ==============================================================================
def process_BRB():
    iso = 'BRB'
    try:
        raw = BASE / iso / 'raw' / 'Barbados Geolocalización Escuelas 2024.xlsx'
        df = pd.read_excel(raw)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        edu_col = next((c for c in df.columns if 'level' in c or 'tipo' in c), None)
        lat_col = next((c for c in df.columns if 'lat' in c), None)
        lon_col = next((c for c in df.columns if 'lon' in c or 'lng' in c), None)
        name_col = next((c for c in df.columns if 'name' in c or 'nombre' in c), None)
        id_col = next((c for c in df.columns if 'code' in c or 'id' in c), None)

        if edu_col:
            df['nivel_primaria'] = df[edu_col].fillna('').str.contains('Primary|Primaria', case=False).astype(int)
            df['nivel_secbaja']  = df[edu_col].fillna('').str.contains('Secondary|Secundaria', case=False).astype(int)
            df['nivel_secalta']  = df['nivel_secbaja']
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja'] = 0
            df['nivel_secalta'] = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1)].copy()
        df['sector'] = 'Public'  # no sector info in file
        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='No sector info in file')
        print(f"  {iso}: {len(df):,} total (no sector info)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")


# ==============================================================================
# CHILE
# ==============================================================================
def process_CHL():
    iso = 'CHL'
    try:
        raw = BASE / iso / 'raw' / '20230912_Directorio_Oficial_EE_2023_20230430_WEB.csv'
        df = pd.read_csv(raw, sep=';', low_memory=False)
        df.columns = [c.lower() for c in df.columns]

        # Universo de escuelas:
        # • Públicas: directorio ∩ SAE (Sistema de Admisión Escolar) — replica
        #   el universo legacy R (CHL_script.qmd:327 `left_join(sae, directorio)`).
        # • Privadas (cod_depe=4, Particular Pagado): se conservan del directorio
        #   completo, ya que SAE solo cubre escuelas subvencionadas. El R legacy
        #   era public-only y no las incluía.
        sae_path = BASE / iso / '17. Chile' / 'A1_Oferta_Establecimientos_etapa_regular_2023_Admisión_2024.csv'
        sae = pd.read_csv(sae_path, sep=';', low_memory=False, encoding='utf-8-sig')
        sae.columns = [c.lower().strip().lstrip('﻿') for c in sae.columns]
        sae_rbds = set(
            sae[sae['cod_ense'].isin([10, 110, 310, 410, 510, 610, 710, 810, 910])]
            ['rbd'].astype(int).unique()
        )
        keep_mask = df['rbd'].astype(int).isin(sae_rbds) | (df['cod_depe'] == 4)
        df = df[keep_mask].copy()

        # Identify ENS columns (education type codes)
        ens_cols = [c for c in df.columns if c.startswith('ens_')]

        # Mapeo alineado al legacy R (CHL_script.qmd:111-113):
        #   cod_ense = 110 (Educación Básica, grados 1-8) → primaria=1 Y secbaja=1
        #     porque básica cubre BID primaria (1-6) + parte de secbaja (7-8).
        #   cod_ense ∈ {310, 410, 510, 610, 710, 810, 910} (Educación Media) → secalta=1
        #     porque media corresponde a grados 9-12 ≈ BID secalta. (El grado 9 es
        #     técnicamente secbaja BID pero el R lo agrupa con secalta; lo seguimos
        #     para mantener match. Decisión metodológica heredada, ver step01_notes.)
        ens_df = df[ens_cols].copy()
        df['nivel_primaria'] = ens_df.isin([110]).any(axis=1).astype(int)
        df['nivel_secbaja']  = ens_df.isin([110]).any(axis=1).astype(int)
        df['nivel_secalta']  = ens_df.isin([310, 410, 510, 610, 710, 810, 910]).any(axis=1).astype(int)

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        # Lat/lon may be named LATITUD / LONGITUD (uppercase in directorio)
        # Values use comma as decimal separator (e.g. '-18,487200000000000')
        lat_c = next((c for c in df.columns if c.lower() == 'latitud'), None)
        lon_c = next((c for c in df.columns if c.lower() == 'longitud'), None)
        if lat_c:
            df['latitud']  = df[lat_c].astype(str).str.replace(',', '.', regex=False).pipe(pd.to_numeric, errors='coerce')
            df['longitud'] = df[lon_c].astype(str).str.replace(',', '.', regex=False).pipe(pd.to_numeric, errors='coerce')

        # Aggregate to school level (one row per rbd)
        # COD_DEPE codes:
        # 1=Corp.Municipal, 2=DAEM Municipal, 3=Part.Subvencionado (state-voucher, no tuition → Public),
        # 4=Part.Pagado (full-tuition, truly Private), 5=Corp.Adm.Delegada, 6=SLE
        agg_dict = {
            'nom_rbd': ('nom_rbd', 'first'),
            'sector_raw': ('cod_depe', lambda x: 'Private' if (x == 4).any() else 'Public'),
            'nivel_primaria': ('nivel_primaria', 'max'),
            'nivel_secbaja': ('nivel_secbaja', 'max'),
            'nivel_secalta': ('nivel_secalta', 'max'),
        }
        if lat_c:
            agg_dict['latitud']  = ('latitud', 'first')
            agg_dict['longitud'] = ('longitud', 'first')

        grouped = df.groupby('rbd').agg(**agg_dict).reset_index()
        grouped.rename(columns={'rbd': 'id_centro', 'nom_rbd': 'nombre_centro',
                                 'sector_raw': 'sector'}, inplace=True)
        grouped['id_centro'] = grouped['id_centro'].astype(str)
        if lat_c is None:
            grouped['latitud']  = np.nan
            grouped['longitud'] = np.nan
        grouped['adm0_pcode'] = iso

        save_cima(grouped, iso)
        record(iso, grouped)
        print(f"  {iso}: {len(grouped):,} total (Public={(grouped['sector']=='Public').sum():,}, Private={(grouped['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# COLOMBIA
# ==============================================================================
def process_COL():
    iso = 'COL'
    try:
        caratula = pd.read_csv(
            BASE / iso / 'raw' / 'DANE_2023' / 'Carátula única de la sede educativa.CSV',
            encoding='latin-1', engine='python', on_bad_lines='skip')
        caratula.columns = [c.lower().strip() for c in caratula.columns]

        # Active schools only (not liquidated/inactive)
        if 'estado_id' in caratula.columns:
            caratula = caratula[caratula['estado_id'] != 0]
        if 'novedad_id' in caratula.columns:
            caratula = caratula[caratula['novedad_id'] == 9]

        # Sector - keep both public (1) and private (2)
        if 'sector_id' in caratula.columns:
            caratula['sector'] = caratula['sector_id'].map({1: 'Public', 2: 'Private'}).fillna('Unknown')
        else:
            caratula['sector'] = 'Unknown'

        id_col = 'sede_codigo' if 'sede_codigo' in caratula.columns else caratula.columns[0]
        name_col = 'sede_nombre' if 'sede_nombre' in caratula.columns else None

        # Characteristics (niveles)
        caract = pd.read_csv(
            BASE / iso / 'raw' / 'DANE_2023' / 'Características generales del servicio ofrecido por sede educativa.CSV',
            encoding='latin-1', low_memory=False)
        caract.columns = [c.lower().strip() for c in caract.columns]

        # nivelense_id: 1=Preescolar, 2=Primaria, 3=Secbaja, 4=Media
        niv_col = next((c for c in caract.columns if 'nivel' in c and 'ense' in c), None)
        scode = 'sede_codigo' if 'sede_codigo' in caract.columns else caract.columns[0]

        if niv_col:
            k12_ids = caract[caract[niv_col].isin([2, 3, 4])][scode].unique()
            caract_agg = caract[caract[scode].isin(k12_ids)].groupby(scode).agg(
                nivel_primaria=(niv_col, lambda x: int(2 in x.values)),
                nivel_secbaja=(niv_col, lambda x: int(3 in x.values)),
                nivel_secalta=(niv_col, lambda x: int(4 in x.values)),
            ).reset_index()
        else:
            caract_agg = caract[[scode]].drop_duplicates().copy()
            caract_agg['nivel_primaria'] = 1
            caract_agg['nivel_secbaja'] = 0
            caract_agg['nivel_secalta'] = 0

        caract_agg.rename(columns={scode: id_col}, inplace=True)

        # Merge
        df = caratula.merge(caract_agg, on=id_col, how='inner')

        # Coordinates
        try:
            coords = pd.read_csv(BASE / iso / 'raw' / 'Colombia_CE_coordenadas.csv',
                                  encoding='latin-1', sep=';', low_memory=False)
            coords.columns = [c.lower().strip() for c in coords.columns]
            lat_col = next((c for c in coords.columns if 'lat' in c), None)
            lon_col = next((c for c in coords.columns if 'lon' in c or 'lng' in c), None)
            coord_id = next((c for c in coords.columns if 'sede' in c or 'codigo' in c), None)
            if lat_col and lon_col and coord_id:
                coords = coords[[coord_id, lat_col, lon_col]].copy()
                coords.rename(columns={coord_id: id_col, lat_col: 'latitud', lon_col: 'longitud'}, inplace=True)
                df = df.merge(coords, on=id_col, how='left')
        except Exception:
            df['latitud'] = np.nan
            df['longitud'] = np.nan

        if 'latitud' not in df.columns:
            df['latitud'] = np.nan
        if 'longitud' not in df.columns:
            df['longitud'] = np.nan

        df['id_centro'] = df[id_col].astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# Override the legacy COL parser above: the raw DANE Carátula file contains a
# small set of unquoted commas that cause pandas tokenization to skip valid
# public K-12 schools. Keep the old definition for reference and use this
# repaired one at runtime.
def process_COL():
    iso = 'COL'
    try:
        caratula_path = BASE / iso / 'raw' / 'DANE_2023' / 'Carátula única de la sede educativa.CSV'
        caratula_text = caratula_path.read_text(encoding='latin-1')
        for source, target in {
            'Bogotá, D.C.': 'Bogotá D.C.',
            'Archipiélago de San Andrés, Providencia y Santa Catalina': 'Archipiélago de San Andrés Providencia y Santa Catalina',
            'www,': 'www.',
        }.items():
            caratula_text = caratula_text.replace(source, target)

        caratula = pd.read_csv(StringIO(caratula_text), encoding='latin-1', low_memory=False)
        caratula.columns = [c.lower().strip() for c in caratula.columns]

        if 'estado_id' in caratula.columns:
            caratula = caratula[caratula['estado_id'] != 0]
        if 'novedad_id' in caratula.columns:
            caratula = caratula[caratula['novedad_id'] == 9]

        if 'sector_id' in caratula.columns:
            caratula['sector'] = caratula['sector_id'].map({1: 'Public', 2: 'Private'}).fillna('Unknown')
        else:
            caratula['sector'] = 'Unknown'

        id_col = 'sede_codigo' if 'sede_codigo' in caratula.columns else caratula.columns[0]
        name_col = 'sede_nombre' if 'sede_nombre' in caratula.columns else None
        caratula[id_col] = caratula[id_col].astype(str).str.replace(r'\.0$', '', regex=True)

        caract = pd.read_csv(
            BASE / iso / 'raw' / 'DANE_2023' / 'Características generales del servicio ofrecido por sede educativa.CSV',
            encoding='latin-1',
            low_memory=False,
        )
        caract.columns = [c.lower().strip() for c in caract.columns]

        niv_col = next((c for c in caract.columns if 'nivel' in c and 'ense' in c), None)
        scode = 'sede_codigo' if 'sede_codigo' in caract.columns else caract.columns[0]
        caract[scode] = caract[scode].astype(str).str.replace(r'\.0$', '', regex=True)

        if niv_col:
            k12_ids = caract[caract[niv_col].isin([2, 3, 4])][scode].unique()
            caract_agg = caract[caract[scode].isin(k12_ids)].groupby(scode).agg(
                nivel_primaria=(niv_col, lambda x: int(2 in x.values)),
                nivel_secbaja=(niv_col, lambda x: int(3 in x.values)),
                nivel_secalta=(niv_col, lambda x: int(4 in x.values)),
            ).reset_index()
        else:
            caract_agg = caract[[scode]].drop_duplicates().copy()
            caract_agg['nivel_primaria'] = 1
            caract_agg['nivel_secbaja'] = 0
            caract_agg['nivel_secalta'] = 0

        caract_agg.rename(columns={scode: id_col}, inplace=True)

        df = caratula.merge(caract_agg, on=id_col, how='inner')

        try:
            coords = pd.read_csv(
                BASE / iso / 'raw' / 'Colombia_CE_coordenadas.csv',
                encoding='latin-1',
                sep=';',
                low_memory=False,
            )
            coords.columns = [c.lower().strip() for c in coords.columns]
            lat_col = next((c for c in coords.columns if 'lat' in c), None)
            lon_col = next((c for c in coords.columns if 'lon' in c or 'lng' in c), None)
            coord_id = next((c for c in coords.columns if 'sede' in c or 'codigo' in c), None)
            if lat_col and lon_col and coord_id:
                coords = coords[[coord_id, lat_col, lon_col]].copy()
                coords[coord_id] = coords[coord_id].astype(str).str.replace(r'\.0$', '', regex=True)
                coords.rename(columns={coord_id: id_col, lat_col: 'latitud', lon_col: 'longitud'}, inplace=True)
                df = df.merge(coords, on=id_col, how='left')
        except Exception:
            df['latitud'] = np.nan
            df['longitud'] = np.nan

        if 'latitud' not in df.columns:
            df['latitud'] = np.nan
        if 'longitud' not in df.columns:
            df['longitud'] = np.nan

        df['id_centro'] = df[id_col].astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# COSTA RICA
# ==============================================================================
def process_CRI():
    iso = 'CRI'
    try:
        raw = BASE / iso / 'raw' / 'NominaCentrosEducativos2024.xlsx'

        def read_sheet(sheet, skip=5):
            df = pd.read_excel(raw, sheet_name=sheet, skiprows=skip)
            df.columns = [str(c).strip().replace('\n', ' ') for c in df.columns]
            dep_col = next((c for c in df.columns if 'DEPEND' in c.upper()), None)
            id_col  = next((c for c in df.columns if 'CODIGO' in c.upper() or 'PRESUP' in c.upper()), None)
            name_col = next((c for c in df.columns if 'NOMBRE' in c.upper() or 'INSTITUCION' in c.upper()), None)
            circ_col = next((c for c in df.columns if 'CIRCUIT' in c.upper()), None)
            return df, dep_col, id_col, name_col, circ_col

        # Primary (I y II Ciclos)
        prim_df, dep, id_c, nm, circ = read_sheet('I y II Ciclos')
        if circ:
            prim_df = prim_df[prim_df[circ].notna()].copy()
        prim_df['nivel_primaria'] = 1
        prim_df['nivel_secbaja']  = 0
        prim_df['nivel_secalta']  = 0

        # Secondary (Colegios)
        sec_df, dep2, id_c2, nm2, circ2 = read_sheet('Colegios')
        if circ2:
            sec_df = sec_df[sec_df[circ2].notna()].copy()
        sec_df['nivel_primaria'] = 0
        sec_df['nivel_secbaja']  = 1
        sec_df['nivel_secalta']  = 1

        # Standardize columns for concat
        for df, d, i, n in [(prim_df, dep, id_c, nm), (sec_df, dep2, id_c2, nm2)]:
            if d:
                df.rename(columns={d: 'DEPENDENCIA'}, inplace=True)
            if i:
                df.rename(columns={i: 'id_centro'}, inplace=True)
            if n:
                df.rename(columns={n: 'nombre_centro'}, inplace=True)

        combined = pd.concat([prim_df, sec_df], ignore_index=True)

        if 'DEPENDENCIA' in combined.columns:
            # SUB = Subvencionado (escuelas privadas con subsidio del MEP).
            # Costa Rica las trata como categoría aparte; conceptualmente son
            # privadas con financiamiento estatal. Las clasificamos como Private
            # para mantener match exacto con el legacy R en Public (que filtra
            # solo `dependencia=="PUB"`). El legacy las excluye completamente;
            # CIMA las preserva como Private para análisis de accesibilidad.
            combined['sector'] = combined['DEPENDENCIA'].apply(
                lambda x: 'Private' if str(x).strip().upper() in ('PRI', 'SUB') else 'Public')
        else:
            combined['sector'] = 'Public'

        if 'id_centro' not in combined.columns:
            combined['id_centro'] = combined.index.astype(str)
        if 'nombre_centro' not in combined.columns:
            combined['nombre_centro'] = ''

        combined['id_centro'] = combined['id_centro'].astype(str)

        # Las privadas (PRI) no tienen codigo_presup en el raw del MEP (es
        # presupuesto estatal, no aplica). Asignar synthetic IDs para no perderlas.
        invalid_set = ['0', '0.0', '0000', 'nan', 'None', '']
        mask_priv_no_id = (combined['sector'] == 'Private') & combined['id_centro'].isin(invalid_set)
        n_priv_synth = mask_priv_no_id.sum()
        if n_priv_synth:
            combined.loc[mask_priv_no_id, 'id_centro'] = [
                f"CR_PRI_{i:05d}" for i in range(n_priv_synth)
            ]
            print(f"    CRI: {n_priv_synth:,} privadas con codigo_presup=0 asignadas synthetic ID")

        # Para Public con codigo_presup invalido: asignar synthetic IDs (igual
        # que el R recupera estas escuelas con prefijo "no_id_*"). Sin esto
        # faltan ~11 escuelas K-12 públicas reales que sí tienen matrícula y
        # coordenadas pero no recibieron código presupuestal.
        mask_pub_no_id = (combined['sector'] == 'Public') & combined['id_centro'].isin(invalid_set)
        n_pub_synth = mask_pub_no_id.sum()
        if n_pub_synth:
            combined.loc[mask_pub_no_id, 'id_centro'] = [
                f"CR_PUB_{i:05d}" for i in range(n_pub_synth)
            ]
            print(f"    CRI: {n_pub_synth:,} PUB sin codigo_presup asignadas synthetic ID")

        # Aggregate by id_centro
        agg = combined.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            sector=('sector', lambda x: 'Private' if 'Private' in x.values else 'Public'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
        ).reset_index()

        # Coordinates from MEP_CE_PUBLICOS.xlsx (separate geo file used by the R script)
        geo_raw = BASE / iso / 'raw' / '20250711_MEP_CE_PUBLICOS.xlsx'
        try:
            geo = pd.read_excel(geo_raw)
            geo.columns = [c.lower().strip().replace(' ', '_') for c in geo.columns]
            geo_id = next((c for c in geo.columns if 'codpres' in c or 'cod_pres' in c or 'id_centro' in c), None)
            geo_lat = next((c for c in geo.columns if 'lat' in c), None)
            geo_lon = next((c for c in geo.columns if 'lon' in c or 'lng' in c), None)
            if geo_id and geo_lat and geo_lon:
                geo_coords = geo[[geo_id, geo_lat, geo_lon]].copy()
                geo_coords.rename(columns={geo_id: 'id_centro',
                                           geo_lat: 'latitud', geo_lon: 'longitud'}, inplace=True)
                geo_coords['latitud']  = pd.to_numeric(geo_coords['latitud'], errors='coerce')
                geo_coords['longitud'] = pd.to_numeric(geo_coords['longitud'], errors='coerce')
                # Normalize ID: convert to int string to strip leading zeros
                def norm_id(x):
                    try:
                        return str(int(float(str(x))))
                    except:
                        return str(x).strip()
                geo_coords['id_centro'] = geo_coords['id_centro'].apply(norm_id)
                agg['id_centro'] = agg['id_centro'].apply(norm_id)
                # Aggregate to one coord per school
                geo_coords = geo_coords.groupby('id_centro').first().reset_index()
                agg = agg.merge(geo_coords, on='id_centro', how='left')
            else:
                agg['latitud'] = np.nan
                agg['longitud'] = np.nan
        except Exception as e_geo:
            print(f"    CRI geo file error: {e_geo}")
            agg['latitud'] = np.nan
            agg['longitud'] = np.nan

        agg['adm0_pcode'] = iso

        # K-12 filter
        agg = agg[(agg['nivel_primaria'] == 1) | (agg['nivel_secbaja'] == 1)].copy()

        save_cima(agg, iso)
        record(iso, agg, note='Coords from MEP_CE_PUBLICOS.xlsx; private schools from NominaCentros PRI flag')
        print(f"  {iso}: {len(agg):,} total (Public={(agg['sector']=='Public').sum():,}, Private={(agg['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# DOMINICAN REPUBLIC
# ==============================================================================
def _parse_dom_coord(value, *, force_west: bool = False):
    """Extract a decimal coordinate from MINERD's messy DOM coord strings."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    s = str(value).strip()
    if not s or s.lower() == 'nan':
        return np.nan

    matches = re.findall(r"-?\d+(?:[.,]\d+)?", s.replace(" ", ""))
    if not matches:
        return np.nan

    try:
        parsed = float(matches[-1].replace(",", "."))
    except ValueError:
        return np.nan

    if force_west and parsed > 0:
        parsed = -parsed
    return parsed


def _build_dom_prior_year_coord_lookup(df, *, ano_col: str | None):
    """Valid 2022-2023 DOM coordinates keyed by id_centro for fallbacks."""
    if not ano_col or ano_col not in df.columns or "id_centro" not in df.columns:
        return pd.DataFrame(columns=["id_centro", "latitud_prev", "longitud_prev"])

    lat_raw = next((c for c in df.columns if 'lat' in c and 'coord' in c), None)
    lon_raw = next((c for c in df.columns if 'lon' in c and 'coord' in c), None)
    if not lat_raw or not lon_raw:
        return pd.DataFrame(columns=["id_centro", "latitud_prev", "longitud_prev"])

    prior = df[df[ano_col] == 20222023].copy()
    if prior.empty:
        return pd.DataFrame(columns=["id_centro", "latitud_prev", "longitud_prev"])

    prior["latitud_prev"] = prior[lat_raw].apply(_parse_dom_coord)
    prior["longitud_prev"] = prior[lon_raw].apply(lambda v: _parse_dom_coord(v, force_west=True))
    valid = (
        prior["latitud_prev"].between(17.0, 21.5)
        & prior["longitud_prev"].between(-75.5, -68.0)
    )
    prior = prior.loc[valid, ["id_centro", "latitud_prev", "longitud_prev"]].copy()
    if prior.empty:
        return prior
    return prior.groupby("id_centro", as_index=False).first()


def _find_dom_year_column(df):
    """Find the DOM academic-year column by its values, not just its name."""
    valid_years = {20222023, 20232024}
    for col in df.columns:
        numeric = pd.to_numeric(df[col], errors='coerce').dropna()
        if numeric.empty:
            continue
        values = set(numeric.astype(int).unique().tolist())
        if values and values.issubset(valid_years):
            return col
    return None


def process_DOM():
    iso = 'DOM'
    try:
        raw = BASE / iso / 'raw' / 'RTz-8sq-centros-educativos-de-republica-dominicana-periodo-escolar-2023-2024csv.csv'
        df = pd.read_csv(raw, sep=';', encoding='latin-1', low_memory=False)
        # Normalize: lowercase + replace spaces/accents
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        # Derive ids on the full raw file first so we can build a fallback
        # lookup from the older 2022-2023 rows before filtering to 2023-2024.
        if 'centros' in df.columns:
            df['id_centro'] = df['centros'].astype(str).str.extract(r'^(\d+)')[0]
            df['nombre_centro'] = df['centros'].astype(str).str.extract(r'-\s*(.*)$')[0]
        else:
            df['id_centro'] = df.index.astype(str)
            df['nombre_centro'] = ''

        # Filter to academic year 2023-2024 (más reciente). El legacy R usa
        # 2022-2023 (DOM_script.qmd:26) y por tanto no incluye privadas (no
        # publicadas en ese año). CIMA usa 2023-2024 para preservar las ~2,500
        # escuelas privadas. Diferencia con legacy se explica por año en notas
        # del dashboard / step01_consultor_questions.
        ano_col = _find_dom_year_column(df)
        prior_coords = _build_dom_prior_year_coord_lookup(df, ano_col=ano_col)
        if ano_col:
            df = df[df[ano_col] == 20232024].copy()

        sector_col = next((c for c in df.columns if c.lower() == 'sector'), None)
        df['sector'] = df[sector_col].apply(
            lambda x: 'Private' if str(x).strip().upper() == 'PRIVADO' else 'Public') if sector_col else 'Public'

        # Mapeo alineado al legacy R (DOM_script.qmd:30-32):
        #   contains('PRIMARIO') → primaria=1 Y secbaja=1 (Nivel Básico cubre ambos ciclos)
        #   nivel == 'SECUNDARIO' (igualdad exacta) → secalta=1
        # Esto excluye de secalta a las 3,442 escuelas "INICIAL - PRIMARIO - SECUNDARIO"
        # y 128 "PRIMARIO - SECUNDARIO" (que sí ofrecen Nivel Medio físicamente). Es
        # decisión heredada del consultor — pregunta abierta en step01_consultor_questions.
        is_primario = df['nivel'].fillna('').str.contains('PRIMARIO', case=False)
        is_secundario_exact = df['nivel'].fillna('').str.strip().eq('SECUNDARIO')
        df['nivel_primaria'] = is_primario.astype(int)
        df['nivel_secbaja']  = is_primario.astype(int)   # Básico cubre ambos ciclos
        df['nivel_secalta']  = is_secundario_exact.astype(int)  # Medio (solo "SECUNDARIO" exacto)

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        # Coordinates — el raw 2023-2024 tiene problemas de parseo: latitudes con
        # comas trailing y a veces el signo negativo de la longitud "filtrado" en
        # la columna de latitud (e.g. lat='18.16494,-', lon='71.701323'). Limpieza
        # mínima para recuperar los valores válidos.
        lon_raw = next((c for c in df.columns if 'lon' in c and 'coord' in c), None)
        lat_raw = next((c for c in df.columns if 'lat' in c and 'coord' in c), None)
        if lat_raw:
            df['latitud'] = df[lat_raw].apply(_parse_dom_coord)
        else:
            df['latitud'] = np.nan
        if lon_raw:
            df['longitud'] = df[lon_raw].apply(lambda v: _parse_dom_coord(v, force_west=True))
            # DOM siempre está en hemisferio oeste; si falta el signo negativo
            # (probable spillover en parseo), forzarlo.
            df.loc[(df['longitud'] > -60) | (df['longitud'] < -75), 'longitud'] = np.nan
        else:
            df['longitud'] = np.nan

        # Recover impossible / missing 2023-2024 coords from the same
        # school's valid 2022-2023 row.
        n_dom_backfilled = 0
        if not prior_coords.empty:
            df = df.merge(prior_coords, on='id_centro', how='left')
            lat_invalid = df['latitud'].isna() | ~df['latitud'].between(17.0, 21.5)
            lon_invalid = df['longitud'].isna() | ~df['longitud'].between(-75.5, -68.0)
            use_prior = (
                (lat_invalid | lon_invalid)
                & df['latitud_prev'].notna()
                & df['longitud_prev'].notna()
            )
            n_dom_backfilled = int(use_prior.sum())
            df.loc[use_prior, 'latitud'] = df.loc[use_prior, 'latitud_prev']
            df.loc[use_prior, 'longitud'] = df.loc[use_prior, 'longitud_prev']
            df = df.drop(columns=['latitud_prev', 'longitud_prev'])

        # Aggregate by id_centro (drop rows with no id)
        df = df[df['id_centro'].notna() & (df['id_centro'] != 'nan')].copy()
        agg = df.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            sector=('sector', lambda x: 'Private' if 'Private' in x.values else 'Public'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
            latitud=('latitud', 'first'),
            longitud=('longitud', 'first'),
        ).reset_index()
        agg['adm0_pcode'] = iso

        save_cima(agg, iso)
        note = 'Año académico 2023-2024. Legacy R usa 2022-2023 (que no incluye privadas en raw MINERD). Diferencia explicada por año.'
        if n_dom_backfilled:
            note += f' Backfill DOM desde 2022-2023 para {n_dom_backfilled} escuela(s) con coord 2023-2024 inválida.'
        record(iso, agg, note=note)
        print(f"  {iso}: {len(agg):,} total (Public={(agg['sector']=='Public').sum():,}, Private={(agg['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# ECUADOR
# ==============================================================================
def process_ECU():
    iso = 'ECU'
    try:
        raw_csv = BASE / iso / 'raw' / '2_MINEDUC_RegistrosAdministrativos_2024-2025Inicio.csv'
        df = pd.read_csv(raw_csv, sep=';', low_memory=False, encoding='utf-8')
        df.columns = [c.lower().strip() for c in df.columns]

        tipo_col = next((c for c in df.columns if 'tipo educ' in c or 'tipo_educ' in c), None)
        if tipo_col:
            df = df[df[tipo_col].str.strip() == 'Ordinaria'].copy()

        sost_col = next((c for c in df.columns if 'sostenimie' in c or 'sostenimien' in c), None)
        if sost_col:
            df['sector'] = df[sost_col].apply(
                lambda x: 'Private' if str(x).strip() == 'Particular' else 'Public')
        else:
            df['sector'] = 'Public'

        niv_col = next((c for c in df.columns if 'nivel educ' in c or 'nivel_educ' in c), None)
        if niv_col:
            # Educacion Basica / EGB cubre grados 1-10 (primaria + secundaria baja)
            basica_mask = df[niv_col].fillna('').str.contains(r'B[áa]sica|EGB', case=False, regex=True)
            df['nivel_primaria'] = basica_mask.astype(int)
            df['nivel_secbaja']  = basica_mask.astype(int)
            df['nivel_secalta']  = df[niv_col].fillna('').str.contains('Bachillerato', case=False).astype(int)
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja'] = 0
            df['nivel_secalta'] = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()

        id_col   = next((c for c in df.columns if 'amie' in c), None)
        name_col = next((c for c in df.columns if 'nombre_inst' in c or 'nombre' in c), None)

        # Coordinates from shapefile (UTM Zone 17S → WGS84)
        shp_path = BASE / iso / 'raw' / 'mineduc_ies_20242025_02122024' / 'ie_2024_2025' / 'ie_2024_2025.shp'
        try:
            import shapefile as shp_lib
            from pyproj import Transformer
            sf = shp_lib.Reader(str(shp_path))
            flds = [f[0].lower() for f in sf.fields[1:]]
            amie_idx = next((i for i, f in enumerate(flds) if 'amie' in f), None)
            records = [(str(r[amie_idx]), sf.shape(i).points[0][0], sf.shape(i).points[0][1])
                       for i, r in enumerate(sf.iterRecords())
                       if amie_idx is not None and sf.shape(i).shapeType != 0]
            coords_df = pd.DataFrame(records, columns=['amie', 'easting', 'northing'])
            # ECU shapefile is EPSG:32717 (UTM 17S) — reproject to WGS84
            transformer = Transformer.from_crs("EPSG:32717", "EPSG:4326", always_xy=True)
            lon_4326, lat_4326 = transformer.transform(
                coords_df['easting'].values, coords_df['northing'].values)
            coords_df['longitud'] = lon_4326
            coords_df['latitud'] = lat_4326
        except Exception as e_shp:
            print(f"    ECU shapefile error: {e_shp}")
            coords_df = None

        if id_col:
            df[id_col] = df[id_col].astype(str)
            if coords_df is not None:
                df = df.merge(coords_df, left_on=id_col, right_on='amie', how='left')
            else:
                df['latitud'] = np.nan
                df['longitud'] = np.nan
            df['id_centro'] = df[id_col]
        else:
            df['id_centro'] = df.index.astype(str)
            df['latitud'] = np.nan
            df['longitud'] = np.nan

        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# GUATEMALA
# ==============================================================================
def process_GTM():
    iso = 'GTM'
    try:
        shp_path = BASE / iso / 'raw' / 'sire_2024_filtrado' / 'sire_2024_filtrado.shp'
        import shapefile as shp_lib
        sf = shp_lib.Reader(str(shp_path), encoding='latin-1')
        flds = [f[0] for f in sf.fields[1:]]   # keep original names (may have accent chars)
        flds_lower = [f.lower() for f in flds]

        # GTM field layout (confirmed):
        # 0=cã³digo(codigo), 1=departamen, 2=mupio, 3=municipio, 4=nombre,
        # 5=direcciã³n, 6=nivel, 7=sector, 8=situacion, 9=jornada,
        # 10=modalidad, 11=plan, 12=latitud, 13=longitud, 14=verficar
        idx_id     = 0
        idx_nombre = 4
        idx_nivel  = 6
        idx_sector = 7
        idx_sit    = 8
        idx_lat    = 12
        idx_lon    = 13

        rows = []
        for i, rec in enumerate(sf.iterRecords()):
            r = list(rec)
            sit = str(r[idx_sit]).strip().upper()
            # Alineado al consultor R (GTM_script.qmd:22): excluir solo
            # CERRADA DEFINITIVAMENTE; CERRADA TEMPORALMENTE sí se incluye porque
            # son escuelas que pueden reabrir y tienen coordenadas válidas.
            if sit == 'CERRADA DEFINITIVAMENTE':
                continue
            nivel_val = str(r[idx_nivel]).strip().upper()
            if 'ADULTOS' in nivel_val:
                continue
            sector_val = str(r[idx_sector]).strip().upper()
            sector = 'Private' if sector_val == 'PRIVADO' else 'Public'
            nivel_prim = 1 if nivel_val == 'PRIMARIA' else 0
            nivel_secb = 1 if 'BASICO' in nivel_val else 0
            nivel_seca = 1 if 'DIVERSIFICADO' in nivel_val else 0

            if not (nivel_prim or nivel_secb or nivel_seca):
                continue

            try:
                lat = float(r[idx_lat]) if r[idx_lat] not in (None, '', 0) else np.nan
                lon = float(r[idx_lon]) if r[idx_lon] not in (None, '', 0) else np.nan
            except (ValueError, TypeError):
                lat, lon = np.nan, np.nan

            rows.append({
                'id_centro': str(r[idx_id]),
                'nombre_centro': str(r[idx_nombre]),
                'sector': sector,
                'nivel_primaria': nivel_prim,
                'nivel_secbaja': nivel_secb,
                'nivel_secalta': nivel_seca,
                'latitud': lat,
                'longitud': lon,
                'adm0_pcode': iso,
            })

        df = pd.DataFrame(rows)

        # Aggregate by id_centro
        df = df.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            sector=('sector', lambda x: 'Private' if 'Private' in x.values else 'Public'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
            latitud=('latitud', 'first'),
            longitud=('longitud', 'first'),
            adm0_pcode=('adm0_pcode', 'first'),
        ).reset_index()

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# GUYANA
# ==============================================================================
def process_GUY():
    iso = 'GUY'
    try:
        raw = BASE / iso / 'raw' / 'School Data-Mapping.xlsx'
        df = pd.read_excel(raw)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        # GUY columns: School_Name, School_ID, Type (P=Primary, PT=Primary+Technical,
        # S=Secondary, N=Nursery), Latitude, Longitude
        # Type values observed: P, PT, N, S, etc.
        type_col = 'Type' if 'Type' in df.columns else next(
            (c for c in df.columns if 'type' in c.lower() or 'level' in c.lower()), None)

        if type_col:
            df['nivel_primaria'] = df[type_col].fillna('').str.contains('P', case=False).astype(int)
            df['nivel_secbaja']  = df[type_col].fillna('').str.upper().isin(['S', 'ST', 'SEC']).astype(int)
            df['nivel_secalta']  = df['nivel_secbaja']
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja']  = 0
            df['nivel_secalta']  = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1)].copy()
        df['sector'] = 'Public'
        id_col   = 'school_id' if 'school_id' in df.columns else df.columns[2]
        name_col = 'school_name' if 'school_name' in df.columns else df.columns[1]
        # Use exact column names (already lowercased) to avoid false matches
        # e.g. 'lat' in 'staff_population' was a bug
        lat_col  = 'latitude' if 'latitude' in df.columns else None
        lon_col  = 'longitude' if 'longitude' in df.columns else None
        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        # GUY raw has duplicate school codes (some with different names — ministry data issue)
        df = df.drop_duplicates(subset='id_centro', keep='first')

        save_cima(df, iso)
        record(iso, df, note='All government schools; no private in file')
        print(f"  {iso}: {len(df):,} total (all Public)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")


# ==============================================================================
# HONDURAS
# ==============================================================================
def process_HND():
    iso = 'HND'
    try:
        raw = BASE / iso / 'raw' / 'SIPLIE_nivel nacional.xlsx'
        df = pd.read_excel(raw, sheet_name='Detalle', skiprows=7)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        # Use 'digo' to match both 'codigo' and 'código' (accent-safe)
        id_col   = next((c for c in df.columns if 'digo' in c and 'centro' in c), None)
        if id_col is None:
            id_col = next((c for c in df.columns if 'digo' in c or 'codigo' in c), None)
        name_col = next((c for c in df.columns if 'nombre' in c and 'centro' in c), None)
        if name_col is None:
            name_col = next((c for c in df.columns if 'nombre' in c or 'plantel' in c), None)
        niv_col  = next((c for c in df.columns if c == 'nivel' or c.startswith('nivel')), None)
        lat_col  = next((c for c in df.columns if c == 'latitud' or c == 'lat'), None)
        lon_col  = next((c for c in df.columns if c == 'longitud' or c == 'lon'), None)

        df = df[~df[niv_col].fillna('').str.contains('Adultos', case=False)].copy() if niv_col else df

        if niv_col:
            # Strip Pre-Básica before checking for Básica to avoid false match
            niv_clean = df[niv_col].fillna('').str.replace(r'Pre-?[Bb][áa]sica', 'PREBAS', regex=True)
            df['nivel_primaria'] = niv_clean.str.contains('Básica|Basica|Primaria', case=False).astype(int)
            df['nivel_secbaja']  = df['nivel_primaria']
            df['nivel_secalta']  = df[niv_col].fillna('').str.contains('Media', case=False).astype(int)
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja']  = 0
            df['nivel_secalta']  = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secalta'] == 1)].copy()
        df['sector'] = 'Public'
        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        # HND raw has exact duplicate rows for some schools
        df = df.drop_duplicates(subset='id_centro', keep='first')

        save_cima(df, iso)
        record(iso, df, note='SIPLIE covers government schools only')
        print(f"  {iso}: {len(df):,} total (all Public - SIPLIE is government system)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# HAITI
# ==============================================================================
def process_HTI():
    iso = 'HTI'
    try:
        raw = BASE / iso / 'raw' / 'PAPDEF_Schools_Data_For_CIMA.xlsx'
        df = pd.read_excel(raw, skiprows=1)
        # Row 0 is sub-header
        df.columns = [str(c).strip() for c in df.iloc[0]]
        df = df.iloc[1:].reset_index(drop=True)
        df.columns = [c.lower().strip().replace(' ', '_') for c in df.columns]

        name_col = next((c for c in df.columns if 'school' in c or 'name' in c), None)
        lat_col  = next((c for c in df.columns if 'lat' in c), None)
        lon_col  = next((c for c in df.columns if 'lon' in c or 'long' in c), None)

        df['nivel_primaria'] = 1  # no level info - include all
        df['nivel_secbaja']  = 0
        df['nivel_secalta']  = 0
        df['sector'] = 'Public'
        df['id_centro'] = df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='PAPDEF only; no level/sector breakdown available')
        print(f"  {iso}: {len(df):,} total (PAPDEF, no level info)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# MEXICO
# ==============================================================================
def process_MEX():
    iso = 'MEX'
    try:
        raw = BASE / iso / 'raw' / 'siged_total.csv'
        df = pd.read_csv(raw, low_memory=False, encoding='utf-8')

        nivel_col = next((c for c in df.columns if c.lower() == 'nivel'), None)
        ctrl_col  = next((c for c in df.columns if c.lower() == 'control'), None)
        lat_col   = next((c for c in df.columns if 'lat' in c.lower()), None)
        lon_col   = next((c for c in df.columns if 'lon' in c.lower() or 'lng' in c.lower()), None)
        id_col    = next((c for c in df.columns if 'id_centro' in c.lower()), None)
        name_col  = next((c for c in df.columns if 'nombre' in c.lower() and 'centro' in c.lower()), None)

        if nivel_col:
            df['nivel_primaria'] = df[nivel_col].fillna('').str.upper().eq('PRIMARIA').astype(int)
            df['nivel_secbaja']  = df[nivel_col].fillna('').str.upper().eq('SECUNDARIA').astype(int)
            df['nivel_secalta']  = df[nivel_col].fillna('').str.upper().isin(
                ['MEDIA SUPERIOR', 'BACHILLERATO', 'PREPARATORIA']).astype(int)
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja']  = 0
            df['nivel_secalta']  = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        if ctrl_col:
            df['sector'] = df[ctrl_col].apply(
                lambda x: 'Private' if str(x).strip().upper() == 'PRIVADO' else 'Public')
        else:
            df['sector'] = 'Public'

        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''
        df['latitud'] = pd.to_numeric(df[lat_col], errors='coerce') if lat_col else np.nan
        df['longitud'] = pd.to_numeric(df[lon_col], errors='coerce') if lon_col else np.nan
        df['adm0_pcode'] = iso

        # MEX raw has one row per shift (turno: matutino/vespertino/etc).
        # Same id_centro appears 2-3 times with different enrollment counts.
        # Dedup to one row per physical school.
        df = df.drop_duplicates(subset='id_centro', keep='first')

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# PANAMA
# ==============================================================================
def process_PAN():
    iso = 'PAN'
    try:
        dir_raw = BASE / iso / 'raw' / 'Marco muestral 19 DE JUNIO 2024.xlsx'
        geo_raw = BASE / iso / 'raw' / 'Anexo 2 - Georreferencia de Centros Educativos.xlsx'

        directorio = pd.read_excel(dir_raw, skiprows=3)
        directorio.columns = [c.lower().strip().replace(' ', '_') for c in directorio.columns]

        dep_col    = next((c for c in directorio.columns if 'depend' in c), None)
        marco_col  = next((c for c in directorio.columns if 'marco' in c and ('sistem' in c or 'subsist' in c)), None)
        oferta_col = next((c for c in directorio.columns if 'oferta' in c), None)
        estatus_col= next((c for c in directorio.columns if 'estatus' in c or 'status' in c), None)
        id_col     = next((c for c in directorio.columns if 'siace' in c or 'codigo' in c), None)
        name_col   = next((c for c in directorio.columns if 'nombre' in c and 'centro' in c), None)

        if marco_col:
            directorio = directorio[directorio[marco_col].fillna('').str.upper().str.contains('REGULAR')].copy()
        if oferta_col:
            directorio = directorio[directorio[oferta_col].fillna('') != 'NO ESPECIFICADO'].copy()
        if estatus_col:
            directorio = directorio[directorio[estatus_col].fillna('') != 'IPHE'].copy()

        # Sector - include both OFICIAL and PARTICULAR
        if dep_col:
            directorio['sector'] = directorio[dep_col].apply(
                lambda x: 'Private' if str(x).strip().upper() == 'PARTICULAR' else 'Public')
        else:
            directorio['sector'] = 'Public'

        if oferta_col:
            directorio['nivel_primaria'] = directorio[oferta_col].fillna('').str.upper().str.contains('PRIMARIA').astype(int)
            directorio['nivel_secbaja']  = directorio[oferta_col].fillna('').str.upper().str.contains('PREMEDIA').astype(int)
            directorio['nivel_secalta']  = directorio[oferta_col].fillna('').str.upper().str.contains(r'\bMEDIA\b', regex=True).astype(int)
        else:
            directorio['nivel_primaria'] = 1
            directorio['nivel_secbaja']  = 0
            directorio['nivel_secalta']  = 0

        directorio = directorio[(directorio['nivel_primaria'] == 1) |
                                (directorio['nivel_secbaja'] == 1) |
                                (directorio['nivel_secalta'] == 1)].copy()

        directorio['id_centro'] = directorio[id_col].astype(str) if id_col else directorio.index.astype(str)
        directorio['nombre_centro'] = directorio[name_col].astype(str) if name_col else ''

        # Geo join
        geo = pd.read_excel(geo_raw)
        geo.columns = [c.lower().strip().replace(' ', '_') for c in geo.columns]
        lat_col = next((c for c in geo.columns if 'lat' in c or 'end_lat' in c), None)
        lon_col = next((c for c in geo.columns if 'lon' in c or 'lng' in c or 'end_lng' in c), None)
        gid_col = next((c for c in geo.columns if 'siace' in c or 'codigo' in c), None)

        if gid_col and lat_col and lon_col:
            geo = geo[[gid_col, lat_col, lon_col]].copy()
            geo.rename(columns={gid_col: 'id_centro', lat_col: 'latitud', lon_col: 'longitud'}, inplace=True)
            geo['id_centro'] = geo['id_centro'].astype(str)
            directorio = directorio.merge(geo, on='id_centro', how='left')
        else:
            directorio['latitud'] = np.nan
            directorio['longitud'] = np.nan

        directorio['adm0_pcode'] = iso

        save_cima(directorio, iso)
        record(iso, directorio)
        print(f"  {iso}: {len(directorio):,} total (Public={(directorio['sector']=='Public').sum():,}, Private={(directorio['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# PERU
# ==============================================================================
def process_PER():
    iso = 'PER'
    try:
        raw = BASE / iso / 'raw' / 'Padron.csv'
        # utf-8-sig strips the BOM cleanly
        df = pd.read_csv(raw, sep=';', encoding='utf-8-sig', low_memory=False)

        # K-12: B0=Primaria, F0=Secundaria (includes public and private, escolarizada only)
        df = df[
            df['NIV_MOD'].isin(['B0', 'F0']) &
            (df['D_FORMA'] == 'Escolarizada')
        ].copy()

        df['nivel_primaria'] = (df['NIV_MOD'] == 'B0').astype(int)

        # Split F0 into secbaja (grados 1-2, ciclo general) and secalta (grados 3-5,
        # ciclo diversificado) using grade-level enrollment from Matricula_01.xlsx.
        # Padron alone cannot distinguish the two ciclos.
        mat_raw = BASE / iso / 'raw' / 'Matricula_01.xlsx'
        mat = pd.read_excel(mat_raw)
        f0_mat = mat[mat['NIV_MOD'] == 'F0'].copy()
        for col in ['D01', 'D02', 'D03', 'D04', 'D05', 'D06', 'D07', 'D08', 'D09', 'D10']:
            f0_mat[col] = pd.to_numeric(f0_mat[col], errors='coerce').fillna(0)
        f0_mat['_secbaja'] = (f0_mat[['D01', 'D02', 'D03', 'D04']] != 0).any(axis=1).astype(int)
        f0_mat['_secalta'] = (f0_mat[['D05', 'D06', 'D07', 'D08', 'D09', 'D10']] != 0).any(axis=1).astype(int)
        mat_flags = f0_mat.groupby('COD_MOD').agg(
            nivel_secbaja=('_secbaja', 'max'),
            nivel_secalta=('_secalta', 'max')
        ).reset_index()

        df = df.merge(mat_flags, on='COD_MOD', how='left')
        df['nivel_secbaja'] = df['nivel_secbaja'].fillna(0).astype(int)
        df['nivel_secalta'] = df['nivel_secalta'].fillna(0).astype(int)
        # B0 rows get 0 for both sec flags (already 0 from merge default)

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()

        # Sector: GESTION 1=Pub directa, 2=Pub gestion privada (convenio), 3=Privada
        df['sector'] = df['GESTION'].apply(lambda x: 'Private' if x == 3 else 'Public')

        df['id_centro'] = df['COD_MOD'].astype(str) + '-' + df['ANEXO'].astype(str)
        df['nombre_centro'] = df['CEN_EDU'].astype(str)

        def parse_coord(s):
            try:
                return float(str(s).replace(',', '.'))
            except:
                return np.nan

        df['latitud']  = df['NLAT_IE'].apply(parse_coord)
        df['longitud'] = df['NLONG_IE'].apply(parse_coord)
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# PARAGUAY
# ==============================================================================
def process_PRY():
    iso = 'PRY'
    try:
        # Matriculaciones files have sector_o_tipo_gestion and level info
        basica = pd.read_csv(BASE / iso / 'raw' / 'matriculaciones_educacion_escolar_basica.csv',
                             encoding='utf-8', low_memory=False)
        media  = pd.read_csv(BASE / iso / 'raw' / 'matriculaciones_educacion_media.csv',
                             encoding='utf-8', low_memory=False)
        establ = pd.read_csv(BASE / iso / 'raw' / 'establecimientos_2023.csv',
                             encoding='utf-8', low_memory=False)

        # Basica: grades 1-6 = primaria, grades 7-9 = secbaja
        prim_cols = [c for c in basica.columns if 'primer' in c or 'segundo' in c or
                     'tercer' in c or 'cuarto' in c or 'quinto' in c or 'sexto' in c]
        sec_cols  = [c for c in basica.columns if 'septimo' in c or 'octavo' in c or 'noveno' in c]

        basica['nivel_primaria'] = (basica[prim_cols].fillna(0).sum(axis=1) > 0).astype(int) if prim_cols else 1
        basica['nivel_secbaja']  = (basica[sec_cols].fillna(0).sum(axis=1) > 0).astype(int) if sec_cols else 0
        basica['nivel_secalta']  = 0

        # Media: secalta
        media['nivel_primaria'] = 0
        media['nivel_secbaja']  = 0
        media['nivel_secalta']  = 1

        for df_sub in [basica, media]:
            df_sub.rename(columns={'codigo_establecimiento': 'id_centro',
                                   'nombre_institucion': 'nombre_centro'}, inplace=True)

        combined = pd.concat([basica, media], ignore_index=True)
        combined = combined[(combined['nivel_primaria'] == 1) | (combined['nivel_secbaja'] == 1) |
                            (combined['nivel_secalta'] == 1)].copy()

        combined['sector'] = combined['sector_o_tipo_gestion'].apply(
            lambda x: 'Private' if str(x).strip() == 'Privado' else 'Public')

        # Aggregate by school
        agg = combined.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            sector=('sector', lambda x: 'Private' if 'Private' in x.values else 'Public'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
        ).reset_index()

        # Merge coordinates (PRY raw has DMS format like '25°17'13.5"S')
        import re as _re

        def _dms_to_dd(dms_str):
            if pd.isna(dms_str):
                return np.nan
            s = str(dms_str).strip()
            try:
                return float(s)
            except ValueError:
                pass
            m = _re.match(
                r"(-?\d+)[°\xb0\xba]?\s*(\d+)['\u2019]?\s*([\d.]+)?[\"″]?\s*([NSEWnsew])?", s)
            if not m:
                return np.nan
            deg, mins = float(m.group(1)), float(m.group(2))
            secs = float(m.group(3)) if m.group(3) else 0.0
            direction = m.group(4).upper() if m.group(4) else ""
            dd = abs(deg) + mins / 60.0 + secs / 3600.0
            if direction in ("S", "W") or deg < 0:
                dd = -dd
            return dd

        establ.rename(columns={'codigo_establecimiento': 'id_centro'}, inplace=True)
        lat_col = next((c for c in establ.columns if c.lower() == 'latitud'), None)
        lon_col = next((c for c in establ.columns if c.lower() == 'longitud'), None)
        if lat_col and lon_col:
            coords = establ[['id_centro', lat_col, lon_col]].copy()
            coords.rename(columns={lat_col: 'latitud', lon_col: 'longitud'}, inplace=True)
            coords['latitud'] = coords['latitud'].apply(_dms_to_dd)
            coords['longitud'] = coords['longitud'].apply(_dms_to_dd)
            agg = agg.merge(coords, on='id_centro', how='left')
        else:
            agg['latitud'] = np.nan
            agg['longitud'] = np.nan

        agg['id_centro'] = agg['id_centro'].astype(str)
        agg['adm0_pcode'] = iso

        save_cima(agg, iso)
        record(iso, agg)
        print(f"  {iso}: {len(agg):,} total (Public={(agg['sector']=='Public').sum():,}, Private={(agg['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# EL SALVADOR
# ==============================================================================
def process_SLV():
    iso = 'SLV'
    try:
        # CE_2024: contains school code, grade enrollment, lat/lon
        # Row 0 of data is sub-header (Cod, Nombre, etc.) - skip it
        ce_raw = pd.read_excel(BASE / iso / 'raw' / 'CE_2024 El Salvador.xlsx')
        sub_hdr = ce_raw.iloc[0]
        ce = ce_raw.iloc[2:].reset_index(drop=True)  # skip header + totals row
        # Rename columns using sub_hdr, but keep original names for duplicates
        new_cols = []
        for i in range(len(ce_raw.columns)):
            sh = str(sub_hdr.iloc[i]).strip()
            if sh and not sh.startswith('Unnamed') and sh != 'nan':
                new_cols.append(sh if new_cols.count(sh) == 0 else f'{sh}_{i}')
            else:
                new_cols.append(str(ce_raw.columns[i]))
        ce.columns = new_cols

        # School code = first column (index 0), name = second (index 1)
        ce['_code'] = ce.iloc[:, 0]
        ce['_name'] = ce.iloc[:, 1]
        lat_col  = next((c for c in ce.columns if str(c).upper() == 'LATITUD'), None)
        lon_col  = next((c for c in ce.columns if str(c).upper() == 'LONGITUD'), None)

        # Determine levels from grade columns
        # Primary (educacion basica): grades 1-6 (col names '1'-'6')
        prim_g = ['1', '2', '3', '4', '5', '6']
        # Sec baja: grades 7-9 ('7','8','9')
        secb_g = ['7', '8', '9']
        # Sec alta: bachillerato ('1A','2A','3A')
        seca_g = ['1A', '2A', '3A']

        def has_enrollment(row, cols):
            for c in cols:
                if c in row.index:
                    try:
                        if pd.notna(row[c]) and float(row[c]) > 0:
                            return 1
                    except:
                        pass
            return 0

        ce['nivel_primaria'] = ce.apply(lambda r: has_enrollment(r, prim_g), axis=1)
        ce['nivel_secbaja']  = ce.apply(lambda r: has_enrollment(r, secb_g), axis=1)
        ce['nivel_secalta']  = ce.apply(lambda r: has_enrollment(r, seca_g), axis=1)

        ce = ce[(ce['nivel_primaria'] == 1) | (ce['nivel_secbaja'] == 1) |
                (ce['nivel_secalta'] == 1)].copy()

        ce['id_centro']    = ce['_code'].astype(str)
        ce['nombre_centro'] = ce['_name'].astype(str)
        ce['latitud']  = pd.to_numeric(ce[lat_col], errors='coerce') if lat_col else np.nan
        ce['longitud'] = pd.to_numeric(ce[lon_col], errors='coerce') if lon_col else np.nan

        # Join sector from SLV_coord_EDU.csv and add private schools not in CE_2024
        try:
            coord = pd.read_csv(BASE / iso / 'raw' / 'SLV_coord_EDU.csv', encoding='latin-1')
            coord.columns = [c.encode('ascii', 'ignore').decode().strip().upper() for c in coord.columns]
            code_c = coord.columns[0]  # CODIGO C.E.
            sect_c = next((c for c in coord.columns if 'SECTOR' in c), None)
            dpto_c = next((c for c in coord.columns if 'DEPTO' in c or 'DPTO' in c), None)
            lat_c2 = next((c for c in coord.columns if 'COORDENADAS' in c), None)

            if sect_c:
                coord['id_centro'] = coord[code_c].astype(str)
                coord['sector_raw'] = coord[sect_c]
                coord['is_private'] = coord[sect_c].apply(lambda x: 'rivado' in str(x))

                # Merge sector into CE_2024 schools
                ce = ce.merge(coord[['id_centro', 'sector_raw']], on='id_centro', how='left')
                ce['sector'] = ce['sector_raw'].apply(
                    lambda x: 'Private' if pd.notna(x) and 'rivado' in str(x) else 'Public')

                # Add private schools from coord that are NOT in CE_2024
                private_coord = coord[coord['is_private']].copy()
                private_in_ce = set(ce['id_centro'].astype(str))
                extra_private = private_coord[~private_coord['id_centro'].isin(private_in_ce)].copy()
                if len(extra_private) > 0:
                    # Parse coordinates from 'coordenadas_deci' (format: "lat, lon")
                    if lat_c2:
                        def parse_slv_coords(row):
                            try:
                                s = str(row[lat_c2])
                                parts = s.split(',')
                                return float(parts[0]), float(parts[1])
                            except:
                                return np.nan, np.nan
                        extra_private[['latitud', 'longitud']] = extra_private.apply(
                            parse_slv_coords, axis=1, result_type='expand')
                    else:
                        extra_private['latitud'] = np.nan
                        extra_private['longitud'] = np.nan

                    name_c2 = next((c for c in coord.columns if 'NOMBRE' in c), None)
                    extra_private['nombre_centro'] = extra_private[name_c2].astype(str) if name_c2 else ''
                    extra_private['sector'] = 'Private'
                    extra_private['nivel_primaria'] = 1  # assumed K-12 (level unknown)
                    extra_private['nivel_secbaja'] = 0
                    extra_private['nivel_secalta'] = 0
                    extra_private['adm0_pcode'] = iso

                    ce = pd.concat([ce, extra_private[SCHEMA]], ignore_index=True)
            else:
                ce['sector'] = 'Public'
        except Exception as e_slv:
            print(f"    SLV coord join error: {e_slv}")
            ce['sector'] = 'Public'

        ce['adm0_pcode'] = iso

        # SLV has 1 duplicate id_centro (Liceo Francés appears twice from CE + coord join)
        ce = ce.drop_duplicates(subset='id_centro', keep='first')

        save_cima(ce, iso)
        record(iso, ce)
        print(f"  {iso}: {len(ce):,} total (Public={(ce['sector']=='Public').sum():,}, Private={(ce['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# SURINAME
# ==============================================================================
def process_SUR():
    iso = 'SUR'
    try:
        raw = BASE / iso / 'raw' / 'Suriname School List_03202024.xlsx'
        df = pd.read_excel(raw)
        # Keep original column names for DMS parsing, then normalize
        orig_cols = list(df.columns)
        df.columns = [c.lower().strip().replace(' ', '_') for c in orig_cols]

        type_col = next((c for c in df.columns if 'school_type' in c or 'type' in c), None)
        edu_col  = next((c for c in df.columns if 'education_level' in c or 'education' in c or 'level' in c), None)
        name_col = next((c for c in df.columns if 'school_name' in c or 'name' in c), None)
        id_col   = next((c for c in df.columns if 'school_code' in c or 'code' in c or 'id' in c), None)

        if edu_col:
            edu_clean = df[edu_col].fillna('').astype(str).str.strip()
            df['nivel_primaria'] = edu_clean.eq('Primary').astype(int)
            df['nivel_secbaja']  = edu_clean.isin(['Secondary - Academic', 'Secondary - Vocational']).astype(int)
            df['nivel_secalta']  = edu_clean.eq('Upper Secondary').astype(int)
        else:
            df['nivel_primaria'] = 1
            df['nivel_secbaja']  = 0
            df['nivel_secalta']  = 0

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()

        if type_col:
            df['sector'] = df[type_col].apply(
                lambda x: 'Private' if str(x).strip().lower() in ('particulier', 'private', 'privada') else 'Public')
        else:
            df['sector'] = 'Public'

        df['id_centro'] = df[id_col].astype(str) if id_col else df.index.astype(str)
        # Fill missing school codes with synthetic IDs (2 schools have no code in raw)
        mask_no_id = df['id_centro'].isin(['nan', 'None', '']) | df['id_centro'].isna()
        if mask_no_id.any():
            for i, idx in enumerate(df.index[mask_no_id], start=1):
                df.loc[idx, 'id_centro'] = f'SUR_NOID_{i:03d}'
        df['nombre_centro'] = df[name_col].astype(str) if name_col else ''

        # Coordinates are in DMS format: "5 51 0 N" (R script uses dms_to_dd())
        # Note: the column is misspelled 'longtitude' in the source file
        def dms_to_dd(dms_str):
            try:
                parts = str(dms_str).strip().split()
                if len(parts) < 4:
                    return np.nan
                deg, mins, secs, direction = float(parts[0]), float(parts[1]), float(parts[2]), parts[3].upper()
                dd = deg + mins / 60 + secs / 3600
                if direction in ('S', 'W'):
                    dd = -dd
                return dd
            except:
                return np.nan

        lat_col = next((c for c in df.columns if c.lower() == 'latitude'), None)
        # Note misspelling 'longtitude' in source
        lon_col = next((c for c in df.columns if c.lower() in ('longtitude', 'longitude', 'lon')), None)
        df['latitud']  = df[lat_col].apply(dms_to_dd) if lat_col else np.nan
        df['longitud'] = df[lon_col].apply(dms_to_dd) if lon_col else np.nan
        df['adm0_pcode'] = iso

        # SUR has 1 duplicate (NATIN with 2 campuses) + 2 synthetic IDs for missing codes
        df = df.drop_duplicates(subset='id_centro', keep='first')

        save_cima(df, iso)
        record(iso, df, note='Coords from DMS format (R dms_to_dd function replicated in Python)')
        print(f"  {iso}: {len(df):,} total (Public={(df['sector']=='Public').sum():,}, Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")


# ==============================================================================
# URUGUAY
# ==============================================================================
def _deprecated_process_URY_objectid():
    iso = 'URY'
    try:
        import shapefile as shp_lib

        raw_dir = BASE / iso / 'raw'
        shp_files = {
            'Primaria': next(raw_dir.rglob('CEIP.shp'), None),
            'Secundaria': next(raw_dir.rglob('CES.shp'), None),
            'Tecnica': next(raw_dir.rglob('CETP.shp'), None),
        }

        all_rows = []
        for level_name, shp_path in shp_files.items():
            if shp_path is None:
                continue
            sf = shp_lib.Reader(str(shp_path), encoding='latin-1')
            flds = [f[0].lower() for f in sf.fields[1:]]
            id_idx   = next((i for i, f in enumerate(flds) if 'cod' in f or 'id' in f), None)
            name_idx = next((i for i, f in enumerate(flds) if 'nombre' in f or 'name' in f or 'nom' in f), None)
            # CEIP tiene campo Tipo_de_Ed: "JARDÍN DE INFANTES" (nivel_inicial=1) vs
            # "ESCUELAS COMUNES" (nivel_primaria=1). Separar per Diccionario BID.
            tipo_idx = next((i for i, f in enumerate(flds) if 'tipo_de_ed' in f or 'tipo_ed' in f), None)

            for i, rec in enumerate(sf.iterRecords()):
                r = list(rec)
                shp = sf.shape(i)
                if shp.shapeType != 0 and shp.points:
                    lon, lat = shp.points[0]
                else:
                    lon, lat = np.nan, np.nan

                # Determinar niveles por shapefile + tipo
                if level_name == 'Primaria':
                    tipo = str(r[tipo_idx]).strip().upper() if tipo_idx is not None else ''
                    is_jardin = 'JARD' in tipo  # "JARDÍN DE INFANTES" o variantes
                    nivel_inicial = 1 if is_jardin else 0
                    nivel_primaria = 0 if is_jardin else 1
                    nivel_secbaja = 0
                    nivel_secalta = 0
                else:
                    # CES (Secundaria) y CETP (Tecnica): per Diccionario "No hay distincion
                    # entre secundaria baja y alta" → secbaja = secalta = 1
                    nivel_inicial = 0
                    nivel_primaria = 0
                    nivel_secbaja = 1
                    nivel_secalta = 1

                all_rows.append({
                    'id_centro': str(r[id_idx]) if id_idx is not None else str(i),
                    'nombre_centro': str(r[name_idx]) if name_idx is not None else '',
                    'nivel_inicial': nivel_inicial,
                    'nivel_primaria': nivel_primaria,
                    'nivel_secbaja': nivel_secbaja,
                    'nivel_secalta': nivel_secalta,
                    'latitud': lat,
                    'longitud': lon,
                })

        df = pd.DataFrame(all_rows)

        # Aggregate by school
        df = df.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            nivel_inicial=('nivel_inicial', 'max'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
            latitud=('latitud', 'first'),
            longitud=('longitud', 'first'),
        ).reset_index()

        # Filter to K-12 only (exclude pure jardines de infantes)
        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()

        # URY shapefiles are in Web Mercator (EPSG:3857) — reproject to WGS84
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        valid = df['latitud'].notna() & df['longitud'].notna()
        if valid.any():
            lon_4326, lat_4326 = transformer.transform(
                df.loc[valid, 'longitud'].values,
                df.loc[valid, 'latitud'].values,
            )
            df.loc[valid, 'latitud'] = lat_4326
            df.loc[valid, 'longitud'] = lon_4326

        df['sector'] = 'Public'
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='ANEP only - no private schools in raw data')
        print(f"  {iso}: {len(df):,} total (all Public - ANEP system)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
def process_URY():
    iso = 'URY'
    try:
        import shapefile as shp_lib

        raw_dir = BASE / iso / 'raw'
        shp_files = {
            'Primaria': next(raw_dir.rglob('CEIP.shp'), None),
            'Secundaria': next(raw_dir.rglob('CES.shp'), None),
            'Tecnica': next(raw_dir.rglob('CETP.shp'), None),
        }

        all_rows = []
        for level_name, shp_path in shp_files.items():
            if shp_path is None:
                continue

            sf = shp_lib.Reader(str(shp_path), encoding='latin-1')
            flds = [f[0].lower() for f in sf.fields[1:]]
            if level_name == 'Primaria':
                id_field = 'ruee'
                name_candidates = ('nombre',)
            elif level_name == 'Secundaria':
                id_field = 'ruee_calcu'
                name_candidates = ('first_nomb',)
            else:
                id_field = 'ruee'
                name_candidates = ('first_desc',)

            if id_field not in flds:
                raise ValueError(f"URY {level_name}: missing expected id field {id_field}")

            id_idx = flds.index(id_field)
            name_idx = next((i for i, f in enumerate(flds) if f in name_candidates), None)
            tipo_idx = next((i for i, f in enumerate(flds) if f == 'tipo_de_ed' or f == 'tipo_ed'), None)

            for i, rec in enumerate(sf.iterRecords()):
                r = list(rec)
                shp = sf.shape(i)
                if shp.shapeType != 0 and shp.points:
                    lon, lat = shp.points[0]
                else:
                    lon, lat = np.nan, np.nan

                sid = str(r[id_idx]).strip()
                if level_name == 'Primaria':
                    tipo = str(r[tipo_idx]).strip().upper() if tipo_idx is not None else ''
                    if 'ESCUELA ESPECIAL' in tipo:
                        continue
                    is_jardin = 'JARD' in tipo
                    is_comun = 'ESCUELAS COMUNES' in tipo
                    nivel_inicial = 1 if is_jardin else 0
                    nivel_primaria = 1 if is_comun else 0
                    nivel_secbaja = 0
                    nivel_secalta = 0
                else:
                    if level_name == 'Secundaria' and sid in ('', '0', '0.0', 'nan', 'None'):
                        continue
                    nivel_inicial = 0
                    nivel_primaria = 0
                    nivel_secbaja = 1
                    nivel_secalta = 1

                all_rows.append({
                    'id_centro': sid if sid else str(i),
                    'nombre_centro': str(r[name_idx]) if name_idx is not None else '',
                    'nivel_inicial': nivel_inicial,
                    'nivel_primaria': nivel_primaria,
                    'nivel_secbaja': nivel_secbaja,
                    'nivel_secalta': nivel_secalta,
                    'latitud': lat,
                    'longitud': lon,
                })

        df = pd.DataFrame(all_rows)
        df = df.groupby('id_centro').agg(
            nombre_centro=('nombre_centro', 'first'),
            nivel_inicial=('nivel_inicial', 'max'),
            nivel_primaria=('nivel_primaria', 'max'),
            nivel_secbaja=('nivel_secbaja', 'max'),
            nivel_secalta=('nivel_secalta', 'max'),
            latitud=('latitud', 'first'),
            longitud=('longitud', 'first'),
        ).reset_index()

        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) | (df['nivel_secalta'] == 1)].copy()

        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        valid = df['latitud'].notna() & df['longitud'].notna()
        if valid.any():
            lon_4326, lat_4326 = transformer.transform(
                df.loc[valid, 'longitud'].values,
                df.loc[valid, 'latitud'].values,
            )
            df.loc[valid, 'latitud'] = lat_4326
            df.loc[valid, 'longitud'] = lon_4326

        df['sector'] = 'Public'
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df, note='ANEP only - no private schools in raw data')
        print(f"  {iso}: {len(df):,} total (all Public - ANEP system)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# JAMAICA
# ==============================================================================
def process_JAM():
    """JAM has no raw ministry data. Build CIMA from existing ISO_total (R pipeline output).

    Sector decision: the raw source does not report public/private status. Jamaica's
    Ministry of Education confirmed in published statistics that the EMIS universe covers
    only government and grant-aided schools. All 914 K-12 schools are treated as Public.
    """
    iso = 'JAM'
    try:
        csv_path = BASE / iso / 'processed' / f'{iso}_total.csv'
        df = pd.read_csv(csv_path, dtype={'id_centro': str})

        # All JAM schools in EMIS are government or grant-aided (no private sector data)
        if 'nombre_centro' not in df.columns:
            df['nombre_centro'] = ''
        df['sector'] = 'Public'

        for col in ['nivel_primaria', 'nivel_secbaja', 'nivel_secalta']:
            if col not in df.columns:
                df[col] = 0
            df[col] = df[col].fillna(0).astype(int)

        # K-12 filter
        df = df[(df['nivel_primaria'] == 1) | (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        if 'latitud' not in df.columns:
            df['latitud'] = np.nan
        if 'longitud' not in df.columns:
            df['longitud'] = np.nan

        df['adm0_pcode'] = iso
        df = df.drop_duplicates(subset='id_centro', keep='first')

        save_cima(df, iso)
        record(iso, df, note='No raw data - built from ISO_total. Sector=Public (EMIS covers gov+grant-aided only)')
        print(f"  {iso}: {len(df):,} total (Public={len(df):,}, no school names in source)")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()


# ==============================================================================
# MAIN
# ==============================================================================
if __name__ == '__main__':
    print("=" * 65)
    print("Building CIMA files from RAW ministry data (Public + Private)")
    print("=" * 65)
    print()

    processors = {
        'ARG': process_ARG,
        'BHS': process_BHS,
        'BLZ': process_BLZ,
        'BOL': process_BOL,
        'BRA': process_BRA,
        'BRB': process_BRB,
        'CHL': process_CHL,
        'COL': process_COL,
        'CRI': process_CRI,
        'DOM': process_DOM,
        'ECU': process_ECU,
        'GTM': process_GTM,
        'GUY': process_GUY,
        'HND': process_HND,
        'HTI': process_HTI,
        'JAM': process_JAM,
        'MEX': process_MEX,
        'PAN': process_PAN,
        'PER': process_PER,
        'PRY': process_PRY,
        'SLV': process_SLV,
        'SUR': process_SUR,
        'URY': process_URY,
    }

    for iso in PIPELINE_ISOS:
        processors[iso]()

    print()
    print("=" * 65)
    print("SUMMARY TABLE")
    print("=" * 65)
    print(f"{'ISO':<6} {'Total K-12':>12} {'Public':>10} {'Private':>10} {'Georef':>10}")
    print("-" * 55)
    for s in summary:
        print(f"{s['iso']:<6} {s['total_k12']:>12,} {s['public']:>10,} {s['private']:>10,} {s['georef']:>10,}")
    print()

    if errors:
        print("ERRORS:")
        for iso, err in errors.items():
            print(f"  {iso}: {err}")

    summary_df = pd.DataFrame(summary)
    pipeline_summary_path = RESULTS / 'cima_v2_pipeline_summary.csv'
    analysis_summary_path = RESULTS / 'cima_v2_summary.csv'

    # Published analytical summary remains analysis-only for backward compatibility.
    summary_df.to_csv(pipeline_summary_path, index=False, encoding='utf-8')
    summary_df[summary_df['analysis_included']].to_csv(
        analysis_summary_path, index=False, encoding='utf-8'
    )

    # Save errors
    with open(RESULTS / 'cima_v2_errors.json', 'w', encoding='utf-8') as f:
        json.dump(errors, f, ensure_ascii=False, indent=2)

    pipeline_total = int(summary_df['total_k12'].sum())
    analysis_total = int(summary_df.loc[summary_df['analysis_included'], 'total_k12'].sum())
    print(f"\nPipeline universe total (23-country operational scope): {pipeline_total:,} schools")
    print(f"Analysis universe total (21-country published scope): {analysis_total:,} schools")
    print(f"\nSaved: results/cima_v2_pipeline_summary.csv")
    print(f"Saved: results/cima_v2_summary.csv")
    print(f"Saved: results/cima_v2_errors.json")
    print(f"Files written to: data/schools/AR/{{ISO}}/processed/{{ISO}}_total_cima.csv")
