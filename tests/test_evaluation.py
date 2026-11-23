"""Unit tests for the Step 11 evaluation module (synthetic fixtures only)."""

import numpy as np
import pandas as pd

from kindwindow.contracts import intersects_long_gap
from kindwindow.evaluation.evaluation import (
    drop_event_mask,
    flag_long_gap_windows,
    per_home_table,
    retention_mask,
)
from kindwindow.models.baselines import brier_score, macro_brier


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "home": ["a", "a", "b", "b"],
            "t": pd.to_datetime(
                ["2030-01-01T00:00", "2030-01-01T00:30", "2030-01-01T00:00", "2030-01-01T00:30"]
            ),
            "label": [1, 0, 1, 1],
        }
    )


def test_brier_and_macro():
    assert brier_score(np.array([1, 0]), np.array([1.0, 0.0])) == 0.0
    assert brier_score(np.array([1, 0]), np.array([0.5, 0.5])) == 0.25
    assert macro_brier({"a": 0.1, "b": 0.3}) == 0.2


def test_per_home_table_macro_row():
    df = _frame()
    table = per_home_table(df, np.array([0.9, 0.1, 0.8, 0.6]), model="m", split="test")
    assert list(table["home"]) == ["a", "b", "MACRO"]
    assert table.loc[table["home"] == "a", "n"].iloc[0] == 2
    macro = table.loc[table["home"] == "MACRO", "brier"].iloc[0]
    assert macro == (0.01 + 0.10) / 2  # a: mean((.9-1)^2,(.1-0)^2)=.01; b: (.04+.16)/2=.10


def test_retention_mask_below_cutoff_and_all():
    u = np.array([0.05, 0.30, 0.10])
    w = np.ones(3)
    keys = ["k1", "k2", "k3"]
    # Frozen cutoff 0.2: below-cutoff rows retained, above dropped.
    mask = retention_mask(u, w, keys, 0.5, 0.2)
    assert bool(mask[0])  # lowest u always retained
    assert not bool(mask[1])  # highest u dropped
    assert bool(mask[2])
    assert retention_mask(u, w, keys, 1.0, float("inf")).all()


def test_retention_mask_tie_order_is_hash_order():
    # All uncertainties equal to the frozen cutoff: ties retained in
    # hash order until the nominal weight is reached (1 of 3 at target 1/3).
    u = np.array([0.2, 0.2, 0.2])
    w = np.ones(3)
    keys = ["c", "a", "b"]  # hash order: a < b < c
    mask = retention_mask(u, w, keys, 1 / 3, 0.2)
    assert mask.sum() == 1
    assert bool(mask[1])  # key "a" sorts first


def test_gap_flag_matches_contract():
    # Events at 00:00 and 13:00 -> 13 h gap; 12 h threshold flags windows
    # whose [t-120m, t+30m) span intersects (00:00, 13:00).
    times = pd.to_datetime(["2030-02-01T00:00:00", "2030-02-01T13:00:00"]).to_numpy(
        dtype="datetime64[ns]"
    ).astype(np.int64)
    df = pd.DataFrame(
        {
            "home": ["h", "h", "h"],
            "t": pd.to_datetime(
                ["2030-02-01T06:00:00", "2030-02-01T12:00:00", "2030-02-01T20:00:00"]
            ),
        }
    )
    flagged = flag_long_gap_windows(df, {"h": times}, 12)
    expected = [
        intersects_long_gap(
            [pd.Timestamp(t) for t in ["2030-02-01T00:00:00", "2030-02-01T13:00:00"]],
            pd.Timestamp(t),
            12,
        )
        for t in df["t"]
    ]
    assert list(flagged) == expected


def test_gap_flag_no_long_gap():
    times = pd.to_datetime(["2030-02-01T00:00:00", "2030-02-01T01:00:00"]).to_numpy(
        dtype="datetime64[ns]"
    ).astype(np.int64)
    df = pd.DataFrame({"home": ["h"], "t": pd.to_datetime(["2030-02-01T00:30:00"])})
    assert not flag_long_gap_windows(df, {"h": times}, 12).any()


def test_drop_event_mask_deterministic_and_fraction():
    rows = np.arange(10000)
    m1 = drop_event_mask("hh101", "data/hh101.csv", rows, seed=2207, fraction=0.30)
    m2 = drop_event_mask("hh101", "data/hh101.csv", rows, seed=2207, fraction=0.30)
    assert (m1 == m2).all()
    assert 0.27 < m1.mean() < 0.33
    m_other = drop_event_mask("hh101", "data/hh101.csv", rows, seed=1103, fraction=0.30)
    assert (m1 != m_other).any()
    # Row identity matters: different rows get different draws.
    assert m1[:100].mean() != 1.0
