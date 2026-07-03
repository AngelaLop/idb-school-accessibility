# Bitácora de limitaciones — indicadores de accesibilidad geográfica

**Propósito.** Registrar, a medida que se construyen y analizan los indicadores
(Step 09/10 FMM y Step 09b/10b OSRM), las limitaciones observadas — cada una con
evidencia trazable a archivo y número. Al cierre se destila en una **hoja de
limitaciones compacta y con narrativa** para la entrega BID (PR-4).

**Convención por entrada.** Qué · Dónde se observó · Evidencia (archivo + número) ·
Impacto · Destino final.

**Estado:** documento vivo. Última actualización 2026-05-18 (PR-1 y PR-2 cerrados).

---

## A. Cobertura de datos por país

### A1. CRI — escuelas privadas sin coordenadas → indicador público-only de hecho

- **Qué.** Costa Rica no tiene ninguna escuela privada georreferenciada. El
  indicador "Total" coincide exactamente con "Public"; no hay lectura privada.
- **Dónde.** PR-1, Step 09 FMM (2026-05-18).
- **Evidencia.** `data/schools/AR/LAC_schools_k12_with_context.csv`: 601 escuelas
  CRI con `sector=Private`, las 601 con `include_in_spatial_indicators=False` y
  **0 con coordenadas**. Step 09 generó 0 rasters `CRI_*_private.tif`. Step 10
  emite para CRI solo filas `Total` + `Public`, idénticas celda a celda
  (`accessibility_fmm_scl.csv`, 0 filas `Private` para CRI).
- **Causa.** CRI nunca corrió la cascada de geocoding Phase B
  (`578 fill not attempted`, CLAUDE.md). No es público-only por diseño (a
  diferencia de HND/JAM) — es un hueco de datos.
- **Impacto.** CRI accesibilidad = solo sector público. No comparable con países
  que sí tienen sector privado. "Total" sobre-representa lo público.
- **Destino.** Hoja de limitaciones BID (cobertura por país).

### A2. PER — sin tasa de pobreza ADM2 → solo quintiles RWI

- **Qué.** Perú no tiene desagregación por quintil de pobreza; solo por RWI.
- **Dónde.** PR-1, Step 10 (2026-05-18).
- **Evidencia.** `data/population/WorldPop/processed/population_grid_PER.csv`:
  `poverty_rate_adm2` con **0/429.029** celdas no nulas (`nbi_rate_adm2` sí:
  426.791). Step 10 log: `[PER] poverty-quintile cover 0.0% | RWI 99.4%`.
  `accessibility_fmm_scl.csv`: PER trae familias de quintil `rwi_q1..5` y
  `Total`, sin `quintile_1..5`.
- **Causa.** Aguas arriba — Step 06 no pobló `poverty_rate_adm2` para PER.
- **Impacto.** El corte por pobreza no existe para PER; las comparaciones de
  equidad por quintil de pobreza no incluyen PER.
- **Destino.** Hoja de limitaciones BID (cobertura por país) + revisar Step 06
  (¿el archivo de pobreza BID cubre ADM2 de PER? ¿mismatch de clave de join?).

### A3. Cobertura de proxy de riqueza heterogénea entre países

- **Qué.** Qué quintiles de riqueza están disponibles depende del país.
- **Dónde.** PR-1, Step 10 (2026-05-18).
- **Evidencia.** `accessibility_fmm_scl.csv`, familias de quintil presentes a
  nivel país: PAN solo pobreza (sin RWI); PER solo RWI (sin pobreza, ver A2);
  COL / CRI / ECU ambos.
- **Impacto.** No se puede usar un único proxy de riqueza para todos los países
  de forma uniforme. La equidad se reporta con el proxy disponible.
- **Destino.** Hoja de limitaciones BID (cobertura por país) — tabla de qué
  proxy aplica a cada país.

---

## B. Método: FMM vs OSRM

### B1. Sesgo estructurado FMM vs OSRM (no uniforme)

- **Qué.** FMM (fricción) y OSRM (ruteo en red) difieren de forma sistemática,
  no aleatoria — el sesgo depende del área (urbano/rural).
