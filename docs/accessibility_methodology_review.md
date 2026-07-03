# Accessibility methodology review — friction surfaces vs network routing for 21-country LAC platform

**Branch:** `friction-surfaces` · **Author:** Angela Lopez Sanchez (IADB consultancy) · **Status:** Draft for review · **Date:** 2026-05-13

> This document compares four families of accessibility methods candidate for the LAC school accessibility platform: cost-distance FMM on raster friction surfaces, multimodal network routing (r5r / r5py), OSM road routing (OSRM via UrbanPy), and isochrone overlays via openrouteservice (HeiGIT / HDX). It draws on (a) Angela's Panama pilot, (b) Castro, Giambruno & Ortega (2024) on Amazon basin schools, (c) the ipeaGIT *Introduction to Urban Accessibility* book, (d) HeiGIT's HDX *Accessibility Indicators* publications for Chile and Colombia plus their four cited papers, and (e) **a fresh head-to-head FMM-vs-r5py benchmark on PAN top-5 districts run 2026-05-13** (§2.2). Empirical claims are cited to their source; gaps in evidence are flagged as such. The platform commits to reporting **walking and motorized access for each of the three educational levels** (primaria, sec baja, sec alta) for every country — six indicator combinations per country, matching the PAN pilot's Series C/D matrix exactly. The recommendation is not a single method winner; it is a tiered stack with FMM as the scalable default, **required** network-routing reality checks in the 4-5 well-mapped LAC countries, and HDX as a free external benchmark for CHL/COL.

---

## 1 What we are choosing between

The downstream Step 09 (TBD) needs to convert (schools, population, geography) into a travel-time-to-nearest-school metric per 1 km cell and aggregate it by ADM2. Four method families are realistically scalable to 21+ countries:

| Family | Core idea | Reference local code / lit |
|---|---|---|
| **A. FMM on raster friction** | Eikonal solver propagates a wavefront from school cells across a friction raster (min/m). Off-road movement allowed at low speed. | `accesibility/utils/mcp/travel_time.py:148-224` (PAN pilot); Weiss et al. 2018, 2020 (MAP rasters) |
| **B. Network routing on OSM via r5r / r5py / OSRM** | Build a road graph, compute shortest paths from each origin to nearest school. r5r is RAPTOR-based and multimodal; OSRM is motorized/walking with contraction-hierarchies. | `accesibility/utils/r5py/router.py` (PAN, never executed); Castro et al. 2024 (OSRM + UrbanPy, 5 Amazon countries) |
| **C. Isochrones on OSM via openrouteservice (HeiGIT)** | Compute travel-time or distance polygons (isochrones) around each facility, overlay with population raster, sum people inside each ring. Engine is HeiGIT's openrouteservice (OSS, Heidelberg). | HDX dataset *Chile-/Colombia-Accessibility-Indicators*, HeiGIT 2026-02 release |
| **D. Hybrid / per-country switching** | Use the method best suited to each country's OSM quality and terrain. | Not piloted yet |

The platform already provides the inputs both families need: school points in `LAC_schools_k12_clean.csv` (529 k schools, 21 countries), 1 km population/equity grid in `population_grid_{ISO}.csv`, and the BID ADM0/1/2 polygons. With this PR (PR1) we add the **two-mode** MAP friction surfaces clipped to all 23 PIPELINE_ISOS — `{ISO}_walking_2019.tif` and `{ISO}_motorized_2019.tif` per country (plus the 2015 travel-speed reference). That is everything family A needs to compute walking *and* motorized travel times in Step 09.

---

## 2 Evidence from the Panama pilot (FMM and r5py)

The PAN pilot covers **two method families** in the same country with the same school and population inputs, allowing a direct method-vs-method comparison:

- **Family A — FMM on raster friction**: 32 scenarios (`accesibility/scenarios/config.py:154-199`), de-duplicated to 16 unique FMM jobs keyed by `(friction_key, age_group)` (`run_scenarios.py:112-137`). Census + WorldPop populations × MAP + OSM-derived friction × walking + motorized × 4 age groups.
- **Family B — r5py network routing**: walking-only, top-5 districts by school-age population (Panama, Arraiján, Colón, La Chorrera, San Miguelito). Code in `accesibility/utils/r5py/router.py` was originally written but never executed; **the run was completed 2026-05-13** using a modernised driver (`scripts/run_r5py_pan_top5.py`) under the r5py 1.x API (`TravelTimeMatrix`). Output lives in `accesibility/results/r5py/`.

### 2.1 FMM 32-scenario matrix — national aggregates

Headline FMM results for all school-age (6–17), aggregated nationally from `accesibility/results/district_tables/`:

| Scenario | pop_source | friction | mode | ≤15 min | ≤30 min | ≤60 min | nodata |
|---|---|---|---|---:|---:|---:|---:|
| A1 | Census | MAP | motorized | 94.7 % | 96.1 % | 96.6 % | 3.1 % |
| A2 | Census | MAP | walking | **82.4 %** | 94.3 % | 96.4 % | 3.1 % |
| A4 | WorldPop | MAP | walking | 76.7 % | 88.7 % | 93.1 % | 3.7 % |
| B1 | Census | OSM | motorized | 96.5 % | 96.8 % | 96.9 % | 3.1 % |
| B2 | Census | OSM | walking | 83.8 % | 95.0 % | 96.7 % | 3.1 % |

Three observations:

