# Pipeline diagnostic — delta técnico para el entregable BID

**Fecha:** 2026-05-27
**Autor:** geo-architect (consultor)
**Insumo origen:** `docs/bid_deliverable_spec_2026-05-27.md` (spec del lead)
**Alcance:** traducir esa spec en cambios concretos sobre el código actual del pipeline. No se rehace metodología; se evalúa empaquetado, reproducibilidad, escala y bloqueadores reales.

---

## Estado — actualización 2026-05-29

**Shipped desde el cierre del diagnóstico:**

- **PROV-1 / QW-7** (commit `73a1548`) — `uv.lock` trackeado.
- **QW-1 / methodology-review punto 3** (commit `fe1c7da`) — `write_scl_output()` en step 10 (reusado por 10b): append-by-país, sort estable, guarda de unicidad 12-cols, atomic write. Test `tests/test_scl_append_by_iso.py` (5 cases). Verificado sobre OSRM real (17 países preservados, otros 16 byte-idénticos al respaldo).

**Decisiones de scope (Angela / Ceci, 2026-05-29):**

1. Ambas metodologías (FMM + OSRM) se entregan. ARG/MEX/BRA corren OSRM en Colab — ver `notebooks/colab_osrm_country.ipynb` + `notebooks/README.md`. No quedan FMM-only.
2. La "validación Google Maps" del prompt original era un punto puntual para una reunión, NO está en el pipeline. El REWORK del methodology-review §5 cierra: r5py (Panama pilot) es la validación cruzada canónica.
3. Receptor técnico final BID = ambos (División de Educación SCL + BID Data Group).

**FMM completo a 22 países (2026-05-29 turno 2):** la corrección inicial del FMM en este doc decía que faltaba clipping para 17 países; falso — la fricción clipped existía para los 23. Step 09 corrió para los 17 faltantes (~22 min, `_step09_fmm_17missing_then_step10.log`) y step 10 con el writer append-by-país (`fe1c7da`) integró todo: `accessibility_fmm_scl.csv` pasó de 375,696 filas / 5 países a **2,350,992 filas / 22 países**, 0 duplicados. HTI sigue fuera (no WorldPop).

---

## 0. Resumen ejecutivo (para que el resto del documento sea opcional)

- El pipeline **sustantivo** (Steps 01–10b) produce un CSV SCL correcto para 17 países hoy (`results/accessibility/accessibility_osrm_scl.csv`, 47 MB, 258k filas) y está bien testeado. No requiere refactor metodológico.
- El pipeline **operacional** (cómo se invoca, paquetiza, distribuye) está casi vacío respecto a lo que pide la spec del lead: no hay Dockerfile, no hay CLI, no hay LICENSE, no hay sample data, no hay CI, no hay schema versionado, y el agregador 10b tiene un foot-gun severo (sobrescritura silenciosa del CSV global cuando se corre con `--countries SUBSET`).
- El veredicto corto: **no requiere refactor, requiere empaquetado.** El refactor mínimo que sí necesita el código son ~3 cambios chicos (paths configurables, container names únicos, append-by-ISO en step 10b) que se hacen en < 1 semana y desbloquean toda la fase 1.
- ARG/MEX/BRA es problema de **RAM del container OSRM**, no de throughput del cliente: a 300 cells/s sostenidos las matrices terminan en horas, no en días, pero antes hay que poder levantar `osrm-routed` sin OOM. Eso es problema de infra del runner, no del código.

---

## 1. Gap entre el código actual y la forma del entregable que pide el lead

Por cada elemento de la spec §1, §3, §5, §6 del lead.

### 1.1 Imagen Docker principal (`accessibility-platform:v1.0.0`)

**Lead pide:** Python 3.13 + uv + deps geoespaciales + CLI + frontend opcional, ~3–5 GB.

**Estado actual:**
- No existe `Dockerfile` en la raíz. Verificado: `ls Dockerfile* docker-compose* .dockerignore` → todos faltan.
- `pyproject.toml` declara `requires-python = ">=3.13"` con deps geoespaciales correctas (`geopandas>=1.0`, `rasterio>=1.5.0`, `scikit-fmm`, `scipy`, `pyarrow`). Sirve como base.
- `uv.lock` existe pero está en `.gitignore:11` (`uv.lock`) — esto es un anti-patrón para reproducibilidad y la spec explícitamente lo señala (§7.5 mitigación: pinneo estricto + `--frozen`).

**Falta:**
- `docker/Dockerfile` multi-stage (build stage con `uv sync --frozen`, runtime stage minimal con sólo `.venv/` y `pipeline/`).
- Sacar `uv.lock` del gitignore y commitearlo (línea 11 de `.gitignore`).
- `docker/entrypoint.sh` para wrap del CLI.
- `.dockerignore` para excluir `data/`, `results/`, `archive/`, `.git/`, `tests/__pycache__/`.

**Esfuerzo:** 2 días (1 para Dockerfile + .dockerignore + commitear uv.lock; 1 para validar que la imagen corre `01_build_cima.py` end-to-end sin red sobre un volume montado).

### 1.2 Imagen OSRM separada (`accessibility-osrm:v1.0.0`)

**Lead pide:** imagen pinned a `osrm/osrm-backend:v5.27.x`, encapsula extract/partition/customize/routed.

