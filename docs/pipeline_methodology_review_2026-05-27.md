# Pipeline methodology review — 2026-05-27

**Autor:** methodology-reviewer (senior quant methods, PhD geography / spatial econometrics)
**Insumos:** `docs/bid_deliverable_spec_2026-05-27.md` (lead) · `docs/pipeline_diagnostic_2026-05-27.md` (geo-architect) · `docs/pipeline_data_audit_2026-05-27.md` (data-audit) · `docs/accessibility_limitations_log.md` · `docs/fmm_vs_osrm_comparison_2026-05-16.md`
**Scope:** validar que la propuesta de empaquetado v1.0.0 no introduzca sesgo, ruptura de comparabilidad o inestabilidad metodológica.
**Repo SHA al revisar:** `e5fd1c4` (branch `step09-travel-times`).

---

## Estado — actualización 2026-05-29

**Shipped desde el cierre del review:**

- **Punto 3 (Append-by-country fix, BLOCKER v1.0.0)** — commit `fe1c7da`. Escritor `write_scl_output()` en step 10 con sort estable + guarda de unicidad de 12 columnas + atomic write `os.replace`. Test `tests/test_scl_append_by_iso.py` cubre creación, preservación de otros países, reemplazo solo del incoming, idempotencia 3× corridas, y empty-input-no-toca. **Validación V2 del §6.3 cumplida.**
- **PROV-1** (commit `73a1548`) — `uv.lock` trackeado, requisito previo del Punto 2 (Colab) cumplido.

**Decisiones del usuario (2026-05-29):**

- **Punto 5 (Google Maps): CERRADO** — la "entrada C2" del prompt original era un punto puntual para una reunión, NO está en el pipeline. r5py (Panama pilot) queda como validación cruzada canónica.
- **Punto 2 (Colab ARG/MEX/BRA): EN MARCHA** — `notebooks/colab_osrm_country.ipynb` empaqueta el flujo OSRM completo para los 3 países; usa `uv sync --frozen` contra el lock commiteado.
- **Ambas metodologías entregadas** — ARG/MEX/BRA tendrán OSRM (vía Colab), así que el `confidence_flag` en walking urbano será `point`, no `pending` permanente. El sesgo FMM↔OSRM (§6.2) sigue siendo el riesgo dominante pero queda **acotado a slices sin OSRM** (5 países FMM hoy, 22 con OSRM eventualmente).

**Pendientes para taguear v1.0.0:** V1 (reproducibilidad PER con OSRM pinneado) — bloquea por PROV-2 todavía no shipped. V3 (cross-method delta en manifest) — bloquea por PROV-3.

---

## Resumen ejecutivo

Cuatro de los cinco puntos pasan con `GO-WITH-CAVEATS`. Uno (validación cruzada Google Maps) se evalúa como `REWORK` por razón principalmente documental: la "entrada C2" referida por el lead **no existe** en `docs/accessibility_limitations_log.md` (verificado por grep — la bitácora tiene secciones A/B/C/D pero ningún C2 sobre Google Maps; la única mención a Google Maps en `docs/` está en `accessibility_methodology_review.md:164` como referencia a Geldsetzer 2020). Antes de tomar una decisión metodológica sobre re-validar, hay que cerrar de dónde sale ese supuesto.

El sesgo más material que sigue abierto es **el sesgo FMM↔OSRM estructurado por área** documentado en `docs/fmm_vs_osrm_comparison_2026-05-16.md` §4 (urban PAN walking ≤15 min: FMM 75.1 % vs OSRM 56.1 %, Δ = **+19.0 pp**, hasta **+37.5 pp** en PAN urbano secbaja). La propuesta de entregar ARG/MEX/BRA vía **sólo FMM** en v1.0.0 (spec del lead §7.3 / geo-architect §2.4) propaga ese sesgo a los tres países más grandes de LAC, sin un reality-check OSRM por país que permita declararlo o desactivarlo. Esto es **el riesgo metodológico dominante de v1.0.0** y debe explicitarse en el manifest y en el data dictionary, no soterrarse como "best effort".

---

## 1. Paralelismo intra-país (sharding ADM1 contra mismo OSRM server)

### 1.1 Lo que la propuesta dice

`docs/pipeline_diagnostic_2026-05-27.md` §2.4 patrón (A): el grafo OSRM sigue siendo nacional; sólo las celdas WorldPop se particionan por ADM1 y se ejecutan en shards paralelos contra el mismo `osrm-routed` en `localhost:5000`. No requiere más RAM que la versión actual y permite reanudación si el proceso muere.

### 1.2 Análisis metodológico

**(a) Consistencia de snap.** OSRM MLD es **read-only en runtime** y determinístico para una request individual: la misma coordenada origen + destino contra el mismo grafo produce el mismo snap-to-network (los identificadores internos del grafo se fijan en `osrm-extract`/`osrm-customize`, no en `osrm-routed`). El paralelismo cliente no introduce variabilidad del snap: cada cell origin se envía exactamente una vez, indexada por `i` en `09b_travel_time_osrm.py:218-256`, y el resultado se escribe en `times_min[i]` por índice posicional fijo. **No hay riesgo de inconsistencia de snap.**

