# Cambios sin commitear — 2026-05-08

Documenta el trabajo que hizo otro agente sobre las bases CIMA (schema v2 + scope classification + DOM parser) y los outputs de la sesión actual de Claude (population_grid + RWI exploratorio). Ninguno de estos cambios está committeado al cierre de la sesión.

**Por qué este documento**: la usuaria pidió capturar el estado antes de cerrar para que la próxima sesión arranque con contexto claro.

**Inputs verificados**: `git status` + `git diff HEAD` sobre los archivos en working tree, último commit `56da30a chore(step04): re-run MEX/BRA/COL + finalize + payload refresh`.

---

## 1. Schema v2 — nuevas columnas en `CIMA_ENRICHED_COLUMNS`

`pipeline/constants.py` ahora define **47 columnas canónicas** que todos los 21 archivos `{ISO}_total_cima.csv` analysis-scope tienen idénticamente (re-finalizados 2026-05-08 vía Step 02).

### Columnas nuevas o reposicionadas

| Columna | Origen | Función |
|---|---|---|
| `id_national` | Step 01 | Mirror de id_centro a nivel país (existente, ahora explícito en contrato) |
| `year` | Step 01 | Año de referencia de la data raw |
| `qc_scope_class` | Step 02 | Clasificación territorial (ver abajo). **Nueva** |
| `include_in_spatial_indicators` | Step 02 | Policy helper nullable. **Nueva** |
| `geocoded_in_adm1`, `geocoded_in_adm2` | Step 05 | Promovidas al contrato (antes solo si Step 05 había corrido) |
| `geo_adm1_check`, `geo_adm2_check` | Step 05 | Idem |
| `original_in_adm1`, `original_in_adm2` | Step 05 | Idem |
| `orig_adm1_check`, `orig_adm2_check` | Step 05 | Idem |
| `raw_adm1_code`, `raw_adm2_code` | Step 01/02 | Códigos admin del raw para matching determinístico |

**Implicación crítica**: Step 02 ahora **materializa columnas vacías** para países que nunca corrieron Step 05. Antes esas columnas faltaban del CSV; ahora están presentes con valor blank. **No infieras "columna ausente" como "país no geocodificó"** en código de merge — usa columnas vacías como tal.

### Schema canónico activo

Todos los 21 países analysis-scope comparten exactamente las 47 columnas en el mismo orden. La fuente única de verdad es `CIMA_ENRICHED_COLUMNS` en `pipeline/constants.py`. Para cualquier merge LAC-wide de escuelas, usar los archivos vivos `data/schools/AR/{ISO}/processed/{ISO}_total_cima.csv` — **NO usar `data/schools/AR/LAC_merged.csv`** (histórico, structurally stale).

---

## 2. Scope classification — `qc_scope_class` (additive a `coordinate_quality`)

Razón: distinguir un punto válido en territorio insular/remoto de un punto realmente fuera del país. Antes ambos caían en `coordinate_quality=out_of_bounds`.

| Valor | Significado |
|---|---|
| `inside_mainland_bbox` | Dentro de `COUNTRY_BBOX` y dentro del polígono ADM0 |
| `remote_territory_or_island` | Fuera de `COUNTRY_BBOX` pero **dentro** del polígono ADM0 (ej: islas Galápagos para ECU) |
| `near_border_review` | Fuera del ADM0 pero a menos de 5 km del borde — revisar manualmente |
| `outside_country` | Fuera del ADM0 y lejos del borde — error real |
| `invalid_numeric` | EPSG:4326 imposible (lat>90, etc) |
| `missing` | Sin coordenada utilizable |

### `include_in_spatial_indicators` (nullable boolean)

Policy helper derivado de `qc_scope_class` + severidad de `coordinate_quality`:
- `True` → safe to include automatically
- `False` → exclude
- `<NA>` → keep en master table pero requiere review manual

**Regla operativa para downstream**: para decidir si una escuela entra en indicadores espaciales, **consultar `qc_scope_class` y `include_in_spatial_indicators`**, NO solo `coordinate_quality`. Una escuela puede ser `coordinate_quality=out_of_bounds` y aun así `qc_scope_class=remote_territory_or_island` con `include_in_spatial_indicators=True` (caso típico ECU islas).

