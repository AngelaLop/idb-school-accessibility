# Guía operativa: actualizar la base de escuelas de un país

Caso trabajado: **Perú, cosecha MINEDU 2025** (`PER_2025_total.xlsx`).
Repositorio: `AngelaLop/idb-school-accessibility` (v1.0.0).
Última revisión: 2026-08-14.

El procedimiento sirve para cualquier país cuando el ministerio entrega una
versión más reciente de su registro de escuelas. Los números y los nombres de
columna del ejemplo son de Perú; la secuencia de pasos es la misma para el resto.

---

## 0. Alcance

Al terminar este procedimiento quedan:

| Salida | Ruta |
|---|---|
| Base CIMA de Perú reconstruida con la cosecha 2025 | `data/schools/AR/PER/processed/PER_total_cima.csv` |
| QC de coordenadas recalculado (esquema v2, 47 columnas) | mismo archivo |
| Reporte de cambios escuela por escuela | `results/QC/PER_2025_coord_update.csv` |
| Base K-12 de Perú | `data/schools/AR/PER/processed/PER_schools_clean.csv` |
| Base K-12 regional con Perú actualizado | `data/schools/AR/LAC_schools_k12_clean.csv` y `..._with_context.csv` |
| Evaluación de cobertura contra el universo oficial | `results/school_coverage_assessment.csv` |

**Fuera de alcance.** Los indicadores de accesibilidad (steps 09b OSRM y 10b) y
los exports del dashboard no se recalculan aquí. Cambian el conjunto de escuelas
y por lo tanto los tiempos de viaje, pero se corren después, con la base ya
cerrada y validada.

---

## 1. La regla de trabajo

La cosecha nueva manda sobre la vigente, pero nada se reemplaza en silencio.

1. **Escuelas presentes en las dos bases.** Se comparan las coordenadas. Si
   coinciden dentro de 100 m, se confirma la ubicación. Si difieren, se adopta la
   coordenada nueva y el cambio queda registrado en el reporte con la distancia
   del desplazamiento.
2. **Escuelas solo en la base nueva.** Se dan de alta.
3. **Escuelas solo en la base vigente.** Se reportan como bajas y se revisan
   antes de excluirlas. No se borran sin dejar rastro.
4. **Año de referencia.** El país pasa a la cosecha nueva (Perú: 2024 → 2025).
5. **Los dos archivos crudos se conservan.** `Padron.csv` (2023) y
   `PER_2025_total.xlsx` (2025) quedan los dos en `raw/`. El repositorio
   georreferenciado publica la cosecha más reciente.

---

## 2. Requisitos

### 2.1 Software

| Herramienta | Versión | Nota |
|---|---|---|
| git | cualquiera reciente | |
| Python | 3.13 | fijado en `.python-version` |
| uv | reciente | gestor de entorno y dependencias del proyecto |

Instalación de uv en Windows. Se abre PowerShell (no “Símbolo del sistema”) y se
pega el instalador oficial de Astral:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

El `-ExecutionPolicy ByPass` es necesario porque Windows bloquea por defecto la
ejecución de scripts descargados. Alternativa si la máquina tiene winget o si la
política corporativa bloquea el instalador:

```powershell
winget install --id=astral-sh.uv -e
```

También hay instalador MSI y binarios sueltos en
`https://github.com/astral-sh/uv/releases`.

Después de instalar hay que **cerrar y volver a abrir PowerShell** para que tome
el PATH, y comprobar:

```powershell
uv --version
```

Si el comando no aparece, el ejecutable quedó en
`%USERPROFILE%\.local\bin\uv.exe` y falta agregar esa carpeta al PATH.

En macOS o Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

No se usa `pip` directamente. Todo corre con `uv run`, que resuelve el entorno
contra `uv.lock` y garantiza las mismas versiones para todos.

Espacio en disco: unos 5 GB (paquete de datos + entorno).

### 2.2 Accesos