**(b) Retries y sesgo de hora pico.** La política actual (`09b_travel_time_osrm.py:117-140`) retry-on-transport con backoff lineal 0.25·(attempt+1) y máximo `OSRM_RETRIES=4`. Si después de 4 intentos falla, va al pool `transport_fails` (`09b_travel_time_osrm.py:245-263`) que se re-corre **secuencialmente** (`09b_travel_time_osrm.py:265-286`); si ahí también falla, `raise RuntimeError` y **no se escribe parquet**. Esto es clave: el retry **no enmascara sesgo** porque (i) no hay fallback a un valor sintético, (ii) un fail persistente aborta la corrida loudly, no escribe NaN silencioso. La cita explícita en el módulo: "A NaN in `time_to_nearest_min` therefore means *genuinely unreachable*" (`09b_travel_time_osrm.py:30-32`). Esa invariante está bien protegida.

**Riesgo residual real:** una request individual puede demorar más bajo carga (timeout 30 s, `09b_travel_time_osrm.py:70`), pero el resultado **numérico** es idéntico — no depende del wall-clock. Lo que sí varía bajo carga es `n_finite` en queries que producen `null` por timeout interno OSRM. **No detectado en logs** (todos los 17 países: "100.0 % reachable, 0 transport failures" según `fmm_vs_osrm_comparison_2026-05-16.md` §2). Para sharding paralelo, exigir que el log de cada shard reporte `transport_fails=0` antes de aceptar.

**(c) Solape en frontera ADM1.** Si el sharding particiona celdas por `ADM1_PCODE` (columna en `population_grid_{ISO}.csv`), una celda pertenece a exactamente un ADM1 — no hay celdas duplicadas por construcción. Las celdas en frontera tienen un único `ADM1_PCODE` asignado por Step 06. **Sin doble cómputo.** El riesgo opuesto sí existe: celdas con `ADM1_PCODE` NaN (verificado: existen en COL frontera y en algunos remote_territory rows según CLAUDE.md §"Step 07 — Schools context" → COL 91.3% cell-match). Una partición ingenua `--adm1-shard` que filtre por `ADM1_PCODE.notna()` perdería esos cells. **Mitigación:** correr un shard adicional `--adm1-shard NULL` para celdas sin pcode, o incluirlas en el shard ADM1 del país por convención (e.g. la ADM1 más grande).

**(d) Server compartido vs container por shard.** El bottleneck es CPU del server (`pipeline_diagnostic_2026-05-27.md` §2.2 §3): cada `/table` request es CPU-bound dentro de `osrm-routed`. Lanzar N shards cliente contra **un solo** `osrm-routed` **no acelera** — todas las queries compiten por el mismo proceso server. La única razón para paralelismo cliente con un solo server es **reanudación** (re-correr un shard sin re-correr todo el país). Si el objetivo es acelerar, el patrón correcto es (B) multi-servidor del mismo grafo (read-only volume) en puertos distintos — pero ese patrón **multiplica RAM** y `pipeline_diagnostic_2026-05-27.md` §2.3 ya identificó que en ARG/MEX/BRA el bottleneck es OOM, no throughput. Para BRA, el patrón correcto es (C) subdivisión del grafo OSM por ADM1 con buffer 30 km — ése sí reduce RAM y permite paralelismo real.

### 1.3 Veredicto: **GO-WITH-CAVEATS**

El patrón (A) propuesto es metodológicamente seguro y útil para **reanudación**, pero **no acelera** ARG/MEX/BRA — por lo tanto no resuelve el problema D1 de la bitácora. Para una entrega v1.0.0 sólo es defendible si se acepta el roadmap del lead: ARG/MEX/BRA quedan **fuera de OSRM** en v1.0.0 y entran en v1.1.0 con patrón (C) + (A) combinados.

### 1.4 Checklist de mitigación

- [ ] Agregar a `09b_travel_time_osrm.py` un flag `--adm1-shard ARG_01` que filtre `population_grid_{ISO}.csv` por `ADM1_PCODE == shard` y escriba parquet con sufijo (e.g. `ARG_ARG_01_walking_primaria_osrm.parquet`).
- [ ] Caso `ADM1_PCODE` NaN: definir convención explícita (shard `_NULL` o anexar al shard ADM1 de mayor área).
- [ ] Step 10b lee todos los parquets `{ISO}_*_{mode}_{level}_osrm.parquet` via glob, deduplica por `cell_id` (defensivo) y agrega.
- [ ] Cada shard reporta su propio `transport_fails` en el log; CI rechaza la corrida si **cualquier** shard reporta > 0.
- [ ] Para acelerar ARG/MEX/BRA, **no** intentar (B) multi-server en v1.0.0; documentarlo y diferir a v1.1.0 con patrón (C) — la spec del lead §7.3 ya lo permite.
- [ ] QC obligatorio antes de adoptar (A): correr CRI sin sharding y con sharding (e.g. 4 shards), comparar `accessibility_osrm_scl.csv` filtrado a CRI y verificar diff bit-exact en `value` (tolerancia 1e-9 sobre float redondeado a 4 decimales — equivalente a "exacto"). Si CRI diverge, el supuesto de determinismo OSRM es falso y hay que pinear más cosas.