1. **MAP vs OSM (as raster friction) differs by ~1.5 pp at ≤15 min walking** (A2 vs B2). Both fed to the SAME FMM eikonal solver — only the friction layer differs. Panama has dense OSM coverage in the metro corridor and Caribbean coast; in that geography the two friction surfaces are nearly interchangeable. This is *not* a generalisable result: a country with sparse OSM (HTI, GUY, much of rural BOL) would diverge much more.
2. **Census vs WorldPop differs by ~5.7 pp** (A2 vs A4). This is *not* a method gap; it is a population-source gap. Census households are georeferenced near roads (88.7 % of school-age georef rate, `PAN_PILOT_REFERENCE.md:75`), inflating accessibility especially in Ngäbe-Buglé and Kuna Yala where the comarca Census shows >90 % within 30 min and WorldPop shows 42–88 % (`PAN_PILOT_REFERENCE.md:111-112`). For 21-country cross-country comparability WorldPop is the only realistic source — most countries don't publish georeferenced microdata. The Panama pilot validates that WorldPop is a credible substitute (r=0.984 at corregimiento level, `PAN_PILOT_REFERENCE.md:71`).
3. **3.1 % nodata** in the Census-based scenarios corresponds to 5 districts where every population cell sits on a MAP nodata cell (`PAN_PILOT_REFERENCE.md:101-109`). All five are in Darién or indigenous comarcas. **MAP and OSM both lack coverage there** (`PAN_PILOT_REFERENCE.md:121`). Network routing has the same gap with the additional quirk that off-road population is "unreachable" rather than "slow" — see §2.2 finding 3.

### 2.2 FMM vs r5py head-to-head on PAN top-5 districts (the empirical bias, 2026-05-13)

A modernised driver (`accessibility_platform/scripts/run_r5py_pan_top5.py`) was run against the same inputs the PAN pilot uses (OSM PBF `panama-260114.osm.pbf`, the 247 k georeferenced school-age households in the top-5 districts, schools from `PAN_all_schools.geojson` plus a 15 km buffer per district). Walking-only, `max_time=60min`, `percentiles=[50]`, stratified sample of up to 8000 households per district.

The full run took **~6 minutes** for all 5 districts (the contracted network is cached in `~/AppData/Local/r5py/`). Compute is therefore NOT the bottleneck — engineering surface area (per-country PBF, Java runtime) is.

Compared head-to-head against FMM Series A2 (`accesibility/results/district_tables/A2_census_walking_all.csv`, Census × MAP × walking) at the same 5 `cod_dist` codes:

| cod_dist | district | FMM ≤15 | r5py ≤15 | **Δ** | FMM ≤30 | r5py ≤30 | **Δ** | FMM ≤60 | r5py ≤60 | **Δ** |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0808 | Panama        | 92.7 % | 52.1 % | **−40.6** | 99.5 %  | 82.1 % | **−17.4** | 100.0 % | 97.6 % | −2.4 |
| 1301 | Arraiján      | 87.9 % | 45.9 % | **−42.0** | 98.7 %  | 77.4 % | **−21.3** | 100.0 % | 95.6 % | −4.4 |
| 0301 | Colón         | 82.6 % | 54.1 % | **−28.5** | 98.6 %  | 82.8 % | **−15.8** | 99.7 %  | 98.4 % | −1.2 |
| 1307 | La Chorrera   | 84.6 % | 36.7 % | **−47.9** | 96.5 %  | 70.5 % | **−26.0** | 100.0 % | 91.5 % | −8.5 |
| 0810 | San Miguelito | 97.2 % | 72.4 % | **−24.8** | 100.0 % | 86.8 % | **−13.2** | 100.0 % | 99.8 % | −0.2 |
| | **mean \|Δ\|** | | | **36.8** | | | **18.7** | | | **3.4** |

**Three findings that change the framing of this whole document:**

1. **FMM-MAP-walking systematically overestimates accessibility relative to r5py-walking, by a lot, at the ≤15 min band.** The mean absolute divergence is **36.8 percentage points** — roughly the difference between "near-universal access" and "a typical Latin American mid-tier urban area". This is not noise, not method variance — it is a structural bias and it goes the same direction in all 5 districts.

2. **The bias shrinks with the time threshold.** At ≤30 min the gap drops to 18.7 pp; at ≤60 min to 3.4 pp. The two methods agree on the "how many people are within an hour" question. They diverge sharply on "how many are within fifteen minutes". Across the 5 districts the range of |Δ| is 24.8–47.9 pp at ≤15 min, 13.2–26.0 pp at ≤30 min, and only 0.2–8.5 pp at ≤60 min.

3. **The likely mechanism is grid smoothing.** FMM operates on a 1 km raster: a single road segment crossing one cell makes that whole cell "fast", and the eikonal wavefront propagates with anisotropic-corrected metric distance regardless of road topology. r5py walks the OSM graph: a 1 km walk from origin to school is only ≤15 min if there's an actual ~12 min walking route, respecting one-way constraints, dead-ends, and the absence of pedestrian rights-of-way through private parcels. In dense urban areas (where OSM is well-mapped) the network-routing answer is closer to ground truth and FMM looks optimistic.

**What this means for the LAC platform:**

- The PAN headline number from §2.1 — "82.4 % at ≤15 min walking, MAP A2" — should be read as an **upper bound**, not as a faithful estimate, in dense urban districts. A network-routing equivalent would put the same number probably in the 45-60 % range. The difference is policy-meaningful: under FMM you would conclude "most people are within walking distance"; under r5py you would conclude "barely half are".
- In sparse-OSM contexts (rural BOL, HTI, GUY) the bias likely *reverses* or shrinks: r5py reports "unreachable" for many origins because the graph is incomplete; FMM at least gives a number using off-road speeds. We do not have local evidence for that yet — only Petricola 2022's verbatim caveat ("areas not reached by a road … considered out of range, while the raster-based method will consider open space routing"). See §8 for the planned follow-up in GUY.
- **This is a heterogeneous-bias problem, not a winner-loser problem.** FMM overshoots in dense-OSM urban areas; network routing undershoots in sparse-OSM rural areas. No single method gives the right answer across the 23 LAC countries.

### 2.3 What the pilot still does NOT answer

