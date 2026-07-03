"""Test CIMA file schema and data integrity."""

import pandas as pd
import pytest
from conftest import REQUIRED_COLUMNS, _cima_path
from pipeline.constants import CIMA_ENRICHED_COLUMNS


def load(iso):
    return pd.read_csv(_cima_path(iso), dtype={"id_centro": str})


class TestSchema:
    """Every CIMA file must have the correct column structure."""

    def test_required_columns_present(self, iso):
        df = load(iso)
        missing = set(REQUIRED_COLUMNS) - set(df.columns)
        assert not missing, f"{iso}: missing columns {missing}"

    def test_sector_values(self, iso):
        df = load(iso)
        valid = {"Public", "Private", "Unknown"}
        actual = set(df["sector"].dropna().unique())
        invalid = actual - valid
        assert not invalid, f"{iso}: invalid sector values {invalid}"

    def test_level_flags_binary(self, iso):
        df = load(iso)
        for col in ["nivel_primaria", "nivel_secbaja", "nivel_secalta"]:
            vals = set(df[col].dropna().unique())
            assert vals <= {0, 1}, f"{iso}: {col} has non-binary values {vals}"

    def test_at_least_one_level(self, iso):
        df = load(iso)
        has_level = (
            (df["nivel_primaria"] == 1)
            | (df["nivel_secbaja"] == 1)
            | (df["nivel_secalta"] == 1)
        )
        n_bad = (~has_level).sum()
        assert n_bad == 0, f"{iso}: {n_bad} rows with no K-12 level flag"

    def test_id_centro_not_null(self, iso):
        df = load(iso)
        n_null = df["id_centro"].isna().sum()
        assert n_null == 0, f"{iso}: {n_null} null id_centro values"

    def test_adm0_pcode_matches_iso(self, iso):
        df = load(iso)
        vals = df["adm0_pcode"].unique()
        assert len(vals) == 1 and vals[0] == iso, (
            f"{iso}: adm0_pcode should be '{iso}', got {vals}"
        )

    def test_step02_enriched_columns_present(self, iso):
        df = load(iso)
        missing = set(CIMA_ENRICHED_COLUMNS) - set(df.columns)
        assert not missing, f"{iso}: missing Step 02 enriched columns {missing}"

    def test_step02_enriched_column_order_locked(self, iso):
        df = load(iso)
        assert list(df.columns) == list(CIMA_ENRICHED_COLUMNS), (
            f"{iso}: finalized CIMA columns differ from canonical order"
        )
