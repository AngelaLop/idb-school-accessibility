# Meta RWI vs tasas de pobreza IDB — piloto Colombia (2026-05-08)

**Scope**: ejercicio exploratorio de validación cruzada entre el Relative Wealth Index (RWI) de Meta y las tasas de pobreza subnacionales del IDB, agregando RWI ponderado por población a la unidad ADM2. Define los usos defendibles de RWI en el platform y propone integración con la sección "Población, Nivel Socioeconómico y Área" de la dashboard.

**Pipeline**: `pipeline/06_pop_exploratory.py`
**Outputs**: `results/exploratory/rwi_vs_poverty/COL_*` (CSVs + 5 figuras)

---

## 1. Objetivo

Determinar si el RWI de Meta — un índice de riqueza relativa de hogares estimado por satélite + ML a resolución de ~2.4 km — puede usarse en el platform de accesibilidad escolar para LAC, y bajo qué condiciones. La pregunta operativa es: **¿RWI sustituye, complementa o no aporta** respecto a las tasas oficiales de pobreza del IDB (Mapa de Pobreza 2020 para COL)?

## 2. Metodología

### 2.1 Agregación ponderada por población

Para cada celda RWI (centroide de tile Bing quadkey nivel 14, ~2.388 km × 2.388 km), se suma la población WorldPop 100m que cae dentro de una ventana cuadrada de 0.02148° centrada en el lat/lon de la celda. Este es el `pop_per_rwi_cell`.

Las celdas se asocian espacialmente a polígonos ADM2 BID por punto-en-polígono (`predicate=within`). Para cada ADM2 se calcula:

$$\text{RWI}_{adm2} = \frac{\sum_i \text{rwi}_i \cdot \text{pop}_i}{\sum_i \text{pop}_i}$$

Esta es la agregación recomendada por Chi et al. (2022, PNAS) — el paper de origen del dataset RWI — para combinar estimaciones grid-level a unidades administrativas. Sin ponderación por población se sobrevalora la señal de celdas vacías o casi vacías.

### 2.2 Tests de validación

- **Spearman ρ** (rank correlation) intra-país, con bootstrap CI 95% (n=1,000)
- **Pearson r** complementario
- **Decile crosstab**: matriz 10×10 RWI decile (invertido) × pobreza decile, con métricas `decile_exact_match` y `decile_within_one`

Spearman es la métrica primaria porque la pregunta de política pública no es "¿coinciden los valores?" sino "¿coinciden las unidades más pobres?".

### 2.3 Per-school assignment

Para cada escuela georreferenciada del archivo CIMA se asigna la celda RWI más cercana vía cKDTree (k=1). Se reporta distancia en km. Escuelas con `rwi_dist_km > 5` (~6.8% del total en COL) se marcan sin RWI por estar fuera de la cobertura útil de Meta (zonas dispersas Amazonia/Orinoquía sin built-up detectable).

## 3. Inputs

| Fuente | Path | Stats COL |
|---|---|---|
| Meta RWI | `data/Poverty Rates/meta-rwi/COL/col_relative_wealth_index.csv` | 46,105 celdas, error medio 0.49 |
| WorldPop 100m | `data/population/WorldPop/COL/col_pop_2023_CN_100m_R2025A_v1.tif` | EPSG:4326, constrained UN-adjusted |
| Polígonos BID ADM2 | `data/bounderys/LAC/level 2/lac-level-2.shp` | 1,122 municipios COL |
| Pobreza IDB | `data/Poverty Rates/lac-level-2.csv` | 1,122 rows, fuente "Mapa de pobreza (2020)" |
| CIMA escuelas | `data/schools/AR/COL/processed/COL_total_cima.csv` | 50,033 escuelas (48,951 con coords) |

## 4. Resultados — Colombia

### 4.1 Cobertura y sanity check

