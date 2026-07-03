# Auditoría de trazabilidad y reproducibilidad — Accessibility Platform

**Fecha:** 2026-05-27
**Autor:** data-audit (consultor externo)
**Insumos:** `docs/bid_deliverable_spec_2026-05-27.md` (lead) · `docs/pipeline_diagnostic_2026-05-27.md` (geo-architect) · pipeline + datos en `C:\Users\lopez\github\IDB\accessibility_platform`
**Alcance:** flujo de datos punta a punta para que el entregable BID sea **trazable** (qué versión de qué insumo entró) y **reproducible** (la misma corrida hoy = la misma corrida en 6 meses).
**Repo SHA al cierre de auditoría:** `e5fd1c4` (branch `step09-travel-times`).

---

## Estado — actualización 2026-05-29

**Shipped desde el cierre de la auditoría:**

- **PROV-1** (commit `73a1548`) — `uv.lock` trackeado, sacado de `.gitignore:9`.
- **QW-1** (commit `fe1c7da`) — escritor `write_scl_output()` con append-by-país, sort estable, guarda de unicidad y atomic write. Test de regresión en `tests/test_scl_append_by_iso.py`. Esto cierra el bullet "estado mutable global" del §2.5 / §3.

**Pendientes (en el orden del plan):** PROV-2 (digest OSRM + `pbf_osm_timestamp` por país) — el notebook Colab para ARG/MEX/BRA YA persiste el timestamp como adelanto, ver `notebooks/colab_osrm_country.ipynb` celda 9. PROV-3 (manifest JSON por corrida + `data_year` real) — pendiente. Decisión de scope: ambas metodologías (FMM + OSRM) se entregan; ARG/MEX/BRA corren OSRM en Colab.

**FMM completo a 22 países (2026-05-29 turno 2):** la corrección inicial del FMM en este doc señalaba que solo 5 países tenían rasters. Esa observación era correcta en ese momento. Después se confirmó que la fricción clipped existe para los 23 y se corrió step 09 para los 17 faltantes (~22 min). El step 10 con el writer append-by-país (`fe1c7da`) integró todo: `accessibility_fmm_scl.csv` pasó de 375,696 filas / 5 países a **2,350,992 filas / 22 países**, 0 duplicados. El §3.4 que listaba "FMM 17 países / 67 MB" ahora es correcto en ISOs (22) pero el tamaño es de ~250 MB. HTI sigue fuera (no WorldPop).

---

## 0. Resumen ejecutivo

Cuatro hallazgos estructurales bloquean la trazabilidad como insumo BID:

1. **`uv.lock` está gitignored** (`.gitignore:9`). El repo declara `requires-python = ">=3.13"` y rangos `>=` para todas las deps geoespaciales pesadas (`pyproject.toml:7-19`). No hay archivo congelado en el repo: la misma corrida en 2 fechas distintas trae versiones distintas de `rasterio`, `geopandas`, `pyproj`, `scikit-fmm`. **Rompedor de reproducibilidad #1.**
2. **OSRM imagen sin tag** (`09b_osrm_build_and_run.sh:24` → `IMG="osrm/osrm-backend"`). La imagen es DockerHub `latest` implícito y los grafos MLD están versionados internamente por OSRM. Una misma corrida en distintas fechas puede generar grafos no comparables. **Rompedor #2.**
3. **OSM extracts de Geofabrik son "latest" en el momento de la corrida** (`09b_osrm_build_and_run.sh:63` → `curl -fL ... -latest.osm.pbf`). Los logs ya muestran que la corrida 17/22 países usó 5 fechas distintas de extract entre 2026-05-17 y 2026-05-22 (un país por día). No hay manifest. **Rompedor #3.**
4. **El año está parcheado por dispersión** — `10_accessibility_aggregate.py:90 YEAR=2023` literal · `09_travel_time_fmm.py:73-76` con sufijo `walking_2019`/`motorized_2019` · `06_pop_grid.py:73-78` con `clipped_global_*_2023_CN_1km_R2025A_UA_v1.tif` literal. El "año del indicador" en el CSV final es el del WorldPop, no el de los datos ministeriales; la columna CIMA `year` (que sí está) **se descarta** al agregar. **Rompedor #4.**

Verde / amarillo / rojo agregado:

| Eje | Estado | Comentario |
|---|---|---|
| Inputs externos identificados | YELLOW | Todas las fuentes están en el repo o el árbol de datos, pero el repo no captura las versiones (ver §1). |
| Determinismo del output | YELLOW | Pipeline FMM/Step 10 = determinista bit-exact si todas las versiones están pinneadas. OSRM Step 09b/10b = sí salvo la ventana de empate al elegir nearest school cuando 2+ schools tienen el mismo tiempo (ver §2). |
| Layout de cache / entregable | RED | No hay distinción declarada entre cache reconstruible, scratch y ground-truth. Cuesta más sacar (~6 GB de fricción global + ~21 GB de raw schools) que dejar entrar al entregable lo que sobra. |
| Re-runnability granular | RED | Re-correr 1 país requiere hoy ~6 dependencias implícitas y rompe el CSV global (bug step-10b documentado en `pipeline_diagnostic_2026-05-27.md` §3.4). |
| Manifest de provenance | RED | No existe. Hay JSONs sueltos por país (`_ministry_counts.json`) y un `_manifest.csv` del Step 06, pero no consolidados ni atados a la corrida final. |

---

## 1. Trazabilidad de inputs externos

Para cada fuente: ¿está la versión declarada? ¿se podría re-correr en 6 meses y obtener bit-exact lo mismo?

### 1.1 OSM — extracts Geofabrik por país (input OSRM, Step 09b)

- **Fuente.** `https://download.geofabrik.de/{region}/{country}-latest.osm.pbf`. URL canónica en `09b_osrm_build_and_run.sh:63`.
- **Versión en repo hoy.** **No versionado.** El directorio `data/transportation/osrm/` (que es donde el driver guarda los `.osm.pbf` cacheados, `09b_osrm_build_and_run.sh:23`) está **vacío** al momento de esta auditoría (`du -sh data/transportation/osrm/` → 4 KB). Los `.osm.pbf` fueron borrados o reciclados — no hay snapshot conservado.
- **Versión inferible.** Solo a través de los **logs**: cada `osrm-extract` imprime `[info] timestamp: 2026-MM-DDTHH:MM:SSZ` del PBF. Inventario de los logs presentes en `results/`:

  | Log | Fecha del extract Geofabrik |
  |---|---|
  | `_step09b_osrm_cri_ecu_per.log` | 2026-05-17T20:20:44Z |
  | `_step09b_osrm_batch1_9countries.log` (BLZ SUR JAM GUY SLV DOM PAN HND? GTM?) | 2026-05-20T20:21:13Z |
  | `_step09b_osrm_batch1b_HND_GTM_URY.log` | 2026-05-21T20:20:52Z |
  | `_step09b_osrm_PRY.log` | 2026-05-21T20:20:52Z |
  | `_step09b_osrm_CHL_BOL.log` | 2026-05-22T20:21:01Z |

  El entregable OSRM actual (`results/accessibility/accessibility_osrm_scl.csv`, 17 países) mezcla **5 fechas distintas de Geofabrik**, separadas por hasta 6 días.