### Validación ECU implementada

Las 21 filas ECU actualmente en `coordinate_quality=out_of_bounds` quedaron clasificadas como `qc_scope_class=remote_territory_or_island` con `include_in_spatial_indicators=True`. Confirmado por la otra agente.

---

## 3. Cambios estructurales en `qc_core.py` y `02_qc_coordinates.py`

### Step 02 ahora carga ADM0 además de ADM1/ADM2

```python
boundaries_by_level = {
    0: load_boundaries(level=0),  # nuevo
    1: load_boundaries(level=1),
    2: load_boundaries(level=2),
}
```

### `ADM0_BOUNDARY_PCODE_MAP` — gotcha crítico documentado

El shapefile BID ADM0 (`lac-level-0.shp`) usa códigos **distintos** a los 3-letter ISO que usa el resto del pipeline. Ej: BRA → BR, BLZ → BZ, ECU → EC. El mapping está en `constants.py:ADM0_BOUNDARY_PCODE_MAP`. Para cualquier ADM0 containment test, usar este mapa, NO asumir que el shapefile usa ISO3.

### Helpers nuevos en `qc_core.py`

- `_classify_scope_row(row, iso, country_geom)` → emite `qc_scope_class`
- `_spatial_indicator_policy(row)` → emite `include_in_spatial_indicators` (nullable)
- `_distance_to_polygon_boundary_km(lat, lon, geom)` → distancia great-circle al edge más cercano
- `ADM0_BORDER_REVIEW_DISTANCE_KM = 5.0` km (umbral para `near_border_review`)

### `_evidence_for_row` ahora hardened a NaN

Reemplazó `row.get(key) or default` (rompía con NaN) por helpers `_txt` y `_int` que manejan correctamente NaN y celdas vacías. Estable para los 21 países.

---

## 4. DOM `process_DOM` — reescritura del parser de coordenadas

`pipeline/01_build_cima.py:process_DOM` reescrito para resolver corrupción del raw MINERD 2023-2024.

### Helpers nuevos

| Helper | Función |
|---|---|
| `_parse_dom_coord(value, force_west)` | Extrae decimal robustamente de strings tipo `'18.16494,-'` o `'71.701323'`. Si `force_west=True` y el valor sale positivo, le mete el signo negativo (DOM siempre es hemisferio oeste). |
| `_build_dom_prior_year_coord_lookup(df, ano_col)` | Construye un diccionario `id_centro → (lat, lon)` con coords válidas del año académico **2022-2023** del mismo raw. Sirve como fallback. |
| `_find_dom_year_column(df)` | Encuentra la columna de año académico **por valores** (busca celdas con valor 20222023 o 20232024) en lugar de por nombre — el header del raw MINERD es inconsistente entre versiones. |

### Lógica de backfill

1. Filtrar a `año = 20232024` (current vintage CIMA — preserva privadas)
2. Parsear coords con `_parse_dom_coord`
3. Validar contra bbox DOM: `lat ∈ [17.0, 21.5]`, `lon ∈ [-75.5, -68.0]`
4. Para escuelas con coord 2023-2024 inválida: **buscar la coord 2022-2023 del mismo `id_centro`** y usarla como fallback
5. Reportar n_dom_backfilled en el `note` del `record(iso, agg, note=...)` para auditoría

### id_centro=2694 — resuelto vía backfill 2022-2023

El raw MINERD 2023-2024 tenía `id_centro=2694` (FRANCISCO EMILIO ORTEGA) con `latitud='18033415.0'` (8 dígitos sin punto decimal — corruption). El backfill implementado por la otra agente lo resolvió:

- En el `DOM_total_cima.csv` actual, esa escuela está en `lat=19.8321, lon=-71.01`, `coordinate_source=original`, `coordinate_quality=gps_validated`, `qc_scope_class=inside_mainland_bbox`, `include_in_spatial_indicators=True`.
- El valor correcto vino del raw 2022-2023 vía `_build_dom_prior_year_coord_lookup`.

Verificación cross-país: las 8,925 filas DOM tienen `latitud ∈ [17.80, 19.91]` y `longitud ∈ [-71.91, -68.36]` — sin valores corruptos remanentes. Solo 1 fila con `longitud=NaN` (escuela sin GPS, esperable).