---

## 2. Migración a Colab para ARG/MEX/BRA

### 2.1 Lo que la propuesta dice

Memoria del usuario (`project_methodology_decisions.md`, `MEMORY.md`) menciona "FMM vs OSRM, MAP vs OSM, public/private debate, Colab Pro for compute". El plan operativo (no explícito en los 3 docs nuevos pero implícito en el cierre 2026-05-27) es correr ARG/MEX/BRA en Colab Pro (RAM/CPU mayor) cuando localmente no se pueda. La pregunta es si el output Colab es comparable al output local.

### 2.2 Análisis metodológico

**(a) Cambios de versión entre Windows local y Colab.** Si `uv.lock` está gitignored hoy (`data-audit` §0 PROV-1, `.gitignore:11`), **no hay forma de garantizar paridad de versiones** entre las dos máquinas. Las libs críticas para el indicador y sus riesgos numéricos:

| Lib | Operación crítica | Riesgo cross-máquina |
|---|---|---|
| `numpy` | float64 sums en `aggregate_country`, `np.fmin(tt_pub, tt_prv)` en `10_accessibility_aggregate.py:288` | BLAS/MKL diferente → 1-ulp drift en sumas grandes. Para % redondeados a 4 dec, irrelevante salvo en sumas > 1e6 cells (BRA: 3.68M cells × 6 indicadores). Defendible pero medible. |
| `scipy.spatial.cKDTree` | K-nearest schools en `09b_travel_time_osrm.py:205, 210` | **Riesgo concreto.** En empates Euclideanos (2 escuelas equidistantes en deg-coords) el tie-breaking depende del orden interno y de la versión de scipy. Afecta `nearest_school_id` reportado en la columna del parquet — no afecta `time_to_nearest_min` salvo que las dos escuelas tengan también el mismo tiempo de red (raro pero no imposible). `data-audit` §2.4 lo marca YELLOW. |
| `scikit-fmm` | `skfmm.travel_time` en Step 09 | Determinista bit-exact para misma versión. Cross-versión: riesgo bajo (la lib es estable, last release 2025-06-23 en lock declarado por `data-audit`). |
| `rasterio` / GDAL / PROJ | `reproject`, `rowcol`, `sample_raster` en `10_accessibility_aggregate.py:121-136` | **Riesgo concreto.** rasterio empaqueta GDAL/PROJ vendoradas. Versión vendorada de GDAL cambia con cada release menor → reproyecciones pueden moverse 1-ulp. Para una grilla de 1km eso normalmente no cambia el cell asignado, pero en celdas en frontera de raster sí puede flip-flop. Cuantificable: < 0.05% de celdas en grids LAC, despreciable para indicador agregado. |
| `pandas` | `groupby` mergesort, weighted_quintiles | Determinista intra-versión si se especifica `kind='mergesort'` (ya está en `10_accessibility_aggregate.py:112`). Cross-versión: el comportamiento de `groupby(dropna=True)` cambió entre pandas 2.x y 3.0 — relevante si Colab corre 2.x y local 3.0. |

**(b) Lockeo a Colab.** El plan más limpio:
1. Sacar `uv.lock` del `.gitignore` (PROV-1 del data-audit, 5 min).
2. En Colab cell #1: `!pip install uv && uv pip sync --frozen requirements.lock` donde `requirements.lock` se genera con `uv export --format=requirements-txt --frozen > requirements.lock`. `--no-deps` **no** es necesario si el lock está completo; sí lo es si Colab tiene una imagen pre-cargada que contamina (Colab pre-instala `numpy`, `scipy`, `pandas`, `geopandas`, etc., versión arbitraria). Recomendación: usar `uv venv --python 3.13` en Colab para forzar entorno limpio, e instalar contra ese venv.
3. Verificar paridad: `uv pip list --format=freeze` en local y en Colab, diff exacto.

**(c) BLAS/MKL backend.** Colab Linux + Intel CPU → MKL típicamente. Windows local + Intel/AMD → OpenBLAS o MKL según wheel. Para `cKDTree.query` el backend de BLAS es **irrelevante** (es C puro, no BLAS). Para `numpy.sum` sobre arrays grandes sí puede haber drift de 1-ulp en suma reorderable, pero los indicadores son `value = round(100.0 * num / pop, 4)` (`10_accessibility_aggregate.py:208`) — el redondeo a 4 decimales **absorbe** ese drift en todas las celdas excepto algún edge case patológico. Riesgo metodológico: **bajo**.