- **Cómo se obtuvo.** Descarga automatizada vía `curl -fL` en cada corrida (`09b_osrm_build_and_run.sh:63`). Sin caché versionada — si el `.osm.pbf` ya existe en `_pbf/{iso}.osm.pbf`, se reusa (línea 62), pero ese cache vive fuera de Git.
- **¿Declarada en el pipeline?** **No.** El log impreso por OSRM contiene el timestamp pero el script no lo extrae ni lo persiste fuera del log. Riesgo: alto.
- **¿Re-runnable en 6 meses?** **No bit-exact.** Geofabrik publica diariamente; `-latest.osm.pbf` cambia bajo los pies. Para bit-exactitud habría que (a) congelar el `.osm.pbf` en el entregable o (b) anclar a un snapshot Geofabrik fechado (Geofabrik mantiene archivos históricos en `/region/country-YYYY-MM-DD.osm.pbf` para algunos países, no todos).

### 1.2 MAP friction surfaces — walking 2019 y motorized 2019 (input FMM, Step 09)

- **Fuente.** Malaria Atlas Project, Weiss et al. 2020 (https://malariaatlas.org/explorer/). Walking-only friction surface 2019 y motorized friction surface 2019 (R20**A series).
- **Versión en repo hoy.** Dos rasters globales en `data/transportation/surface_friction/`:
  - `202001_Global_Walking_Only_Friction_Surface_2019/202001_Global_Walking_Only_Friction_Surface_2019.tif` — 691 MB, mtime 2026-05-11. Resolución ~30 arc-sec, EPSG:4326, NoData=-9999.
  - `202001_Global_Motorized_Friction_Surface_2019/202001_Global_Motorized_Friction_Surface_2019.tif` — 708 MB, mtime 2026-05-11.
  - Tercer raster `201501_Global_Travel_Speed_Friction_Surface_2015/...` está presente pero NO se usa por Step 09 (solo se clipea por Step 07 para uso histórico; ver `07_friction_clip.py:79-89`).
- **Cómo se obtuvo.** Descarga manual desde MAP — no hay script en el repo. El prefijo `202001_` denota la fecha de publicación MAP (2020-01); el sufijo `2019` es la vintage del dato. Los archivos `.properties` que acompañan son metadatos de GeoServer (no contenido científico).
- **¿Declarada en el pipeline?** **Sí, en el filename**, pero no en un manifest declarativo. Step 07 referencia el path completo en `07_friction_clip.py:79-89`. Step 09 referencia solo el sufijo `_2019` (`09_travel_time_fmm.py:73-76` `walking_2019`, `motorized_2019`).
- **¿Re-runnable en 6 meses?** **Sí, si los `.tif` están preservados** (binarios estables, fechados por nombre). MAP no ha publicado un 2020/2021/2022 motorized en R20**A todavía; cuando lo haga, habrá que decidir si seguir con 2019 o actualizar. **Riesgo:** medio — el filename usa el año pero no hay hash congelado.

### 1.3 WorldPop population — 100m CN base + LAC school-age 1km

- **Fuente.** WorldPop (https://hub.worldpop.org/). Series:
  - 100m population count (CN, country-norm a UN WPP) — release R2025A, año 2023.
  - LAC school-age 1km — release R2025A_UA, año 2023, sexo y bucket etario (`f_05`, `m_05`, `f_10`, `m_10`, `f_15`, `m_15`).
- **Versión en repo hoy.**
  - Por país en `data/population/WorldPop/{ISO}/{iso}_pop_2023_CN_100m_R2025A_v1.tif`. PAN ejemplo: 6.4 MB, mtime 2026-05-08.
  - LAC 1km en `data/population/WorldPop/LAC/clipped_global_{f,m}_{05,10,15}_2023_CN_1km_R2025A_UA_v1.tif`. mtime 2026-03-11. **El sufijo `clipped_global_` indica que ya están clipeados a LAC** (paso preprocesamiento previo al repo).
- **Cómo se obtuvo.** Descarga manual (no hay script). El filename es la versión: `2023` = año del dato, `CN` = constrained-non-UN-adj alternative (decisión documentada en `MEMORY.md → project_pop_grid_un_adjustment_decision.md` — UA solo existe a 1km en R2025A, se prefirió CN a 100m por granularidad), `R2025A` = release Q1 2025, `UA` (solo en 1km school-age) = UN-Adjusted, `v1` = vintage del release.
- **¿Declarada en el pipeline?** **Sí en el filename** y replicada en código:
  - `06_pop_grid.py:73-78` referencia los nombres con sufijo `_2023_CN_1km_R2025A_UA_v1.tif` literal.
  - `06_pop_grid.py:99` resuelve dinámicamente el 100m vía glob `*pop_*100m*.tif` — tolera variantes en el filename pero no las versiona.
- **¿Re-runnable en 6 meses?** **Sí si se preservan los `.tif`.** WorldPop R2025B (Q3 2025) y futuras releases publicarán versiones nuevas sin sobrescribir las anteriores en el hub, pero el repo asume el filename específico `_2023_CN_*_R2025A_*` — un upgrade rompe el pipeline silenciosamente con `FileNotFoundError` (`06_pop_grid.py:188`).
- **Validación auxiliar.** `data/population/WorldPop/processed/_manifest.csv` (escrito por Step 06) registra `pop_total` por país y tamaño del grid. **Es la mejor pista de provenance** que el pipeline produce hoy a este nivel, pero no captura la versión WorldPop, solo los conteos derivados.

### 1.4 CIMA per país — datos ministeriales

- **Fuente.** Ministerios de educación de cada país. Cada uno con su URL/portal (SIGED MEX, INEP BRA, MINEDUC ECU, etc.).
- **Versión en repo hoy.** El **mejor** capture de provenance del repo. Cada país tiene `data/schools/AR/{ISO}/raw/_ministry_counts.json` con:
  - `derived_date`: fecha del filtrado/snapshot (todas las 21 = "2026-03-19", con excepción de BHS y BRB no auditados aún).
  - `primary_raw_file`: nombre del archivo ministerial original.
  - `filter_applied`: la lógica de selección (e.g. CHL "unique RBDs", BRA "tp_situacao_funcionamento==1").
  - `total_universe`, `public_universe`, `breakdown` por categoría.
  - `notes` libre.

  Ejemplos verificados (`cat data/schools/AR/{ISO}/raw/_ministry_counts.json`):
  - **PAN**: "Marco muestral 19 DE JUNIO 2024.xlsx" + "Anexo 2 - Georreferencia de Centros Educativos.xlsx", mtime 2026-03-17. 3,660 universo total.
  - **MEX**: "siged_total.csv" — universo 243,842; público 209,629. "scraped/exported from SIGED portal".
  - **BRA**: "microdados_censo_escolar_2023/dados/microdados_ed_basica_2023.csv" — Censo Escolar INEP 2023.
  - **CHL**: "20230912_Directorio_Oficial_EE_2023_20230430_WEB.csv" — fecha en el filename.
  - **DOM**: año académico 20232024.
  - **BHS**: `_ministry_counts.json` solo existe en `raw/old/` (universo legacy 77); el actualizado para el onboarding 2026-05-13 no se escribió. **Hueco.**

- **Cobertura del año.** La columna `year` en cada `{ISO}_total_cima.csv` registra la cohorte ministerial:

  | ISO | year | ISO | year | ISO | year |
  |---|---|---|---|---|---|
  | ARG | 2024 | DOM | _vacío_ | PAN | 2024 |
  | BHS | _vacío_ | ECU | 2024 | PER | 2024 |
  | BLZ | 2024 | GTM | 2024 | PRY | 2023 |
  | BOL | 2023 | GUY | 2024 | SLV | 2024 |
  | BRA | 2023 | HND | 2023 | SUR | 2024 |
  | BRB | 2024 | HTI | 2022 | URY | 2024 |
  | CHL | 2023 | JAM | 2024 | | |
  | COL | 2023 | MEX | 2024 | | |
  | CRI | 2024 | | | | |

  **Heterogéneo: 2022–2024.** El indicador final agregado escribe `year=2023` constante (`10_accessibility_aggregate.py:90`) — la cohorte ministerial real del CSV CIMA se pierde al agregar.

- **¿Declarada en el pipeline?** **Parcialmente.** El `_ministry_counts.json` está dentro de `data/`, que está gitignored (`.gitignore:28`). No viaja con el repo y BID no tendría visibilidad de él salvo que se incluya en el bundle de inputs.
- **¿Re-runnable en 6 meses?** Solo si los raws siguen disponibles en el portal ministerial — los ministerios suelen rotar URLs y reemplazar datasets sin versionar. **Riesgo:** alto. El `_ministry_counts.json` es el único respaldo de qué archivo se usó.

### 1.5 Polígonos administrativos BID — adm0, adm1, adm2

- **Fuente.** Capa interna del BID. Filename `lac-level-{0,1,2}.shp` en `data/bounderys/LAC/level {0,1,2}/`. No hay URL canónica documentada.
- **Versión en repo hoy.** Shapefiles + auxiliares (.cpg, .dbf, .prj, .shx) + CSVs (`lac-level-{1,2}.csv`).
  - `lac-level-0.shp` 96 MB, mtime 2026-03-01.
  - `lac-level-1.shp` (idem nivel 1).
  - `lac-level-2.csv` 1.3 MB, mtime 2026-04-13. **El CSV tiene mtime distinto del SHP** — sugiere que fue regenerado o curado por separado.
- **Cómo se obtuvo.** No documentado en el repo. Probablemente compartido por Ceci/BID al inicio del proyecto. CLAUDE.md no menciona origen.
- **¿Declarada en el pipeline?** Solo por path absoluto (`06_pop_grid.py:68-70`, `07_friction_clip.py:76`, `09_travel_time_fmm.py:70`, `10_accessibility_aggregate.py:64`). Sin hash, sin fecha, sin versión semántica.
- **Caveat de schema.** `lac-level-0.shp` tiene 3 codes legacy de 3 letras (ARG/BHS/JAM) y 20 de 2 letras (PA/BR/...); el pipeline lo resuelve dinámicamente (`07_friction_clip.py:91-117` `ADM0_PCODE_TO_ISO3`, `09_travel_time_fmm.py:108-115`). Esto **funciona** pero documenta una inconsistencia de proveniencia del shapefile que conviene declarar.
- **¿Re-runnable en 6 meses?** **Sí si los `.shp` se preservan.** Lectura limpia. Si BID los actualiza con nuevos pcodes o cambia ADM2 (e.g. nueva provincia ARG), el pipeline rompe silenciosamente al join. **Riesgo:** medio.

### 1.6 Meta RWI (Relative Wealth Index)

- **Fuente.** Meta Data for Good vía HDX. URL canónica preservada por archivo: `data/Poverty Rates/meta-rwi/{ISO}/metadata-*-csv.csv`. Ejemplo COL: `https://data.humdata.org/dataset/.../col_relative_wealth_index.csv`.
- **Versión en repo hoy.** 18 países (`ARG BLZ BOL BRA COL CRI DOM ECU GTM GUY HND JAM MEX NIC PER PRY SLV SUR`). Cada uno con `{iso}_relative_wealth_index.csv` + `metadata-...csv` que captura `created`, `last_modified`, `download_url`, `id`. Ejemplo COL: created `2021-04-08T17:42:01`, dataset ID `76f2a2ea-ba50-40f5-b79c-db95d668b843`.
- **¿Declarada en el pipeline?** **Excelente** — esto es lo más cerca de un manifest formal que hay en el repo. El metadata.csv viaja junto al dato. `06_pop_grid.py:67` resuelve dinámicamente.
- **¿Re-runnable en 6 meses?** **Sí.** Meta congeló el modelo RWI (decisión documentada en `MEMORY.md → project_rwi_vs_poverty_pilot.md`); no se actualiza. Los CSVs son estáticos.
- **Caveat.** Falta para 5 países del scope (`BHS BRB CHL HTI PAN URY`) — `06_pop_grid.py` produce esos grids sin columna `rwi` (NaN). **Documentado** en el limitations log A3.

### 1.7 IDB poverty rates / NBI

- **Fuente.** "Mapa de pobreza" oficial por país, cosechado por equipo BID. CSV `data/Poverty Rates/lac-level-2.csv` (1.3 MB).
- **Versión en repo hoy.** Un solo CSV con columna `POVERTY_SOURCE` que documenta vintage **por fila** (por ADM2). Distribución de fuentes verificada:

  | Fuente | Filas | Fuente | Filas |
  |---|---:|---|---:|
  | Mapa de pobreza (2010) | 6,098 | NBI (2011) | 335 |
  | Mapa de pobreza (2020) | 3,635 | NBI (2005) | 153 |
  | Mapa de pobreza (2011) | 499 | NBI (2017) | 196 |
  | Mapa de pobreza (2019) | 406 | | |
  | Mapa de pobreza (2012-2014) | ~916 | _(blanco)_ | 293 |

  Vintage **muy heterogénea** (rango 2005–2020). El pipeline agrega como si fuera un valor único.

- **¿Declarada en el pipeline?** Sí, columna `POVERTY_SOURCE` viaja con el dato — pero **no llega al output final**. `06_pop_grid.py` toma `POVERTY_RATE` y `NBI_RATE` y los anexa al grid por ADM2 (`poverty_rate_adm2`, `nbi_rate_adm2`), descartando `POVERTY_SOURCE`. El CSV SCL final solo lleva el quintil derivado, sin trazar a qué vintage corresponde.
- **¿Re-runnable en 6 meses?** Estático en el repo. El CSV mtime es 2026-04-13. **Riesgo:** bajo en reproducibilidad, alto en interpretabilidad (mezcla un quintil 2010 con uno 2020).
- **Hueco PER.** PER no tiene `poverty_rate_adm2` poblado (`MEMORY.md → project_per_no_poverty`). Limitations log A2.

### 1.8 OSRM Docker image (engine, no dato)

- **Fuente.** `osrm/osrm-backend` en DockerHub. Versión upstream `v5.27.x` (de la spec del lead) con perfiles `foot.lua` y `car.lua`.
- **Versión en repo hoy.** **Sin tag** (`09b_osrm_build_and_run.sh:24` → `IMG="osrm/osrm-backend"` = `:latest` implícito).
- **¿Declarada en el pipeline?** **No.** Ni tag, ni digest.
- **¿Re-runnable en 6 meses?** **No.** DockerHub mueve `:latest`. Y peor: cualquier cambio incompatible en el formato de grafo MLD entre versiones de OSRM rompe la cadena `extract → partition → customize → routed` (el grafo y el server tienen que ser de la misma versión).
- **Cita en geo-architect doc.** `pipeline_diagnostic_2026-05-27.md` §1.2 ya identificó este gap. Se confirma aquí como crítico.

### 1.9 Resumen de versiones declaradas vs. silenciosas

| Fuente | Declarada en pipeline | Capture método | Riesgo silencioso |
|---|---|---|---|
| OSM extracts Geofabrik | No (solo en logs) | Log `[info] timestamp:` | **Alto** |
| MAP friction 2019 | Sí (filename) | Filename + path literal | Bajo |
| WorldPop pop_2023_R2025A | Sí (filename) | Filename + path literal | Bajo (riesgo: upgrade de release) |
| CIMA per país | Parcial (`_ministry_counts.json` gitignored) | JSON sidecar gitignored | **Alto** |
| Polígonos BID | No | Solo path | Medio |
| Meta RWI | **Sí** (sidecar `metadata-...csv` con dataset_id) | Sidecar versionado | Bajo |
| IDB poverty CSV | Por fila (`POVERTY_SOURCE`) **no propaga al output** | Columna del CSV input | Medio (interpretabilidad) |
| OSRM Docker image | **No** | Implícito `:latest` | **Alto** |
| Libs Python (numpy, rasterio, …) | Parcial (`pyproject.toml` rango `>=`) | `uv.lock` **gitignored** | **Alto** |

---

## 2. Determinismo del output

¿Dado los mismos inputs físicos, dos corridas producen el mismo output bit-exact? Análisis por componente:

### 2.1 Step 06 — population grid (FMM input)

- **Operaciones críticas.** `rasterio.warp.reproject` con `Resampling.sum` para agregar 100m → 1km (`06_pop_grid.py:103-126`), `scipy.ndimage.label` 8-conectividad para clusters (`06_pop_grid.py:135-178`), `scipy.spatial.cKDTree` para nearest-neighbor RWI (`06_pop_grid.py:56`).
- **Determinismo.** `reproject` es determinista. `label` con structure fija es determinista. **`cKDTree.query` puede romper empates de manera no determinística** en versiones distintas de SciPy (tie-breaking por orden interno) — pero hay pocas ties en deg-coords + un solo nearest.
- **Riesgos float.** El cálculo `cell_area_km2 = cell_h_km * cell_h_km * np.cos(np.radians(lats))` (`06_pop_grid.py:138`) usa float64 por defecto en numpy — bit-exact entre corridas con la misma numpy.
- **Veredicto:** GREEN si numpy + scipy + rasterio están pinneados. YELLOW sin pinneo.

### 2.2 Step 07 — friction clipping

- Operación pura: leer ventana del raster global y escribir clipeado. `rasterio.windows.from_bounds` es determinista. **GREEN.**

### 2.3 Step 09 — FMM travel time

- `skfmm.travel_time` (scikit-fmm 2025.6.23 en `uv.lock`) — solver eikonal numérico. Es **determinista bit-exact** para un mismo input float64 y misma versión de la librería.
- **Riesgos:** la librería declara "no exactamente monótono en el set de fuentes" — documentado en `MEMORY.md → project_fmm_sector_monotonicity_fix.md` y mitigado por `np.fmin(tt_pub, tt_prv)` en `10_accessibility_aggregate.py:288`.
- **Veredicto:** GREEN si scikit-fmm pinneado.

### 2.4 Step 09b — OSRM travel time

- **Lo determinista.** `cKDTree.query` para K-nearest (mismo tema 2.1), `osrm-routed` con `--algorithm mld` sobre un grafo fijo (`09b_osrm_build_and_run.sh:80`). El grafo MLD, una vez construido con un `osrm-extract` + `osrm-customize` específicos, es determinista en sus respuestas `/table`.
- **Lo no determinista (RED).**
  1. **Paralelismo de threads** (`ThreadPoolExecutor(max_workers=12)`, `09b_travel_time_osrm.py:247`). Los threads consumen futures en orden de completar, no de submisión (`as_completed` línea 249). Pero esto **no afecta el output** porque cada `query_cell(i)` escribe en `times_min[i]` por índice fijo. **OK.**
  2. **Build OSRM** depende de **threads del host** (`Threads: 22` en el log CRI). Lua scripts de `foot`/`car` son deterministas; el orden de procesamiento de ways/nodes dentro de `osrm-extract` con N threads puede afectar IDs internos pero no el resultado del MLD (compactado en `osrm-partition` y `osrm-customize`). **YELLOW.**
  3. **`time_to_nearest_min = min(K nearest)`.** Si dos escuelas empatan en tiempo, el `np.nanargmin` toma la primera por orden, que viene del `idx_matrix` de cKDTree — depende de cómo cKDTree rompe empates Euclideanos. **Veredicto:** GREEN para el VALOR (siempre el mismo `min`), YELLOW para `nearest_school_id` (puede variar entre runs si hay 2 escuelas equidistantes en deg-coords con igual tiempo en red).
- **Veredicto agregado:** YELLOW.

### 2.5 Step 10 / 10b — agregación SCL

- **Pure pandas.** `groupby` (estable en pandas 3.0 con `kind='mergesort'`), suma sobre floats, `round(..., 4)`. Los pesos quintile usan `kind='mergesort'` explícito (`10_accessibility_aggregate.py:112`) — **determinista.** GREEN.
- **Caveat:** `np.fmin(tt_pub, tt_prv)` en `10_accessibility_aggregate.py:288` con NaN: numpy garantiza que `fmin(NaN, x) = x`. GREEN.

### 2.6 Tabla resumen de determinismo

| Step | Determinismo | Causa principal de varianza |
|---|---|---|
| 06 pop grid | GREEN (con libs pinneadas) | — |
| 07 friction clip | GREEN | — |
| 09 FMM | GREEN (con scikit-fmm pinneada) | — |
| 09b OSRM | YELLOW | `nearest_school_id` en empate; threads de `osrm-extract` |
| 10 / 10b SCL | GREEN | — |

**Riesgos sistémicos a TODOS los steps:**
- Versiones de libs no pinneadas (`.gitignore:9` excluye `uv.lock`).
- Versiones de GDAL/PROJ vendoradas dentro de la rueda de rasterio cambian con cada release menor — pueden mover reproyecciones por 1 ulp.
- Encoding `utf-8-sig` con BOM en outputs (`10_accessibility_aggregate.py:311`, `10b_accessibility_aggregate_osrm.py:102`) — bit-exact OK pero ojo a herramientas downstream.

---

## 3. Cache layer

Cómo categorizar los directorios para el entregable Docker.

### 3.1 Categoría (a) — Ground-truth no perdible

Debe viajar con el entregable BID (o ser regenerable solo desde scripts versionados del repo).

| Path | Tamaño | Naturaleza | Gitignored | Distribución BID |
|---|---:|---|---|---|
| `data/schools/AR/{ISO}/raw/*` | ~21 GB total (raw + processed + LAC merge) | Datos ministeriales originales | Sí (`.gitignore:28`) | **Bundle separado bajo NDA**; no en imagen Docker pública |
| `data/schools/AR/{ISO}/raw/_ministry_counts.json` | <1 MB total | **Provenance ministerial** | Sí (cae bajo `data/`) | **Debe entrar al repo o al bundle como manifest** |
| `data/bounderys/LAC/level {0,1,2}/lac-level-*.{shp,dbf,prj,shx,cpg}` + `lac-level-{1,2}.csv` | 1.2 GB | Polígonos BID admin | Sí | Bundle (no parece ser confidencial pero hoy no se publica) |
| `data/Poverty Rates/lac-level-{1,2}.csv` + `meta-rwi/{ISO}/*.csv` + `metadata-*-csv.csv` | 23 MB | Pobreza BID + RWI Meta | Sí | Bundle (RWI metadata sidecar **debe** preservarse — único provenance formal) |
| `data/population/WorldPop/{ISO}/{iso}_pop_2023_CN_100m_R2025A_v1.tif` | ~3.6 GB | Input WorldPop CN 100m | Sí | Bundle |
| `data/population/WorldPop/LAC/clipped_global_*_2023_CN_1km_R2025A_UA_v1.tif` | ~324 MB | Input WorldPop school-age 1km | Sí | Bundle |
| `data/transportation/surface_friction/202001_Global_*_Friction_Surface_2019/*.tif` | 1.4 GB (2 rasters globales) | MAP friction global | Sí | Bundle (o link al MAP hub con DOI) |

### 3.2 Categoría (b) — Cache reconstruible

Debe estar gitignored, **declararse como reconstruible**, y opcionalmente distribuirse para ahorrar tiempo de bootstrap.

| Path | Tamaño | Naturaleza | Gitignored | Regenerable desde |
|---|---:|---|---|---|
| `data/transportation/surface_friction/clipped/{ISO}/*.tif` | 387 MB | Friction recortado por país | Sí | `07_friction_clip.py` (input categoría a) |
| `data/transportation/travel_times/{ISO}/*.tif` | 571 MB (132 rasters: 22 países × 6 modos/niveles + sector splits para algunos) | FMM travel-time rasters | Sí | `09_travel_time_fmm.py` |
| `data/population/WorldPop/processed/population_grid_{ISO}.csv` | 1.1 GB (22 CSVs) | 1km grid enriquecido | Sí | `06_pop_grid.py` |
| `data/population/WorldPop/processed/_manifest.csv` | <10 KB | Manifest Step 06 | Sí | Step 06 mismo |
| `data/transportation/osrm/{ISO}_{foot,car}/*` + `_pbf/*.osm.pbf` | **0 KB ahora** (borrado), normalmente ~2–8 GB por país | Grafos OSRM MLD + PBF Geofabrik cacheado | Sí | `09b_osrm_build_and_run.sh` (requiere red Geofabrik) |
| `results/osrm/{ISO}_{mode}_{level}_osrm.parquet` | 563 MB (102 parquets, 17 países × 6) | OSRM travel-time matrices por celda | Sí (`.gitignore:53`) | `09b_travel_time_osrm.py` |
| `data/schools/AR/{ISO}/processed/{ISO}_total_cima.csv` + `LAC_*.csv` | ~3 GB | CIMA enriquecido + LAC merge | Sí | Steps 01–07 |

### 3.3 Categoría (c) — Intermediate scratch

Regenerable en cada corrida, no debe distribuirse, no debe asumirse persistente.

| Path | Tamaño | Naturaleza | Gitignored |
|---|---:|---|---|
| `tmp/` | varía | Working dir Angela | Sí (`.gitignore:81`) |
| `results/_step09b_osrm_*.log` (×5) | ~520 KB total | Logs OSRM ad-hoc | **No explícitamente** — caen bajo `results/` no listado; deben gitignorearse |
| `results/_step10*.log` | ~30 KB | Logs Step 10 | Idem |
| `results/_pytest_build_tmp/` | varía | Pytest scratch | Idem |
| `__pycache__/`, `*.pyc`, `.pytest_cache/` | varía | Python | Sí (`.gitignore:2-5`) |

### 3.4 Resultados versionables (categoría (d), implícita)

Outputs canónicos del pipeline que SÍ deben entrar al entregable (lo que BID consume):

| Path | Tamaño | Naturaleza | Gitignored | Distribución |
|---|---:|---|---|---|
| `results/accessibility/accessibility_fmm_scl.csv` | 67 MB | Output FMM 17 países | Sí (`.gitignore:52` `results/accessibility/`) | **Debe entrar al release/tag** |
| `results/accessibility/accessibility_osrm_scl.csv` | 47 MB | Output OSRM 17 países | Sí | **Debe entrar al release/tag** |

**Observación:** los outputs canónicos están gitignored. Esto es defensivo (CSVs grandes regenerables) **pero rompe el flujo BID**: la spec del lead §1.3 lista estos archivos como "entregable principal". Si no se commitean ni a git ni a un release attachment, BID no tiene cómo obtenerlos sin re-correr todo el pipeline.

### 3.5 Recomendación de empaquetado

```
Entregable BID v1.0.0
├── Imagen Docker (~3-5 GB):    código + libs pinneadas + binarios OSRM
├── Bundle de inputs (~6 GB):   categoría (a) — distribuible vía link BID
│   ├── data/schools/...        (con NDA donde aplique)
│   ├── data/bounderys/...
│   ├── data/Poverty Rates/...
│   ├── data/population/WorldPop/...
│   └── data/transportation/surface_friction/202001_*/...
└── Outputs canónicos (~115 MB): los 2 CSVs SCL + manifest
    (publicados como release attachment en GitHub, no como image-internal)
```

---

## 4. Re-runnability granular

### 4.1 Pregunta — re-correr solo Step 10b para 1 país

Dado un país `{ISO}` que ya pasó por Steps 01–09b en otra corrida, ¿qué necesita para reagregar?

**Dependencias mínimas requeridas:**
1. `results/osrm/{ISO}_{walking,motorized}_{primaria,secbaja,secalta}_osrm.parquet` — 6 archivos.
2. `data/population/WorldPop/processed/population_grid_{ISO}.csv`.
3. `data/bounderys/LAC/level 2/lac-level-2.csv` (para nombres ADM).
4. Código `pipeline/10_accessibility_aggregate.py` + `pipeline/10b_accessibility_aggregate_osrm.py`.

**Bug bloqueador (ya identificado por geo-architect §3.4):** correr `--countries {ISO}` **sobrescribe** `results/accessibility/accessibility_osrm_scl.csv` con solo ese país (`10b_accessibility_aggregate_osrm.py:101-102`). Re-correr 1 país hoy **destruye** los otros 21. Bloqueante para el flujo "actualicé ARG".

### 4.2 Pregunta — actualizar al año 2024 (cuando salga WorldPop 2024 / CIMA nuevo) para 1 país, sin re-construir grafos OSRM

¿Qué pasos se saltean? ¿Qué se rompe?

**Pasos que SE saltean (grafo OSRM no cambia):**
- `09b_osrm_build_and_run.sh build_graph` — el grafo MLD vive en `data/transportation/osrm/{ISO}_{foot,car}/` y solo cambia si cambió OSM o el perfil Lua.

**Pasos que SÍ hay que correr:**
- Step 01 (`01_build_cima.py`) — reconstruir CIMA con el raw 2024.
- Step 02 → Step 07 — recalcular QC, geocoding, base K-12, friction clip (si no estaba), schools context.
- Step 06 si WorldPop 2024 salió — re-correr y regenerar el grid.
- Step 09 (FMM) — re-correr (rápido, ~3 min para todo LAC).
- Step 09b — re-correr el cliente OSRM (matrices nuevas).
- Step 10 / 10b — re-agregar.

**Dependencias hardcoded de año que rompen el upgrade:**

| Archivo:línea | Hardcode | Rompe el upgrade a 2024 |
|---|---|---|
| `06_pop_grid.py:73-78` | `clipped_global_*_2023_CN_1km_R2025A_UA_v1.tif` literal | **Sí** — `FileNotFoundError` al buscar `_2024_`. |
| `06_pop_grid.py:96-100` | `*pop_*100m*.tif` glob | No (tolerante al año). |
| `07_friction_clip.py:79-89` | `202001_Global_*_Friction_Surface_2019` literal | **Sí** — si MAP saca friction 2020. |
| `09_travel_time_fmm.py:73-76` | sufijos `walking_2019`, `motorized_2019` | **Sí.** |
| `10_accessibility_aggregate.py:90` | `YEAR = 2023` constante en cada fila del CSV | **Sí** — escribe año incorrecto. |
| `10_accessibility_aggregate.py:91-92` | `METHOD = "FMM"`, `SOURCE = "...FMM sobre fricción MAP 2019..."` | Cosmético, pero confunde. |

`pipeline_diagnostic_2026-05-27.md` §3.1 ya listó estos.

### 4.3 Matriz dependencia país × step

Resumen de qué necesita cada step (Y = depende, N = no, F = file-only, sin lógica de país):

| Step | CIMA país | Polígonos BID | WorldPop país | WorldPop LAC sa | MAP friction global | OSM extract país | Lib Python | Engine OSRM |
|---|---|---|---|---|---|---|---|---|
| 01 build_cima | Y | Y | N | N | N | N | Y | N |
| 02 qc | Y | Y | N | N | N | N | Y | N |
| 03 coverage | Y | Y | N | N | N | N | Y | N |
| 04 geocode | Y | Y | N | N | N | N | Y | N |
| 05 base_k_12 | Y | Y | N | N | N | N | Y | N |
| 06 pop_grid | N | F | Y | F | N | N | Y | N |
| 07 friction_clip | N | F | N | N | F | N | Y | N |
| 07 schools_context | Y | Y | N | N | N | N | Y | N |
| 09 FMM | Y (LAC merge) | F | Y (grid) | N | F (clipped) | N | Y | N |
| 09b OSRM | Y (LAC merge) | F | Y (grid) | N | N | Y | Y | Y |
| 10 / 10b SCL | N | F (admin names) | Y (grid) | N | N | N | Y | N |

**Implicación operacional:**
- Re-correr 09b para 1 país = OSM país + WorldPop país + LAC merge + grafo OSRM cacheado + OSRM engine.
- Re-correr 10b para 1 país = parquet de 09b + grid + admin csv + lib.
- **Re-correr 09 FMM** = mucho más barato; solo necesita friction global + grid + LAC merge.

---

## 5. Provenance statement — manifest design

Diseño concreto del JSON / YAML que viaja con cada CSV SCL canónico. Modelo "snapshot manifest" — uno por corrida.

### 5.1 Path propuesto

```
results/accessibility/
├── accessibility_osrm_scl.csv
├── accessibility_fmm_scl.csv
└── manifests/
    └── {YYYY-MM-DD}_{run_id}_manifest.json
```

`run_id` = sha256 corto (12 chars) del concat de country ISOs + datetime UTC.

### 5.2 Schema JSON (propuesto)

```json
{
  "manifest_schema_version": "1.0.0",
  "pipeline_version": "v1.0.0",
  "repo_git_sha": "e5fd1c4232ef885db166b72e022372bf60373e46",
  "repo_git_branch": "step09-travel-times",
  "repo_dirty": false,
  "run_id": "2026-05-27-a3f8c1e22b9d",
  "run_started_utc": "2026-05-27T14:32:18Z",
  "run_finished_utc": "2026-05-27T18:11:42Z",
  "host": {
    "os": "Linux 6.5.0-21-generic",
    "python": "3.13.1",
    "uv": "0.5.3",
    "cpu_count": 12,
    "ram_gb": 32
  },
  "libs": {
    "rasterio": "1.5.0",
    "geopandas": "1.1.3",
    "scikit-fmm": "2025.6.23",
    "scipy": "1.17.1",
    "numpy": "2.4.4",
    "pandas": "3.0.1",
    "pyproj": "3.7.2",
    "shapely": "2.1.2"
  },
  "engine_osrm": {
    "image": "osrm/osrm-backend",
    "tag": "v5.27.1",
    "digest": "sha256:abc123…",
    "algorithm": "mld"
  },
  "external_inputs": {
    "worldpop_pop": {
      "release": "R2025A",
      "vintage_year": 2023,
      "variant": "CN_100m_v1",
      "source": "https://hub.worldpop.org/"
    },
    "worldpop_school_age_1km": {
      "release": "R2025A_UA",
      "vintage_year": 2023,
      "files": [
        "clipped_global_f_05_2023_CN_1km_R2025A_UA_v1.tif",
        "clipped_global_m_05_2023_CN_1km_R2025A_UA_v1.tif"
        // …
      ],
      "sha256": {"<filename>": "<hash>"}
    },
    "map_friction_walking_2019": {
      "publisher": "Malaria Atlas Project",
      "release": "202001",
      "vintage_year": 2019,
      "file": "202001_Global_Walking_Only_Friction_Surface_2019.tif",
      "sha256": "<hash>"
    },
    "map_friction_motorized_2019": { /* idem */ },
    "bid_boundaries": {
      "version": "2026-03-01",
      "level0_sha256": "<hash>",
      "level1_sha256": "<hash>",
      "level2_sha256": "<hash>"
    },
    "idb_poverty": {
      "file": "lac-level-2.csv",
      "captured_utc": "2026-04-13T17:09:00Z",
      "sha256": "<hash>",
      "note": "Mixed vintage 2005-2020 per POVERTY_SOURCE column"
    },
    "meta_rwi": {
      "model_version": "Meta Data for Good RWI 2021",
      "countries": {
        "COL": {"dataset_id": "76f2a2ea-…", "created": "2021-04-08", "sha256": "<hash>"},
        "MEX": {"dataset_id": "…", "created": "…", "sha256": "<hash>"}
        // … 18 países …
      },
      "missing": ["BHS", "BRB", "CHL", "HTI", "PAN", "URY"]
    }
  },
  "countries": {
    "PAN": {
      "cima_year": 2024,
      "cima_raw_files": ["Marco muestral 19 DE JUNIO 2024.xlsx", "Anexo 2 - Georreferencia de Centros Educativos.xlsx"],
      "cima_derived_date": "2026-03-19",
      "cima_total_universe": 3660,
      "cima_sha256": "<hash of {ISO}_total_cima.csv>",
      "osm_extract_geofabrik_path": "central-america/panama",
      "osm_extract_timestamp": "2026-05-20T20:21:13Z",
      "osm_pbf_sha256": "<hash>",
      "osrm_graph_built_utc": "2026-05-20T15:24:56Z",
      "validation_tier": "standard",
      "final_match_level": "adm2",
      "n_schools_published": 3092,
      "n_schools_include_in_spatial_indicators_true": 3057,
      "worldpop_grid_cells": 19384,
      "n_cells_rwi": 0,
      "n_cells_poverty": 19384
    }
    // … 17 países (BHS/BRB/HTI fuera de OSRM; ARG/MEX/BRA pendientes) …
  },
  "outputs": {
    "accessibility_osrm_scl.csv": {
      "n_rows": 257891,
      "sha256": "<hash>",
      "schema_version": "scl_long_v1"
    },
    "accessibility_fmm_scl.csv": {
      "n_rows": 312044,
      "sha256": "<hash>",
      "schema_version": "scl_long_v1"
    }
  },
  "limitations_log_ref": "docs/accessibility_limitations_log.md@e5fd1c4"
}
```

### 5.3 Cómo se llena

- **`repo_git_sha`, `repo_dirty`** — capturados por la CLI con `git rev-parse HEAD` + `git diff --quiet`.
- **`libs`** — `uv pip freeze` filtrado por el paquete list.
- **`engine_osrm.digest`** — `docker image inspect osrm/osrm-backend:v5.27.1 --format '{{.RepoDigests}}'` después del pull (requiere QW-2 del diagnostic).
- **`osm_extract_timestamp`** — parsear el `[info] timestamp:` del log de `osrm-extract` y guardarlo (hoy se logea pero se pierde).
- **`*_sha256`** — `hashlib.sha256(open(p, 'rb').read()).hexdigest()` por archivo.
- **`cima_*`** — leer `_ministry_counts.json` por país.

### 5.4 Validación

Test en CI que carga el manifest, intenta resolver cada path declarado, calcula los hashes y los compara — fail si alguno no matchea.

---

## 6. Cierre — 3 cambios mínimos no negociables

Lista corta y forzosa. Sin estos 3, el entregable no pasa el filtro de provenance de BID.

### PROV-1 — Commitear `uv.lock` (sacar la línea del `.gitignore`)

- **Archivo:línea.** `.gitignore:9` — eliminar la línea `uv.lock`.
- **Qué cambiar.** Borrar línea 9, `git add uv.lock`, commit.
- **Por qué es no negociable.** Sin esto, **el repo no es reproducible.** `pyproject.toml:7-19` declara solo cotas inferiores (`numpy>=2.0`, `geopandas>=1.0`, `rasterio>=1.5.0`, `scipy>=1.17.1`, `scikit-fmm>=2025.6.23`). Una corrida en 2026-05 trae `numpy 2.4.4` y `scipy 1.17.1` (verificado en `uv.lock` local); una corrida en 2026-12 puede traer `numpy 2.6.x` y `scipy 1.20.x`. `rasterio` empaqueta GDAL/PROJ vendoradas que cambian con cada release menor. La spec del lead §7.5 lo pone explícitamente como mitigación. Está identificado también en `pipeline_diagnostic_2026-05-27.md` QW-7. **5 minutos de trabajo.**

### PROV-2 — Pinear OSRM con tag + digest, y persistir el OSM extract timestamp

- **Archivo:línea.** `09b_osrm_build_and_run.sh:24` — cambiar `IMG="osrm/osrm-backend"` por `IMG="osrm/osrm-backend@sha256:<digest>"` (con tag `v5.27.1` como referencia humana).
- **Qué cambiar.**
  1. Anclar el digest en el shell driver. Una línea.
  2. En `09b_osrm_build_and_run.sh:63-66`, después del `curl`, ejecutar `docker run --rm -v "$OSRM_DIR/_pbf:/data" osmium-tool osmium fileinfo --extended /data/${iso}.osm.pbf | grep -i timestamp` (o leer el timestamp del propio `osrm-extract`) y escribirlo a `data/transportation/osrm/{ISO}_{profile}/_provenance.json` con campos `pbf_url`, `pbf_sha256`, `pbf_osm_timestamp`, `built_utc`, `osrm_image_digest`.
  3. El step-10b lee esos JSON al construir el manifest de output.
- **Por qué es no negociable.** Sin el digest OSRM, la misma corrida con la misma data produce grafos no comparables entre fechas. Sin el timestamp del OSM extract persistido, **el output OSRM actual de 17 países usa 5 fechas distintas de Geofabrik y no hay forma de saber cuáles después de borrar los logs.** Está identificado parcialmente en `pipeline_diagnostic_2026-05-27.md` QW-2 (digest) pero no incluye el segundo punto (persistir el timestamp del extract), que es crítico para provenance independiente del log. **~2 horas de trabajo.**

### PROV-3 — Emitir manifest JSON por corrida + propagar `cima_year` y `pipeline_version` al CSV SCL

- **Archivos:línea.**
  - `10_accessibility_aggregate.py:200-211` y `10b_accessibility_aggregate_osrm.py:96-98` — `emit()` actualmente escribe `year: YEAR` con `YEAR=2023` constante (línea 90). Reemplazar por `data_year: <de la columna CIMA>` (heterogéneo por país, valores reales `2022-2024`) **y** agregar `pipeline_version: __version__` y `manifest_run_id: <run_id>`.
  - Nuevo módulo `pipeline/manifest.py` que:
    1. Genere el `run_id` al inicio del pipeline.
    2. Capture git SHA, libs, engine digest, file hashes de inputs.
    3. Cargue cada `_ministry_counts.json` por país y los embeba.
    4. Cargue cada `_provenance.json` de OSRM por país (de PROV-2).
    5. Escriba el manifest final en `results/accessibility/manifests/{date}_{run_id}_manifest.json` y referencie en cada fila del CSV vía `manifest_run_id`.
- **Por qué es no negociable.**
  1. **El año actual del CSV miente.** El indicador agrega CIMAs de 2022 (HTI) a 2024 (12 países) con `year=2023` constante, porque YEAR está hardcoded al año WorldPop. La columna `year` en el CIMA per-fila existe (verificado: PAN=2024, BRA=2023, ARG=2024, HTI=2022, etc.) y se descarta. Para BID, declarar "año del dato" igual a "año del denominador poblacional" es metodológicamente defendible pero **debe documentarse explícitamente o reportarse separado** (`cima_data_year` ≠ `pop_year` = 2023).
  2. **Sin manifest, las 7 fuentes externas (Geofabrik × país, MAP 2019, WorldPop 2023 R2025A, BID polygons, IDB poverty mixed-vintage, Meta RWI 2021, OSRM v5.27.x) son irrecuperables después del fact.** BID no puede contestar "¿qué versión de qué cosa generó esta cifra?" — y esa pregunta es **la primera** que harán al auditar el dato.
  3. La spec del lead §1.3 lo lista (`_manifest.json` en el path de outputs). No es nuevo; es exigible. `pipeline_diagnostic_2026-05-27.md` ST-8 lo lista también.
- **Esfuerzo.** 1.5 días (el módulo `manifest.py` + los 4 hooks en steps 06/07/09/09b/10/10b + plumbing del run_id). Más caro que PROV-1/PROV-2 pero **fundacional** — todo el resto del provenance se cuelga de acá.

---

## Anexo — Discrepancias entre CLAUDE.md y código observado

1. **Step 06 path WorldPop CN**. CLAUDE.md menciona "WorldPop 100m CN (no UN-adjusted)"; código en `06_pop_grid.py:73-78` carga rasters LAC con sufijo `R2025A_UA_v1.tif` (UN-Adjusted) para los buckets school-age. La decisión está documentada en `MEMORY.md → project_pop_grid_un_adjustment_decision.md`: 100m base = CN, 1km school-age = UA (porque UA solo existe a 1km). El CLAUDE.md actual no distingue; el código sí. **Documentar.**
2. **Año del indicador.** CLAUDE.md no menciona que `YEAR=2023` es constante. La fila del CSV dice "2023" para PAN aunque PAN tiene `year=2024` en su CIMA. Confunde al consumidor.
3. **`data/transportation/osrm/` vacío hoy.** El directorio que el shell driver usa (`09b_osrm_build_and_run.sh:23`) está vacío al snapshot — los grafos de las corridas previas fueron borrados o nunca se conservaron entre máquinas. Implica que cualquier re-corrida hoy bajará 22 PBFs de Geofabrik de nuevo, con timestamps **distintos** a los que produjeron el CSV actual. **Riesgo real.**
4. **22 países publicados, 23 operacionales** (CLAUDE.md líneas 4–9): consistente con `PIPELINE_ISOS` (23) y `ANALYSIS_ISOS` (22), HTI excluido. **OK.** Pero el output OSRM actual cubre 17 (`results/osrm/` único countries verificado: BLZ BOL CHL COL CRI DOM ECU GTM GUY HND JAM PAN PER PRY SLV SUR URY), faltan **ARG BRA MEX BHS BRB** — pendientes documentados en limitations log D1.

---

**Fin del documento.**