- **Dónde.** Sesión 2026-05-16, PAN + COL.
- **Evidencia.** `docs/fmm_vs_osrm_comparison_2026-05-16.md` §4 y §8.
- **Impacto.** FMM se trata como cota superior (optimista). Elegir un método
  cambia el indicador de forma predecible por tipo de zona.
- **Destino.** §8 Caveats de `fmm_vs_osrm_comparison` — **PR-3 lo extiende a 5
  países** (CRI/ECU/PER) y verifica si el patrón se sostiene.
- **Pendiente.** Confirmar con datos CRI/ECU/PER (PR-3).

---

## C. Construcción del indicador y desagregación

### C1. El quintil de riqueza no es intercambiable entre proxies (pobreza vs RWI)

- **Qué.** El indicador del quintil más pobre cambia sustancialmente según se
  use el corte por pobreza (ADM2) o por RWI (grilla ~2,4 km).
- **Dónde.** Análisis ad hoc sobre COL, FMM (2026-05-18).
- **Evidencia.** `accessibility_fmm_scl.csv`, COL nivel país, sector Total,
  comparando `quintile_1` (pobreza) vs `rwi_q1` (RWI), 18 combinaciones
  modo×nivel×banda: `quintile_1` siempre da acceso **mayor** que `rwi_q1`;
  brecha media **+7,2 pp**, siempre positiva (rango +0,3 a +20,9 pp). Peor caso:
  walking · secundaria · ≤15 min ≈ 20 pp. Correlación Pearson de los 18 valores
  = 0,998 (mismo patrón, distinto nivel).
- **Causa.** La pobreza es ADM2 (un municipio = un valor; mezcla celdas remotas
  con bien ubicadas); el RWI es grilla fina (aísla celdas genuinamente
  remotas/pobres). El corte RWI concentra mejor el grupo "peor ubicado".
  Consistente con el piloto RWI-vs-pobreza (Spearman ρ=−0,51, ~25% var.,
  `docs/exploratory_rwi_vs_poverty_COL.md`).
- **Impacto.** No reportar el quintil más pobre como un único número. Declarar
  el proxy usado; idealmente reportar ambas lecturas.
- **Destino.** Hoja de limitaciones BID (desagregación) — y §8 del doc de
  comparación si se extiende a más países en PR-3.

---

## D. Operativo y escalabilidad

### D1. OSRM no escala a los países grandes con la configuración actual

- **Qué.** El costo de OSRM (consulta por celda) crece con el grid; para los
  países grandes el enfoque actual (un servidor, un proceso) es inviable en
  tiempo razonable.
- **Dónde.** PR-2, driver `09b_osrm_build_and_run.sh` (2026-05-18).
- **Evidencia.** Duración wall-clock medida (log `_step09b_osrm_cri_ecu_per.log`):
  CRI 41.933 celdas → ~12m51s; ECU 150.583 → ~37m09s; PER 429.029 → ~2h17m
  (14:43:50 → 17:01:21). El ritmo empeora por celda al crecer el país
  (~600 celdas/s en CRI → ~245–320/s en PER). Conteo de celdas del grid:
  BRA 3.676.290, MEX 1.066.268, ARG 989.317 (los tres mayores con diferencia).
- **Impacto.** Extrapolación (estimación, no medida): MEX/ARG ~6–8 h cada uno;
  **BRA >20 h** + build de grafo OSM pesado. Una entrega OSRM de 23 países no es
  factible con la config actual. FMM en cambio es barato (Step 09: 48 rasters en
  ~3 min) y no tiene este problema.
- **Destino.** Hoja de limitaciones BID (alcance del método OSRM) + decisión de
  scope: o se limita OSRM a un subconjunto y FMM cubre el resto, o se replantea
  (más `--max-workers`, batch por matriz, máquina dedicada/Colab).

---

## Apéndice — para destilar al cierre

Al armar la hoja compacta (PR-4), agrupar en narrativa:
1. **Qué mide el indicador y qué no** (cota superior FMM, año de referencia
   poblacional 2023, vintages mezclados).
2. **Cobertura desigual entre países** (A1, A2, A3) — tabla país × proxy × sector.
3. **Sensibilidad al método** (B1) — FMM vs OSRM.
4. **Sensibilidad a la desagregación** (C1) — elección de proxy de riqueza.
5. **Restricción operativa** (D1) — alcance factible de OSRM por costo de cómputo.