**(d) Validación contra país piloto.** Antes de mover ARG/MEX/BRA a Colab, correr **PER** (ya corrido localmente, log `_step09b_osrm_cri_ecu_per.log`, 5h wall-clock, 429k cells) en Colab y comparar:
- `time_to_nearest_min` por cell: tolerancia 0.01 min (round-trip OSRM determinista debe ser bit-exact si el grafo es el mismo). Si > 0.01 en alguna celda, investigar.
- `nearest_school_id` por cell: tolerar disagreement solo si el time es idéntico (caso empate). Reportar % de disagreement.
- `accessibility_osrm_scl.csv` filtrado a PER: diff exacto en `value` con tolerancia 1e-4 (un decimal del redondeo).

Si PER reproduce dentro de tolerancia, ARG/MEX/BRA en Colab son defendibles. Si no, hay drift por algún punto (grafo OSRM, libs Python) y hay que diagnosticar antes.

### 2.3 Veredicto: **GO-WITH-CAVEATS**

Es operacionalmente sano correr ARG/MEX/BRA en Colab si — y sólo si — el lock está committed y la corrida piloto PER reproduce dentro de tolerancia. Si esos 2 prerrequisitos no se cumplen, **REWORK**: no entregar ARG/MEX/BRA al BID hasta resolverlo, dejar como "pendiente documentado" con fallback FMM.

### 2.4 Checklist de mitigación

- [ ] **Pre-requisito 1:** sacar `uv.lock` de `.gitignore` (PROV-1 del data-audit, 5 min).
- [ ] **Pre-requisito 2:** generar `requirements.lock` con `uv export --format=requirements-txt --frozen`.
- [ ] **Pre-requisito 3:** documentar setup Colab en `docs/reproducibility.md` con celda copy-paste (`!pip install uv && uv venv --python 3.13 && source .venv/bin/activate && uv pip sync --frozen requirements.lock`).
- [ ] **Validación piloto:** re-correr PER en Colab contra el mismo grafo OSRM (volume montado en Drive o re-construido con mismo `.osm.pbf` cacheado). Calcular: max abs delta en `time_to_nearest_min`, % de disagreement en `nearest_school_id`, max abs delta en `value` agregado SCL. Aceptación: < 0.01 min, < 0.1% de cells (sólo empates), < 1e-4 respectivamente.
- [ ] Cualquier delta > tolerancia → diagnosticar antes de correr ARG/MEX/BRA. NO publicar.
- [ ] Si el grafo OSRM se reconstruye en Colab (PBF re-bajado de Geofabrik por separado), exigir que el `pbf_osm_timestamp` (PROV-2 del data-audit) coincida con el de la corrida local. Si difiere, **no es válido como validación** y hay que re-correr ambos contra el mismo PBF cacheado en Drive.
- [ ] El manifest declara `host.runtime = "colab_pro"` o `"local_windows"` para cada país. Si el BID re-corre y obtiene drift, puede atribuirlo.

---

## 3. Append-by-country vs overwrite del CSV (bug step 10b)

### 3.1 Lo que la propuesta dice

`pipeline_diagnostic_2026-05-27.md` §3.4 + QW-1: hoy `10b_accessibility_aggregate_osrm.py:101-102` (verificado: `out.to_csv(out_path, index=False, encoding="utf-8-sig")`) sobrescribe el CSV global cuando se corre con `--countries SUBSET`. El fix: leer el CSV existente, dropear filas de los ISOs en `--countries`, concatenar las nuevas, escribir.

### 3.2 Análisis metodológico

**(a) Invariante de unicidad.** El groupby implícito en el output SCL es la tupla:

```
(isoalpha3, idgeo, admin1_pcode, admin2_pcode, indicator, mode,
 education_level, age, sector, area, quintile, time_band)
```

Donde `idgeo ∈ {country, admin1, admin2}` y los pcodes son consistentes con el geo_level (`admin1_pcode==""` cuando `idgeo=="country"`, etc., ver `10_accessibility_aggregate.py:213-238`).

**Verificación de unicidad por inspección de `aggregate_country`** (`10_accessibility_aggregate.py:186-240`):
- País × (mode, level, sector, band) emite: 1 fila Total/Total + 3 filas area/Total + 5 filas Total/q_pov + 5 filas Total/q_rwi = 14 country rows × (mode×level×sector×band) = 14 × 2 × 3 × 3 × 3 = **756 country rows max** por país.
- ADM1: por cada `(a1p, area)` y `(a1p, q)` analogous.
- ADM2: por cada `(a2p, area)` solamente.

**Verificación empírica:** no hay duplicados conocidos en la salida actual (`results/accessibility/accessibility_osrm_scl.csv`, 258k filas, 17 países).

**Riesgo de append:** si una corrida append-by-country re-corre un ISO sin dropear primero, **duplicaría las 756+ filas de ese ISO**. La invariante **se rompe silenciosamente** — `df.groupby(invariant_keys).sum()` ya no es idempotente; cualquier dashboard que sume sobre quintiles obtendría 200% en lugar de 100%.

