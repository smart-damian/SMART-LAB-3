"""Tests for the ATR maths and the BUY/SELL 'near a level' rule. Numbers are tiny
and hand-made so you can check each answer with a pencil."""
import pandas as pd
import pytest

from alerts import ATR_PERIOD, BUY, SELL, compute_atr, find_alerts, true_range
from levels import SymbolLevels


def _candles(highs, lows, closes):
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes})


def test_atr_period_is_100():
    assert ATR_PERIOD == 100


def test_true_range_uses_gap_to_previous_close():
    # candle 2 sits at 11-12 but the previous close was 10:
    # max(12-11=1, |12-10|=2, |11-10|=1) = 2, bigger than its own High-Low
    tr = true_range(_candles([11, 12], [9, 11], [10, 12]))
    assert list(tr) == [2, 2]


def test_first_candle_true_range_is_high_minus_low():
    assert true_range(_candles([15], [10], [12])).iloc[0] == 5


def test_atr_of_constant_range_candles_is_that_range():
    n = 130   # default period is 100, so we need at least 101 candles
    atr = compute_atr(_candles([101.0] * n, [99.0] * n, [100.0] * n))
    assert atr == pytest.approx(2.0)


def test_atr_wilder_hand_example():
    # True Ranges are 2, 4, 6. With period 2:
    # seed = (2 + 4) / 2 = 3, then (3 * 1 + 6) / 2 = 4.5
    df = _candles([12, 13, 14], [10, 9, 8], [11, 11, 11])
    assert list(true_range(df)) == [2, 4, 6]
    assert compute_atr(df, period=2) == pytest.approx(4.5)


def test_atr_needs_enough_candles():
    with pytest.raises(ValueError, match="at least"):
        compute_atr(_candles([2] * 50, [1] * 50, [1.5] * 50))   # 50 < 101


def test_atr_zero_movement_raises():
    n = 130
    with pytest.raises(ValueError, match="zero"):
        compute_atr(_candles([5.0] * n, [5.0] * n, [5.0] * n))


def test_atr_missing_close_raises():
    with pytest.raises(ValueError, match="missing"):
        compute_atr(pd.DataFrame({"High": [1] * 120, "Low": [1] * 120}))


def _levels(high, low, last):
    return SymbolLevels("X", high=high, low=low, last_close=last, bars=100)


def test_no_alert_when_both_levels_are_far():
    # ATR 1 -> threshold 2. High is 10 above, low is 10 below.
    assert find_alerts(_levels(110, 90, 100), atr=1.0) == []


def test_sell_when_price_is_near_the_high():
    alerts = find_alerts(_levels(101.5, 80, 100), atr=1.0)
    assert [(a.signal, a.level) for a in alerts] == [(SELL, 101.5)]
    assert alerts[0].atr_multiple == pytest.approx(1.5)


def test_buy_when_price_is_near_the_low():
    alerts = find_alerts(_levels(130, 98.5, 100), atr=1.0)
    assert [(a.signal, a.level) for a in alerts] == [(BUY, 98.5)]
    assert alerts[0].atr_multiple == pytest.approx(1.5)


def test_both_signals_in_a_very_tight_range():
    assert [a.signal for a in find_alerts(_levels(101, 99, 100), atr=1.0)] == [SELL, BUY]


def test_exactly_two_atr_counts():
    assert [a.signal for a in find_alerts(_levels(110, 50, 100), atr=5.0)] == [SELL]  # 10 == 2 * 5


def test_just_over_two_atr_does_not_count():
    assert find_alerts(_levels(110.01, 50, 100), atr=5.0) == []
    assert find_alerts(_levels(150, 89.99, 100), atr=5.0) == []


def test_custom_multiplier():
    assert find_alerts(_levels(103, 50, 100), atr=1.0, mult=2.0) == []
    assert len(find_alerts(_levels(103, 50, 100), atr=1.0, mult=3.0)) == 1


def test_missing_last_price_gives_no_alert():
    assert find_alerts(_levels(101, 99, None), atr=1.0) == []
