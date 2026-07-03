"""Per-country information availability table for dashboard step-03.

Single source of truth for the "Información disponible" panel that opens
step-03. Each country is rendered as a **structured field card** in the
frontend (not a free-text paragraph), so this module emits human-friendly
field values rather than composed prose. The frontend reads the payload
columns and renders them as labeled rows.

Card layout per country:
    Tipo:                    A / B / C
    Fuente de información:   <data_source_es>
    Niveles administrativos: <raw_admin_levels_es>
    Tiene direcciones:       Sí / No
    Nivel de validación:     <validation_level_es>
    Método de validación:    <match_method_label_es>
    Correspondencia BID:     <bid_correspondence_es>
    Notas:                   <notes_es>            (only when non-empty)

TYPE rule (derived, not stored):
- A: source has street addresses + admin units → geocoder + admin QC
- B: source has admin units, no street addresses → admin QC only
- C: source has neither → bbox-only QC, no recovery path

Output: `build_country_info_levels(targets_df, schools_df)` returns a DataFrame
keyed by ISO with all columns the dashboard needs.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

try:
    from pipeline.constants import COUNTRY_SCOPE
except ImportError:
    from constants import COUNTRY_SCOPE  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Per-country structured facts
# ---------------------------------------------------------------------------
# Field reference (each ISO is a dict with these keys):
#   country_name_es:      Spanish display name
#   data_source_es:       audience-friendly description of where data comes
#                         from (NOT always "ministerio" — INEP, EMIS, ANEP, etc.)
#                         No leading article — frontend renders as "Fuente: <X>".
#   has_addresses:        bool — does the raw carry street-level addresses?
#   raw_adm1_label_es:    Spanish label for the raw's ADM1 column, or None
#   raw_adm2_label_es:    Spanish label for the raw's ADM2 column, or None
#   raw_locality_label_es: Spanish label for the locality column, or None
#   bid_adm1_correspondence:
#       "match"        — raw ADM1 maps 1:1 to BID lac-level-1
#       "raw_finer"    — raw is finer than BID at this level (e.g. DOM:
#                        raw "Provincia" sits at BID's lac-level-2)
#       "raw_coarser"  — raw is coarser than BID at this level (rare)
#       "n/a"          — column does not exist in the raw
#   bid_adm2_correspondence: same vocabulary
#   match_method:
#       "code"         — raw provides an admin code that maps 1:1 to BID PCODE
#       "name"         — raw provides admin name; we match via normalize+aliases
#       "spatial"      — no raw admin column; containment in BID polygons
#       "n/a"          — bbox-only or no admin QC at all
#   match_method_label_es: human-readable description of the match method
#   route_priority: ordered list of routes step-04 can take, e.g.
#       ["compare_geocoder", "fill_geocoder"]    (Tipo A)
#       ["compare_admin_only", "fill_cascade"]   (Tipo B with cascade ready)
#       ["compare_admin_only"]                   (Tipo B without cascade)
#       []                                       (Tipo C — irrecoverable)
#   cascade_status:
#       "implemented"  — Phase B-2 cascade ready (PAN, DOM, SLV, ECU)
#       "pending"      — Type B but cascade not yet built (CHL)
#       "n/a"          — Type A or C (Tipo C is irrecoverable by definition)
#   notes_es:           extra short note when there is information that the
#                       structured fields above don't already convey

COUNTRY_INFO_LEVELS: dict[str, dict[str, Any]] = {
    "ARG": {
        "country_name_es": "Argentina",
        "data_source_es": "Listado de Establecimientos del Ministerio de Educación (DiNIECE)",
        "has_addresses": True,
        "raw_adm1_label_es": "Provincia",
        "raw_adm2_label_es": "Departamento",
        "raw_locality_label_es": "Localidad",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "code",
        "match_method_label_es": "código oficial de provincia del INDEC",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "BHS": {
        "country_name_es": "Bahamas",
        "data_source_es": (
            "xlsx oficial del Ministry of Education (vía especialista BID, recibido "
            "2026-05-11; 161 filas raw, 138 K-12 tras filtrar Pre-primary, Special y "
            "Virtual School Bahamas)"
        ),
        "has_addresses": True,
        "raw_adm1_label_es": "Area Education",
        "raw_adm2_label_es": None,
        "raw_locality_label_es": "Settlement",
        "bid_adm1_correspondence": "raw_coarser",
        "bid_adm2_correspondence": "n/a",
        "match_method": "name_aggregation",
        "match_method_label_es": (
            "nombre normalizado de Area Education con agregaciones 1-a-muchos "
            "(ADM1_AGGREGATIONS mapea las ~5-6 island families del MoE a los 32 ADM1 del BID) "
            "+ rescue por snap-to-nearest a < 1 km para escuelas archipielágicas en frontera"
        ),
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": (
            "Onboarded al analysis scope 2026-05-13 (validation_tier=limited, n=138). "
            "El MoE agrupa por island families coarse mientras BID desagrega en 32 distritos ADM1; "
            "el spatial-join contra el polígono BID determina adm1_pcode. Phase B-1 (Step 04) pendiente "
            "para 4 missing + 5 placeholder Nassau."
        ),
    },
    "BLZ": {
        "country_name_es": "Belice",
        "data_source_es": "geo_schools del Ministerio de Educación de Belice",
        "has_addresses": True,
        "raw_adm1_label_es": "Distrito",
        "raw_adm2_label_es": None,
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "n/a",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado del distrito",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "BOL": {
        "country_name_es": "Bolivia",
        "data_source_es": "Padrón de Instituciones Educativas del Ministerio de Educación",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        # Raw expone Municipio (~329 unidades) pero BID lac-level-2 son las
        # 112 Provincias bolivianas (nivel intermedio entre Depto y Municipio
        # que el raw NO expone). Raw es más fino que BID.
        "bid_adm2_correspondence": "raw_finer",
        "match_method": "code",
        "match_method_label_es": "código oficial de departamento",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "El Municipio se usa solo para enriquecer las consultas al geocoder; la validación cartográfica se hace a nivel Departamento.",
    },
    "BRA": {
        "country_name_es": "Brasil",
        "data_source_es": "microdatos del Censo Escolar del INEP (Instituto Nacional de Estudos e Pesquisas Educacionais)",
        "has_addresses": True,
        "raw_adm1_label_es": "Estado (UF)",
        "raw_adm2_label_es": "Município",
        "raw_locality_label_es": "Bairro",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "code",
        "match_method_label_es": "código oficial de UF del IBGE",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "BRB": {
        "country_name_es": "Barbados",
        "data_source_es": "archivo de geolocalización limitado, sin columna administrativa",
        "has_addresses": False,
        "raw_adm1_label_es": None,
        "raw_adm2_label_es": None,
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "n/a",
        "bid_adm2_correspondence": "n/a",
        "match_method": "n/a",
        "match_method_label_es": "no aplica",
        "route_priority": [],
        "cascade_status": "n/a",
        "notes_es": "Validación limitada por la pobreza de la fuente.",
    },
    "CHL": {
        "country_name_es": "Chile",
        "data_source_es": "Directorio Oficial de Establecimientos Educacionales del MINEDUC, cruzado con el Sistema de Admisión Escolar",
        "has_addresses": False,
        # Raw del MINEDUC trae Región (16) y Comuna (346) — la unidad más
        # fina del raw NO es Provincia política sino Comuna. La Provincia
        # del BID (56) es un nivel intermedio que el raw no nombra: se
        # deriva en pre-process via COD_PRO_RBD → CL{:03d} (BID PCODE).
        "raw_adm1_label_es": "Región",
        "raw_adm2_label_es": "Comuna",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "raw_finer",
        "match_method": "code",
        "match_method_label_es": "códigos oficiales del MINEDUC: cod_reg_rbd para Región y COD_PRO_RBD para Provincia (este último resuelto en pre-process)",
        "route_priority": ["compare_admin_only"],
        "cascade_status": "pending",
        "notes_es": "El raw expone Comuna (346 unidades) pero la validación se hace contra la Provincia BID (56 unidades), aprovechando que el código COD_PRO_RBD mapea 1:1 al ADM2 del BID.",
    },
    "COL": {
        "country_name_es": "Colombia",
        "data_source_es": "Carátula Única de la Sede Educativa del DANE (sistema oficial del Ministerio de Educación)",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": "Localidad",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y municipio",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "CRI": {
        "country_name_es": "Costa Rica",
        "data_source_es": "nómina oficial de centros educativos del Ministerio de Educación Pública (MEP)",
        "has_addresses": True,
        "raw_adm1_label_es": "Provincia",
        "raw_adm2_label_es": "Cantón",
        "raw_locality_label_es": "Poblado",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de provincia y cantón",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": (
            "650 escuelas no traen coordenadas en el padrón MEP "
            "(601 privadas — el MEP no publica coordenadas privadas — "
            "más 49 públicas sin dirección publicada). Quedan fuera del "
            "universo geocodificable: no son backlog de step-04 sino "
            "exclusión estructural de la fuente."
        ),
    },
    "DOM": {
        "country_name_es": "República Dominicana",
        "data_source_es": "listado de centros educativos del Ministerio de Educación (MINERD)",
        "has_addresses": False,
        "raw_adm1_label_es": "Provincia",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": None,
        # Raw "Provincia" (32) es más fino que BID lac-level-1 (10 Regiones)
        # y mapea al BID lac-level-2 (32 Provincias). Raw "Municipio" (~155)
        # es más fino que BID lac-level-2; BID no tiene polígono más detallado.
        "bid_adm1_correspondence": "raw_finer",
        "bid_adm2_correspondence": "raw_finer",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de provincia",
        "route_priority": ["compare_admin_only", "fill_cascade"],
        "cascade_status": "implemented",
        "notes_es": "El BID agrupa las 32 provincias en 10 Regiones a nivel ADM1; las Provincias del raw equivalen al ADM2 del BID. El Municipio del raw es más fino que cualquier nivel BID disponible. Para escuelas sin coordenadas, asignamos el centroide del Municipio.",
    },
    "ECU": {
        "country_name_es": "Ecuador",
        "data_source_es": "Registros Administrativos del MINEDUC",
        "has_addresses": False,
        "raw_adm1_label_es": "Provincia",
        "raw_adm2_label_es": "Cantón",
        "raw_locality_label_es": "Parroquia",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "code",
        "match_method_label_es": "códigos oficiales del MINEDUC: Cod_Provincia para provincia y Cod_Cantón para cantón",
        "route_priority": ["compare_admin_only", "fill_cascade"],
        "cascade_status": "implemented",
        "notes_es": "Para escuelas sin coordenadas, asignamos el centroide de la Parroquia (la unidad más fina disponible).",
    },
    "GTM": {
        "country_name_es": "Guatemala",
        "data_source_es": "shapefile del Sistema de Información del Registro Educativo (SIRE) del MINEDUC",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y municipio",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "El shapefile actual está pre-filtrado y trae solo 4 escuelas privadas; se solicitó la versión completa al ministerio.",
    },
    "GUY": {
        "country_name_es": "Guyana",
        "data_source_es": "School Data-Mapping del Ministerio de Educación de Guyana",
        "has_addresses": True,
        "raw_adm1_label_es": "Región",
        "raw_adm2_label_es": None,
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "n/a",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de región (con aliases para los códigos numéricos Region 1..10)",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "HND": {
        "country_name_es": "Honduras",
        "data_source_es": "Sistema de Información de la Planificación Educativa (SIPLIE) de la Secretaría de Educación",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y municipio",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "El SIPLIE solo cubre escuelas públicas por diseño; no hay registro de escuelas privadas en esta fuente.",
    },
    "HTI": {
        "country_name_es": "Haití",
        "data_source_es": "fuente parcial (PAPDEF) que no es legible con las herramientas actuales",
        "has_addresses": False,
        "raw_adm1_label_es": None,
        "raw_adm2_label_es": None,
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "n/a",
        "bid_adm2_correspondence": "n/a",
        "match_method": "n/a",
        "match_method_label_es": "no aplica",
        "route_priority": [],
        "cascade_status": "n/a",
        "notes_es": "País excluido del análisis publicado.",
    },
    "JAM": {
        "country_name_es": "Jamaica",
        "data_source_es": "EMIS (sin archivo crudo del ministerio); la base se construyó desde la salida del pipeline anterior",
        "has_addresses": False,
        "raw_adm1_label_es": None,
        "raw_adm2_label_es": None,
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "n/a",
        "bid_adm2_correspondence": "n/a",
        "match_method": "n/a",
        "match_method_label_es": "no aplica",
        "route_priority": [],
        "cascade_status": "n/a",
        "notes_es": "Para Jamaica usamos los polígonos de Parish del BID en la verificación territorial, lo que descarta coordenadas en el mar circundante (mejora marginal sobre el bbox simple). El EMIS solo cubre escuelas de gobierno y grant-aided; todas se clasifican como Públicas en CIMA.",
    },
    "MEX": {
        "country_name_es": "México",
        "data_source_es": "Sistema de Información y Gestión Educativa (SIGED) de la Secretaría de Educación Pública",
        "has_addresses": True,
        "raw_adm1_label_es": "Entidad",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": "Localidad",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "code",
        "match_method_label_es": "clave de entidad del INEGI",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "PAN": {
        "country_name_es": "Panamá",
        "data_source_es": "Marco Muestral del Ministerio de Educación (MEDUCA)",
        "has_addresses": False,
        "raw_adm1_label_es": "Provincia",
        "raw_adm2_label_es": "Distrito",
        "raw_locality_label_es": "Corregimiento",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de provincia y distrito (con aliases para las Comarcas indígenas)",
        "route_priority": ["compare_admin_only", "fill_cascade"],
        "cascade_status": "implemented",
        "notes_es": "Para escuelas sin coordenadas, asignamos el centroide del Corregimiento (la unidad más fina, equivalente al ADM3 del BID).",
    },
    "PER": {
        "country_name_es": "Perú",
        "data_source_es": "Padrón Educativo del MINEDU, cruzado con la Matrícula 2024",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Provincia",
        "raw_locality_label_es": "Localidad",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y provincia",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "",
    },
    "PRY": {
        "country_name_es": "Paraguay",
        "data_source_es": "listado de Establecimientos del Ministerio de Educación y Ciencias",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Distrito",
        "raw_locality_label_es": "Barrio/Localidad",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y distrito",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "Las coordenadas en el raw vienen en formato grados-minutos-segundos (DMS) y se convierten a decimales antes del QC.",
    },
    "SLV": {
        "country_name_es": "El Salvador",
        "data_source_es": "archivo SLV_coord_EDU del Ministerio de Educación (MINED)",
        "has_addresses": False,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Municipio",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento y municipio",
        "route_priority": ["compare_admin_only", "fill_cascade"],
        "cascade_status": "implemented",
        "notes_es": "Para escuelas sin coordenadas, asignamos el centroide del Municipio.",
    },
    "SUR": {
        "country_name_es": "Surinam",
        "data_source_es": "Suriname School List del Ministerio de Educación (MEDOWS)",
        "has_addresses": True,
        "raw_adm1_label_es": "District",
        "raw_adm2_label_es": "Ressort",
        "raw_locality_label_es": "Settlement area",
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "match",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de district y ressort (con aliases para abreviaturas comunes)",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "Las coordenadas en el raw vienen en formato grados-minutos-segundos (DMS). El nivel Ressort tiene granularidad muy fina dentro del District de Paramaribo, lo que produce mismatches reales (no errores de nombre).",
    },
    "URY": {
        "country_name_es": "Uruguay",
        "data_source_es": "registros administrativos de ANEP (CEIP, CES y CETP) enriquecidos con direcciones extraídas del KMZ oficial del MEC (centros_clean.csv) durante el preprocesamiento",
        "has_addresses": True,
        "raw_adm1_label_es": "Departamento",
        "raw_adm2_label_es": "Paraje",
        "raw_locality_label_es": None,
        "bid_adm1_correspondence": "match",
        "bid_adm2_correspondence": "n/a",
        "match_method": "name",
        "match_method_label_es": "nombre normalizado de departamento (paraje no se valida contra ADM2 del BID porque el shapefile lo expone como secciones censales sin nombre)",
        "route_priority": ["compare_geocoder", "fill_geocoder"],
        "cascade_status": "n/a",
        "notes_es": "Las direcciones (calle + número, paraje, localidad) provienen del KMZ oficial del MEC, consolidado con el listado administrativo de ANEP. 2,635 / 2,676 escuelas (98.5%) tienen match en el KMZ; las 41 sin match no se geocodifican. Paraje es el nivel más granular: en Montevideo distingue 62 barrios (CENTRO, PUNTA CARRETAS, CORDÓN, etc.) mientras que localidad colapsa al departamento; en zonas rurales paraje y localidad suelen coincidir. Como direcciones y coordenadas vienen del mismo archivo KMZ, la geocodificación enriquece la cobertura cuando la fuente carece de paraje o número, pero no las valida independientemente. Caveat thresholds: los umbrales de aceptación score-based (≥95 street, 90-95 centroid, <90 reject) se calibraron con muestras de 11 países sin URY (median ArcGIS score URY = 89.19, fuera de la distribución de calibración). Comparaciones cross-country del recovery rate URY deben leerse con esta limitación; recalibración con n=30 ground truth URY queda como backlog metodológico.",
    },
}


# ---------------------------------------------------------------------------
# BID correspondence prose (per-country, per-level) + code-mapping availability
# ---------------------------------------------------------------------------
# Granular per-level prose for the dashboard drawer. Replaces the lumped
# `bid_correspondence_es` field which was misleading for countries where one
# level matches but the other doesn't (BOL ADM2, CHL ADM2, DOM ADM2).
#
# Counts cited come from BID's lac-level-1.shp / lac-level-2.shp (verified
# 2026-05) and the COUNTRY_CONFIG raw column inspection.
#
# Each entry has 4 prose strings:
#   adm1_corr: how raw ADM1 column relates to BID lac-level-1 polygons
#   adm2_corr: how raw ADM2 column relates to BID lac-level-2 polygons
#   adm1_code: code-based mapping path at ADM1, "—" if name-based / none
#   adm2_code: code-based mapping path at ADM2, "—" if name-based / none

BID_PROSE: dict[str, dict[str, str]] = {
    "ARG": {
        "adm1_corr": "Coincide 1:1 (24 provincias del raw = 24 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~526 departamentos del raw = 526 unidades ADM2 del BID)",
        "adm1_code": "cod_prov (INDEC) del raw = ADM1 del BID",
        "adm2_code": "—",
    },
    "BHS": {
        "adm1_corr": (
            "El raw MoE agrupa las escuelas en ~5-6 island families (ABACOS, ANDROS, "
            "ELEUTHERA, EXUMA AND CAYS, GRAND BAHAMA, SWEETINGS CAY) mientras el BID "
            "tiene 32 polígonos ADM1. ADM1_AGGREGATIONS mapea 1-a-muchos: una coord GPS "
            "dentro de cualquier child polygon de su family raw cuenta como MATCH válido. "
            "Se complementa con snap-to-nearest a < 1 km para rescatar archipielágicas "
            "cerca de fronteras de polígono."
        ),
        "adm2_corr": "No aplica — BID no tiene polígonos ADM2 para BHS",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "BLZ": {
        "adm1_corr": "Coincide 1:1 (6 distritos del raw = 6 unidades ADM1 del BID)",
        "adm2_corr": "No aplica — el raw no expone ADM2 y BID tampoco tiene polígonos ADM2 para BLZ",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "BOL": {
        "adm1_corr": "Coincide 1:1 (9 departamentos del raw = 9 unidades ADM1 del BID)",
        "adm2_corr": (
            "El raw expone Municipio (~339 unidades) pero el ADM2 del BID son "
            "las 112 Provincias bolivianas (un nivel intermedio). El raw no "
            "expone Provincia, así que no validamos a este nivel."
        ),
        "adm1_code": "Departamento Código (raw) = ADM1 del BID",
        "adm2_code": "—",
    },
    "BRA": {
        "adm1_corr": "Coincide 1:1 (27 UFs del raw = 27 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~5,570 municípios del raw = 5,572 unidades ADM2 del BID)",
        "adm1_code": "CO_UF (IBGE) del raw = ADM1 del BID",
        "adm2_code": "—",
    },
    "BRB": {
        "adm1_corr": "No aplica — el raw no expone columna administrativa",
        "adm2_corr": "No aplica — BID no tiene polígonos ADM2 para BRB",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "CHL": {
        "adm1_corr": "Coincide 1:1 (16 regiones del raw = 16 unidades ADM1 del BID)",
        "adm2_corr": (
            "El raw expone Comuna (346 unidades) pero el ADM2 del BID son "
            "las 56 Provincias. Resolvemos la diferencia en pre-process: "
            "el código COD_PRO_RBD del raw mapea 1:1 al ADM2 del BID."
        ),
        "adm1_code": "cod_reg_rbd (MINEDUC) del raw = ADM1 del BID",
        "adm2_code": "COD_PRO_RBD (MINEDUC) del raw = ADM2 del BID (resuelto en pre-process)",
    },
    "COL": {
        "adm1_corr": "Coincide 1:1 (33 departamentos del raw = 33 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~1,122 municipios del raw = 1,122 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "CRI": {
        "adm1_corr": "Coincide 1:1 (7 provincias del raw = 7 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (81 cantones del raw = 81 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "DOM": {
        "adm1_corr": (
            "El raw expone Provincia (32 unidades) pero el ADM1 del BID son "
            "las 10 Regiones. El raw es más fino y corresponde al ADM2 del BID."
        ),
        "adm2_corr": (
            "El raw expone Municipio (~155 unidades), más fino que el ADM2 "
            "del BID (32 Provincias). BID no tiene polígono más detallado, "
            "así que no validamos a este nivel."
        ),
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "ECU": {
        "adm1_corr": "Coincide 1:1 (25 provincias del raw = 25 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (224 cantones del raw = 224 unidades ADM2 del BID)",
        "adm1_code": "Cod_Provincia (MINEDUC) del raw = ADM1 del BID",
        "adm2_code": "Cod_Cantón (MINEDUC) del raw = ADM2 del BID",
    },
    "GTM": {
        "adm1_corr": "Coincide 1:1 (22 departamentos del raw = 22 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~342 municipios del raw = 342 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "GUY": {
        "adm1_corr": "Coincide 1:1 (10 regiones del raw = 10 unidades ADM1 del BID)",
        "adm2_corr": "No aplica — el raw no expone ADM2 (BID tiene 27 sub-divisiones de región pero no se nombran en la fuente)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "HND": {
        "adm1_corr": "Coincide 1:1 (18 departamentos del raw = 18 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (298 municipios del raw = 298 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "HTI": {
        "adm1_corr": "No aplica — la fuente no es legible",
        "adm2_corr": "No aplica",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "JAM": {
        "adm1_corr": "No aplica — el raw no expone columna administrativa. Usamos contención espacial contra los polígonos de parish del BID.",
        "adm2_corr": "No aplica — BID no tiene polígonos ADM2 para JAM",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "MEX": {
        "adm1_corr": "Coincide 1:1 (32 entidades del raw = 32 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~2,457 municipios del raw = 2,457 unidades ADM2 del BID)",
        "adm1_code": "clave_entidad (INEGI) del raw = ADM1 del BID",
        "adm2_code": "—",
    },
    "PAN": {
        "adm1_corr": "Coincide 1:1 (13 unidades del raw = 13 unidades ADM1 del BID; incluye 10 provincias + 3 comarcas indígenas)",
        "adm2_corr": "Coincide 1:1 (76 distritos del raw = 76 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "PER": {
        "adm1_corr": "Coincide 1:1 (~25 departamentos del raw = 25 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~196 provincias del raw = 196 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "PRY": {
        "adm1_corr": "Coincide 1:1 (18 departamentos del raw, incluyendo Asunción capital = 18 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~250 distritos del raw = 250 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "SLV": {
        "adm1_corr": "Coincide 1:1 (14 departamentos del raw = 14 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~266 municipios del raw = 266 unidades ADM2 del BID)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "SUR": {
        "adm1_corr": "Coincide 1:1 (10 districts del raw = 10 unidades ADM1 del BID)",
        "adm2_corr": "Coincide 1:1 (~62 ressorts del raw = 62 unidades ADM2 del BID; usamos aliases para abreviaturas comunes)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
    "URY": {
        "adm1_corr": "Coincide 1:1 (19 departamentos del raw = 19 unidades ADM1 del BID)",
        "adm2_corr": "No aplica — el raw no expone ADM2 (BID tiene sub-departamentos pero no se nombran en la fuente)",
        "adm1_code": "—",
        "adm2_code": "—",
    },
}


# ---------------------------------------------------------------------------
# TYPE classification (derived from the structured fields)
# ---------------------------------------------------------------------------

def _classify_type(info: dict[str, Any]) -> str:
    """Map a country's structured info to its TYPE bucket.

    Type rule is purely about what the raw carries (the audience-friendly
    classification users approved), NOT about validation capability:
    - A: has street addresses (admin units optional)
    - B: no addresses but at least one raw admin column
    - C: neither addresses nor any raw admin column

    Note: spatial_only countries like JAM fall into C because the raw has
    no admin name to compare against. The polygon containment check is a
    bbox refinement (excludes sea), not admin validation — surfaced in the
    "Nivel de validación" / notes fields, not in the type.
    """
    if info.get("has_addresses"):
        return "A"
    has_raw_admin = info.get("raw_adm1_label_es") or info.get("raw_adm2_label_es")
    if has_raw_admin:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# Field renderers — produce the strings the frontend renders verbatim
# ---------------------------------------------------------------------------

_VALIDATION_LEVEL_LABEL_ES = {
    "adm2": "ADM2 (la unidad más fina disponible)",
    "adm1": "ADM1 (provincia / departamento / región)",
    "spatial_only": "verificación espacial (la coordenada debe caer dentro de algún polígono del país)",
    "bbox_only": "solo bounding box del país",
}

_BID_CORRESPONDENCE_LABEL_ES = {
    ("match", "match"): "ADM1 y ADM2 del raw coinciden con los polígonos del BID",
    ("match", "n/a"): "ADM1 del raw coincide con el polígono ADM1 del BID",
    ("n/a", "match"): "el raw solo expone el ADM2, que coincide con el ADM2 del BID",
    ("raw_finer", "match"): (
        "el ADM1 del raw es más fino que el ADM1 del BID; corresponde al ADM2 del BID"
    ),
    ("raw_coarser", "match"): "el ADM1 del raw es más grueso que el ADM1 del BID",
    ("raw_coarser", "n/a"): (
        "el ADM1 del raw es más grueso que el ADM1 del BID (agregación 1-a-muchos); "
        "BID no tiene polígonos ADM2 para este país"
    ),
    ("n/a", "n/a"): "no aplica (la fuente no expone unidades administrativas)",
}


def _render_admin_levels(info: dict[str, Any]) -> str:
    """Comma-list of the ADM levels reported by the raw, in coarse-to-fine order."""
    parts: list[str] = []
    for key in ("raw_adm1_label_es", "raw_adm2_label_es", "raw_locality_label_es"):
        v = info.get(key)
        if v:
            parts.append(v)
    if not parts:
        return "—"
    return ", ".join(parts)


def _render_validation_level(scope: dict[str, Any]) -> str:
    return _VALIDATION_LEVEL_LABEL_ES.get(
        scope.get("final_match_level", ""),
        scope.get("final_match_level", "—"),
    )


def _render_bid_correspondence(info: dict[str, Any]) -> str:
    key = (info.get("bid_adm1_correspondence"), info.get("bid_adm2_correspondence"))
    return _BID_CORRESPONDENCE_LABEL_ES.get(key, "—")


def _render_has_addresses(info: dict[str, Any]) -> str:
    return "Sí" if info.get("has_addresses") else "No"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_country_info_levels(
    targets_df: pd.DataFrame | None = None,
    schools_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Materialize the country-info table consumed by the dashboard.

    Args:
        targets_df: optional DataFrame from `compute_geocode_targets` aggregation
            with columns iso, n_missing, n_out_of_bounds, n_mismatches,
            n_centroids. Used to attach target counts so the funnel panel can
            render directly without recomputing.
        schools_df: optional DataFrame with iso, current_total to compute totals
            per type.

    Returns:
        DataFrame keyed by ISO with all fields the dashboard needs.
    """
    # geocode_targets now ships 3 rows per ISO (sector ∈ {total, public,
    # private}). The drawer renders TOTAL counts only; the per-sector slices
    # are consumed by the Step03Step04Funnel sector toggle on the frontend.
    if targets_df is None or targets_df.empty:
        targets_lookup: dict = {}
    else:
        if "sector" in targets_df.columns:
            totals_only = targets_df[targets_df["sector"] == "total"]
        else:
            totals_only = targets_df  # legacy CSV without sector column
        targets_lookup = totals_only.set_index("iso").to_dict("index")
    schools_lookup = (
        {} if schools_df is None or schools_df.empty
        else schools_df.set_index("iso").to_dict("index")
    )

    rows: list[dict[str, Any]] = []
    for iso, info in COUNTRY_INFO_LEVELS.items():
        scope = COUNTRY_SCOPE.get(iso, {})
        type_letter = _classify_type(info)
        targets = targets_lookup.get(iso, {})
        schools = schools_lookup.get(iso, {})
        bid_prose = BID_PROSE.get(iso, {})

        rows.append({
            "iso": iso,
            "country_name_es": info["country_name_es"],
            "type": type_letter,
            "pipeline_enabled": bool(scope.get("pipeline_enabled", True)),
            "analysis_included": bool(scope.get("analysis_included", True)),
            # Rendered card fields (frontend renders verbatim)
            "data_source_es": info["data_source_es"],
            "raw_admin_levels_es": _render_admin_levels(info),
            "has_addresses_label_es": _render_has_addresses(info),
            "validation_level_es": _render_validation_level(scope),
            "match_method_label_es": info["match_method_label_es"],
            # Per-level BID correspondence + code mapping (replaces the lumped
            # `bid_correspondence_es` field which conflated multiple cases).
            "bid_adm1_correspondence_es": bid_prose.get("adm1_corr", "—"),
            "bid_adm2_correspondence_es": bid_prose.get("adm2_corr", "—"),
            "bid_adm1_code_es": bid_prose.get("adm1_code", "—"),
            "bid_adm2_code_es": bid_prose.get("adm2_code", "—"),
            # Legacy lumped field — kept for backwards compat; new drawer uses
            # the four per-level fields above.
            "bid_correspondence_es": _render_bid_correspondence(info),
            "notes_es": info.get("notes_es", "") or "",
            # Raw structured fields (kept for downstream consumers / debugging)
            "has_addresses": info["has_addresses"],
            "raw_adm1_label_es": info["raw_adm1_label_es"],
            "raw_adm2_label_es": info["raw_adm2_label_es"],
            "raw_locality_label_es": info["raw_locality_label_es"],
            "bid_adm1_correspondence": info["bid_adm1_correspondence"],
            "bid_adm2_correspondence": info["bid_adm2_correspondence"],
            "match_method": info["match_method"],
            "final_match_level": scope.get("final_match_level"),
            # Routing for the step-04 funnel
            "route_priority": "|".join(info["route_priority"]),
            "cascade_status": info["cascade_status"],
            # Counts — pending = candidates step-04 still needs to process;
            # already_* = how many step-04 already handled (Phase B-1 / B-2).
            "n_schools": int(schools.get("current_total") or 0) if schools else None,
            "n_pending_missing": int(targets.get("n_pending_missing") or 0) if targets else None,
            "n_pending_out_of_bounds": int(targets.get("n_pending_out_of_bounds") or 0) if targets else None,
            "n_pending_mismatches": int(targets.get("n_pending_mismatches") or 0) if targets else None,
            "n_pending_centroids": int(targets.get("n_pending_centroids") or 0) if targets else None,
            "n_already_geocoded": int(targets.get("n_already_geocoded") or 0) if targets else None,
            "n_already_cascade": int(targets.get("n_already_cascade") or 0) if targets else None,
        })
    return pd.DataFrame(rows)


def main() -> None:
    """CLI entrypoint for inspection: write the table standalone."""
    from pathlib import Path

    out_dir = Path("results") / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step03_country_info_levels_preview.csv"
    df = build_country_info_levels()
    df.to_csv(out_path, index=False, encoding="utf-8")
    print(f"Wrote: {out_path} ({len(df)} countries)")
    counts = df["type"].value_counts().to_dict()
    print("Type distribution:", counts)


if __name__ == "__main__":
    main()