- Repositorio público `AngelaLop/idb-school-accessibility` (clonar no requiere accesos; escribir sí).
- Paquete de datos de trabajo (no viaja por git, ver §3).

---

## 3. Dónde va la data

`data/` y `results/` están en `.gitignore` **a propósito**. Los datos crudos de
los ministerios no se redistribuyen por el repositorio y el bundle completo pesa
unos 68 GB (contrato en `DATA_MANIFEST.md`). Para actualizar un país no hace
falta el bundle completo.

### 3.1 Paquete mínimo para este trabajo

Se descomprime respetando exactamente esta estructura, tomando la raíz del repo
clonado como punto de partida:

```
idb-school-accessibility/
├── data/
│   ├── bounderys/LAC/                       605 MB   polígonos BID ADM0/1/2 (step 02)
│   │   ├── level 0/lac-level-0.shp (+ .dbf .prj .shx .cpg)
│   │   ├── level 1/lac-level-1.shp (+ ...)
│   │   └── level 2/lac-level-2.shp (+ ... y lac-level-2.csv)
│   ├── population/WorldPop/processed/
│   │   └── population_grid_PER.csv           59 MB   grilla 1 km (step 07)
│   └── schools/AR/
│       ├── LAC_merged.csv                    65 MB   resolución de id_edificio (step 05)
│       ├── PER/raw/
│       │   ├── PER_2025_total.xlsx          2,8 MB   cosecha nueva
│       │   ├── Padron.csv                    42 MB   cosecha 2023: nombre y admin declarada
│       │   ├── Matricula_01.xlsx             29 MB   matrícula por grado (ciclo de secundaria)
│       │   └── _ministry_counts.json                 derivación del universo oficial
│       ├── PER/processed/
│       │   └── PER_total_cima.csv            12 MB   base vigente, insumo del diff
│       └── {ISO}/processed/                 152 MB   *_schools_clean.csv y *_schools_with_context.csv
│                                                     de los otros 21 países (solo para rearmar la base LAC)
└── results/
    └── presentacion_BID/
        └── school_coverage_assessment_final.xlsx     universo oficial por país (step 03)
```

Total: alrededor de **970 MB**. Si el trabajo se limita a Perú y no se rearma la
base regional (steps 01 a 03), alcanza con unos **730 MB**: se pueden omitir
`LAC_merged.csv`, la grilla de población y los `processed/` de los otros países.

### 3.2 Datos fuera del repo

Si prefiere no copiar los datos dentro del árbol del repositorio:

```bash
export IDB_ACCESS_DATA_ROOT=/ruta/a/data
export IDB_ACCESS_RESULTS_ROOT=/ruta/a/results
```

```powershell
# Windows PowerShell
$env:IDB_ACCESS_DATA_ROOT = "D:\idb\data"
$env:IDB_ACCESS_RESULTS_ROOT = "D:\idb\results"
```

Sin esas variables, los scripts usan `<repo>/data` y `<repo>/results`
(`pipeline/_paths.py`).

### 3.3 Verificar que quedó todo en su lugar

El paquete incluye `verificar_paquete.py`. Se copia a la raíz del repositorio y
se corre desde ahí:

```bash
uv run python verificar_paquete.py
```

Comprueba archivo por archivo, incluidos los acompañantes de los shapefiles
(`.dbf`, `.shx`, `.prj`), y avisa qué falta y para qué step hace falta.

---

## 4. Montar el entorno

```bash
git clone https://github.com/AngelaLop/idb-school-accessibility.git
cd idb-school-accessibility

uv sync                    # instala el entorno pinneado desde uv.lock
uv run pytest tests/ -q    # control de que el clon quedó sano
```

En un clon sin datos, buena parte de la suite se salta (`skipped`): son los tests
que leen archivos del bundle. Lo que no puede haber es ningún `failed`.

Rama de trabajo antes de tocar nada:

```bash
git checkout -b feat/per-cosecha-2025
```

---

## 5. Paso 1. Perfilar el archivo nuevo antes de tocar código

