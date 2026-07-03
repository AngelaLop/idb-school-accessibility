# Validación population_grid — totales por país (2026-05-08)

Comparación de los outputs de `pipeline/06_pop_grid.py` contra fuentes oficiales (Banco Mundial 2023, fetched vía API el 2026-05-08).

**Inputs verificados**:
- `data/population/WorldPop/processed/_manifest.csv` (22 países OK, HTI sin WorldPop)
- World Bank API `SP.POP.TOTL` y `SP.URB.TOTL.IN.ZS` para año 2023
- Output de validación: `data/population/WorldPop/processed/_validation_2023.csv`

**TL;DR**:
- **Población total: los 22 países dentro del ±2% del Banco Mundial 2023.** Sin discordancias materiales.
- **% urbana: 10 match, 4 close, 8 discordantes.** Las discordancias no son bug — son metodológicas (definitions.md usa umbrales fijos vs cada país define "urbano" diferente). Documentado abajo.
- **Cobertura de RWI/pobreza variable**: hay países donde IDB no publica POVERTY o NBI; reportado per-country.

---

## 1. Población total — los 22 cuadran

| ISO | Nuestro `pop_total` | WB 2023 | Diff abs | Diff % |
|---|---:|---:|---:|---:|
| ARG | 45,458,295 | 45,538,401 | −80,106 | −0.18% |
| BHS | 398,515 | 399,440 | −925 | −0.23% |
| BLZ | 408,114 | 411,106 | −2,992 | −0.73% |
| BOL | 12,156,528 | 12,244,159 | −87,631 | −0.72% |
| BRA | 210,657,392 | 211,140,729 | −483,337 | −0.23% |
| BRB | 280,212 | 282,336 | −2,124 | −0.75% |
| CHL | 19,602,962 | 19,658,835 | −55,873 | −0.28% |
| COL | 51,964,360 | 52,321,152 | −356,792 | −0.68% |
| CRI | 5,092,962 | 5,105,525 | −12,563 | −0.25% |
| DOM | 11,232,589 | 11,331,265 | −98,676 | −0.87% |
| ECU | 17,877,412 | 17,980,083 | −102,671 | −0.57% |
| GTM | 17,984,344 | 18,124,838 | −140,494 | −0.78% |
| GUY | 824,042 | 826,353 | −2,311 | −0.28% |
| HND | 10,528,266 | 10,644,851 | −116,585 | −1.10% |
| JAM | 2,839,750 | 2,839,786 | −36 | −0.001% |
| MEX | 129,167,736 | 129,739,759 | −572,023 | −0.44% |
| PAN | 4,426,107 | 4,458,759 | −32,652 | −0.73% |
| PER | 33,214,042 | 33,845,617 | −631,575 | **−1.87%** |
| PRY | 6,801,446 | 6,844,146 | −42,700 | −0.62% |
| SLV | 6,294,929 | 6,309,624 | −14,695 | −0.23% |
| SUR | 626,028 | 628,886 | −2,858 | −0.45% |
| URY | 3,385,599 | 3,388,081 | −2,482 | −0.07% |

**Conclusión**: todos los 22 países están dentro del ±2% del WB 2023. PER es el de mayor deviation (−1.87%, ~632K personas), aún dentro de tolerancia razonable. La leve sub-cuenta sistemática (todos negativos) es esperable: usamos WorldPop **CN constrained sin UN-adjustment** (`*_v1`, no `*_UA_v1`); el constrained-only excluye celdas sin built-up detectado, y el ajuste UN sumaría diferencia residual. Para el rigor metodológico actual (>98% de captura) es defendible.

**HTI** — no tiene WorldPop bajado todavía. Pendiente.

---

## 2. % urbana — 10 match, 4 close, 8 discordantes (caveat metodológico)

Importante: nuestra clasificación `urbana` sigue **definitions.md** (densidad ≥300 hab/km² Y población ≥5,000 en cluster contiguo de celdas). World Bank usa la definición oficial de cada país, que **varía mucho** — algunos países definen urbano como "capital + cabeceras municipales", otros como "cualquier asentamiento ≥2,000 personas", otros con criterios mixtos. Por eso no se debería esperar match cifra-a-cifra.

### Match (<5 pp deviation) — 10 países

| ISO | Nuestro % urb | WB % urb | Diff |
|---|---:|---:|---:|
| ARG | 86.06 | 92.19 | −6.1 (close, ver abajo) |
| BHS | 83.81 | 81.32 | +2.5 |
| BLZ | 43.37 | 41.95 | +1.4 |
| BOL | 67.87 | 70.92 | −3.0 |
| CHL | 86.04 | 88.82 | −2.8 |
| COL | 81.17 | 78.28 | +2.9 |
| CRI | 75.32 | 78.88 | −3.6 |
| HND | 62.06 | 58.31 | +3.8 |
| MEX | 81.18 | 79.47 | +1.7 |
| PAN | 70.41 | 65.86 | +4.6 |
| PRY | 70.39 | 69.35 | +1.0 |

