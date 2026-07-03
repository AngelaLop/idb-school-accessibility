# RWI piloto para BRA + MEX + ARG en Colab

Los rasters WorldPop 100m de estos 3 países superan los 3 GB en memoria, así que el script local crashea con `MemoryError`. Este notebook usa **lectura ventaneada** (rasterio Window) — lee solo los ~3 KB de pixeles alrededor de cada celda RWI individual, no el raster entero.

## Cómo correrlo

### Opción A — Google Drive mount (recomendada)

1. Sincronizar la carpeta del proyecto (`accessibility_platform/`) con Google Drive
2. Abrir `RWI_BRA_MEX_ARG.ipynb` en Colab (Archivo → Subir notebook, o abrir desde Drive)
3. Verificar que `PROJECT_ROOT` en la celda de paths apunte a donde está el proyecto en Drive (default: `/content/drive/MyDrive/IDB/accessibility_platform`)
4. Runtime → Ejecutar todo
5. Cuando termine, los `*.tests.csv` quedan automáticamente en `results/exploratory/rwi_vs_poverty/` del proyecto (Drive sync)

### Opción B — Subida manual

1. Crear una carpeta en Colab con esta estructura mínima (panel "Files" izquierda):
   ```
   /content/
   ├── data/
   │   ├── Poverty Rates/
   │   │   ├── lac-level-2.csv
   │   │   └── meta-rwi/
   │   │       ├── BRA/bra_relative_wealth_index.csv
   │   │       ├── MEX/mex_relative_wealth_index.csv
   │   │       └── ARG/arg_relative_wealth_index.csv
   │   ├── population/WorldPop/
   │   │   ├── BRA/bra_pop_2023_CN_100m_R2025A_v1.tif
   │   │   ├── MEX/mex_pop_2023_CN_100m_R2025A_v1.tif
   │   │   └── ARG/arg_pop_2023_CN_100m_R2025A_v1.tif
   │   └── bounderys/LAC/level 2/
   │       ├── lac-level-2.shp
   │       ├── lac-level-2.dbf
   │       ├── lac-level-2.shx
   │       ├── lac-level-2.prj
   │       └── lac-level-2.cpg
   ```
2. En la celda de paths cambiar `PROJECT_ROOT = '/content'`
3. Saltar la celda de Drive mount
4. Ejecutar todo
5. Al final usar la celda de `files.download(...)` para bajar los 3 `tests.csv`

## Inputs requeridos

| Archivo | Tamaño | Origen |
|---|---|---|
| `{iso}_relative_wealth_index.csv` | 9-25 MB | [Meta Data for Good](https://dataforgood.facebook.com/dfg/tools/relative-wealth-index) o ya en el repo |
| `{iso}_pop_2023_CN_100m_R2025A_v1.tif` | 150-480 MB | [WorldPop 2023 R2025A](https://hub.worldpop.org/) o ya en el repo |
| `lac-level-2.{shp,dbf,prj,shx,cpg}` | 17 MB | data/bounderys/LAC/level 2/ del repo |
| `lac-level-2.csv` | 1 MB | Tasas pobreza BID, ya en el repo |

Total ~1 GB. Cabe en Colab Free.

## Outputs

Por país, en `results/exploratory/rwi_vs_poverty/`:

- `{ISO}_rwi_adm2_aggregated.csv` — RWI ponderado por población por ADM2
- `{ISO}_rwi_vs_poverty_merged.csv` — agregado + tasas pobreza BID
- `{ISO}_rwi_vs_poverty_tests.csv` — **el dashboard lee este**: Spearman ρ + CI, decile match, R²

## Paso final (local)

Copiar los 3 `tests.csv` a `results/exploratory/rwi_vs_poverty/` del repo local, después:

```bash
uv run python pipeline/export_dashboard_data.py
cp results/dashboard/dashboard_payload.json ../accessibility-dashboard/content/dashboard-payload.json
cd ../accessibility-dashboard
npm run build
```

El dashboard `step-06 → RWI multi-país` reclasifica BRA + MEX + ARG de "pendiente"/"bloqueado" a "validado" automáticamente.

## Cuánto tarda

Estimado en Colab Free (CPU):

| País | Celdas RWI | Tiempo aproximado |
|---|---:|---:|
| ARG | ~44K | 4-8 min |
| MEX | ~150K | 12-20 min |
| BRA | ~173K | 15-25 min |

Total: ~35-55 min. Si el runtime se cierra por inactividad, se puede correr país por país.

## Si querés correr otros países más adelante

El notebook está diseñado genérico — solo cambiar la lista `['ARG', 'MEX', 'BRA']` en la celda "Correr" por cualquier ISO que tenga RWI publicado. Los 5 sin datos RWI (BRB, CHL, PAN, URY + similar) no van a funcionar — Meta no publicó.

## Por qué este notebook existe (técnicamente)

`pipeline/06_pop_exploratory.py:95` hace `pop = src.read(1)` que carga el raster WorldPop entero a un array numpy. Para BRA eso son **9.35 GB en RAM** (46827 × 53607 pixels × 4 bytes float32) y la máquina local tira `MemoryError`.

La función `aggregate_rwi_by_adm2_windowed` en el notebook hace `src.read(1, window=Window(...))` por celda RWI. Cada ventana es 27×27 pixels = ~3 KB. 173,000 celdas × 3 KB = ~500 MB transient memory máximo. Cabe en cualquier máquina razonable.

El fix definitivo (refactor del script local) está pendiente como backlog. Por ahora el notebook resuelve los 3 países que necesitamos.