**Estado actual:**
- `pipeline/09b_osrm_build_and_run.sh:25` usa `IMG="osrm/osrm-backend"` SIN TAG. Esto significa que cada build de hoy y dentro de 6 meses puede traer una imagen distinta — es un bug de reproducibilidad serio.
- No hay Dockerfile propio que la fija; se usa la imagen upstream directo desde DockerHub.

**Falta:**
- Decidir: ¿pinear con `osrm/osrm-backend:v5.27.1` directamente en el shell driver, o construir una imagen `accessibility-osrm` que lo wrappee? La spec del lead dice "imagen separada", pero por costo/beneficio basta con **pinear el tag y publicar el digest** (`osrm/osrm-backend@sha256:...`) en `docker-compose.yml` y en el driver.
- `docker-compose.yml` en la raíz que declare ambos services (`platform`, `osrm`) con volumes compartidos.

**Esfuerzo:** 0.5 día (pinear digest + compose) si no se construye una imagen wrapper. 2 días si se construye `Dockerfile.osrm` con healthcheck + entrypoint.

### 1.3 CLI uniforme (`accessibility-cli run/validate/diff/rebuild/smoke`)

**Lead pide:** Click o Typer, 5 subcomandos.

**Estado actual:**
- No existe `cli/`. Verificado.
- `pipeline/run_all.py:13-17` corre sólo Steps 01–03 vía `subprocess.run` — no es el contrato CLI que la spec necesita.
- Cada script de pipeline tiene su propio `argparse` con flags inconsistentes:
  - `09_travel_time_fmm.py:398` → `--countries --modes --levels --sectors --overwrite --log-level`
  - `09b_travel_time_osrm.py:301` → `--country` (singular) `--mode --level --k --max-workers --port --overwrite --log-level`
  - `10_accessibility_aggregate.py:317` → `--countries --log-level`
  - `10b_accessibility_aggregate_osrm.py:108` → `--countries --log-level`
  - `06_pop_grid.py` → `--countries`
- El driver `09b_osrm_build_and_run.sh` toma posicionales (`bash ... CRI ECU PER`), no flags.

**Falta:**
- Carpeta `cli/` con `main.py` (Typer recomendado: type hints + sub-commands).
- `cli/commands/run.py` → orchestrador que internamente importa los scripts numerados vía `importlib.util.spec_from_file_location` (mismo patrón que ya usa `10b:_load_step10` líneas 48–55).
- `cli/commands/validate.py` → checklist de inputs montados.
- `cli/commands/diff.py` → compara dos CSVs SCL.
- `cli/commands/rebuild.py` → invoca steps por nombre (`--from step01`).
- `cli/commands/smoke.py` → corre BLZ end-to-end.
- Estandarizar `--country` (singular) vs `--countries` en todos los scripts. Recomendado: `--countries` plural en todos lados, accept 1+ valores.

**Esfuerzo:** 1 semana. La parte gruesa es decidir si `cli/` re-implementa lógica (mala idea — duplica código) o sólo orquesta los scripts existentes vía subprocess/importlib (recomendado).

### 1.4 Parametrización país/año vía env vars

**Lead pide (§9.3, §3.3):** env vars `IDB_ACCESS_DATA_ROOT`, `IDB_ACCESS_RESULTS_ROOT`. Año configurable por país.

**Estado actual:**
- Todos los scripts hardcodean `PROJECT_ROOT = Path(__file__).resolve().parent.parent`. Verificado:
  - `09_travel_time_fmm.py:61`, `09b_travel_time_osrm.py:63`, `10_accessibility_aggregate.py:61`, `10b_accessibility_aggregate_osrm.py:40`, `06_pop_grid.py:60`, `05_base_k_12_clean.py:37`, `07_schools_context.py:26`, `07_friction_clip.py:71`, `03_coverage_assessment.py:36`.
- El año está hardcodeado:
  - `09_travel_time_fmm.py:73-76` → `"walking_2019"`, `"motorized_2019"` (suffix de friction MAP).
  - `10_accessibility_aggregate.py:90` → `YEAR = 2023`.
  - `06_pop_grid.py:73-78` → `clipped_global_*_2023_CN_1km_R2025A_UA_v1.tif` literal.
- El shell driver hardcodea el puerto OSRM (`09b_osrm_build_and_run.sh:25` `PORT=5000`).

**Falta:**
- Helper común `pipeline/_paths.py` que lea `os.environ.get("IDB_ACCESS_DATA_ROOT", PROJECT_ROOT / "data")` y `IDB_ACCESS_RESULTS_ROOT`. Todos los scripts importan de ahí.
- Promote year a CLI arg con default = año vigente de la spec.
- `09b_osrm_build_and_run.sh` debe aceptar `OSRM_PORT` env var con default 5000.

**Esfuerzo:** 1.5 día (los 9 scripts comparten el mismo patrón, sed-and-test).

### 1.5 Mount points `/data` y `/results`

**Lead pide (§3.1):** containers reciben volúmenes mounted en `/data/` y `/results/`.

**Estado actual:**
- El shell driver ya monta `/data` adentro del container OSRM (`09b_osrm_build_and_run.sh:67-72` `-v "$dir:/data"`). Eso es para el container OSRM, no para el container Python.
- El container Python no existe, así que el mount no está cableado.

