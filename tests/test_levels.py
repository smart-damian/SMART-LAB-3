"""Tests for the pure logic and the database layer. No internet needed.

Run from the project root with:  python -m pytest -v
(`python -m` puts the current folder on the import path so
`import levels` works.)

Each test uses tiny hand-made numbers so you can verify the expected answer
with a pencil. That is how you learn to trust (or doubt) your own code.
"""
import pandas as pd
import pytest

import storage
from levels import SymbolLevels, analyze, find_level


def test_high_is_most_frequent_running_max():
    # newest first: 8,6,7,9,5 -> running max 8,8,8,9,9 -> 8 appears most
    s = pd.Series([5, 9, 7, 6, 8])
    assert find_level(s, "high") == 8


def test_low_is_most_frequent_running_min():
    # newest first: 2,4,2,1,3 -> running min 2,2,2,1,1 -> 2 appears most
    s = pd.Series([3, 1, 2, 4, 2])
    assert find_level(s, "low") == 2


def test_tie_goes_to_most_recent_level():
    # newest first: 5,9 -> running max 5,9 (tie) -> newest (5) wins
    assert find_level(pd.Series([9, 5]), "high") == 5


def test_lookback_ignores_old_candles():
    # the old 100 is outside a 3-candle window and must not count
    assert find_level(pd.Series([100, 1, 2, 3]), "high", lookback=3) == 3


def test_nan_gaps_are_ignored():
    assert find_level(pd.Series([float("nan"), 5, 8, 6]), "high") == 8


def test_empty_or_all_nan_raises():
    with pytest.raises(ValueError):
        find_level(pd.Series([], dtype=float), "high")
    with pytest.raises(ValueError):
        find_level(pd.Series([float("nan")]), "low")


def test_bad_kind_raises():
    with pytest.raises(ValueError):
        find_level(pd.Series([1, 2]), "middle")


def test_analyze_full_result():
    df = pd.DataFrame({
        "High": [5, 9, 7, 6, 8],
        "Low": [3, 1, 2, 4, 2],
        "Close": [4, 5, 5, 5, 7],
    })
    result = analyze("TEST", df)
    assert result == SymbolLevels("TEST", high=8.0, low=2.0, last_close=7.0, bars=5)


def test_analyze_missing_columns_raises():
    with pytest.raises(ValueError):
        analyze("TEST", pd.DataFrame({"Close": [1, 2]}))


def test_saving_twice_same_day_updates_not_duplicates():
    conn = storage.connect(":memory:")
    storage.save_levels(conn, [SymbolLevels("X", 10.0, 5.0, 7.0, 50)], run_date="2026-10-24")
    storage.save_levels(conn, [SymbolLevels("X", 11.0, 5.0, 7.0, 51)], run_date="2026-10-24")
    rows = storage.history(conn, "X")
    assert len(rows) == 1
    assert rows[0][1] == 11.0  # second run replaced the first


def test_history_is_newest_first():
    conn = storage.connect(":memory:")
    storage.save_levels(conn, [SymbolLevels("X", 1.0, 0.5, None, 10)], run_date="2026-10-23")
    storage.save_levels(conn, [SymbolLevels("X", 2.0, 0.5, None, 10)], run_date="2026-10-24")
    dates = [row[0] for row in storage.history(conn, "X")]
    assert dates == ["2026-10-24", "2026-10-23"]
