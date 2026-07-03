# Colab runner — OSRM ARG / MEX / BRA

Notebook empaquetado para correr el indicador OSRM de accesibilidad escolar
para los 3 países que no entran en una laptop de 16 GB. Los pasos 01–07 del
pipeline (CIMA + QC + grid WorldPop + clip fricción) corren localmente como
siempre; este notebook ejecuta **09b + 10b** en Colab Pro/Pro+ y devuelve el
CSV SCL agregado que mergea con los 17 países ya producidos en local.

> El fix `write_scl_output` (commit `fe1c7da`) hace que correr el notebook
> para un país NO sobrescriba los otros 21 en `accessibility_osrm_scl.csv`.
> Antes de ese fix, este notebook habría sido inseguro.

---

## Antes de abrir el notebook — subir inputs a Drive

Estructura mínima en `MyDrive/idb_access/`:

```
idb_access/
├── inputs/
│   ├── schools/
│   │   └── AR/LAC_schools_k12_with_context.csv   (09b lee ESTE, no el CIMA — ~92 MB, todos los países)
│   ├── bin/
│   │   └── osrm/                       (vacío; el notebook cachea acá los binarios compilados)
│   ├── bounderys/
│   │   └── LAC/
│   │       ├── level 0/lac-level-0.{shp,shx,dbf,prj,cpg}
│   │       ├── level 1/lac-level-1.{shp,shx,dbf,prj,cpg}
│   │       └── level 2/lac-level-2.{shp,shx,dbf,prj,cpg,csv}
│   ├── population/
│   │   └── WorldPop/processed/population_grid_{ISO}.csv
│   ├── Poverty Rates/
│   │   ├── lac-level-2.csv
│   │   └── meta-rwi/{ISO}/...        (opcional; ARG/MEX tienen RWI, BRA también)
│   └── transportation/
│       └── osrm/_pbf/                 (vacío; el notebook baja el PBF acá)
└── outputs/
    ├── osrm/                          (parquets de 09b — uno por modo × nivel)
    └── accessibility/
        └── accessibility_osrm_scl.csv  (subí el CSV actual de 17 países acá
                                          para que el append junte todo)
```

Inputs que **de verdad** consume el notebook:
- `schools/AR/LAC_schools_k12_with_context.csv` — escuelas de todo LAC (09b
  filtra por país). **Es el que lee 09b, NO el CIMA por país.** ~92 MB, se sube
  una vez y sirve para los 3 países.
- `population/WorldPop/processed/population_grid_{ISO}.csv` — uno por país.
- `bounderys/LAC/level 2/lac-level-2.csv` — polígonos BID, lo usa 10b al agregar.

### OSRM se compila desde fuente (no hay paquete conda ni Docker en Colab)

conda-forge no tiene `osrm-backend` y Colab no permite Docker/udocker (lo marca
como uso prohibido). El notebook **compila `osrm-backend v5.27.1` desde fuente**
(gcc-12 + libtbb-dev; legítimo, es solo compilar C++) y **cachea los binarios en
`inputs/bin/osrm/` en Drive**. La 1ra corrida tarda ~20-30 min en compilar; las
siguientes restauran el binario en segundos.

**Cuánto pesa el bundle por país (aprox):**

| País | CIMA | Grid WorldPop | Total país | Total bundle (con LAC compartidos) |
|---|---|---|---|---|
| ARG | ~10 MB | ~100 MB | ~110 MB | ~1.4 GB (incluye polígonos LAC) |
| MEX | ~50 MB | ~110 MB | ~160 MB | ~1.5 GB |
| BRA | ~150 MB | ~370 MB | ~520 MB | ~1.9 GB |

El PBF de OpenStreetMap (ARG ~900 MB, MEX ~1.2 GB, BRA ~3.5 GB) NO se sube a
Drive — el notebook lo descarga directamente de Geofabrik en cada corrida y lo
cachea bajo `inputs/transportation/osrm/_pbf/` para reusarlo.

---

## Runtime de Colab

