"""Timeframe settings and candle resampling.

Why a separate file: every number that differs between 5m and 1d lives HERE,
in one table. To tune a timeframe you edit this table, not the logic.

Yahoo Finance limits (the reason the numbers below look the way they do):
  * 5m, 15m, 30m candles: only the last ~60 days are available
  * 1h candles:           only the last ~730 days
  * 4h candles:           NOT offered at all, so we download 1h and build 4h
                          candles ourselves (see resample_ohlc)
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# How many of the newest candles feed the high/low calculation (and the ATR).
# One shared number so all timeframes are tuned together; change it here only.
LOOKBACK = 150


@dataclass(frozen=True)
class Timeframe:
    name: str                  # label used on the command line and in Telegram
    interval: str              # the interval we ask Yahoo for
    period: str                # how far back to download (must hold >= LOOKBACK candles)
    minutes: int               # length of one candle; used to judge if saved state is stale
    lookback: int = LOOKBACK   # max candles fed to find_level() and ATR
    resample_to: str | None = None   # pandas rule, only when Yahoo lacks the interval


# `period` is chosen so that a download always contains MORE than LOOKBACK
# candles, even for symbols that skip weekends. Example: "4mo" of daily candles
# is only ~85 weekday candles, so 1d uses "1y" now that the lookback is 150.
TIMEFRAMES: dict[str, Timeframe] = {
    "5m": Timeframe("5m", interval="5m", period="5d", minutes=5),
    "15m": Timeframe("15m", interval="15m", period="5d", minutes=15),
    "30m": Timeframe("30m", interval="30m", period="1mo", minutes=30),
    "1h": Timeframe("1h", interval="1h", period="3mo", minutes=60),
    "4h": Timeframe("4h", interval="1h", period="6mo", minutes=240, resample_to="4h"),
    "1d": Timeframe("1d", interval="1d", period="1y", minutes=1440),
}


# ============================================================================
# ALERT 2 SETTING - the ONE place that controls the multi-timeframe alert.
# Edit only the values inside ALERT2(...) below; code, workflow and Telegram
# messages all read from it.
#
#   timeframes : which timeframes are watched. Default: ALL of the ones above.
#                Or list some, e.g. ("5m", "1h", "4h"). Order doesn't matter.
#   min_agree  : how many of them must show the SAME signal (SELL or BUY) on the
#                same symbol before Alert 2 fires. Alert 2 then tells you which
#                timeframes agree.
# ============================================================================
@dataclass(frozen=True)
class Alert2Setting:
    timeframes: tuple[str, ...]
    min_agree: int = 2

    def __post_init__(self):
        names = tuple(self.timeframes)
        # Bad settings fail immediately with a clear message ("fail fast"),
        # instead of silently never alerting.
        if len(names) < 2:
            raise ValueError("Alert 2 needs at least two timeframes")
        if len(set(names)) != len(names):
            raise ValueError("Alert 2 timeframes must not repeat")
        unknown = [n for n in names if n not in TIMEFRAMES]
        if unknown:
            raise ValueError(f"unknown timeframe(s) in the Alert 2 setting: {', '.join(unknown)}; "
                             f"choose from {', '.join(TIMEFRAMES)}")
        if not 2 <= self.min_agree <= len(names):
            raise ValueError(f"min_agree must be between 2 and {len(names)}, got {self.min_agree}")
        # Keep them fastest-first so display order and the "base" are predictable.
        object.__setattr__(self, "timeframes", tuple(sorted(names, key=lambda n: TIMEFRAMES[n].minutes)))

    @property
    def base(self) -> str:
        """The FASTEST timeframe. Its job evaluates Alert 2 (it runs most often,
        so the alert is as little late as possible) and READS the saved results of the others."""
        return self.timeframes[0]

    @property
    def partners(self) -> tuple[str, ...]:
        """Every other timeframe in the setting: their saved results are read by the base run."""
        return self.timeframes[1:]


ALERT2 = Alert2Setting(
    timeframes=tuple(TIMEFRAMES),   # all timeframes above
    min_agree=2,                    # any 2 of them agreeing is enough
)


def get_timeframe(name: str) -> Timeframe:
    """Look up a timeframe, with a helpful error instead of a bare KeyError."""
    try:
        return TIMEFRAMES[name]
    except KeyError:
        raise ValueError(f"unknown timeframe {name!r}; choose from {', '.join(TIMEFRAMES)}") from None


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Merge small candles into bigger ones (e.g. four 1h candles -> one 4h).

    A bigger candle's High is the max of the small Highs, its Low the min of the
    small Lows, its Close the last Close. Averaging would be wrong: a High is
    "the highest price reached", not an average.

    Caveat: bucket edges follow the timezone of Yahoo's timestamps, so they may
    not line up with your broker's 4h candles.
    """
    rules = {"High": "max", "Low": "min"}
    if "Open" in df.columns:
        rules["Open"] = "first"
    if "Close" in df.columns:
        rules["Close"] = "last"
    out = df.resample(rule).agg(rules)
    # Empty buckets (market closed) become NaN rows; drop them.
    return out.dropna(subset=["High", "Low"])
