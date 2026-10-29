"""Offline tests for timeframes.py, notifier.py and scan_tf.py (no internet, no Telegram).

Run from the project root: python -m pytest -v
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

import notifier
import scan_tf
from alerts import SELL, Alert, Confluence
from timeframes import (ALERT2, LOOKBACK, TIMEFRAMES, Alert2Setting, get_timeframe,
                        resample_ohlc)


def test_all_six_timeframes_exist():
    assert list(TIMEFRAMES) == ["5m", "15m", "30m", "1h", "4h", "1d"]


def test_every_lookback_is_150():
    assert LOOKBACK == 150
    assert all(tf.lookback == 150 for tf in TIMEFRAMES.values())


def test_candle_lengths_in_minutes():
    assert {n: tf.minutes for n, tf in TIMEFRAMES.items()} == {
        "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def test_unknown_timeframe_gives_clear_error():
    with pytest.raises(ValueError, match="unknown timeframe"):
        get_timeframe("2m")


def test_4h_is_built_from_1h_because_yahoo_has_no_4h():
    tf = get_timeframe("4h")
    assert tf.interval == "1h" and tf.resample_to == "4h"


def test_resample_uses_max_of_highs_min_of_lows_last_close():
    idx = pd.date_range("2026-10-26 00:00", periods=4, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "Open": [1, 2, 3, 4], "High": [5, 9, 7, 6],
        "Low": [3, 1, 2, 4], "Close": [4, 5, 6, 7],
    }, index=idx)
    out = resample_ohlc(df, "4h")
    assert len(out) == 1
    row = out.iloc[0]
    assert (row["Open"], row["High"], row["Low"], row["Close"]) == (1, 9, 1, 7)


def test_resample_drops_empty_buckets():
    idx = pd.to_datetime(["2026-10-26 00:00", "2026-10-26 12:00"], utc=True)
    df = pd.DataFrame({"High": [2, 3], "Low": [1, 2]}, index=idx)
    assert len(resample_ohlc(df, "4h")) == 2  # the empty 4h buckets between are gone


def test_split_message_never_cuts_a_line():
    text = "\n".join(f"line-{i:03d}" for i in range(100))
    chunks = notifier.split_message(text, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 101 for c in chunks)
    assert "".join(chunks).split() == text.split()  # nothing lost or reordered


def test_send_requires_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(notifier.NotifyError):
        notifier.send_telegram("hi")


def _flat_candles(rows=130):
    """Every candle: Close 100, High 101, Low 99 -> True Range is always 2."""
    idx = pd.date_range("2026-08-01", periods=rows, freq="1D")
    return pd.DataFrame({"High": 101.0, "Low": 99.0, "Close": 100.0}, index=idx)


def _fake_fetch(symbols, period, interval):
    cols = pd.MultiIndex.from_product([["GOOD"], ["High", "Low", "Close"]])
    df = _flat_candles()
    df.columns = cols
    return df


def test_run_scan_skips_missing_symbol_but_keeps_good_one():
    results, atrs, skipped = scan_tf.run_scan(get_timeframe("1d"), ["GOOD", "BAD"], fetch=_fake_fetch)
    assert [r.symbol for r in results] == ["GOOD"]
    assert skipped == ["BAD"]
    assert abs(atrs["GOOD"] - 2.0) < 1e-9


def test_build_table_and_alert_message_text():
    tf = get_timeframe("1d")
    now = datetime(2026, 10, 26, 14, 5, tzinfo=timezone.utc)
    results, atrs, skipped = scan_tf.run_scan(tf, ["GOOD", "BAD"], fetch=_fake_fetch)
    table = scan_tf.build_table(tf, results, atrs, skipped, now=now)
    assert table.startswith("LEVELS 1d  2026-10-26 14:05 UTC")
    assert "GOOD" in table and "skipped: BAD" in table
    msg = scan_tf.build_alert_message(
        tf, [Alert("GOOD", SELL, 101.0, 100.0, 1.0, 2.0, 0.5)], 2.0, now=now)
    assert msg.startswith("ALERT 1 (LEVEL) 1d  2026-10-26 14:05 UTC  (within 2 ATR)")
    assert "SELL" in msg and "0.50" in msg


# ---- the single Alert 2 setting

def test_default_setting_watches_every_timeframe_and_needs_two_to_agree():
    assert ALERT2.timeframes == ("5m", "15m", "30m", "1h", "4h", "1d")
    assert ALERT2.min_agree == 2
    assert ALERT2.base == "5m"
    assert ALERT2.partners == ("15m", "30m", "1h", "4h", "1d")


def test_setting_sorts_fastest_first_whatever_the_input_order():
    s = Alert2Setting(("4h", "1h", "1d"), min_agree=2)
    assert s.timeframes == ("1h", "4h", "1d")
    assert s.base == "1h" and s.partners == ("4h", "1d")


def test_setting_rejects_bad_values():
    with pytest.raises(ValueError, match="at least two"):
        Alert2Setting(("5m",))
    with pytest.raises(ValueError, match="repeat"):
        Alert2Setting(("5m", "5m"))
    with pytest.raises(ValueError, match="unknown"):
        Alert2Setting(("5m", "2m"))
    with pytest.raises(ValueError, match="min_agree"):
        Alert2Setting(("5m", "15m"), min_agree=1)
    with pytest.raises(ValueError, match="min_agree"):
        Alert2Setting(("5m", "15m"), min_agree=3)


# ---- Alert 2 message

def _now():
    return datetime(2026, 10, 26, 14, 5, tzinfo=timezone.utc)


def test_confluence_message_lists_only_the_timeframes_that_agree():
    conf = Confluence("GOOD", SELL, 100.0, {"5m": 0.5, "4h": 1.25})
    msg = scan_tf.build_confluence_message([conf], 2.0, now=_now())
    assert msg.startswith("ALERT 2 (MULTI-TIMEFRAME)  2+ of 6 agree")
    assert "GOOD SELL @ 100.00  (2 agree)" in msg
    assert "5m 0.50" in msg and "4h 1.25" in msg
    assert "15m" not in msg and "30m" not in msg and "1d" not in msg


def test_confluence_message_wraps_to_three_timeframes_per_line():
    conf = Confluence("GOOD", SELL, 100.0,
                      {"5m": 0.5, "15m": 0.6, "30m": 0.7, "1h": 0.8})
    lines = scan_tf.build_confluence_message([conf], 2.0, now=_now()).splitlines()
    assert "  5m 0.50   15m 0.60   30m 0.70" in lines
    assert "  1h 0.80" in lines


def test_confluence_message_follows_a_custom_setting():
    conf = Confluence("GOOD", SELL, 100.0, {"1h": 0.5, "4h": 1.25, "1d": 0.9})
    setting = Alert2Setting(("1h", "4h", "1d"), min_agree=3)
    msg = scan_tf.build_confluence_message([conf], 2.0, now=_now(), setting=setting)
    assert msg.startswith("ALERT 2 (MULTI-TIMEFRAME)  3+ of 3 agree")