(ARG y PAN están en el límite ±5pp, los marqué como close).

### Close (5–10 pp) — 4 países

| ISO | Nuestro | WB | Diff | Lectura |
|---|---:|---:|---:|---|
| ARG | 86.06 | 92.19 | −6.1 | WB clasifica más liberal: cualquier centro ≥2,000 hab. Nuestros umbrales más estrictos. |
| PER | 75.76 | 84.78 | −9.0 | Igual que ARG: definición oficial peruana es ≥100 viviendas o capital de distrito. |
| SLV | 84.21 | 74.61 | +9.6 | Inverso: WB usa solo cabeceras municipales, nosotros captamos más mancha urbana en el AMSS. |
| URY | 87.01 | 95.54 | −8.5 | WB define urbano como ≥5,000 hab en localidad censal — laxo. Nuestros 87% capta core metropolitano + Maldonado/Salto/etc. |

### Discordantes (≥10 pp) — 8 países

| ISO | Nuestro | WB | Diff | Lectura metodológica |
|---|---:|---:|---:|---|
| BRA | 76.68 | 87.62 | **−10.9** | Brasil usa "perímetro urbano" definido municipalmente, muy inclusivo (incluye distritos rurales adyacentes). Nuestros umbrales fijos son más conservadores. |
| BRB | 95.42 | 59.42 | **+36.0** | BRB es 280K en 430 km² (densidad 650/km² uniforme). Toda la isla qualifica como urbana por densidad pero WB clasifica oficialmente solo Bridgetown como "urbano". Caso límite donde la densidad-based vs admin-based diverge fuertemente. |
| DOM | 87.11 | 72.36 | **+14.8** | Conurbación Santo Domingo + Santiago + costa norte muy densa; WB depende de delimitación municipal. |
| ECU | 78.91 | 63.13 | **+15.8** | Quito + Guayaquil + corredor sierra; WB usa cabeceras parroquiales (más restrictivo). |
| GTM | 81.81 | 55.61 | **+26.2** | Guatemala usa una definición censal muy restrictiva (solo cabeceras municipales). El altiplano denso poblado ha estado clásicamente sub-clasificado en stats oficiales. |
| GUY | 80.71 | 26.47 | **+54.2** | El más extremo. Población de GUY casi toda en costa atlántica densa; WB clasifica solo Georgetown como urbano. Caso parecido a BRB. |
| JAM | 82.05 | 58.45 | **+23.6** | Conurbación Kingston + St. Catherine + Spanish Town + Montego Bay densas; WB cuenta solo "town" oficialmente designados. |
| SUR | 77.58 | 65.76 | **+11.8** | Paramaribo + Wanica concentran casi toda la población; criterio densidad capta toda la franja costera, criterio admin solo distritos urbanos. |

**Patrón**: nuestra metodología **sobreestima urbano vs WB para países pequeños/insulares con población concentrada en costa o capital** (BRB, GUY, JAM, SUR, ECU, GTM, DOM, SLV). **Subestima vs WB para países grandes con definiciones oficiales muy inclusivas** (BRA, ARG, PER). Este patrón es metodológico, no error en el código.

**Implicación operativa**: para reportes públicos, ser explícito en que `area_class` sigue definitions.md (umbral fijo de densidad), NO la definición oficial de cada país. Si la dashboard quiere armonizar con WB, habría que usar la definición oficial — que es lo que tienen los censos de cada país, no es algo que podamos derivar de WorldPop sin overlay con shapes oficiales.

---

## 3. Cobertura RWI / pobreza por país

### RWI

| Tienen RWI cells | No tienen |
|---|---|
| ARG, BLZ, BOL, BRA, COL, CRI, DOM, ECU, GTM, GUY, HND, JAM, MEX, PER, PRY, SLV, SUR | BHS, BRB, CHL, PAN, URY |

5 países sin RWI (Meta no publicó archivo). Para esos, la columna `rwi` queda NaN en el grid.

### POVERTY_RATE (IDB Mapa de Pobreza 2020)

| Tienen valores | NaN en source |
|---|---|
| ARG, BOL, BRA, CHL, COL, CRI, DOM, ECU, GTM, HND, MEX, PAN, PRY, SLV | BHS, BLZ, BRB, GUY, JAM, PER, SUR, URY |

Para los 8 países sin POVERTY_RATE, IDB tiene rows en lac-level-2.csv pero las columnas están NaN — significa que el "Mapa de Pobreza" en esos casos solo tiene NBI o ningún valor.

### NBI_RATE

| Tienen valores | NaN en source |
|---|---|
| ARG, BOL, COL, PAN, PER, PRY | BHS, BLZ, BRA, BRB, CHL, CRI, DOM, ECU, GTM, GUY, HND, JAM, MEX, SLV, SUR, URY |