- Whether the bias has the same direction and magnitude for **motorized travel** (only the walking comparison has been run as of 2026-05-13).
- Whether the bias **reverses in sparse-OSM countries** (per Petricola 2022's qualitative claim, but no local LAC evidence yet).
- Whether the 5 NoData districts can be rescued by manual road digitisation in OSM (compute is not the bottleneck — local data collection is).
- Whether the cell-size smoothing in FMM (~1 km, isotropic-corrected, `travel_time.py:51-57`) misses small-scale heterogeneity that matters for primary-school catchments at sub-district granularity.

---

## 3 Evidence from the literature

### 3.1 Castro, Giambruno & Ortega (2024) — *El largo camino a la escuela: análisis de accesibilidad a centros educativos públicos en asentamientos de la Amazonía*

Local copy: `C:\Users\lopez\github\IDB\lit\Acceso a la educación en la Amazonía Urbana.pdf`

**Scope.** 5 LAC countries (BRA, BOL, COL, ECU, PER), Amazon basin only, public schools only, walking only, 1 km² grid, 3 levels (primaria / sec baja / sec alta). Population from WorldPop ages 5-9 / 10-14 / 15-19. Schools from CIMA (same source as this platform).

**Method.** UrbanPy package built on OSRM, OSM data from Geofabrik. Cite (footnote 2, p. 4): "Se utilizó OSRM con datos viales de OpenStreetMap extraídos de Geofabrik para modelar rutas. En Brasil se emplearon subdivisiones regionales. **El enrutador solo considera vías internas a cada país, por lo que puede omitir accesos más cercanos en zonas fronterizas o entre subregiones.**" Areas dispersas (<200 hab AND <150 hab/km²) are excluded from analysis.

**Headline result** (Figure 2, p. 6): for the Amazon-basin average across 5 countries, walking access to nearest public school by level —

| Level | ≤15 min | ≤30 min | >30 min |
|---|---:|---:|---:|
| Primaria | 72 % | 88 % | 12 % |
| Sec baja | 64 % | 84 % | 15 % |
| Sec alta | 56 % | 80 % | 20 % |

Country-level (Fig 3, p. 7): Brazil ~80 % at ≤15 min for primaria, Bolivia worst. 3 % of secundaria alta students travel >2 hours, 1 % have no terrestrial access at all.

**Caveats the authors put on the record** (p. 5):
- "Aproximación al acceso físico potencial, no debe interpretarse como una medición del acceso efectivo."
- Climate (flooding, heat, rain) can multiply travel times.
- Capacity and quality of schools not modeled.
- Areas dispersas excluded.

**Why this matters for our decision.** Castro et al. demonstrate that OSRM + UrbanPy is *operationally feasible* across 5 LAC countries with the same school dataset we will use. The reported numbers are lower than PAN A2 (72 % vs 82 % at ≤15 min primaria), which is consistent with Amazon-specific selection: PAN is national and includes metro corridors where access is near-universal. The Castro authors *do not* compare OSRM against FMM or r5py.

### 3.2 Pereira, Herszenhut, Saraiva et al. — *Introduction to Urban Accessibility* (IPEA, ongoing)

Local copy: `C:\Users\lopez\github\IDB\lit\Introduction_urban_accessibility_Book.pdf`. Rendered online at https://ipeagit.github.io/intro_access_book/ (table of contents fetched 2026-05-13).

**Methods covered.** Chapter 3 (https://ipeagit.github.io/intro_access_book/3_calculando_acesso.en.html) teaches *only* `{r5r}` for routing and `{accessibility}` for indicator computation. OSRM, OpenTripPlanner, hereR are not introduced. **Raster cost-distance methods (FMM, MAP friction) are not discussed anywhere in Chapter 3.**

**One quantitative claim** (Section 3.1.3): "{r5r} can calculate travel time matrices between 6 and 200 times faster than other multimodal routing softwares" (citing Higgins et al. 2022). No accuracy or route-quality comparison is offered.

**OSM-coverage caveat** (Section 3.1.2, textbox): "OpenStreetMap (OSM) is a geographic database … maintained by a community of volunteers, so the coverage and quality of its data can widely vary between regions … OSM data tend to have better coverage and quality in more developed regions and in urban areas with large populations."

**Accessibility measures** (Chapter 2): four place-based families are defined — minimum cost, cumulative opportunities, gravity, floating catchment. The chapter calls cumulative opportunities "easy to calculate and communicate" and flags gravity as requiring a calibrated decay function (β) from household-travel surveys. The IADB platform will need cumulative opportunities (≤15 / ≤30 / ≤60 min, matching Castro and the Panama pilot) — gravity is not realistic at 21-country scale without country-specific decay calibration.

**What the book does not provide**: any LAC- or Brazil-specific empirical comparison between r5r and OSRM or FMM; any recommendation on choosing a routing engine; any guidance on how to handle OSM gaps. The book is instructional, not comparative.

### 3.3 HeiGIT *Accessibility Indicators* on HDX (already published for CHL and COL)

Source: Humanitarian Data Exchange, dataset *Chile — Accessibility Indicators* (id `9013c094-036e-42e8-95d8-82b1c99a4e99`) and the sibling *Colombia — Accessibility Indicators*. Producer: **HeiGIT — Heidelberg Institute for Geoinformation Technology**, the applied-research GmbH spun out of GIScience Heidelberg. License: **CC BY-SA**. Update frequency: every six months. Last updated 2026-02-25. Local copies: `C:\Users\lopez\Downloads\metadata-chile-accessibility-indicators.csv` (full CKAN metadata, 14 KB) and `Colombia - Accessibility Indicators _ Humanitarian Dataset _ HDX.html` (snapshot in repo root). The two countries share identical schema, resource layout, and methodology — verified by `grep "access/col/" → access/col/` in the COL HTML matching the `access/chl/` pattern in the CHL metadata.

#### 3.3.1 What the dataset actually contains

Per country the publisher ships nine resources: three services (`education`, `hospitals`, `primary_healthcare`) × three formats (`*.gpkg` raw isochrone polygons, `*_long.csv`, `*_wide.csv` aggregated to ADM 1-4). CHL totals: education 520 MB gpkg + 161 KB long-CSV + 90 KB wide-CSV; hospitals 169 MB gpkg + 549 KB long + 172 KB wide; primary_healthcare 291 MB gpkg + 600 KB long + 180 KB wide. The long-CSV schema (from the CKAN `notes` field, verbatim):

> "**name**: Region or country name. **iso**: ISO3 country code. **id**: Unique identifier for the administrative unit. **country**: ISO3 country code. **admin_level**: Administrative level of the unit. **category**: Service category — `education`, `hospitals` or `primary_healthcare`. **range_type**: Method used for the catchment zone — `distance` or `time`. **range**: Distance (in meters) or Time away (in seconds) from schools used to generate the polygon. **population**: Total population within the specified range. **school_age_population**: Number of school-age individuals within the range. **school_age_population_share**: Cumulative percentage of school-age population. **school_age_population_interval**: Incremental school-age population added in the current distance band. **school_age_population_interval_share**: Proportion of new school-age population in the current interval. **population_share**: Cumulative percentage of total population. **population_interval**: Incremental population added in the current distance band. **population_interval_share**: Share of the total population represented by the current interval."

The `*_interval` columns explicitly hold the *non-cumulative* delta-by-band, alongside the cumulative `_share` columns — this is the same convention as Castro et al.'s ≤15 / 15-30 / 30-60 bins. The schema directly supports the dashboard's planned ≤15/≤30/≤60 banding.

#### 3.3.2 Method, verbatim from HDX metadata

> "To assess accessibility to education and healthcare, we use travel-time isochrones — polygons representing areas reachable within a given time or distance **by car**. We overlay these isochrones with WorldPop population data, which provides 100m-resolution estimates. This allows us to calculate the population within time intervals from 10 to 120 minutes away from hospital services and **distance intervals from 5 to 50 km away from schools**. The unit of analysis is defined by geoboundaries country borders, and where available we also summarise results at finer administrative levels (ADM 1-4)."

So for the *education* service this is *not* time-binned — it is **straight-line driving distance binned at 5, 10, …, 50 km from each school**, then overlaid on WorldPop. The car-mode assumption applies even to schools. For *health* (hospitals + primary_healthcare) the bins are time-based (10 min – 2 h) and again car-mode.

#### 3.3.3 Engine: openrouteservice on a forked GraphHopper 4.0

From the GIScience GitHub README of the engine (https://github.com/GIScience/openrouteservice): ORS is **"a forked and edited version of graphhopper 4.0"**, dual-licensed under **GPL-3.0 / LGPL-3.0**. GraphHopper supports Dijkstra and A* with bidirectional variants and Contraction Hierarchies (CH) preparation — the same algorithmic family OSRM and r5r build on. The Isochrones service is implemented via shortest-path-tree expansion on the contracted graph (`graphhopper/docs/isochrone/java.md`, GraphHopper master).

So the *engine* class (Family C in §1) is methodologically a sibling of Family B (OSRM in Castro et al.; r5r/r5py in the ipeaGIT book). All three depend on:
- An OSM PBF as the road graph.
- A per-mode speed table by `fclass` tag.
- A pre-contracted graph for fast many-to-many.

The differences are practical, not algorithmic: ORS exposes a polished isochrone endpoint and is what HeiGIT operates as a production service; OSRM is C++-fast for shortest-path matrices; r5r adds GTFS-multimodal and the RAPTOR transit algorithm. None of the three is fundamentally more accurate than the others on OSM-only walking/driving access.

#### 3.3.4 The four methodological references HeiGIT cites — read them carefully

| Paper | Domain | Engine used | Local file |
|---|---|---|---|
| Geldsetzer, Reinmuth, Ouma, Lautenbach et al. 2020 *Lancet Healthy Longevity* | sub-Saharan Africa, healthcare for 60+ | **AccessMod 5 — raster cost-distance**, ORS used only as validation against Google Maps at 40 random points | https://pmc.ncbi.nlm.nih.gov/articles/PMC7574846/ |
| Petricola, Reinmuth, Lautenbach et al. 2022 *Int J Health Geographics* | Cyclone Idai Mozambique 2019 | openrouteservice isochrones, walking AND driving profiles | https://pmc.ncbi.nlm.nih.gov/articles/PMC9559768/ |
| Klipper, Zipf, Lautenbach 2021 *AGILE-GISS* | Jakarta floods | OpenRouteService network analysis | https://agile-giss.copernicus.org/articles/2/4/2021/ |
| Ruiz Sánchez, Reinmuth, Albornoz, Lautenbach, Zipf 2025 *AGILE-GISS* | Porto Alegre flood 2024 | OpenRouteService + edge betweenness centrality | https://agile-giss.copernicus.org/articles/6/10/2025/ |

Three observations from reading these:

1. **Geldsetzer 2020 — the foundational paper in HeiGIT's own citation list — does NOT use openrouteservice as its primary method.** It uses *AccessMod 5* (raster cost-distance, WHO-supported software) with the speed table: "100 km/h to motorways and primary roads, 50 km/h to secondary roads, and 30 km/h to tertiary roads. Barren land and built-up areas were assigned a travel speed of 5 km/h and forests a 2 km/h walking speed." That is Family A (raster cost-distance), not Family C (isochrones). They only invoke ORS for validation: "selecting at random 40 locations in sub-Saharan Africa" and comparing travel-time estimates from AccessMod, ORS, and Google Maps. So HeiGIT's own foundational publication validates that raster cost-distance is the credible workhorse for sub-Saharan healthcare access at 1×1 km resolution.

2. **Petricola 2022 explicitly recommends a hybrid raster + network approach.** Two verbatim quotes that matter for our decision:
   > "All areas not reached by a road in the network model will be considered out of range, while the raster-based method will consider open space routing."
   > "[future work should consider] combining raster- and network-based methods to overcome their respective limitations."

   In other words: the HeiGIT team *themselves* document that pure network routing (their own product family) loses information off-road, and recommend complementing it with raster methods (our planned Tier 1). This is empirical, peer-reviewed evidence from the same group that ships the HDX dataset.

3. **Klipper 2021 and Ruiz Sánchez 2025 are flood-impact studies, not methodology papers.** They demonstrate ORS in disaster-response contexts (Jakarta 2013, Porto Alegre 2024). Neither benchmarks ORS against alternative methods.

#### 3.3.5 Documented limitations (HDX caveats verbatim)

> "OSM Completeness: This analysis relies on OpenStreetMap (OSM) data. While OSM is the most complete open map of the world, data quality varies significantly by region. In areas with unmapped roads or facilities, accessibility may be underestimated.
> Population Estimates: Population counts are derived from WorldPop top-down estimates (constrained). These are statistical models based on census projections and satellite imagery, not direct census counts, and may contain inaccuracies at the local pixel level.
> Travel Time Assumptions: Isochrones are calculated using standard vehicle speeds for different road types. These models do not account for real-time traffic, seasonal weather conditions (e.g., flooding), or road surface degradation.
> Boundary Precision: Administrative boundaries are sourced from geoBoundaries. These may differ slightly from official government demarcations or other schemas."

To these the cited papers add explicit caveats:
- Petricola 2022: "the healthcare facilities list within OSM might not be complete as well as the classification accuracy of healthcare facilities cannot be guaranteed"; "an exhaustive and reliable list of damaged health facilities was not available" — i.e. validation is opportunistic, not systematic. They also document a 1.9× post-cyclone increase in mapped road length in the flooded regions, illustrating how OSM data quality follows events of interest.
- Geldsetzer 2020 acknowledges no time-of-day variation, no border-crossing cost, and incomplete facility databases.

#### 3.3.6 What this changes for our decision

Three things, none of them small:

1. **For LAC schools the HDX dataset is NOT a drop-in benchmark.** Education is binned by *distance* (5-50 km) and modelled *by car*. For a 6-year-old in Bolivia's altiplano, "are you within 5 km driving distance of a primary school?" is the wrong question — they walk, and 5 km is well over an hour on foot at altitude. For hospitals and primary healthcare the time-based, car-based HDX dataset *is* a clean apples-to-apples benchmark if and when the IDB platform extends to health. CHL and COL are the two countries already covered. ARG, BRA, MEX likely have it too (HDX confirms it is "one of many HeiGIT exports") but the user has only verified CHL and COL locally.

2. **HeiGIT's own foundational paper (Geldsetzer 2020) uses a raster cost-distance method, not isochrones.** They publish the HDX product as the productionised, scheduled, easy-to-consume artefact, but their methodological backbone for the original sub-Saharan analysis is the same family as our Tier 1 FMM. This *supports* the Tier-1 choice in §6 with peer-reviewed precedent from inside the same group.

3. **Petricola 2022's explicit recommendation is hybrid raster+network.** Adding the HDX dataset as a benchmark layer for the IDB platform — particularly for health if/when that scope is added — is consistent with their own future-work recommendation. We do not have to pick between Family A and Family C; HeiGIT itself does both.

### 3.4 Other references the platform already cites

- **Higgins et al. 2022** (the speed claim above): r5r is reportedly 6-200× faster than alternatives, but the comparison is on multimodal urban scenarios. School access on walking-only with no GTFS is the easiest case — speed advantages of r5r over OSRM compress here.
- **MAP friction surfaces** (Weiss et al. 2018, *Nature*, "A global map of travel time to cities to assess inequalities in accessibility in 2015"; Weiss et al. 2020, *Nature Medicine*, "Global maps of travel time to healthcare facilities"): these are the *only* operational global friction surfaces available. They are derived from a fusion of OSM, road maps, land cover, and slope; their NoData gaps in Darién and indigenous comarcas reflect upstream OSM sparsity — i.e., the same problem network routers face.

---

## 4 Trade-offs that matter for a 21-country platform

Each row is a criterion that distinguishes the methods in practice. Cells with a clear winner are bolded; ties or "depends" are left plain.

| Criterion | A. FMM (MAP raster) | B-1. r5r / r5py (OSM graph) | B-2. OSRM + UrbanPy (OSM graph) | C. ORS isochrones (HeiGIT/HDX) |
|---|---|---|---|---|
| **Cross-country reproducibility** | **High** — global raster, same units, no per-country wiring | Medium — Java runtime, country-by-country PBF loading, OSM parser pipeline | Medium — per-country .osrm builds, UrbanPy CLI | Medium — HeiGIT ships every 6 months; per-country gpkg + CSV (CCBY-SA) |
| **Sensitivity to OSM coverage** | Low — friction = continuous surface, off-road allowed | **High** — countries with sparse OSM produce broken paths | **High** — same as r5r | **High** — explicitly flagged by HDX caveat ("may underestimate in unmapped areas") |
| **Off-road movement** | **Yes** at low speed (~5 km/h motorized off-road, 2.5 km/h walking in PAN, `build_osm_friction.py:83,110`) | No — only roads in OSM | No — only roads in OSM | No — Petricola 2022 explicitly notes raster method "considers open space routing" while network model does not |
| **Road-network fidelity** | Low — 1 km cell aggregates roads; one-way / dead-ends not modeled | **High** — turn restrictions, one-way, network topology respected | **High** — same as r5r | **High** — same algorithmic family (CH on OSM via GraphHopper 4.0 fork) |
| **Compute cost** | Low — PAN pilot ran all 16 FMMs on a laptop in minutes | Medium — Java JVM, contraction hierarchies. r5r reports "6-200× faster than other multimodal routing" | Low-Medium — OSRM CH is fast | Medium — engine compute is HeiGIT's, not ours; you only consume CSV/gpkg |
| **Multimodal (GTFS) support** | No | **Yes** — r5r's main advantage | No | No (ORS supports walk/bike/car/wheelchair but the HDX product is car-only) |
| **Time-of-day variability** | No | **Yes** | Limited | No |
| **NoData handling** | Cells with friction=NaN are unreachable; FMM propagates around them | A graph-disconnected origin returns Inf travel time | Same as r5r | Population outside the outermost ring (5 km for schools, 120 min for hospitals) is counted but unranked |
| **Edge effect / cross-border** | Need a buffer beyond ADM0 (PR1 uses 0.05° / ~5 km, see `07_friction_clip.py:140-148`) | Country-by-country PBF — Castro flags this gap explicitly | Same as Castro | Same as B — per-country graph |
| **Output shape** | Continuous travel-time raster (1×1 km) | OD travel-time matrix per origin × destination | OD travel-time matrix | **Polygon isochrones + aggregated CSV** at ADM 1-4. Ready-to-use, but coarser than the underlying raster |
| **Transport mode default** | Either (PAN ran both motorized + walking, friction surface specific) | All four (walk, bike, car, transit) | Motorized or walking | **Car only** for the HDX product, even for schools |
| **Binning convention** | Continuous → bin in aggregation (PAN uses ≤15 / ≤30 / ≤60) | Continuous → bin in aggregation | Continuous → bin in aggregation | **Pre-binned at source**: 5/10/…/50 km for schools; 10 min/…/120 min for health |
| **Local LAC precedent** | None — PAN pilot is first | None piloted locally — Higgins 2022 cited | **Castro et al. 2024** — 5 LAC countries, public schools, walking | **HeiGIT/HDX 2026-02** — published for CHL and COL, license CC BY-SA |
| **What you already have running** | **Yes** — PAN 32 scenarios shipped | r5py code written, never executed | Not yet wired into pipeline | **Downloadable today** — gpkg + long/wide CSVs per country |

---

## 5 LAC-specific factors that swing the choice

These cut across the matrix and decide what is actually deployable.

1. **OSM coverage is wildly heterogeneous.** Anecdotally and by ipeaGIT's own caveat (3.1.2), OSM in BRA, MEX, COL, ARG, CHL is dense enough for network routing to be credible; in HTI, GUY, SUR, BLZ, JAM, parts of rural BOL/PER it is too sparse for an OSRM/r5r-only approach to give an honest answer. **FMM on MAP friction degrades gracefully in those countries** (it falls back to land-cover speeds); network routers fail loudly (Inf travel time) or silently (unrealistic detours).

2. **CIMA school coordinate quality varies by 30 percentage points** between best (CHL, ECU after Step 02 fix) and worst (parts of BOL, JAM spatial-only). Network routing assumes the origin and destination are *on* the network. A school with `coordinate_source=geocoded_centroid` and a network router will report large detours that are an artefact of the snap-to-network. FMM snaps origins to the nearest valid cell within 3 pixels (`travel_time.py:117-145`), which is friendlier to lower-precision coordinates.

3. **The platform already publishes ≤15 / ≤30 / ≤60 min cumulative-opportunity bands** in the dashboard's planned structure. Cumulative opportunity is the simplest measure (ipeaGIT Ch. 2: "easy to calculate and communicate") and is *agnostic* to the routing engine. The dashboard does not require gravity or FCA, which would reward r5r's flexibility. **Switching engines later would not invalidate already-shipped output.**

4. **Castro et al. is the closest LAC analog and chose OSRM+UrbanPy.** Their explicit dispersed-area exclusion (<200 hab AND <150 hab/km²) tells us the method *cannot* serve the most isolated populations honestly. The IDB platform's full scope includes those populations — FMM at least gives them a number (which they can then validate against ground truth) rather than a missing row.

5. **Reproducibility cost.** r5r needs Java 11 + a per-country PBF + manual memory tuning (`-Xmx2G` per ipeaGIT Ch. 3). OSRM needs per-country `.osrm` files. FMM needs only the (already clipped, PR1) friction raster and the schools CSV. For a pipeline that runs `--countries all`, the lowest-ceremony approach wins.

---

## 6 Recommendation

The right answer at 21-country scale is **not** "always FMM" or "always OSRM". The new local evidence (§3.4) shows FMM and r5py disagree by ~37 pp at ≤15min walking in dense urban PAN districts, with FMM systematically optimistic. That is too large to ignore, but FMM still has properties no network router can match (graceful degradation in sparse-OSM countries, off-road support, single global friction surface for all 23 countries). The structure below picks FMM as the *scalable workhorse* while requiring a network-routing reality check in the countries where OSM is dense enough to support it.

**Tier 1 (default, all 23 PIPELINE_ISOS, full cross-product of 3 levels × 2 modes):** FMM on the clipped MAP friction surfaces for **walking AND motorized**, computed independently for each of the three educational levels (primaria, secundaria baja, secundaria alta). The OSM-derived friction stays as an optional comparison surface where road density is high. Reasons:

- The IDB platform reports six indicator combinations per country: `{primaria, sec_baja, sec_alta} × {walking, motorized}`. Walking *and* motorized travel times are computed for *every* educational level — not split by level. This matches the **PAN pilot Series C + D matrix** (`accesibility/scenarios/config.py:168-198`), which already runs 2 friction families (MAP + OSM) × 2 modes (walking + motorized) × 3 age groups (primary / secondary / high school) × 2 population sources (census + worldpop) — 24 scenarios in those two series alone.
- PR1 already ships *both* the MAP walking (`{ISO}_walking_2019.tif`) and MAP motorized (`{ISO}_motorized_2019.tif`) friction surfaces per country plus the 2015 travel-speed raster as a third reference. The friction layer itself does not depend on the educational level — the level enters as the *origins* (school subset by `nivel_*` flag) and the *demand* (school-age band 5-9 / 10-14 / 15-19 from `population_grid_{ISO}.csv`). FMM dedup keys on `(friction, level)`, so the number of unique travel-time rasters per country is **2 modes × 3 levels = 6 rasters**, not 6 × 2.
- FMM is the only family that degrades gracefully where OSM is sparse (HTI, GUY, SUR, BRB, JAM, parts of BOL/PER).
- WorldPop population is already on a 1 km grid that aligns naturally with the MAP raster (same resolution, both EPSG:4326).
- Cumulative-opportunity bands (≤15 / ≤30 / ≤60 min) are the dashboard output; FMM produces them directly per mode and per level.

How this scope compares to the reference benchmarks:

| Source | Walking | Motorized | Per educational level | Note |
|---|---|---|---|---|
| **IDB platform (this design)** | ✓ | ✓ | ✓ primaria + sec baja + sec alta | 6 indicator combinations per country |
| PAN pilot (Lopez 2026) | ✓ A2/A4/B2/B4 | ✓ A1/A3/B1/B3 | ✓ Series C + D × 3 age groups | 32 scenarios total, dedup to 16 FMM runs |
| Castro et al. 2024 | ✓ | ✗ | ✓ primaria + sec baja + sec alta | Walking-only by design |
| HeiGIT / HDX 2026-02 (CHL, COL) | ✗ | ✓ | ✗ (one "education" bin only) | "by car" for education (5-50 km distance bins) and health (10-120 min time bins) |
| ipeaGIT *Intro to Urban Accessibility* | ✓ | car not central | book teaches the method, not a specific levels schema | r5r supports car but the book emphasises walk + transit |

**Tier 2 (REQUIRED for headline countries, network-routing reality check):** Run **r5py or OSRM + UrbanPy** for BRA, MEX, COL, ARG, CHL — the LAC countries where OSM is densest *and* where FMM is most likely to overstate accessibility (per §2.2 evidence). Compare the ≤15 / ≤30 / ≤60 % at ADM2 level against FMM. Where the gap is < 10 pp the FMM number stands. Where the gap is ≥ 10 pp **the dashboard surfaces BOTH numbers with an explanatory tooltip**. This is not optional validation — without it the platform inherits the same ~37 pp upward bias the PAN pilot now demonstrates.

We have local evidence that r5py is fast (~6 min for 5 PAN districts on a laptop) once the contracted network is cached. Per-country first-time builds may take longer (~10-30 min) but are one-time cost. The engineering investment for these 5 countries is comparable to one week of work, not a quarter.

**Tier 2-bis (ORS-isochrone benchmark, free):** Pull the **HeiGIT/HDX dataset** for CHL and COL (and any other LAC country HeiGIT ships) as a *third* parallel reference. Use only the long-CSV at ADM2/ADM3. Two limits to declare upfront when comparing:
- HeiGIT bins education by *distance* (5-50 km), not time, and assumes *car* mode. The closest apples-to-apples comparison is FMM motorized at thresholds equivalent to 5/10/20 km at a representative country speed — *not* a direct match.
- Hospitals / primary_healthcare are time-binned (10-120 min by car) and *are* a clean reference if the IDB platform extends to health accessibility (Phase-2).

**Tier 3 (case studies, when sponsored):** Run **r5r or r5py** for 3-5 metropolitan ADM2 polygons (Lima, Bogotá, São Paulo, Buenos Aires, Mexico DF) where GTFS is published. r5r adds value where transit matters; for walking-only school access to the *nearest* facility, it offers no incremental insight over OSRM and would inflate the engineering surface area for a marginal gain.

**Reject** building OSRM and r5r as parallel primary outputs for all 21 countries: the OSM coverage problem makes the result dishonest in 6+ of them, and the engineering cost (per-country PBF builds, Java runtime in CI, network-edge fixes) is high. Petricola 2022 from HeiGIT's own citation list explicitly recommends "combining raster- and network-based methods to overcome their respective limitations" — that is what the tiered structure does.

---

## 7 What this implies for the pipeline

1. **PR1 (this branch):** ships the 23-country clip of *all three* MAP friction rasters — walking 2019, motorized 2019, travel-speed 2015. Inputs sufficient for Tier 1 in *both* modes.
2. **Step 09 (TBD):** FMM travel-time computation per country, **per mode × per educational level** (2 × 3 = 6 unique FMM runs per country). Port `accesibility/utils/mcp/travel_time.py` into `pipeline/09_travel_time_fmm.py`. Three changes from the PAN code:
   - **Parametrise the mean-latitude metric conversion** — `travel_time.py:48,56` is hard-coded to PAN 8.4° (`_PAN_MEAN_LAT_DEG = 8.4`). It must become per-country, computed from the country's ADM0 centroid latitude. The dx/dy error from using the wrong latitude is ~`cos(Δlat)` and matters for countries far from 8°N (CHL down to -56°, MEX up to +32°).
   - **Loop over `friction_key ∈ {walking_2019, motorized_2019}` × `level ∈ {primaria, sec_baja, sec_alta}`** per country. Schools are subset by the corresponding `nivel_*` flag in `LAC_schools_k12_clean.csv` to define the FMM sources per level.
   - Outputs: `data/transportation/travel_times/{ISO}/{ISO}_{mode}_{level}.tif` (one float32 raster per country × mode × level). 23 × 2 × 3 = **138 travel-time rasters** at Tier 1.
3. **Step 10 (TBD):** zonal aggregation to ADM2 cumulative-opportunity bands, **per mode × per level**. Port `accesibility/results/aggregate.py` and the multipart dissolve fix (`aggregate.py:45-75`), but key on `ADM2_PCODE` (BID polygons) instead of `cod_dist` (Panama-specific). Population from `population_grid_{ISO}.csv`: use `pop_5_9` for primaria, `pop_10_14` for sec_baja, `pop_15_19` for sec_alta. Output schema needs both `mode` ∈ {walking, motorized} and `level` ∈ {primaria, sec_baja, sec_alta} columns so the dashboard can pivot. Bands ≤15 / ≤30 / ≤60 min match the PAN pilot and Castro et al. exactly. 23 countries × 6 combinations = **138 rows per ADM2 band**, times the number of ADM2 polygons (~3,000 across LAC), gives ~414 k rows of output — comfortably small.
4. **Validation, 2-country subset:** for BRA and COL, also build the OSRM+UrbanPy stack and compare ADM2-level ≤15 / ≤30 / ≤60 % bands for both modes. Document divergence > 5 pp.
5. **Tier 2-bis ORS benchmark (free):** pull HeiGIT HDX dataset for CHL and COL. For health (`hospitals` + `primary_healthcare`) it's a direct time-binned car-mode benchmark. For education the HDX bins are distance-based (5/10/20 km) — convert to approximate-time equivalents at country motorways speed (~80 km/h primary) only as a coarse sanity check, not a primary comparison.
6. **NOT in scope for the first iteration:** r5r/r5py, gravity / FCA measures, GTFS multimodal, time-of-day variation, network-based routing as the primary output for any country.

---

## 8 Honest gaps in this review

- ~~No local FMM vs network-routing benchmark.~~ **Closed 2026-05-13.** Comparison documented in §2.2. Key finding: FMM systematically overestimates ≤15 min accessibility by ~37 pp vs r5py in dense urban PAN districts. The bias shrinks with the threshold (~3 pp at ≤60 min).
- **No evidence on the reverse bias in sparse-OSM countries.** Petricola 2022 documents that network routing "considers out of range" what raster methods reach via off-road propagation, but we have no quantitative local evidence for HTI / GUY / SUR / rural BOL. A second benchmark in one sparse-OSM country (probably GUY, smallest with sparse OSM) would close this. Estimated effort: ~1 day if the OSM PBF is available, much less compute than PAN since GUY has fewer schools and households.
- **The PAN comparison is walking-only.** r5py was run with `transport_modes=[WALK]`. The same exercise should be repeated for motorized to test whether the bias has the same magnitude / direction. Estimated effort: half a day, the driver script (`scripts/run_r5py_pan_top5.py`) just needs `TransportMode.CAR` swapped in and the `max_time` raised.
- **The ipeaGIT book does not compare methods.** Their speed claim ("6-200× faster") for r5r is uncalibrated for our use case (walking-only, no GTFS).
- **MAP raster NoData ≠ network NoData.** This was an earlier overstatement (we said "they fail in the same 5 PAN districts"). Per §2.2 and Petricola 2022, the NoData boundaries are *different*: raster methods reach off-road, network methods do not. The 5 PAN districts both methods fail on are the indigenous-comarca / Darién intersection — which is the worst case for both, but not for the same reason.
- **Castro et al. do not publish per-country tables with raw numbers**, only stacked bar charts (Figures 2-3). To compare the FMM-Tier-1 output against their work quantitatively we would need to reproduce their UrbanPy run for at least 1 country.

---

## 9 References

**Peer-reviewed literature**

- Castro, N., Giambruno, C., & Ortega, C. (2024). *El largo camino a la escuela: análisis de accesibilidad a centros educativos públicos en asentamientos de la Amazonía*. Local PDF: `C:\Users\lopez\github\IDB\lit\Acceso a la educación en la Amazonía Urbana.pdf`.
- Geldsetzer, P., Reinmuth, M., Ouma, P. O., Lautenbach, S. et al. (2020). "Mapping physical access to health care for older adults in sub-Saharan Africa and implications for the COVID-19 response: a cross-sectional analysis". *Lancet Healthy Longevity* 1(1), e32-e42. https://pmc.ncbi.nlm.nih.gov/articles/PMC7574846/
- Petricola, S., Reinmuth, M., Lautenbach, S. et al. (2022). "Assessing road criticality and loss of healthcare accessibility during floods: the case of Cyclone Idai, Mozambique 2019". *International Journal of Health Geographics*. https://pmc.ncbi.nlm.nih.gov/articles/PMC9559768/
- Klipper, I. G., Zipf, A., & Lautenbach, S. (2021). "Flood Impact Assessment on Road Network and Healthcare Access at the example of Jakarta, Indonesia". *AGILE-GISS*. https://agile-giss.copernicus.org/articles/2/4/2021/
- Ruiz Sánchez, R., Reinmuth, M., Albornoz, C., Lautenbach, S., & Zipf, A. (2025). "Changes in Road Centrality and Hospital Access Redundancy: Impacts of the 2024 Flood in the Metropolitan Core of Porto Alegre, Brazil". *AGILE-GISS*. https://agile-giss.copernicus.org/articles/6/10/2025/
- Pereira, R. H. M., Herszenhut, D., Saraiva, M., et al. *Introduction to Urban Accessibility: a Practical Guide in R*. Ipea. https://ipeagit.github.io/intro_access_book/. Local PDF: `C:\Users\lopez\github\IDB\lit\Introduction_urban_accessibility_Book.pdf`.
- Weiss, D. J. et al. (2018). "A global map of travel time to cities to assess inequalities in accessibility in 2015". *Nature* 553, 333-336.
- Weiss, D. J. et al. (2020). "Global maps of travel time to healthcare facilities". *Nature Medicine* 26, 1835-1838. Friction surfaces: https://data.malariaatlas.org/.
- Higgins, C. D. et al. (2022). "{r5r}: rapid realistic routing on multimodal transport networks with R5 in R". *Findings*.

**Datasets & software**

- HeiGIT, *Chile — Accessibility Indicators*. HDX dataset id `9013c094-036e-42e8-95d8-82b1c99a4e99`, updated 2026-02-25. License CC BY-SA. https://data.humdata.org/dataset/chile-accessibility-indicators
- HeiGIT, *Colombia — Accessibility Indicators*. HDX, same publisher and methodology. https://data.humdata.org/dataset/colombia-accessibility-indicators
- HeiGIT, *Open Healthcare Access Map* blog post (2021). https://heigit.org/introducing-the-open-healthcare-access-map/
- GIScience Heidelberg, *openrouteservice* (GitHub). Dual-licensed GPL-3.0 / LGPL-3.0. Forked from GraphHopper 4.0. https://github.com/GIScience/openrouteservice
- GraphHopper, isochrone documentation. https://github.com/graphhopper/graphhopper/blob/master/docs/isochrone/java.md
- AccessMod 5 (WHO), raster cost-distance for healthcare access (the engine in Geldsetzer 2020).
- geoBoundaries — the ADM source HeiGIT uses. https://www.geoboundaries.org/

**Internal repository references**

- `C:\Users\lopez\github\GIS\Final Project\PAN_PILOT_REFERENCE.md`
- `accesibility/utils/mcp/travel_time.py`
- `accesibility/results/aggregate.py`
- `accesibility/scenarios/config.py`
- `accesibility/utils/r5py/router.py`
- `accesibility/utils/preprocessing/build_osm_friction.py`
- `accesibility/results/district_tables/{A1..D12}_*.csv` (32 scenario CSVs)
- HDX metadata snapshot: `C:\Users\lopez\Downloads\metadata-chile-accessibility-indicators.csv`
- Colombia HDX HTML snapshot: `C:\Users\lopez\github\IDB\accessibility_platform\Colombia - Accessibility Indicators _ Humanitarian Dataset _ HDX.html`