- **ARG, MEX:** Colab Pro con runtime **High-RAM** (25 GB) alcanza.
- **BRA:** Colab Pro+ con runtime **High-RAM** (51 GB) recomendado. Aún así,
  la corrida completa puede exceder el límite de sesión Colab (12 h) —
  preparate para reanudar (los parquets se reusan, el step 10b solo necesita
  los 6 parquets para la agregación final).
- GPU/TPU **no se usan** (OSRM es CPU-bound).

---

## Cómo correr

1. Abrí `colab_osrm_country.ipynb` en Colab:
   - desde GitHub: `File → Open notebook → GitHub → AngelaLop/accessibility_platform`, pega la URL
   - o subilo desde tu disco
2. En la **celda 1** (parámetros), seteá:
   - `COUNTRY = "ARG"` (o `"MEX"` o `"BRA"`)
   - `DRIVE_ROOT` si tu Drive folder no es exactamente `MyDrive/idb_access`
3. `Runtime → Change runtime type → Hardware accelerator: None, RAM: High-RAM`.
4. `Runtime → Run all`.
5. Cuando termine, los outputs ya quedaron en
   `/MyDrive/idb_access/outputs/accessibility/accessibility_osrm_scl.csv` y
   los 6 parquets en `/MyDrive/idb_access/outputs/osrm/`.
6. Repetí cambiando `COUNTRY` para los otros dos.

El CSV SCL acumula los 3 países sin pisar — gracias al fix `write_scl_output`.

---

## Tiempos esperados

| País | Build foot+car | Walking × 3 niveles | Motorized × 3 niveles | Step 10b | **Total** |
|---|---|---|---|---|---|
| ARG | ~15 min | ~3 h | ~30 min | ~30 s | **~3.5 h** |
| MEX | ~15 min | ~3.5 h | ~30 min | ~30 s | **~4 h** |
| BRA | ~25 min | ~10-15 h | ~2 h | ~1 min | **~13-17 h** |

Estimaciones derivadas del throughput observado en local
(`pipeline_diagnostic_2026-05-27.md` §2.3): ~300 cells/s sostenidos.

---

## Después de correr los 3

Descargá `accessibility_osrm_scl.csv` final desde Drive y subilo a
`results/accessibility/` en tu repo local (reemplaza al de 17 países). Los
dashboards y consumidores downstream ya van a ver los 22 países (los 17 viejos
+ ARG + MEX + BRA, totalizando 20 + JAM/SUR si ya estaban). HTI siempre queda
fuera por scope.

---

## Pendientes que afectan este notebook

- **FMM para ARG / MEX / BRA** — Step 09 FMM requiere los rasters de fricción
  MAP 2019 clipeados por país. Hoy solo existen para COL/CRI/ECU/PAN/PER
  (`data/transportation/travel_times/` tiene 5 directorios). Cuando subas la
  fricción global a Drive y corras step 07 + step 09 para estos 3, agregamos
  una sección FMM al notebook. Por ahora va solo OSRM (que es el método
  primario por validación con r5py).
- **QW-2 / PROV-2 (digest OSRM pinneado + `_provenance.json` por país)** — el
  notebook YA persiste `pbf_osm_timestamp` por país (celda 9). El digest OSRM
  todavía está implícito en la versión de conda-forge — anotalo manualmente al
  manifest hasta que se shippee QW-2 en el shell driver.

---

## Troubleshooting

**OOM durante `osrm-extract` o `osrm-customize`** — runtime con poca RAM.
`Runtime → Change runtime type → High-RAM` (Pro+) y reintentar.

**"Session disconnected" en medio de una matriz** — Colab corta sesiones largas.
Para BRA es probable. Re-corré el notebook desde la celda donde estaba; los
parquets ya escritos en Drive se reusan (el código chequea `.exists()` antes
de recalcular).

**`Cannot connect to OSRM port 5000`** — `osrm-routed` murió (revisar
`proc.poll()` o el log). Casi siempre es OOM.

**`No module named pipeline`** — asegurate de que la celda 5 clonó el repo en
`/content/repo` y que el `cd` se aplicó al llamar `uv run python pipeline/...`.

**El CSV final no tiene los 3 países nuevos** — verificá en la última celda
que `set(df['isoalpha3'])` los incluya. Si no, probablemente la celda 13 (step
10b) falló — revisar logs.