Nunca se edita el pipeline sin haber mirado el archivo primero.

```bash
uv run python -c "
import pandas as pd
df = pd.read_excel('data/schools/AR/PER/raw/PER_2025_total.xlsx')
print(df.shape); print(list(df.columns))
for c in ['nivel','gestion','area']:
    print(c, df[c].value_counts().to_dict())
print('duplicados COD_MOD+ANEXO:', df.duplicated(['COD_MOD','ANEXO']).sum())
print('coordenadas nulas:', df[['NLAT_IE','NLONG_IE']].isna().sum().to_dict())
"
```

Resultado en Perú:

| Chequeo | Valor |
|---|---|
| Filas / columnas | 54.034 / 9 |
| Columnas | CODINST, COD_MOD, ANEXO, CODLOCAL, nivel, gestion, area, NLAT_IE, NLONG_IE |
| nivel | Primaria 38.336, Secundaria 15.698 |
| gestion | Pública 40.487, Privada 13.547 |
| area | Rural 32.952, Urbana 21.082 |
| Duplicados COD_MOD+ANEXO | 0 |
| Coordenadas nulas | 0 |

Lo importante es lo que el archivo **no** trae: nombre del centro, dirección y
departamento/provincia/distrito. El QC de Perú valida las coordenadas contra el
polígono declarado a nivel de provincia (`final_match_level = adm2`), y esa
declaración sale del `Padron.csv`. Por eso el Padrón sigue siendo insumo aunque
la cosecha nueva mande en coordenadas.

---

## 6. Paso 2. Comparar cosechas antes de reemplazar nada

Este paso es de solo lectura y produce el reporte de cambios. El script viaja en
el paquete de datos como `compare_vintages.py`: se copia a la raíz del
repositorio y se corre con `uv run python compare_vintages.py`. El código
completo, por si hay que adaptarlo a otro país:

```python
"""Compara la base vigente (CIMA) contra la cosecha nueva. Solo lectura."""
import numpy as np, pandas as pd
from pathlib import Path

CIMA  = Path("data/schools/AR/PER/processed/PER_total_cima.csv")
NUEVA = Path("data/schools/AR/PER/raw/PER_2025_total.xlsx")
OUT   = Path("results/QC/PER_2025_coord_update.csv")
UMBRAL_KM = 0.1                      # 100 m: por debajo es la misma ubicación

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 2 * R * np.arcsin(np.sqrt(a))

vieja = pd.read_csv(CIMA, dtype={"id_centro": str}, low_memory=False)
vieja = vieja[["id_centro", "latitud", "longitud", "sector"]]
vieja.columns = ["id_centro", "lat_prev", "lon_prev", "sector_prev"]

nueva = pd.read_excel(NUEVA)
nueva["id_centro"] = nueva["COD_MOD"].astype(str) + "-" + nueva["ANEXO"].astype(str)
nueva = nueva.rename(columns={"NLAT_IE": "lat_new", "NLONG_IE": "lon_new"})
nueva = nueva[["id_centro", "lat_new", "lon_new", "nivel", "gestion", "area", "CODLOCAL"]]

m = vieja.merge(nueva, on="id_centro", how="outer", indicator=True)
m["delta_km"] = haversine_km(m["lat_prev"], m["lon_prev"], m["lat_new"], m["lon_new"])

def clasificar(r):
    if r["_merge"] == "right_only":                     return "alta"
    if r["_merge"] == "left_only":                      return "baja"
    if pd.isna(r["lat_prev"]) or pd.isna(r["lon_prev"]): return "coord_nueva_sin_previa"
    if pd.isna(r["lat_new"])  or pd.isna(r["lon_new"]):  return "sin_coord_nueva"
    return "coord_confirmada" if r["delta_km"] <= UMBRAL_KM else "coord_actualizada"

m["estado"] = m.apply(clasificar, axis=1)
print(m["estado"].value_counts().to_string())
mov = m.loc[m["estado"] == "coord_actualizada", "delta_km"]
print(mov.describe(percentiles=[.5, .9, .99]).round(2).to_string())
print("  >1 km:", (mov > 1).sum(), " >10 km:", (mov > 10).sum(), " >50 km:", (mov > 50).sum())

OUT.parent.mkdir(parents=True, exist_ok=True)
m.drop(columns="_merge").to_csv(OUT, index=False, encoding="utf-8")
```

