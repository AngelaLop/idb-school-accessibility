"""
Preprocess admin data for countries where the raw ministerial file needs
reshaping before QC can join it to the CIMA id_centro or to BID polygons.

Run this BEFORE pipeline/02_qc_coordinates.py when:
- Raw ministerial data is updated
- BID polygons change
- New countries are added

Usage:
    uv run python pipeline/00_preprocess_admin.py
    uv run python pipeline/00_preprocess_admin.py --only URY
    uv run python pipeline/00_preprocess_admin.py --only CHL

Currently handles:
- URY: merges 3 shapefiles (CEIP/CES/CETP) into a single admin CSV with
  (id_centro, departamento). The CIMA id_centro is the stable school code
  (`RUEE` / `Ruee_Calcu`), not the shapefile row `OBJECTID`.
- CHL: resolves the name mismatch between MINEDUC education Deprov (column
  NOM_DEPROV_RBD) and BID political Provincia (ADM2_EN). Uses the numeric
  COD_PRO_RBD which maps 1:1 to BID's ADM2_PCODE ('CL0{code}').
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

from _paths import DATA_ROOT  # noqa: E402

BASE = DATA_ROOT / "schools" / "AR"
BID_LEVEL_2 = DATA_ROOT / "bounderys" / "LAC" / "level 2" / "lac-level-2.shp"


# ─── URY ──────────────────────────────────────────────────────────────────────

def preprocess_ury() -> Path:
    """Combine CEIP/CES/CETP shapefiles + centros_clean.csv into an enriched admin CSV.

    The shapefile pass is the authoritative SCOPE: each row's stable ANEP id
    (`RUEE` in CEIP/CETP, `Ruee_Calcu` in CES) and `departamento` come from
    the shapefile DBFs (with mojibake repair), matching what process_URY in
    01_build_cima.py uses to build the CIMA.

    The centros_clean.csv pass ENRICHES each row with localidad/paraje/calle/
    n_de_puerta extracted from the MEC's KMZ. Joining by RUEE matches 98.5%
    of shapefile rows (2,636 / 2,677) and unlocks Phase B-1 geocoding for
    URY — previously URY was treated as Type B (admin-only) because the
    addresses lived in a sibling CSV that this preprocessor was discarding.

    DBFs have mixed encoding — some records store accented chars as proper
    latin-1 bytes (e.g. 'PAYSANDÚ' = 0xDA), others store already-UTF-8-encoded
    bytes which get rendered as mojibake when read as latin-1 ('PAYSANDÃ\x9a').
    We try latin-1 first, then repair mojibake via the latin-1 -> bytes -> utf-8
    roundtrip for the affected records.
    """
    import shapefile as shp_lib

    def _fix_mojibake(s: str) -> str:
        """Repair latin-1-read-as-utf-8 mojibake if present.
        'SAN JOSÃ\x89' -> 'SAN JOSÉ'. Harmless on clean strings.
        """
        if "Ã" not in s:
            return s
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s

    raw_dir = BASE / "URY" / "raw"
    sources = [
        (next(raw_dir.rglob("CEIP.shp"), None), "Departamen", "RUEE"),
        (next(raw_dir.rglob("CES.shp"), None), "FIRST_Depa", "Ruee_Calcu"),
        (next(raw_dir.rglob("CETP.shp"), None), "FIRST_Depa", "Ruee"),
    ]

    rows = []
    for shp_path, dep_field, id_field in sources:
        if shp_path is None:
            print(f"  WARNING: URY shapefile not found")
            continue
        sf = shp_lib.Reader(str(shp_path), encoding="latin-1")
        fields = [f[0] for f in sf.fields[1:]]
        id_idx = fields.index(id_field)
        dep_idx = fields.index(dep_field)
        records = list(sf.iterRecords())
        for r in records:
            sid = str(r[id_idx]).strip()
            if id_field == "Ruee_Calcu" and sid in {"", "0", "0.0", "nan", "None"}:
                continue
            rows.append({
                "id_centro": sid,
                "departamento": _fix_mojibake(str(r[dep_idx]).strip()),
            })
        print(f"  Read {shp_path.name}: {len(records)} records")

    df = pd.DataFrame(rows).drop_duplicates(subset="id_centro")

    # Enrich with addresses from centros_clean.csv (extracted from MEC KMZ).
    # Filter to K-12 subsystems (CEIP primary, CES secondary, CETP technical
    # secondary). CFE is teacher training and not in K-12 scope.
    clean_path = raw_dir / "centros_clean.csv"
    if clean_path.exists():
        clean = pd.read_csv(clean_path, low_memory=False)
        clean = clean[clean["subsitema"].isin(["CEIP", "CES", "CETP"])].copy()
        clean["id_centro"] = clean["ruee"].dropna().astype("Int64").astype(str)
        clean = clean.drop_duplicates(subset="id_centro", keep="first")

        # Compose a geocoder-ready street string. Skip the door number if it
        # signals "no number" (S/N, sin número) since concatenating it would
        # produce "RUTA 1 KM 76 S/N" which still geocodes but adds noise.
        no_number = {"", "s/n", "sin numero", "sin número", "nan", "none"}
        clean["calle"] = clean["calle"].fillna("").astype(str).str.strip()
        clean["n_de_puerta"] = clean["n_de_puerta"].fillna("").astype(str).str.strip()
        has_num = ~clean["n_de_puerta"].str.lower().isin(no_number)
        clean["calle_full"] = clean["calle"].where(
            ~has_num, clean["calle"] + " " + clean["n_de_puerta"]
        )

        addr_cols = ["id_centro", "localidad", "paraje", "calle_full", "n_de_puerta"]
        df = df.merge(clean[addr_cols], on="id_centro", how="left")
        df = df.rename(columns={"calle_full": "calle"})
        n_with_addr = df["localidad"].notna().sum()
        print(f"  Enriched with centros_clean.csv: {n_with_addr:,}/{len(df):,} rows have addresses")
    else:
        print(f"  WARNING: centros_clean.csv not found — URY will remain Type B (admin-only)")
        for col in ("localidad", "paraje", "calle", "n_de_puerta"):
            df[col] = ""

    out = raw_dir / "URY_admin.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"  Wrote {out} — {len(df):,} rows, {df['departamento'].nunique()} departamentos")
    return out


# ─── CHL ──────────────────────────────────────────────────────────────────────

def preprocess_chl() -> Path:
    """Resolve CHL's MINEDUC deprov vs BID political Provincia mismatch.

    Raw CHL column NOM_DEPROV_RBD reports MINEDUC's education Deprov which
    doesn't align with BID's political Provincia shapefile (e.g. "ÑUBLE"
    education region maps to 3 political provincias Diguillín/Punilla/Itata;
    "SANTIAGO SUR" education maps to "Maipo" political).

    The numeric COD_PRO_RBD (political provincia code) maps 1:1 to BID's
    ADM2_PCODE ('CL0{code}') for all 56 CHL provinces. This script uses that
    to resolve the correct provincia name from BID's lac-level-2.shp.
    """
    import geopandas as gpd

    # Build the code lookup from BID polygons: COD_PRO_RBD -> ADM2_EN
    gdf = gpd.read_file(BID_LEVEL_2)
    chl_poly = gdf[gdf["ADM0_PCODE"] == "CHL"][["ADM2_PCODE", "ADM2_EN"]].copy()
    chl_poly["cod_pro"] = (
        chl_poly["ADM2_PCODE"]
        .str.replace("CL0", "", n=1)
        .str.replace("CL", "", n=1)
        .astype(int)
    )
    code_to_name = dict(zip(chl_poly["cod_pro"], chl_poly["ADM2_EN"]))
    print(f"  BID provincia codes: {len(code_to_name)}")

    raw_path = BASE / "CHL" / "raw" / "20230912_Directorio_Oficial_EE_2023_20230430_WEB.csv"
    raw = pd.read_csv(
        raw_path,
        sep=";",
        encoding="latin-1",
        low_memory=False,
        usecols=["RBD", "COD_REG_RBD", "COD_PRO_RBD", "NOM_COM_RBD"],
    )
    raw["provincia_bid"] = raw["COD_PRO_RBD"].map(code_to_name)
    raw["id_centro"] = raw["RBD"].astype(str)
    # COD_REG_RBD (1..16) maps 1:1 to BID's ADM1_PCODE "CL{:02d}". Carry the
    # numeric region code so Step 02 can do code-based ADM1 matching (the
    # name-based path can't run for CHL because the directorio uses MINEDUC's
    # education-deprov nomenclature, which doesn't always align with BID
    # political names).
    raw["cod_reg_rbd"] = raw["COD_REG_RBD"]

    unmatched = raw["provincia_bid"].isna().sum()
    if unmatched > 0:
        print(f"  WARNING: {unmatched:,} CHL rows have COD_PRO_RBD not in BID polygons")

    out = raw[["id_centro", "provincia_bid", "NOM_COM_RBD", "cod_reg_rbd"]].rename(
        columns={"NOM_COM_RBD": "comuna"}
    )
    out_path = BASE / "CHL" / "raw" / "CHL_admin.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(
        f"  Wrote {out_path} — {len(out):,} rows, "
        f"{out['provincia_bid'].nunique()} provincias"
    )
    return out_path


# ─── CLI ──────────────────────────────────────────────────────────────────────

PREPROCESSORS = {
    "URY": preprocess_ury,
    "CHL": preprocess_chl,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=list(PREPROCESSORS.keys()),
        help="Run only one country's preprocessing step (default: all).",
    )
    args = parser.parse_args()

    targets = [args.only] if args.only else list(PREPROCESSORS.keys())

    for iso in targets:
        print(f"\n=== {iso} ===")
        PREPROCESSORS[iso]()

    print("\nDone.")


if __name__ == "__main__":
    main()