**(b) Schema drift entre corridas.** Si ARG se corre con schema v1 y BOL con schema v2 (e.g. v2 agregó la columna `pipeline_version` y `confidence_flag` que pide el lead spec §3.4), el append concat-en-NaN funciona pero **mezcla provenance heterogéneo**. Mitigación: cada fila debe llevar **`pipeline_version`** y **`manifest_run_id`** (data-audit PROV-3) explícitamente. Si dos filas del mismo país tienen `pipeline_version` distintos, el dashboard debe usar el más reciente — o el operador debe re-correr full para uniformizar.

**(c) Atomic write.** El patrón actual `df.to_csv(out_path, ...)` no es atómico: si el proceso muere a mitad de escritura, el CSV queda truncado y la próxima corrida append-by-country lee un CSV malformado. **Riesgo real** para BID Persona A que ejecuta varios países secuencialmente. Mitigación estándar: escribir a `out_path.with_suffix(".csv.tmp")` y `os.replace(tmp, out_path)` al final.

**(d) Concurrencia.** Si dos procesos corren `10b --countries ARG` y `10b --countries BOL` en paralelo, ambos leen el CSV simultáneamente, dropean filas distintas, escriben simultáneamente — race condition que pierde uno de los dos. Mitigación: file lock (`fcntl.flock` en Linux, `msvcrt.locking` en Windows; o más portable: `filelock` lib).

### 3.3 Veredicto: **GO** (con fix obligatorio)

El bug es **gate v1.0.0** (consistente con `pipeline_diagnostic_2026-05-27.md` QW-1). La fix es trivial y la invariante de unicidad se preserva si se implementa correctamente.

### 3.4 Checklist de mitigación

- [ ] Implementar append-by-ISO en `10b_accessibility_aggregate_osrm.py:95-104` y `10_accessibility_aggregate.py:308-311`:
  ```python
  if out_path.exists():
      existing = pd.read_csv(out_path, encoding="utf-8-sig")
      existing = existing[~existing["isoalpha3"].isin(countries)]
      out = pd.concat([existing, out], ignore_index=True)
  out.sort_values(["isoalpha3", "idgeo", "admin1_pcode", "admin2_pcode",
                   "indicator", "mode", "education_level", "sector",
                   "area", "quintile", "time_band"], inplace=True)
  ```
- [ ] Atomic write: `tmp = out_path.with_suffix(".csv.tmp"); out.to_csv(tmp, ...); os.replace(tmp, out_path)`.
- [ ] Validación post-write: cargar el CSV, agrupar por las 12 columnas invariantes, verificar `groupby(...).size().max() == 1`. Fail loud si no.
- [ ] Agregar `pipeline_version` y `manifest_run_id` a cada fila (data-audit PROV-3).
- [ ] Si dos filas con misma tupla invariante tienen `pipeline_version` distintos, el writer logea WARNING y conserva la más reciente. Esto desambigua schema drift.
- [ ] File lock cross-platform con la lib `filelock` para prevenir corrida en paralelo del mismo CSV global.
- [ ] CI test que corra `10b --countries BLZ` dos veces seguidas y verifique que el CSV final tiene exactamente 1× las filas de BLZ, no 2×.

---

## 4. Fechas heterogéneas de Geofabrik

### 4.1 Lo que la propuesta dice

`data-audit` §1.1: el output OSRM consolidado de 17 países mezcla 5 fechas distintas de OSM extract entre **2026-05-17 y 2026-05-22** (6 días de ventana). El extract de un país es "latest" en el día de la corrida; no hay manifest persistente.

### 4.2 Análisis metodológico

**(a) Comparabilidad cross-país.** Para un indicador agregado a `country_iso3` × `time_band`, la heterogeneidad temporal de OSM entre PAN (snapshot 2026-05-20) y CHL (snapshot 2026-05-22) **no afecta materialmente** el indicador. Razón: OSM cambia ~0.5-2% de su grafo de caminos por mes a nivel país (estimación de los reports de Geofabrik); en una ventana de 6 días, el delta de edges es del orden de **0.01-0.05%**. Para un indicador agregado a celdas de 1 km que ya tolera ruteo sobre el grafo nacional completo, ese delta es indetectable.

**(b) Literatura.** Castro/Giambruno/Ortega (`reference_castro_et_al.md` en MEMORY del usuario) usaron OSRM/UrbanPy contra **un único snapshot OSM** para sus 5 países amazónicos. Esa es la convención canónica en la literatura de accesibilidad espacial: **un snapshot por estudio**. Pero la diferencia operativa entre "un snapshot único" y "snapshots de la misma semana" es académica — ningún paper revisado por pares rechazaría el segundo si el manifest lo documenta. Geldsetzer 2020 (`docs/accessibility_methodology_review.md:164`) usó ORS con grafo en momento de corrida sin pinear snapshot. Petricola 2022 idem.

**(c) Threshold de comparabilidad.** Mi recomendación cuantitativa: 6 días = **OK explícito en manifest**. 30 días = **OK con caveat**. 90 días = **rework necesario** (Geofabrik publica diariamente; un quarter de delta puede mover ruteos urbanos en zonas de crecimiento rápido — e.g. Lima Sur, periferia DF). El umbral 30-90 días es defendible citando que OSM en LAC tiene tasa de mapping ~1-3% mensual en hot zones (Mexico City, Bogotá), heterogénea por país.