Resultado en Perú (base vigente: 53.338 escuelas; cosecha 2025: 54.034):

| Estado | Escuelas |
|---|---:|
| `coord_confirmada` (movimiento ≤ 100 m) | 51.345 |
| `coord_actualizada` (movimiento > 100 m) | 1.438 |
| `alta` (solo en 2025) | 1.251 |
| `baja` (solo en la base vigente) | 555 |

Desplazamiento de las 1.438 actualizadas: mediana 0,48 km, percentil 90 3,94 km,
438 por encima de 1 km, 41 por encima de 10 km, 7 por encima de 50 km, máximo
326 km.

**Qué hay que mirar a mano:** las escuelas con desplazamiento mayor a 10 km. Un
salto de 300 km no es una corrección de precisión, es un cambio de ubicación
declarada o un error en alguna de las dos cosechas. Se ordena el reporte por
`delta_km` descendente y se revisan esas filas una por una antes de aceptar el
reemplazo.

---

## 7. Paso 3. Tres decisiones que hay que dejar escritas

Estas no las resuelve el pipeline. Se acuerdan con el equipo y se documentan en
el PR y en `docs/accessibility_limitations_log.md`.

**1. Las 555 bajas.** Escuelas que están en la base vigente y no en la cosecha
2025. Pueden ser cierres reales, fusiones o cambios de código. La cosecha nueva
es la que se publica, así que quedan fuera de la base georreferenciada, pero la
lista se conserva en el reporte de cambios y se consulta con MINEDU.

**2. El ciclo de secundaria.** La cosecha 2025 trae `nivel = Secundaria` sin
distinguir ciclo. La separación entre secundaria baja (grados 1 y 2) y alta
(grados 3 a 5) se hace con la matrícula por grado de `Matricula_01.xlsx`. De las
15.698 secundarias de 2025, 15.083 tienen matrícula registrada y 615 no (son
altas posteriores a ese corte). Regla provisoria: esas 615 se marcan en los dos
ciclos, que es la lectura maximalista y coincide con el criterio usado en
República Dominicana. Lo correcto es pedir a MINEDU la matrícula del mismo año
de la cosecha.

**3. El sector.** El Padrón clasificaba por `GESTION` (1 pública directa,
2 pública de gestión privada, 3 privada) y el pipeline trataba 1 y 2 como
públicas. La cosecha 2025 trae solo `Pública` / `Privada`. Contrastando las
52.783 escuelas presentes en ambas, la asignación coincide en todas menos 17
(0,03%). El mapeo directo es válido; las 17 se listan y se consultan.

---

## 8. Paso 4. Cambios de código

Son dos archivos. **No se toca `pipeline/02_qc_coordinates.py`**: su
`RAW_CONFIG["PER"]` sigue apuntando a `raw/Padron.csv`, que es de donde salen
DPTO y PROV para validar las coordenadas contra el polígono declarado. El cruce
es por `id_centro` con unión por izquierda, así que las 1.251 altas sin
correspondencia en el Padrón quedan con estado `NO_RAW_ADM`, que es la etiqueta
correcta: no hay admin declarada contra la cual comparar.

### 8.1 `pipeline/01_build_cima.py`, función `process_PER`

Reemplazar el cuerpo completo de la función por:

```python
def process_PER():
    iso = 'PER'
    try:
        # Cosecha vigente: PER_2025_total.xlsx (MINEDU, corte 2025). Una fila por
        # servicio educativo (COD_MOD + ANEXO) con nivel, gestion, area y coordenada.
        # No trae nombre ni admin declarada: ambos salen de Padron.csv por id_centro.
        raw = BASE / iso / 'raw' / 'PER_2025_total.xlsx'
        df = pd.read_excel(raw)
        df.columns = [str(c).strip() for c in df.columns]
        df['id_centro'] = df['COD_MOD'].astype(str) + '-' + df['ANEXO'].astype(str)

        # El archivo ya viene acotado a Primaria + Secundaria (K-12).
        df['nivel_primaria'] = (df['nivel'].str.strip() == 'Primaria').astype(int)

        # Secundaria no distingue ciclo. Se separa con la matricula por grado
        # (Matricula_01.xlsx): D01-D04 = ciclo bajo, D05-D10 = ciclo alto.
        mat = pd.read_excel(BASE / iso / 'raw' / 'Matricula_01.xlsx')
        f0 = mat[mat['NIV_MOD'] == 'F0'].copy()
        grados = [f'D{i:02d}' for i in range(1, 11)]
        for col in grados:
            f0[col] = pd.to_numeric(f0[col], errors='coerce').fillna(0)
        f0['_secbaja'] = (f0[grados[:4]] != 0).any(axis=1).astype(int)
        f0['_secalta'] = (f0[grados[4:]] != 0).any(axis=1).astype(int)
        flags = f0.groupby('COD_MOD').agg(
            nivel_secbaja=('_secbaja', 'max'),
            nivel_secalta=('_secalta', 'max'),
        ).reset_index()
        df = df.merge(flags, on='COD_MOD', how='left')

        es_sec = df['nivel'].str.strip() == 'Secundaria'
        # Secundarias sin matricula (altas de la cosecha nueva): se marcan los dos
        # ciclos hasta que MINEDU entregue la matricula del mismo anio.
        sin_matricula = es_sec & df['nivel_secbaja'].isna()
        df.loc[sin_matricula, ['nivel_secbaja', 'nivel_secalta']] = 1
        df.loc[~es_sec, ['nivel_secbaja', 'nivel_secalta']] = 0
        df[['nivel_secbaja', 'nivel_secalta']] = (
            df[['nivel_secbaja', 'nivel_secalta']].fillna(0).astype(int)
        )

        # Guarda del contrato K-12 (el archivo ya viene filtrado por construccion).
        df = df[(df['nivel_primaria'] == 1) |
                (df['nivel_secbaja'] == 1) |
                (df['nivel_secalta'] == 1)].copy()

        # Sector: gestion Privada -> Private, resto Public. Coincide con la regla
        # anterior sobre GESTION 1/2/3 del Padron en 52.766 de 52.783 escuelas.
        df['sector'] = np.where(
            df['gestion'].astype(str).str.strip().str.startswith('Priv'),
            'Private', 'Public')

        # Nombre del centro desde el Padron: la cosecha 2025 no lo trae.
        pad = pd.read_csv(BASE / iso / 'raw' / 'Padron.csv', sep=';',
                          encoding='utf-8-sig', low_memory=False)
        pad['id_centro'] = pad['COD_MOD'].astype(str) + '-' + pad['ANEXO'].astype(str)
        pad = pad.drop_duplicates('id_centro')[['id_centro', 'CEN_EDU']]
        df = df.merge(pad, on='id_centro', how='left')
        df['nombre_centro'] = df['CEN_EDU'].fillna('').astype(str)

        df['latitud'] = pd.to_numeric(
            df['NLAT_IE'].astype(str).str.replace(',', '.'), errors='coerce')
        df['longitud'] = pd.to_numeric(
            df['NLONG_IE'].astype(str).str.replace(',', '.'), errors='coerce')
        df['adm0_pcode'] = iso

        save_cima(df, iso)
        record(iso, df)
        print(f"  {iso}: {len(df):,} total "
              f"(Public={(df['sector']=='Public').sum():,}, "
              f"Private={(df['sector']=='Private').sum():,})")
    except Exception as e:
        errors[iso] = str(e)
        print(f"  {iso}: ERROR - {e}")
        traceback.print_exc()
```