**El "DOM raw corruption fix" ya NO es pending task** (CLAUDE.md tiene la línea desactualizada — corregida en este pase).

---

## 5. Tests actualizados (5 archivos)

| Archivo | Qué se modificó |
|---|---|
| `tests/test_cima_schema.py` | Schema check expandido a 47 columnas + nuevos campos scope |
| `tests/test_qc_schema_v2.py` | Coverage de `qc_scope_class` y `include_in_spatial_indicators` |
| `tests/test_qc_finalize.py` | Asserts de territory containment + island case |
| `tests/test_qc_rollout_smoke.py` | Smoke test post-rollout 21 países |
| `tests/test_build_preserves_enrichment.py` | Refactor estructural significativo (utilidades context manager + uuid) |

---

## 6. Outputs auto-regenerados

Los 4 CSVs de dashboard se regeneraron automáticamente al re-correr el pipeline:

- `results/dashboard/dashboard_geocode_targets.csv`
- `results/dashboard/dashboard_qc_baseline.csv`
- `results/dashboard/dashboard_step03_candidates.csv` (3,500+ filas modificadas, mayor cambio)
- `results/qc_finalize_summary.csv`

---

## 7. Outputs de la sesión actual de Claude (population_grid)

Archivos nuevos no committed que produjo la sesión 2026-05-08:

| Path | Contenido |
|---|---|
| `pipeline/06_pop_exploratory.py` | Análisis RWI vs pobreza IDB (COL pilot completo) |
| `pipeline/06_pop_grid.py` | Build canónico del grid 1km enriquecido (production) |
| `scripts/validate_pop_grid_totals.py` | Validación contra Banco Mundial 2023 |
| `docs/exploratory_rwi_vs_poverty_COL.md` | Reporte metodológico RWI |
| `docs/validation_pop_grid_2026-05.md` | Validación poblacional |
| `data/population/WorldPop/processed/` | 22 grids 1km + manifest + validation CSV + figures |
| `results/exploratory/rwi_vs_poverty/` | RWI agregaciones, tests, figuras COL |
| `results/audit/` | Outputs de auditoría step-03 (de la otra agente) |
| `results/pop_grid_run.log` | Log del run de los 22 países (vacío por buffering, pero ahí queda el archivo) |

Y reorganización de carpetas:
- `data/Poverty Rates/meta-rwi/{ISO}/` — RWI por país (16 carpetas)
- `data/population/WorldPop/{ISO}/` — WorldPop 100m por país (22 carpetas)

---

## 8. Pending tasks visibles en CLAUDE.md (post-cambios)

Reemplazado: ~~"adm1_pcode + adm2_pcode per school: enrich in Step 02 spatial join write-back"~~ — ya implementado.

~~"DOM raw corruption fix" — `id_centro=2694` con `latitud='18033415.0'`~~ — **resuelto vía backfill 2022-2023 en `process_DOM`**. CLAUDE.md tiene la línea desactualizada y se corrige en este pase.

Otros pending heredados sin cambio:
- Phase B-2 cascade for CHL
- Phase B-1 re-run for partial countries (ARG, CRI, GTM, HND, PER, PRY, SUR, URY)
- DOM ciclo distinction
- CHL bug #7 (cod_ense 710/810/910)
- Country QC report toolbox

---

## 9. Para la próxima sesión

Antes de empezar #10 (schools_with_context), conviene:

1. **Decidir si committear lo de la otra agente y lo de esta sesión juntos o por separado**. Sugerencia: committear primero los cambios estructurales del schema v2 + scope + DOM (un commit grande pero coherente), y por separado el work de population_grid + exploratorio (otro commit). El segundo depende del primero conceptualmente, pero pueden coexistir sin él.
2. **Validar las 21 bases CIMA contra el contrato 47-col** — tests deberían pasar pero conviene un `uv run pytest tests/ -v` antes de derivar nada de las bases.
3. **DOM auditoría rápida**: confirmar que `id_centro=02694` tiene `qc_scope_class=invalid_numeric` y `include_in_spatial_indicators=False` después del finalize. Si no, el guard en Step 01 hay que escribirlo antes de derivar la base LAC integrada.