**Falta:**
- `docker-compose.yml`:
  ```yaml
  services:
    platform:
      image: ghcr.io/IDB-EDU/accessibility-platform:v1.0.0
      volumes:
        - ./data:/data:ro
        - ./results:/results:rw
      environment:
        - IDB_ACCESS_DATA_ROOT=/data
        - IDB_ACCESS_RESULTS_ROOT=/results
    osrm:
      image: osrm/osrm-backend@sha256:... # pinned digest
      volumes:
        - ./data/transportation/osrm:/data:rw
      ports:
        - "5000:5000"
  ```
- El cliente OSRM `09b_travel_time_osrm.py:68` apunta a `localhost:5000`. Dentro del compose network, el hostname pasa a ser `osrm` no `localhost`. Hay que parametrizar.

**Esfuerzo:** 0.5 día (compose + agregar `OSRM_HOST` env var al cliente Python).

### 1.6 Output schema SCL versionado

**Lead pide (§3.4, §7.6):** schema declarado en un solo lugar, versión `pipeline_version=v1.0.0` en cada fila, validable con pandera/pydantic.

**Estado actual:**
- El schema vive disperso:
  - `10_accessibility_aggregate.py:200-211` (FMM) y `10b_accessibility_aggregate_osrm.py:96-98` (OSRM provenance overwrite).
  - Columnas emitidas: `isoalpha3, idgeo, admin1_pcode, admin1_name, admin2_pcode, admin2_name, indicator, mode, education_level, age, sector, area, quintile, time_band, value, population_base, year, method, source`.
- **No hay columna `pipeline_version`.** Verificado en el dict de `emit()` en 10_accessibility_aggregate.py:200-211.
- **No hay validación post-write.** Si una columna desaparece en un refactor, nadie se entera hasta que falla un dashboard.
- El schema 10b difiere del spec del lead en nombres: lead pide `country_iso3` (spec §3.4), código emite `isoalpha3`; lead pide `band`, código emite `time_band`; lead pide `n_pop_denominator/n_schools_in_scope`, código emite `population_base` y no incluye `n_schools_in_scope` ni `confidence_flag`.

**Falta:**
- Mover el schema a `pipeline/constants.py` o `cli/schemas.py` como un `pandera.DataFrameSchema` o equivalente Pydantic.
- Agregar `pipeline_version` (lectura de `__version__` del paquete) y `confidence_flag` al emit.
- Decidir si renombramos columnas para alinear con la spec del lead (breaking change para los dashboards downstream — coordinar) o si la spec del lead se ajusta a las columnas actuales. **Discordancia con el lead:** los dashboards ya consumen el schema actual; un rename es ruido sin beneficio claro. Recomendación: pedirle al lead que ajuste la spec a las columnas reales (`isoalpha3`, `time_band`, `population_base`) y agregar sólo las columnas nuevas opcionales (`pipeline_version`, `confidence_flag`, `n_schools_in_scope`).
- Validador en CI que cargue el CSV de output del smoke test y verifique columnas + dtypes + dominios.

**Esfuerzo:** 1 día schema central + validador. +1 día si se cierran las discrepancias de nombres con el lead.

### 1.7 Sample data BLZ y smoke test < 5 min