### 8.2 `pipeline/03_coverage_assessment.py`, `COUNTRY_META["PER"]`

El año de referencia que se escribe en la columna `year` de la base sale de acá.
Cambiar el primer elemento de la tupla de `2024` a `2025` y anotar la fuente en
las notas. Si el universo oficial (`public_universe`, `total_universe`) también
se actualiza, se cambia en el Excel de universos
(`results/presentacion_BID/school_coverage_assessment_final.xlsx`), que tiene
precedencia sobre este archivo. Un valor no se toca sin documento del ministerio
que lo respalde.

---

## 9. Paso 5. Correr el pipeline

### 9.0 Respaldar y limpiar la base vigente

Este paso no es opcional. `save_cima` conserva las columnas de enriquecimiento
del archivo anterior cruzando por `id_centro`. Si el CIMA viejo sigue ahí, la
base nueva hereda el QC de 2023 (`coordinate_quality`, `year = 2024`, códigos
administrativos) para las escuelas que coinciden y lo deja vacío para las altas.
Es evidencia de otra cosecha y hay que recalcularla entera.

```bash
mv data/schools/AR/PER/processed/PER_total_cima.csv \
   data/schools/AR/PER/processed/PER_total_cima_2023_backup.csv
```

```powershell
# Windows PowerShell
Move-Item data\schools\AR\PER\processed\PER_total_cima.csv `
          data\schools\AR\PER\processed\PER_total_cima_2023_backup.csv
```

El respaldo es el insumo del reporte de cambios del §6, así que se hace después
de haber corrido esa comparación (o se apunta el script al backup).

### 9.1 Step 01, construir la base solo para Perú

`01_build_cima.py` no tiene filtro por país: corre los 23. Sin el bundle
completo, los otros 22 fallan y además el script sobreescribe
`results/cima_v2_summary.csv` con un solo país. Para correr únicamente Perú:

```bash
uv run python -c "import sys; sys.path.insert(0,'pipeline'); import importlib; importlib.import_module('01_build_cima').process_PER()"
```

Salida esperada:

```
  PER: 54,034 total (Public=40,487, Private=13,547)