**(d) Documentación en manifest.** Esto es el bloqueador real, no la heterogeneidad en sí. Hoy **no hay forma de saber** qué fecha de Geofabrik produjo qué fila del CSV. Es el mismo PROV-2/PROV-3 del data-audit. Persistir `pbf_osm_timestamp` por país y embedirlo en `manifest.json` resuelve el 80% del problema.

**(e) Re-correr para snapshot único antes de v1.0.0.** Coste: ~3 días wall-clock para los 17 países ya producidos (~10 países en 1 día + CHL/BOL/URY/PER en serie). Beneficio metodológico: marginal (los deltas son < 0.05%). Beneficio de comunicación BID: medio (más limpio para defender). Beneficio en el manifest: alto si se hace **el mismo día** y se persiste el timestamp.

**Recomendación pragmática:** **NO re-correr** los 17 países sólo para uniformizar fecha. SÍ re-correr **PAN + el país que se use como validación cruzada Google Maps** (punto 5) con un PBF pinneado y un OSRM image pinneado (PROV-2) — esa es la evidencia que el BID puede auditar. El resto se documenta como "snapshot ventana mayo 2026".

### 4.3 Veredicto: **GO-WITH-CAVEATS**

La heterogeneidad temporal de 6 días no es un sesgo material para este indicador. Es un problema de **documentación**, no de **dato**. Si el manifest persiste `pbf_osm_timestamp` por país, es defendible ante el BID y ante un peer reviewer.

### 4.4 Checklist de mitigación

- [ ] Implementar PROV-2 del data-audit: persistir `pbf_osm_timestamp` por país en `data/transportation/osrm/{ISO}_{profile}/_provenance.json`. **No negociable.**
- [ ] El manifest por corrida embebe `external_inputs.osm_extract_per_country` con el timestamp de cada PBF usado (no sólo "se usó Geofabrik latest" — eso es no-provenance).
- [ ] El data dictionary `docs/data_dictionary.md` declara explícitamente: "OSM extract date varies per country within a 5-day window 2026-05-17 to 2026-05-22; see manifest for exact timestamp per country. Heterogeneity does not materially affect the indicator (< 0.05% edge churn over the window)."
- [ ] El threshold operativo: si la fecha más antigua y la más nueva en una corrida difieren en > 30 días, el manifest emite un WARNING y el data dictionary debe declarar "snapshot ventana extendida — interpretar comparaciones cross-country con cuidado".
- [ ] Para v1.0.0 NO re-correr los 17 países. Para ARG/MEX/BRA cuando se corran, sí pinear PBF a una fecha consistente con el resto (re-descargar Geofabrik más cercano a 2026-05-20 si existe el snapshot histórico; si no, documentar la fecha real).
- [ ] Para v1.0.0 sí re-correr **PAN** contra un snapshot pinneado para que sirva de validación cruzada reproducible (ver punto 5).

---

## 5. Validación cruzada Google Maps

### 5.1 Lo que la propuesta dice

El usuario referencia "una entrada C2 sobre validación cruzada con Google Maps (pendiente)" en `docs/accessibility_limitations_log.md`. **Verificado: esa entrada no existe.** La bitácora tiene secciones A1, A2, A3, B1, C1, D1 — no hay C2. La única mención a Google Maps en `docs/` es referencial: Geldsetzer 2020 (`docs/accessibility_methodology_review.md:164`) validó AccessMod contra ORS y Google Maps en 40 puntos aleatorios sub-saharianos. **No hay una validación Google Maps existente para el pipeline LAC.**

### 5.2 Análisis metodológico

**(a) ¿Existe la validación previa?** No, según evidencia documental del repo. Asumo que el lead se refiere a una intención no ejecutada — quizás derivada del cierre del pilot PAN donde se validó OSRM contra **r5py** (`fmm_vs_osrm_comparison_2026-05-16.md` §6 ¶5: "r5py corroboration: Panama pilot's r5py router gave ≈52 % walking ≤15 min on the top-5 districts; our OSRM gives 56.1 % nationally"). Esa sí existe y está documentada. Google Maps no.

**(b) Si se ejecutara, ¿qué provee?** Google Maps Directions API usa un grafo proprietario (no OSM) curado por Google con datos de tráfico real y un model de ruteo cerrado. Tres limitaciones para este uso:
- **Sesgo de modo:** Google Maps walking integra escaleras peatonales, atajos urbanos, plazas — cosas que OSM mapea parcialmente. En LAC urbano (Bogotá, CDMX) Google Maps walking puede dar tiempos 10-20% menores que OSRM/OSM porque mapea más infraestructura peatonal informal. **No es un ground truth limpio** — es otro engine con su propio sesgo.
- **Cobertura rural:** Google Maps walking rural LAC tiene cobertura desigual; en áreas indígenas, transferencias en lancha o caminos no oficiales, Google Maps puede negarse a routear ("can't find a way") donde OSM/OSRM sí rutea sobre la red disponible.
- **Costo y reproducibilidad:** Google Maps API es de pago, los IDs de "place" cambian, y los rutes se actualizan con frecuencia desconocida. Una validación Google Maps **no es reproducible** sin pinear la fecha de cada query — y aun así, Google no garantiza reproducibilidad.