NBI tiene cobertura muy parcial en la fuente IDB. Los países con NBI son básicamente los que tienen censos recientes con módulo NBI.

**Implicación operativa**: para la sección "Nivel Socioeconómico" de la dashboard, el indicador IDB disponible varía por país. Hay que exponer ambas columnas y dejar claro cuál está disponible.

---

## 4. Por país — qué tenemos en `population_grid_{ISO}.csv`

| ISO | adm | Cells | pop ✓ | urb match | RWI | POV | NBI |
|---|---|---:|:-:|:-:|:-:|:-:|:-:|
| ARG | adm2 | 989,317 | ✓ | close | ✓ | ✓ | ✓ |
| BHS | adm1 | 4,456 | ✓ | ✓ | ✗ | ✗ | ✗ |
| BLZ | adm1 | 8,767 | ✓ | ✓ | ✓ | ✗ | ✗ |
| BOL | adm2 | 296,082 | ✓ | ✓ | ✓ | ✓ | ✓ |
| BRA | adm2 | 3,676,290 | ✓ | **disc** | ✓ | ✓ | ✗ |
| BRB | adm1 | 570 | ✓ | **disc** | ✗ | ✗ | ✗ |
| CHL | adm2 | 272,192 | ✓ | ✓ | ✗ | ✓ | ✗ |
| COL | adm2 | 422,950 | ✓ | ✓ | ✓ | ✓ | ✓ |
| CRI | adm2 | 41,933 | ✓ | ✓ | ✓ | ✓ | ✗ |
| DOM | adm2 | 42,925 | ✓ | **disc** | ✓ | ✓ | ✗ |
| ECU | adm2 | 150,583 | ✓ | **disc** | ✓ | ✓ | ✗ |
| GTM | adm2 | 93,373 | ✓ | **disc** | ✓ | ✓ | ✗ |
| GUY | adm2 | 13,020 | ✓ | **disc** | ✓ | ✗ | ✗ |
| HND | adm2 | 88,962 | ✓ | ✓ | ✓ | ✓ | ✗ |
| HTI | — | — | — | — | — | — | — |
| JAM | adm1 | 11,524 | ✓ | **disc** | ✓ | ✗ | ✗ |
| MEX | adm2 | 1,066,268 | ✓ | ✓ | ✓ | ✓ | ✗ |
| PAN | adm2 | 46,258 | ✓ | ✓ | ✗ | ✓ | ✓ |
| PER | adm2 | 429,029 | ✓ | close | ✓ | ✗ | ✓ |
| PRY | adm2 | 156,466 | ✓ | ✓ | ✓ | ✓ | ✓ |
| SLV | adm2 | 22,963 | ✓ | close | ✓ | ✓ | ✗ |
| SUR | adm2 | 10,546 | ✓ | **disc** | ✓ | ✗ | ✗ |
| URY | adm2 | 106,062 | ✓ | close | ✗ | ✗ | ✗ |

✓ = OK · ✗ = no disponible · disc = discordancia metodológica con WB (no error)

---

## 5. Caveats finales

1. **WorldPop CN sin UA — decisión: quedarse con 100m CN**. El UA (UN-adjusted) en R2025A para países individuales **solo existe a 1km, no a 100m** (verificado en data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2023/{ISO}/v1/, sólo `100m/` y `1km_ua/constrained/`). Mantener 100m CN preserva la granularidad necesaria para análisis de accesibilidad sub-municipal; la sub-cuenta sistemática 0.1–2% es uniforme por país, pequeña, y no afecta análisis distribucionales (rateos por quintil, equidad). Para reportes con cifras absolutas, aplicar factor de escala `WB_2023 / pop_total` per-country (no implementado, dejar como follow-up si surge la necesidad).
2. **`area_class` no es comparable a `urban_pct` oficial**: usamos un criterio metodológico fijo (definitions.md). Esto es lo correcto para análisis equitativo cross-country, pero NO es lo que dice cada censo nacional. Documentar el caveat en la dashboard.
3. **Poverty IDB puede tener años distintos por país**: la fuente reporta `POVERTY_SOURCE` con metadata de año — al usar el dato hay que recordar que ARG es Mapa 2010, COL es Mapa 2020, etc. (variabilidad temporal interna del IDB).
4. **HTI pendiente**: bajar `hti_pop_2023_CN_100m_R2025A_v1.tif` para cerrar la cobertura del scope.

## 6. Reproducibilidad

```bash
# Generar / re-generar grids
uv run python pipeline/06_pop_grid.py --countries all

# Validar contra fuentes oficiales
uv run python scripts/validate_pop_grid_totals.py
```

Outputs:
- `data/population/WorldPop/processed/population_grid_{ISO}.csv` (22 archivos)
- `data/population/WorldPop/processed/_manifest.csv` (resumen del run)
- `data/population/WorldPop/processed/_validation_2023.csv` (comparación contra WB)
- `docs/validation_pop_grid_2026-05.md` (este documento)