```

Con el bundle completo de los 23 países, el comando estándar del README es
`uv run python pipeline/01_build_cima.py`.

### 9.2 Step 02, QC de coordenadas y esquema v2

```bash
uv run python pipeline/02_qc_coordinates.py --mode finalize --finalize-only --countries PER
```

Es el paso más pesado: carga los tres niveles de polígonos BID y hace los cruces
espaciales. Escribe de vuelta `PER_total_cima.csv` con las 47 columnas del
contrato, incluidas `coordinate_quality`, `qc_scope_class` e
`include_in_spatial_indicators`.

### 9.3 Step 03, cobertura contra el universo oficial

```bash
uv run python pipeline/03_coverage_assessment.py
```

Agrega la columna `year` (2025, del cambio de §8.2) y actualiza
`results/school_coverage_assessment.csv`.

### 9.4 Step 05, base K-12

```bash
uv run python pipeline/05_base_k_12_clean.py --countries PER
```

### 9.5 Step 07, contexto y base regional

```bash
uv run python pipeline/07_schools_context.py --step join --countries PER
uv run python pipeline/07_schools_context.py --step lac
uv run python pipeline/07_schools_context.py --step lac-clean
```

`join` cruza las escuelas de Perú con la grilla de población de 1 km. `lac` y
`lac-clean` reconstruyen las dos bases regionales, y para eso necesitan los
archivos `processed/` de los 22 países.

---

## 10. Paso 6. Verificación

Nada se reporta como terminado sin haber mirado estas cuatro cosas.

**1. Conteos.** La base nueva tiene que tener 54.034 filas, sin `id_centro`
duplicado, con 40.487 públicas y 13.547 privadas, 38.336 primarias, 15.613
secundaria baja y 15.413 secundaria alta.

```bash
uv run python -c "
import pandas as pd
d = pd.read_csv('data/schools/AR/PER/processed/PER_total_cima.csv', dtype={'id_centro':str}, low_memory=False)
print('filas', len(d), '| duplicados', d.id_centro.duplicated().sum(), '| year', d.year.unique())
print(d.sector.value_counts().to_string())
print(d[['nivel_primaria','nivel_secbaja','nivel_secalta']].sum().to_string())
print(d.coordinate_quality.value_counts().to_string())
print(d.include_in_spatial_indicators.value_counts(dropna=False).to_string())
"
```

**2. Calidad de coordenadas.** Comparar contra la base 2023, que tenía 53.338
escuelas repartidas así: `gps_validated` 50.684 (95,0%), `adm_mismatch` 2.209,
`cluster_centroid` 283, `geocoder_disagrees` 93, `boundary_zone` 62,
`gps_unverified` 7. Un corrimiento fuerte en esa distribución hay que explicarlo
antes de seguir. Se espera algo de aumento en `adm_mismatch` por las 1.251 altas
sin admin declarada.

**3. Tests.**

```bash
uv run pytest tests/ -q
```

Los que importan acá: `test_counts.py` (mínimo de 50.000 escuelas para Perú y
cero duplicados), `test_cima_schema.py` (columnas obligatorias) y
`test_qc_schema_v2.py` (valores admitidos en el esquema v2). Cero `failed`.

**4. El reporte de cambios.** `results/QC/PER_2025_coord_update.csv` con las
filas de desplazamiento mayor a 10 km ya revisadas, y una nota corta sobre qué se
encontró.

---

## 11. Paso 7. Entregar

En el repositorio van **solo los cambios de código**. `data/` y `results/` están
ignorados, así que los archivos producidos se devuelven por el mismo canal por el
que llegó el paquete de datos.

```bash
git add pipeline/01_build_cima.py pipeline/03_coverage_assessment.py
git commit -m "feat(PER): cosecha MINEDU 2025 como base vigente"
git push -u origin feat/per-cosecha-2025
```

El PR describe, con números:

- de qué cosecha a cuál, y con qué archivo fuente
- cuántas coordenadas se confirmaron, cuántas se actualizaron, cuántas altas y
  cuántas bajas
- las tres decisiones del §7 y quién las validó
- qué quedó pendiente de consulta con el ministerio

---

## 12. Lo que viene después (no lo hace el consultor)

Cambiar el conjunto de escuelas cambia los tiempos de viaje. Una vez cerrada la
base:

1. Step 09b (OSRM) para Perú: requiere Docker y el extracto OSM del país, o el
   notebook de Colab (`notebooks/colab_osrm_country.ipynb`).
2. Step 10b, agregación al formato SCL.
3. Export del dashboard.

---

## 13. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `KeyError: 'COD_MOD'` al leer `Padron.csv` | el archivo tiene BOM y el nombre de la primera columna queda como `ï»¿COD_MOD` | leerlo con `encoding='utf-8-sig'` |
| Acentos rotos (`P�blica`) en la consola | la terminal de Windows no está en UTF-8 | es solo la impresión en pantalla; verificar el dato en el CSV, no en la consola |
| La base nueva trae `year = 2024` y `coordinate_quality` para unas escuelas y vacío para otras | se corrió el step 01 sin mover el CIMA viejo | rehacer desde §9.0 |
| `results/cima_v2_summary.csv` quedó con un solo país | se corrió `01_build_cima.py` completo sin el bundle de los 23 países | restaurar el archivo desde el paquete de datos y usar el comando de un solo país (§9.1) |
| El step 02 no encuentra los polígonos | falta `data/bounderys/LAC/` o cambió el nombre de las carpetas `level 0/1/2` | revisar la estructura del §3.1 |
| El step 07 `lac` falla | faltan los `processed/` de los otros países | copiarlos del paquete o limitar el trabajo a Perú |