- **Población WorldPop captada por celdas RWI**: 52.29 M ≈ 51.96 M oficial COL 2023 (overlap 0.6% por solapamiento de ventanas, aceptable)
- **ADM2 con al menos 1 celda RWI**: 1,101 / 1,122 (98.1%)
- **ADM2 sin cobertura** (n=21): islas (San Andrés, Providencia) y municipios pequeños en Chocó, Boyacá, Santander, Valle del Cauca — territorios rurales o costeros con baja built-up
- **Top 5 ADM2 más ricos** (RWI weighted): Medellín, Sabaneta, Envigado, Itagüí (Valle de Aburrá metropolitano) + Villa del Rosario (metro Cúcuta) — ranking validado cualitativamente
- **Bottom 5**: La Victoria, Puerto Alegría, Puerto Arica (Amazonas) + Papunahua, Yavaraté (Vaupés) — territorios indígenas amazónicos remotos, ranking esperable

### 4.2 Correlación RWI vs pobreza IDB (n=1,056 ADM2 con ≥5 celdas y pop>0)

| Target | Spearman ρ | 95% CI | Pearson r | Decile exacto | Decile ±1 |
|---|---:|---|---:|---:|---:|
| POVERTY_RATE | **−0.507** | [−0.548, −0.460] | −0.542 | 17.5% | 41.9% |
| NBI_RATE | **−0.435** | [−0.483, −0.375] | — | 19.2% | 40.1% |

**Interpretación**:
- Ambas correlaciones son negativas (esperado), altamente significativas (p < 1e-69), y de magnitud moderada (ρ² ≈ 0.25).
- RWI explica ~25% de la varianza en el ranking de pobreza IDB. El otro 75% no.
- **Hallazgo contraintuitivo**: RWI correlaciona más fuerte con pobreza monetaria (POVERTY_RATE, ρ=−0.51) que con NBI multidimensional (ρ=−0.44). La hipótesis a priori era la inversa (RWI mide asset wealth, conceptualmente más cerca de NBI). Posible explicación: NBI 2020 en COL está saturada/comprimida (mean 22.9%, rango 1.6–96), reduciendo varianza disponible para correlacionar.
- **Decile within ±1 = 42%** vs random 30%: el ranking grueso (cuartil-tipo) sí se mantiene, pero el match exacto a decile es modesto. **RWI no reemplaza pobreza IDB** para clasificación individual de unidades admin.

### 4.3 Patrón espacial de discordancia (figura 3)

Cuando se mapea `(decile pobreza IDB) − (decile RWI invertido)`, emergen dos clusters:

- **Costa caribe (Bolívar, Magdalena, Norte de Santander)**: RWI alto, pobreza alta — built-up visible desde satélite (casas/infra) pero economía informal y vulnerable. Ejemplos: Clemencia (RWI +0.50 / pobreza 70%), Sitionuevo (+0.50 / 72%), Puerto Santander (+0.60 / 64%).
- **Andes rurales (Cundinamarca, Santander, Caldas, Risaralda)**: RWI bajo, pobreza baja — población dispersa sin built-up visible pero economía agraria estable y propiedad de tierra. Ejemplos: San Cayetano (RWI −0.31 / pobreza 32%), Marulanda (−0.44 / 35%).

**Conclusión metodológica**: RWI no mide "pobreza", mide "infraestructura built-up visible desde satélite con calibración a wealth de hogares". Las dos no son equivalentes; la discordancia es informativa, no error.

### 4.4 Heterogeneidad sub-municipal (figura 4 — Bogotá D.C.)

El IDB asigna a Bogotá una sola tasa de pobreza: **24.1%**. El RWI muestra dentro de Bogotá:
- 185 celdas internas
- Rango: **−0.98 (Ciudad Bolívar/Usme/sur) a +1.52 (Chapinero/Usaquén/norte)**
- Spread = 2.5 desviaciones estándar nacionales

**Esta es la única dimensión que RWI aporta de manera única**: granularidad sub-municipal en grandes ciudades donde IDB no tiene resolución.

### 4.5 Estratificación por sector (per-school, figura 5)

48,951 escuelas COL con coordenadas, 45,643 dentro de 5 km de una celda RWI (93.2%). Análisis por quintil RWI nacional:

**Composición sector × quintil RWI**:

| Quintil | % Public | % Private | n |
|---|---:|---:|---:|
| Q1 más pobre | **99.7%** | 0.3% | 9,155 |
| Q2 | 99.5% | 0.5% | 9,123 |
| Q3 | 98.0% | 2.0% | 9,110 |
| Q4 | 79.5% | 20.5% | 9,130 |
| Q5 más rico | **51.9%** | **48.1%** | 9,125 |

En zonas más pobres no existe oferta privada. En zonas más ricas la mitad es privada. Cuantifica numéricamente la segregación del sistema educativo COL.

**Cobertura de educación media (nivel_secalta) en escuelas públicas**:

| Quintil | % públicas con secalta |
|---|---:|
| Q1 más pobre | **8.7%** |
| Q2 | 11.4% |
| Q3 | 17.0% |
| Q4 | 30.8% |
| Q5 más rico | **45.8%** |

**Brecha 5×**: en zonas pobres apenas 1 de cada 11 escuelas públicas ofrece secundaria alta. En zonas ricas casi 1 de cada 2.

## 5. Caveats y limitaciones

1. **RWI mide asset wealth, NO pobreza monetaria**. Las dos métricas son conceptualmente distintas. Esperar correlación moderada (ρ ≈ 0.5–0.7), no perfecta. Chi et al. (2022) reportan correlaciones similares (0.7–0.9 contra DHS dentro-país); en LAC las correlaciones tienden a ser más bajas que en África por varias razones documentadas en la literatura (mayor formalidad económica, urbanización más alta).
2. **RWI es relativo dentro de país, no comparable entre países**. La media es ~0 por construcción nacional.
3. **3,308 escuelas COL están a >5 km de cualquier celda RWI**: zonas sin built-up suficiente para que Meta genere predicción. Concentradas en Amazonas, Vichada, Vaupés, Guainía. **No tienen RWI asignable**; deben marcarse como `rwi=null` y excluirse de análisis estratificados.
4. **21 ADM2 sin celdas RWI dentro**: islas y municipios pequeños rurales. Cobertura efectiva ADM2 = 98.1%.
5. **Saturación de NBI**: en COL 2020 la NBI está concentrada en valores bajos-medios; correlaciones más estables se obtendrán en países con mayor varianza en NBI.

## 6. Uso recomendado en el platform

### 6.1 Sí (defendible)

- **Métrica complementaria de contexto socioeconómico relativo**, expresada como z-score nacional o quintil/decile. Nunca como porcentaje, nunca como categoría binaria "pobre/no pobre".
- **Granularidad sub-municipal en grandes ciudades**: Bogotá, Medellín, Cali, Barranquilla, Cartagena, Cúcuta. Permite mapas internos donde IDB no tiene resolución.
- **Estratificación de métricas de accesibilidad**: "% estudiantes a 15 min de su escuela más cercana" → desagregable por quintil RWI → expone brecha de equidad.
- **Targeting / hotspots**: identificar áreas con simultáneamente bajo RWI Y baja accesibilidad → priorización de inversión.
- **Etiqueta por escuela**: cada escuela inherits un `rwi_school` continuo + quintil nacional para análisis distribucionales.

### 6.2 No (no defendible)

- Reemplazar la tasa de pobreza IDB en outputs oficiales.
- Clasificar binariamente "esta escuela atiende a niños pobres / no pobres".
- Estimar tasas de pobreza absolutas para unidades sub-municipales.
- Comparar entre países (RWI es relativo intra-país).

### 6.3 Convención propuesta para la dashboard

Sección **"Población, Nivel Socioeconómico y Área"** (nueva):
1. **Población**: WorldPop totales 2023 (ya disponible, columna existente).
2. **Nivel Socioeconómico**: dos columnas paralelas:
   - **Tasa de pobreza IDB** (oficial, fuente: lac-level-2.csv) — usar siempre que haya valor al nivel municipio/provincia.
   - **RWI (z-score nacional + quintil)** — para granularidad sub-municipal y per-school.