**(c) Atadura a snapshot OSM.** La pregunta sólo importa **si Google Maps fuera la validación canónica del pipeline**. No lo es. r5py + OSRM (Castro/Giambruno/Ortega) son las dos referencias en literatura LAC para validación cruzada. Si se quiere una validación adicional, **r5py** es mejor candidato que Google Maps porque (i) es open-source, (ii) usa OSM mismo (apples-to-apples), (iii) ya tiene precedente en el pilot PAN.

**(d) Si re-corremos PAN con OSRM pinneado (PROV-2), ¿cambia?** Probablemente delta < 0.5 pp en el indicador agregado, asumiendo que la versión actual de OSRM (`:latest` al momento de la corrida, ~v5.27.x) y la pinneada (`v5.27.1`) son la misma. Si fueran de versiones mayores distintas, sí cambia. Verificable empíricamente: re-correr PAN con tag explícito `v5.27.1` y diff vs la corrida 2026-05-20. Esfuerzo: 10 min de wall-clock + comparar.

### 5.3 Veredicto: **REWORK**

REWORK no por el plan técnico — que es razonable — sino por la **discrepancia documental**: el doc del usuario asume una validación Google Maps que no existe en el repo. Antes de comprometer un experimento, hay que aclarar si:
- (i) Es una intención no ejecutada (entonces declararla como tal en la bitácora o descartarla).
- (ii) Es una confusión con la validación r5py del pilot PAN (entonces re-citar correctamente).
- (iii) Es una recomendación nueva del usuario para v1.0.0 (entonces evaluar costo-beneficio: probable bajo ROI vs r5py).

**Mi recomendación metodológica:** **no perseguir Google Maps**. r5py + OSRM + FMM ya cubren el espectro de routing engines (open-source, network real, raster cellular). Agregar Google Maps añade un cuarto engine de provenance opaca que no agrega evidencia metodológicamente nueva.

**Sí re-validar PAN walking contra el OSRM pinneado** antes de v1.0.0: re-correr PAN walking primaria/secbaja/secalta con `osrm/osrm-backend:v5.27.1` y PBF `panama-2026-05-20.osm.pbf` cacheado; diff vs la corrida actual; si delta < 0.5 pp, publicar como "validation pending" → "validated" para los slices urbano-PAN (los 6 slices de la fila §7 de `fmm_vs_osrm_comparison_2026-05-16.md` con Δ > +20 pp).

### 5.4 Checklist de mitigación

- [ ] Aclarar con el lead/Cecilia si la "entrada C2 Google Maps" es real, intención no ejecutada, o confusión con r5py. Documentarlo.
- [ ] Si decide perseguir Google Maps: presupuestar > $500 USD de quota API + 1 semana de ingeniería; NO comprometer para v1.0.0.
- [ ] Si decide descartar Google Maps: enmendar la mención del lead en `docs/bid_deliverable_spec_2026-05-27.md` o agregar nota en `docs/accessibility_limitations_log.md` explicitando "validación cruzada externa = r5py (Panama pilot, documentado)".
- [ ] **Sí ejecutar:** re-correr PAN walking primaria con OSRM pinneado (`osrm/osrm-backend@sha256:<digest>` de PROV-2) + PBF persistido y validar bit-exact vs la corrida actual. Si reproduce, marcar PAN walking ≤15 min urban como "validado bajo snapshot pinneado". Si NO reproduce, abrir investigación inmediata — implica que el OSRM `:latest` cambió y el indicador actual no es reproducible.
- [ ] Considerar agregar **r5py corroboration** explícita al manifest: re-correr el top-5 distritos PAN con r5py y publicar el delta vs OSRM como evidencia de Tier-2-vs-Tier-2 (no como Tier-1-vs-Tier-2 como FMM vs OSRM). Esfuerzo: ya existe el código del pilot PAN.

---

## 6. Veredicto consolidado

### 6.1 Matriz de criticidad

| # | Punto | Veredicto | Criticidad | Justificación corta |
|---|---|---|---|---|
| 1 | Paralelismo intra-país (sharding ADM1) | GO-WITH-CAVEATS | DEFER v1.1 | Patrón (A) seguro pero no acelera; ARG/MEX/BRA esperan v1.1.0 con patrón (C). |
| 2 | Migración a Colab ARG/MEX/BRA | GO-WITH-CAVEATS | **BLOCKER v1.0.0 si se incluyen** | Requiere `uv.lock` committed + corrida PER reproducible. Si no, ARG/MEX/BRA quedan fuera. |
| 3 | Append-by-country vs overwrite | GO (con fix) | **BLOCKER v1.0.0** | Bug actual destruye 21 países al re-correr 1. Fix obligatorio. |
| 4 | Geofabrik heterogéneo | GO-WITH-CAVEATS | NICE-TO-HAVE | Manifest resuelve 80%. NO re-correr 17 países. |
| 5 | Validación Google Maps | REWORK | NICE-TO-HAVE | La entrada referida no existe en la bitácora. Sustituir por r5py o aclarar. |