**Lead pide (§6 ítem #3, §6 ítem #12):** `examples/sample_BLZ/` + `accessibility-cli smoke` que corre end-to-end < 5 min.

**Estado actual:**
- No existe `examples/`. Verificado.
- BLZ ya está implementado y corrió: `results/osrm/BLZ_*_osrm.parquet` × 6 archivos, ~410 KB cada uno.
- Tiempo real de BLZ medido en `results/_step09b_osrm_batch1_9countries.log`:
  - Build foot + car: 15:24:56 → 15:25:15 = **19s**
  - 6 matrices walking + motorized: 15:25:17 → 15:28:24 = **3:07 min**
  - Total país: **~3:30 min**. Smoke test < 5 min es alcanzable.

**Falta:**
- `examples/sample_BLZ/` con:
  - `data/schools/AR/BLZ/raw/` (raw ministerial BLZ — públicos según spec §7.4)
  - `data/schools/AR/BLZ/processed/BLZ_total_cima.csv`
  - `data/bounderys/LAC/level{0,1,2}/lac-level-*-BLZ-only.shp`
  - `data/population/WorldPop/processed/population_grid_BLZ.csv`
  - `data/transportation/surface_friction/clipped/BLZ/BLZ_{walking,motorized}_2019.tif`
  - `expected_hashes.json` con SHA-256 de outputs esperados.
- `tests/test_smoke_blz.py` que dispara el CLI smoke y compara hashes.

**Esfuerzo:** 2 días. La parte cara es recortar el lac-level-0/1/2 .shp a BLZ-only y el friction global a BLZ; ya existe `07_friction_clip.py` que lo hace.

### 1.8 Checklist de impecabilidad (spec §6)

Estado actual del checklist completo del lead:

| # | Ítem | Estado |
|---|---|---|
| 1 | `LICENSE` Apache-2.0 | ✗ ausente |
| 2 | README ES + EN | ✗ falta (sólo README.md inglés según `pyproject.toml:5`) |
| 3 | Smoke test < 5 min | ✗ ausente (datos sí; CLI no) |
| 4 | `docs/data_dictionary.md` | ✗ ausente |
| 5 | `docs/architecture.md` Mermaid | ✗ ausente |
| 6 | `docs/add_new_country.md` | ✗ ausente (info dispersa en CLAUDE.md) |
| 7 | `docs/update_year.md` | ✗ ausente |
| 8 | `docs/reproducibility.md` + expected_hashes.json | ✗ ausente; `uv.lock` está gitignored |
| 9 | Security scan (trivy + pip-audit) | ✗ ausente |
| 10 | `docs/data_privacy.md` | ✗ ausente |
| 11 | `docs/support.md` | ✗ ausente |
| 12 | Sample data en `examples/` | ✗ ausente |
| 13 | CI verde | ✗ ausente — verificado: `ls .github/workflows/` no existe |
| 14 | Tag `v1.0.0` + release notes | ✗ ausente |
| 15 | Bitácora de limitaciones vinculada al README | ✓ existe `docs/accessibility_limitations_log.md`; ✗ no linkeada desde README |

**Total: 14 de 15 ítems faltan o están incompletos.** El único que ya existe (limitations log) sólo necesita un link en README.

---

## 2. Cuellos de botella de escala (números reales de los logs)

### 2.1 Throughput observado por país

Calculado de `results/_step09b_osrm_*.log` (los logs ya documentan rate=N/s cada 5%; muestreo de las medias estables, no del transitorio inicial).

| País | WorldPop cells | Schools elig. (primaria) | Rate sostenido walking (cells/s) | Tiempo 1 matriz | Build foot+car | Total país (6 matrices + build) |
|---|---:|---:|---:|---|---|---|
| BLZ | 8,767 | 207 | ~290 | 43 s | 19 s | **~3:30 min** |
| SUR | 10,546 | — | ~160 | 63 s | 32 s | ~5 min |
| JAM | 11,524 | — | ~380 | 33 s | 43 s | ~5 min |
| GUY | 13,020 | — | ~260 | 50 s | 23 s | ~6 min |
| SLV | 22,963 | — | ~500 | 46 s | 58 s | ~8 min |
| CRI | 41,933 | 3,438 | ~625 | 67 s | 56 s | ~13 min |
| DOM | 42,925 | — | ~456 | 94 s | 2:33 min | ~17 min |
| HND | 88,962 | 9,733 | ~250 (degradado) | ~3 min | 2:00 min | ~38 min |
| GTM | 93,373 | 14,467 | ~400 | ~2 min | 3:10 min | ~30 min |
| URY | 106,062 | 1,787 | ~160 (anómalo en secalta walking, 27 min) | ~5 min | 1:17 min | **~62 min** |
| ECU | 150,583 | 14,597 | ~400 | ~6 min | 4:29 min | ~40 min |
| PRY | 156,466 | 5,850 | ~500 | ~5 min | 3:02 min | ~42 min |
| CHL | 272,192 | 7,776 | ~300 | ~15 min | (cache hit) | ~80 min |
| BOL | 296,082 | — | ~360 | ~13 min | — | ~80 min |
| PER | 429,029 | 36,229 | ~250 | 18:30 min/walk | 7:14 min | **~5 h** |

PER walking/primaria: 14:51:21 → 15:09:52 = 18:31 min para 429k cells = 386 c/s. PER walking/secbaja: 15:09:57 → 15:38:26 = 28:29 min = 251 c/s. La caída entre primaria y secbaja **con menos schools (14,477 vs 36,229)** es contraintuitiva y sugiere que el costo no escala lineal con K=50 nearest schools, sino con la geografía de las queries (rutas más largas en zonas remotas con menos cobertura escolar son más caras de routear).

### 2.2 Diagnóstico CPU/HTTP/RAM

**Dónde se va el tiempo, según los logs y el código:**

1. **OSRM `osrm-extract` + `osrm-partition` + `osrm-customize`** (build del grafo): único costo CPU-bound puro. Numbers from PER: 14:44:04 → 14:51:19 = **7:15 min para foot + car**. CHL/BOL no aparece porque el grafo ya estaba cacheado (`09b_osrm_build_and_run.sh:52` el skip por `network.osrm.mldgr`). Esto es un one-shot por país.

2. **KDTree de schools** (`09b_travel_time_osrm.py:205` `cKDTree(...)`): sub-segundo incluso para PER (36k schools). No es bottleneck.

3. **OSRM `/table` round-trip** (`09b_travel_time_osrm.py:154-165`): **el bottleneck dominante.** Confirmado por el rate: a 300 c/s con `max_workers=12` y K=50 destinations por request, el server hace ~15,000 pairs/s. Es HTTP-bound del lado del cliente y CPU-bound del lado del server (cada `/table` con 51 coords = 51 snap-to-network + MLD multi-target routing). El cliente ya tiene la optimización correcta: 1 conexión keep-alive por thread (`09b_travel_time_osrm.py:97-114` `_get_conn`), lo que resolvió el bug de 38.5% (ephemeral ports exhausted).

4. **Agregación SCL en step 10b** (`10b_accessibility_aggregate_osrm.py:58-104`): in-memory groupby sobre el grid, < 1 min por país incluso para CHL (272k cells × 2 modos × 3 niveles × 3 bandas = 4.9M filas teóricas, pero el output total 17 países es 258k filas → mucho colapsado por sumas).

**Costo total ladder:**
- Build grafo: **3–8 min por país** (one-shot, cacheado).
- Travel-time matrices: **proporcional a WorldPop cells × 6 modos/niveles**.
- Aggregate: **sub-minuto por país**.

**Veredicto:** OSRM `/table` es el cuello de botella. Está bien optimizado del lado del cliente (keep-alive). Acelerarlo más requiere o (a) más threads (limitado por el server de un solo proceso, en mi laptop testing >16 workers no mejora), o (b) **paralelismo a nivel de server**: levantar N instancias del mismo grafo en puertos distintos detrás de un round-robin, o (c) `osrm-routed --threads K` con K > 1 (default es N=hilos del host).

### 2.3 Proyección as-is para ARG / MEX / BRA

WorldPop cells verificados directamente del filesystem:
- ARG: **989,318** cells (`population_grid_ARG.csv` wc -l → 989,318 + 1 header)
- MEX: **1,066,269**
- BRA: **3,676,291**

Asumiendo rate sostenido de 300 c/s (conservador, similar a CHL/BOL):

| País | Cells | Tiempo 1 matriz | Total 6 matrices | + Build foot+car |
|---|---:|---|---|---|
| ARG | 989k | 55 min | **5:30 h** | ~12 min |
| MEX | 1.07M | 59 min | **5:55 h** | ~12 min |
| BRA | 3.68M | 3:24 h | **20:24 h** | ~25 min |

A 600 c/s (mejor caso, similar a SLV/CRI):
- ARG: 2:45 h total
- MEX: 2:58 h total
- BRA: 10:12 h total

**Pero el problema real reportado en `project_session_close_2026-05-27.md` no es throughput — es OOM al levantar `osrm-routed` en una laptop de 16 GB.** El grafo MLD de BRA pesa ~5–10 GB RAM (estimado a partir de los logs disponibles: HND `RAM: peak bytes used: 490717184` ≈ 468 MB para 2.8M edges; BRA con ~50M edges escalaría a ~8 GB sólo para la build; runtime de `osrm-routed` suele ser 2–3x).

### 2.4 Patrón de paralelismo recomendado para ARG/MEX/BRA

Hay 3 opciones, ordenadas por costo-beneficio:

**(A) Sharding por ADM1 — cliente-side, mismo grafo nacional.** El grafo OSRM sigue siendo nacional (no se divide la red — eso rompe ruteo cross-frontera dentro del país). Sólo se particionan las **celdas WorldPop** por ADM1 y se corre `09b_travel_time_osrm.py` por shard, escribiendo parquets parciales `{ISO}_{ADM1}_{mode}_{level}_osrm.parquet`. Step 10b los concatena. Esto NO acelera (mismo servidor OSRM), pero **permite reanudar** si el proceso muere a mitad de camino — clave en BRA 20h.

- Esfuerzo: 1 día (agregar `--adm1-shard ARG_01` flag a 09b, modificar 10b para auto-discovery de shards).
- No requiere más RAM que la versión actual.

**(B) Multi-servidor OSRM detrás de un round-robin.** Levantar N instancias de `osrm-routed` en puertos 5000..500N, todos sirviendo el **mismo grafo via volume read-only** (esto OSRM lo soporta porque MLD es read-only en runtime). Cliente Python hace round-robin entre puertos.

- Esfuerzo: 2 días (modificar driver shell + agregar pool de connections en el cliente).
- **NO ayuda con OOM** — peor, multiplica la RAM por N (cada `osrm-routed` carga el grafo). Sólo sirve si la máquina tiene RAM de sobra y el bottleneck es CPU del server.

**(C) Subdivisión real del grafo OSRM por ADM1 con OSM clipping.** Recortar el `.osm.pbf` a un buffer de la ADM1 (e.g. `osmium extract -p adm1.geojson`) y construir un grafo por shard. Esto **sí reduce RAM** (cada grafo es 1/24 del nacional para ARG con 24 provincias). Costo: las rutas que cruzan ADM1 quedan rotas o subóptimas — para cells **cerca de la frontera ADM1** el resultado es incorrecto. Mitigación: buffer de 30 km al recortar (`osmium extract --strategy complete_ways -b ...`), recalcular distance-to-school sólo dentro de la ADM1 + buffer, y rechazar destinos fuera.

- Esfuerzo: 1 semana. Es el camino verdadero para BRA.
- Reduce RAM proporcionalmente al tamaño del shard.
- Riesgo metodológico: requiere QC contra una corrida nacional en un país más chico (CRI con/sin shard) para confirmar < 1% de divergencia.

**Recomendación:** para BID v1.0.0, entregar ARG/MEX/BRA vía FMM (ya funciona, ver `results/accessibility/accessibility_fmm_scl.csv` 67 MB) con OSRM marcado como pendiente. Para v1.1.0 implementar (A) + (C) como dos PRs separados. La spec del lead (§7.3) ya documenta esto explícitamente.

---

## 3. Bloqueadores de replicabilidad

### 3.1 Hardcodes

| Archivo:línea | Hardcode | Por qué importa |
|---|---|---|
| `09b_osrm_build_and_run.sh:24` | `IMG="osrm/osrm-backend"` (sin tag/digest) | Reproducibilidad rota. Mismo comando en 6 meses puede traer una imagen distinta. |
| `09b_osrm_build_and_run.sh:25` | `PORT=5000` | Si BID corre dos países en paralelo se pisan. |
| `09b_osrm_build_and_run.sh:78` | `name="osrm-run"` (container name único global) | Mismo problema: `docker rm -f osrm-run` borra el de otra corrida. |
| `09b_osrm_build_and_run.sh:31-45` | `GEOFABRIK_PATH` hardcoded — BHS/BRB no listados | BID no puede agregar país sin editar el shell. |
| `09b_osrm_build_and_run.sh:67-72` | `MSYS_NO_PATHCONV=1` en cada `docker run` | Asume Git Bash en Windows. En Linux/macOS no hace daño pero ensucia. |
| `09b_travel_time_osrm.py:68-69` | `OSRM_HOST = "localhost"`, `OSRM_PORT = 5000` | El puerto sí se puede pasar por `--port`. El host no — rompe el caso docker-compose donde el host es `osrm`. |
| `09b_travel_time_osrm.py:307` | `--max-workers default=12` | Sin justificación documentada; en máquinas con 4 cores degrada por context-switching. |
| `09_travel_time_fmm.py:73-76` | `"walking_2019"`, `"motorized_2019"` literal | Año del friction MAP. Si el lead pide "actualizar año" eso no cambia. |
| `10_accessibility_aggregate.py:90` | `YEAR = 2023` literal | Año del WorldPop. Hardcoded en el output. |
| `10_accessibility_aggregate.py:91-93` | `METHOD = "FMM"` y `SOURCE` literal | OK como provenance; se vuelve a sobrescribir en 10b. |
| `06_pop_grid.py:73-78` | `clipped_global_*_2023_CN_1km_R2025A_UA_v1.tif` literal | Año WorldPop encadenado al nombre de archivo. |
| Todos los scripts | `PROJECT_ROOT = Path(__file__).resolve().parent.parent` | Asume layout fijo. Imposible montar `/data` y `/results` en distintos volumes. |

### 3.2 Supuestos de filesystem

- **Layout `data/schools/AR/{ISO}/...`** — el "AR" intermedio es legado del esquema original; en `pyproject.toml` no aparece justificación. La spec del lead lo replica (§3.1) pero un BID que no conoce el contexto pensará que es Argentina. Vale la pena documentar o renombrar a `data/schools/{ISO}/`.
- **`data/transportation/osrm/{ISO}_{profile}/`** está en gitignore (heredado de `.gitignore:30 data/`). Los grafos pesan: PER probablemente 2–5 GB cada uno (`osrm-extract` + `.mldgr` + `.cnbg` + `.partition` + ~10 archivos más). Si BID no los tiene cacheados, **cada corrida nueva re-bajaría Geofabrik y reconstruiría todos los grafos** — ~3 h adicionales para los 17 países sólo en build.
- **`results/osrm/*.parquet`** existe en disco (`ls results/osrm/ | wc -l` = 104 archivos, suma 564 MB) pero **NO está en `.gitignore` explícitamente** — sólo cae bajo `results/` no listado. Verificado: `.gitignore` sólo excluye `results/geocode_cache.json` y `results/qc_coordinate_report.csv`. Esto es accidental pero significa que `git status` en otra máquina los marca como "Untracked" y el dev podría borrarlos sin querer.
- **Junction-links Windows-only** mencionados en CLAUDE.md (working dirs adicionales: `accessibility_platform-dashboard-assets/pipeline/dashboard_assets`, etc.). El pipeline core no parece depender de ellos, pero los workflows de Angela sí. Habría que limpiar antes de entregar.

### 3.3 Dependencias implícitas

- **Docker Desktop corriendo** — el shell driver falla con `Cannot connect to the Docker daemon` si no está prendido. No hay precheck.
- **Imagen `osrm/osrm-backend` cacheada** — primera corrida pulla ~1 GB de DockerHub. Si BID está en una VM con firewall que bloquea DockerHub, falla mudo.
- **Geofabrik disponible** — `09b_osrm_build_and_run.sh:63` `curl -fL` a `download.geofabrik.de`. Servicio público gratuito pero con rate limit. **Bloqueado por firewall BID es escenario real** (spec §7.1 lo prevé). No hay fallback.
- **Fechas de extracts Geofabrik inconsistentes entre países** — cada `.osm.pbf` se descarga "latest" en el momento de la corrida. Si BLZ se corrió el 2026-05-21 y BRA se corre el 2026-06-15, el grafo de BRA tiene 3 semanas más de mapping. **No hay manifest de qué fecha de extract se usó para qué país.** Esto es problema para reproducibilidad bit-exact.
- **WorldPop ya descargado** — Step 06 asume `data/population/WorldPop/{ISO}/{iso}_pop_2023_CN_100m_R2025A_v1.tif` existe. No hay paso de download automatizado; tiene que estar pre-bajado.
- **MAP friction global** — friction 2019 raster es ~5 GB. Tampoco hay download automatizado.

### 3.4 Estado mutable global

- **`10b_accessibility_aggregate_osrm.py:101-102`**: bug crítico de overwriting.
  ```python
  out_path = OUT_DIR / "accessibility_osrm_scl.csv"
  out.to_csv(out_path, index=False, encoding="utf-8-sig")
  ```
  Si BID corre `uv run python pipeline/10b_accessibility_aggregate_osrm.py --countries PAN`, el CSV global se **sobrescribe con sólo PAN**. Los 17 países previos se pierden. Verificado leyendo `run()` líneas 58–104: no hay merge con el CSV existente. Mismo bug en `10_accessibility_aggregate.py:310-311` (FMM).

  Por qué importa: BID-side el use case típico es "actualicé ARG, reagregar sólo ARG". Hoy ese flujo borra los otros 21 países. **Esto es gate v1.0.0.**

- **`results/osrm/{ISO}_{mode}_{level}_osrm.parquet`** es upsert por país (cada `09b` sobrescribe su parquet — está OK porque `--overwrite` flag está documentado). No es el problema, el problema está en step 10b.

- **`results/_step09b_osrm_*.log`** son creados con nombres ad-hoc (`_step09b_osrm_batch1b_HND_GTM_URY.log`, `_step09b_osrm_CHL_BOL.log`). El driver shell no los emite; Angela los redirige manualmente desde la consola. Para BID, el log debería ir a `results/logs/run_{ISO}_{timestamp}.log` automáticamente.

---

## 4. Lista priorizada de cambios

### 4.1 Quick wins (días, no semanas) — bloquean v1.0.0

| # | Cambio | Archivo:línea | Esfuerzo | Bloqueador? |
|---|---|---|---|---|
| QW-1 | Append-by-ISO en step 10b: leer CSV existente, dropear filas de los ISO en `--countries`, concatenar las nuevas. Ídem step 10. | `10b_accessibility_aggregate_osrm.py:95-104`, `10_accessibility_aggregate.py:308-311` | 0.5 día | **SÍ — gate v1.0.0** |
| QW-2 | Pinear `IMG="osrm/osrm-backend@sha256:..."` con digest verificado. | `09b_osrm_build_and_run.sh:24` | 0.5 h | SÍ |
| QW-3 | Container names únicos por ISO: `name="osrm-run-${iso}"`. | `09b_osrm_build_and_run.sh:78` | 5 min | SÍ |
| QW-4 | `OSRM_HOST` y `OSRM_PORT` como env vars con default en cliente Python. | `09b_travel_time_osrm.py:68-69, 309-310` | 1 h | SÍ |
| QW-5 | `PROJECT_ROOT` lee de `IDB_ACCESS_DATA_ROOT` / `IDB_ACCESS_RESULTS_ROOT` con fallback. Helper común `pipeline/_paths.py`. | `09_travel_time_fmm.py:61`, `09b_travel_time_osrm.py:63`, `10*.py`, `06_pop_grid.py:60`, `07_*.py`, `05_*.py:37`, `03_*.py:36` | 1.5 día | SÍ |
| QW-6 | Agregar `pipeline_version` a cada fila del output. Leer de `pipeline/__init__.py:__version__`. | `10_accessibility_aggregate.py:200-211`, `10b_accessibility_aggregate_osrm.py:96-98` | 1 h | SÍ |
| QW-7 | Commitear `uv.lock` (sacar línea 11 del `.gitignore`). | `.gitignore:11` | 5 min | SÍ |
| QW-8 | LICENSE Apache-2.0 en raíz. | nuevo `LICENSE` | 5 min | SÍ |
| QW-9 | `.dockerignore` raíz. | nuevo `.dockerignore` | 10 min | SÍ |
| QW-10 | Logs estructurados a `results/logs/{step}_{ISO}_{YYYY-MM-DDTHHmmss}.log`. | `09b_osrm_build_and_run.sh:47`, `09b_travel_time_osrm.py:316`, todos los logging.basicConfig | 0.5 día | NO (pero $$$ para troubleshoot BID) |
| QW-11 | `--countries` plural y consistente en todos los scripts. | `09b_travel_time_osrm.py:303` (singular hoy) | 1 h | NO |
| QW-12 | Vincular `docs/accessibility_limitations_log.md` desde README. | `README.md` | 5 min | NO |
| QW-13 | Añadir BHS y BRB a `GEOFABRIK_PATH` con fallback (BRB no tiene extract dedicado; usar `central-america/all-of-cw.osm.pbf` recortado o un OSM extract custom). | `09b_osrm_build_and_run.sh:31-45` | 1 día (incluye QC del extract) | NO (pendiente identificado en limitations log) |

**Total quick wins: ~5 días de trabajo si una persona dedicada.**

### 4.2 Estructurales (semanas)

| # | Cambio | Esfuerzo |
|---|---|---|
| ST-1 | Dockerfile multi-stage (build + runtime). `.dockerignore` agresivo. Validar imagen < 5 GB. | 2 días |
| ST-2 | `docker-compose.yml` con services `platform` + `osrm`, volumes, networks. | 0.5 día |
| ST-3 | `cli/` con Typer: subcomandos `run / validate / diff / rebuild / smoke`. Wrappea los scripts numerados vía `importlib.util`. | 1 semana |
| ST-4 | `examples/sample_BLZ/` con todos los artefactos + `expected_hashes.json`. | 2 días |
| ST-5 | `tests/test_smoke_blz.py` end-to-end < 5 min, valida hashes. | 1 día |
| ST-6 | Schema SCL central (pandera o pydantic) en `pipeline/schemas.py` + validador en CI. Alineación de nombres con la spec del lead (`country_iso3` vs `isoalpha3`, `band` vs `time_band`, etc.) o ajuste de la spec a las columnas reales (recomendado). | 2 días |
| ST-7 | `.github/workflows/` con `tests.yml` (pytest + smoke), `docker_build.yml` (push a ghcr.io), `security_scan.yml` (trivy + pip-audit). | 2 días |
| ST-8 | `_manifest.json` por corrida: fechas de extract Geofabrik usadas, hash de cada parquet de input, versión del pipeline, OS / Python / uv versions. | 1.5 día |
| ST-9 | Logger uniforme (`structlog` o `rich.logging`) con campos `iso`, `mode`, `level`, `step`. JSON lines para el frontend. | 1 día |
| ST-10 | `docs/data_dictionary.md`, `docs/architecture.md` (Mermaid), `docs/add_new_country.md`, `docs/update_year.md`, `docs/reproducibility.md`, `docs/data_privacy.md`, `docs/support.md`. | 3 días (escribir + capturas) |
| ST-11 | README ES (la spec del lead pide ambos idiomas). | 0.5 día |
| ST-12 | Sharding por ADM1 para ARG/MEX/BRA (patrón A de §2.4): `--adm1-shard` flag en 09b, auto-discovery en 10b. | 2 días |
| ST-13 | Subdivisión real del grafo OSRM con buffer 30 km (patrón C de §2.4) para BRA. QC contra corrida nacional en CRI. | 1 semana — fase 2 |
| ST-14 | Streamlit frontend (4 páginas). | 1 semana — fase 2 |
| ST-15 | Health-check command que verifica los 5 artefactos (CIMA, ADM polígonos, friction, WorldPop grid, OSRM extract) por país. | 1 día |

**Total estructurales fase 1: ~3 semanas. Fase 2: ~2.5 semanas.**

---

## 5. Veredicto

> ¿El pipeline actual está listo para la spec del lead as-is, o requiere refactor previo?

**Respuesta corta: sí con quick wins. No requiere refactor metodológico ni del pipeline core. Requiere ~5 días de quick wins críticos + ~3 semanas de tooling estructural para llegar a v1.0.0 limpio.**

### 5.1 Quick wins que bloquean el v1.0.0

De los QW-1..QW-9 identificados arriba, estos son los **inegociables** para que el entregable sea aceptable como "el BID lo corre y obtiene el CSV reproducible":

1. **QW-1 (append-by-ISO en step 10b).** Sin esto, el comando "actualizar Argentina" borra los otros 21 países. Este es el peor foot-gun del repo actual. **0.5 día.**
2. **QW-2 (pinear digest de osrm-backend).** Sin esto, el repo no es reproducible: la misma corrida en distintas fechas trae imágenes distintas. **30 min.**
3. **QW-5 (paths configurables).** Sin esto el Dockerfile no puede montar `/data` y `/results` como volumes — el container está obligado a tener el código y los datos en el mismo árbol. **1.5 día.**
4. **QW-6 (pipeline_version en el output).** Sin esto, BID no puede diferenciar dos corridas con distintas versiones del código si el CSV se les pierde en email. **1 h.**
5. **QW-7 (`uv.lock` commiteado).** Sin esto la spec §7.5 falla — no hay pinneo estricto. **5 min.**
6. **QW-8 (LICENSE).** Gate explícito del checklist del lead (§6 ítem 1). **5 min.**

### 5.2 Lo que se puede diferir a fase 2

- Streamlit frontend (ST-14): la spec del lead lo lista como fase 2 explícita.
- Sharding ARG/MEX/BRA (ST-12, ST-13): la spec del lead acepta que se entregan vía FMM en v1.0.0.
- BHS/BRB con OSRM (QW-13): la spec lo lista como pendiente documentado.
- README ES (ST-11): nice-to-have, no bloquea aceptación si EN está bien.

### 5.3 Mi punto de discordancia con la spec del lead

El lead dice (§9.3) que "solo dos cosas" son refactor del pipeline: parametrizar paths + estandarizar logs. **Yo agrego una tercera obligatoria: el bug de overwriting en step 10b (QW-1).** Ese es refactor de lógica, no de tooling — está dentro del cuerpo del aggregator, no en un adapter. Sin ese fix, el CLI del lead persona-A "actualicé un país, lo reagrego" produce silenciosamente un CSV truncado. Es bug-fix obligatorio, no nice-to-have.

### 5.4 Roadmap recomendado

| Semana | Trabajo | Entrega |
|---|---|---|
| 1 | QW-1 a QW-9 (5 días) + arranque `examples/sample_BLZ/` (ST-4) | Pipeline corre con paths configurables, sin foot-guns, append-by-ISO, LICENSE en su lugar. |
| 2 | Dockerfile + docker-compose (ST-1, ST-2) + smoke test (ST-5) | Imagen builds, BLZ corre adentro del container en < 5 min. |
| 3 | CLI (ST-3) + logs (ST-9, QW-10) + schema central (ST-6) | `accessibility-cli run --countries BLZ` funciona end-to-end. |
| 4 | CI (ST-7) + manifest (ST-8) + health check (ST-15) | PRs verdes, manifest por corrida. |
| 5 | Docs (ST-10) + README ES (ST-11) + tag v1.0.0 | Release. |
| 6 | Buffer + training grabado + feedback Cecilia | Aceptación BID. |

Lo que **no** debe entrar en fase 1: ningún rework de la lógica de FMM/OSRM, ninguna re-corrida masiva de Steps 01–07 (esos datos ya están y son los que se entregan), ningún rename de columnas del schema que rompa los dashboards downstream (sólo agregar columnas opcionales).