3. **Área** (urbana / no urbana / dispersa): pendiente de implementar siguiendo `definitions.md` (umbrales de densidad y población mínima sobre WorldPop). Define el filtro geográfico canónico del platform.

Las tres dimensiones se mostrarían lado a lado, con la convención clara: pobreza IDB es la métrica oficial; RWI es complementaria con caveat explícito en el tooltip.

## 7. Próximos pasos

- **Replicar el ejercicio** para los 15 países restantes con RWI disponible (BLZ, BOL, BRA, CRI, DOM, ECU, GTM, GUY, HND, MEX, NIC, PER, PRY, SLV, SUR). Faltan RWI para 5 países publicables (BRB, CHL, JAM, PAN, URY).
- **Implementar columna `area`** (urbana/no-urbana/dispersa) según `definitions.md` sobre WorldPop 100m. Asignación per-school + agregación a ADM2.
- **Integración a dashboard**: definir contrato de payload (`rwi_school`, `rwi_quintil_nacional`, `pop_total`, `area`) y wire-through al frontend.
- **Recalibración con NBI**: probar con países donde NBI tenga más varianza (Centroamérica, Andes).

## 8. Referencias

- **Chi, G., Fang, H., Chatterjee, S., & Blumenstock, J. E. (2022)**. Microestimates of wealth for all low- and middle-income countries. *PNAS*, 119(3), e2113658119. — Origen del dataset RWI; reporta correlaciones de 0.7–0.9 contra DHS dentro-país; recomienda agregación ponderada por población.
- **Jean, N., Burke, M., Xie, M., Davis, W. M., Lobell, D. B., & Ermon, S. (2016)**. Combining satellite imagery and machine learning to predict poverty. *Science*, 353(6301), 790–794. — Marco metodológico base del enfoque grid-based wealth/poverty estimation.
- **Aiken, E., Bellue, S., Karlan, D., Udry, C., & Blumenstock, J. E. (2022)**. Machine learning and phone data can improve targeting of humanitarian aid. *Nature*, 603, 864–870. — Caso operativo de uso de RWI agregado para targeting de transferencias en Togo.
- **Pokhriyal, N., & Jacques, D. C. (2017)**. Combining disparate data sources for improved poverty prediction and mapping. *PNAS*, 114(46), E9783–E9792. — Combinación de fuentes dispares para mapear pobreza, validación contra DHS.

## 9. Reproducibilidad

```bash
# Pipeline completo COL
uv run python pipeline/06_pop_exploratory.py --countries COL --step all

# Por etapas
uv run python pipeline/06_pop_exploratory.py --countries COL --step aggregate
uv run python pipeline/06_pop_exploratory.py --countries COL --step tests
uv run python pipeline/06_pop_exploratory.py --countries COL --step viz
uv run python pipeline/06_pop_exploratory.py --countries COL --step schools
```

**Outputs generados**:
- `results/exploratory/rwi_vs_poverty/COL_rwi_adm2_aggregated.csv` (1,101 rows × 10 cols)
- `results/exploratory/rwi_vs_poverty/COL_rwi_vs_poverty_tests.csv` (2 rows: POVERTY + NBI)
- `results/exploratory/rwi_vs_poverty/COL_rwi_vs_poverty_merged.csv` (1,056 rows post-filtro)
- `results/exploratory/rwi_vs_poverty/COL_schools_rwi.csv` (48,951 rows)
- `results/exploratory/rwi_vs_poverty/figures/COL_scatter_rwi_vs_poverty.png`
- `results/exploratory/rwi_vs_poverty/figures/COL_decile_heatmap.png`
- `results/exploratory/rwi_vs_poverty/figures/COL_choropleth_discordance.png`
- `results/exploratory/rwi_vs_poverty/figures/COL_bogota_intra_municipal_rwi.png`
- `results/exploratory/rwi_vs_poverty/figures/COL_schools_by_rwi_quintile.png`
- `results/exploratory/rwi_vs_poverty/figures/COL_top20_outliers.csv`