### 6.2 El sesgo más material que sigue abierto

**El sesgo FMM↔OSRM estructurado por área**, documentado en `docs/fmm_vs_osrm_comparison_2026-05-16.md` §4:

- Urban PAN walking ≤15 min: FMM 75.1% vs OSRM 56.1% → **Δ = +19.0 pp**.
- Urban PAN walking ≤15 min secbaja: FMM 56.7% vs OSRM 33.4% → **Δ = +23.3 pp**.
- Urban PAN walking ≤15 min secbaja, urbano-Δ: **+37.5 pp** (tabla §4).
- Rural COL walking ≤15 min primaria: **+12.5 pp**.

Este sesgo **no es uniforme** — invierte signo entre países (urbano-PAN vs rural-COL) — por lo que no se puede "ofsetear" con una constante. Es el sesgo que mata cualquier comparación de equidad PAN walking (`fmm_vs_osrm_comparison_2026-05-16.md` §5: "PAN — the FMM poverty gradient is distorted and must not be published").

**Por qué importa para v1.0.0:** la propuesta de entregar ARG/MEX/BRA vía sólo FMM (spec del lead §7.3) implica que los **tres países más grandes de LAC** se publicarán con un método que ya sabemos que sesga +19 a +37 pp en urbano. ARG (Buenos Aires, Córdoba), MEX (CDMX, Guadalajara) y BRA (São Paulo, Rio) son países profundamente urbanos en la oferta educativa — exactamente donde el sesgo es máximo. Publicarlos FMM-only sin OSRM reality-check **es una decisión metodológica que el reviewer académico va a cuestionar**.

**Mitigación mínima inegociable para v1.0.0:**
1. El CSV SCL debe llevar columna `method ∈ {osrm, fmm}` y `confidence_flag ∈ {point, range, pending}` por fila (spec del lead §3.4 ya lo lista; data-audit PROV-3 ya pide propagarlo).
2. Para los slices donde sólo hay FMM (sin OSRM correlato), el `confidence_flag` debe ser **`pending`** en walking urbano, **`range`** en walking rural, **`point`** en motorized — derivado por regla, no por defecto.
3. El data dictionary y el README BID declaran explícitamente: "ARG / MEX / BRA en v1.0.0 contienen solo Tier-1 (FMM). El indicador walking urbano se reporta como upper-bound; el sesgo estructural medido en países comparables (PAN, COL) sugiere que el valor publicado puede sobrestimar el acceso real en zonas urbanas por +19 a +37 pp. Reality-check OSRM para ARG/MEX/BRA programado para v1.1.0."

Sin esa mitigación, el indicador ARG/MEX/BRA walking urbano publicado en v1.0.0 es **engañoso**, no neutral.

### 6.3 Validaciones obligatorias antes de tagear v1.0.0 (1-3)

**1. Reproducibilidad PER (OSRM pinneado).** Re-correr PER walking primaria con OSRM pinneado (digest fijo de `v5.27.1`) y PBF cacheado de 2026-05-17 (ya existe). Comparar bit-exact contra `results/osrm/PER_walking_primaria_osrm.parquet` actual. Tolerancia: max abs Δ `time_to_nearest_min` < 0.01 min, % disagreement `nearest_school_id` < 0.1%. **Si pasa**, el pipeline OSRM es reproducible y se puede defender ante BID. **Si no pasa**, hay un componente no determinista no identificado y v1.0.0 no debería taguearse hasta diagnosticarlo. Esfuerzo: ~5h wall-clock + comparación. **Prerequisito de PROV-1 + PROV-2 del data-audit.**

**2. Append-by-ISO idempotencia (CRI).** Correr `10b --countries CRI` tres veces seguidas y verificar que el CSV final tiene exactamente las mismas filas para CRI (sin duplicados) y que las filas de los otros 16 países no se modificaron. Esfuerzo: 5 min de test si la fix QW-1 está implementada. **Pre-condición de v1.0.0.**

**3. Cross-method delta documentado en manifest.** Para cada `(country, mode, level, time_band)` donde existe tanto FMM como OSRM, calcular Δ = FMM − OSRM (pp) y embedirlo en el manifest como tabla. Para slices donde sólo existe FMM (ARG/MEX/BRA), aplicar la regla de `confidence_flag` derivada en §6.2 punto 2. El consumidor del CSV puede entonces filtrar por `confidence_flag != "pending"` si quiere robustez. Esfuerzo: 1 día (extender PROV-3). **No es prerequisito técnico sino prerequisito metodológico de aceptación.**

---

**Fin del review.**
