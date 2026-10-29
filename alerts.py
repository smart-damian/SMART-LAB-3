"""ATR, the BUY/SELL "near a level" rule, repeat suppression and the
multi-timeframe alert.

Everything here is pure: numbers and dicts in, answer out. No internet, no
files. That keeps the trading rules easy to test and easy to change later.
(Reading and writing the saved state lives in state.py.)

ATR (Average True Range) in plain words
---------------------------------------
How far a candle typically moves on this symbol and timeframe: the "normal
size of a move". It makes distances comparable: 0.0050 is huge for EURUSD on
5m candles but tiny for gold on daily candles. "Within 2 ATR" means "within
two normal candle-moves", whatever the symbol.

True Range of one candle = the biggest of:
  High - Low,  |High - previous Close|,  |Low - previous Close|
(the last two catch gaps between candles). ATR = a smoothed average of those.

The two alerts
--------------
ALERT 1 (LEVEL): per timeframe.
    SELL when (high - last price) <= 2 ATR   -> price is close to the high
    BUY  when (last price - low)  <= 2 ATR   -> price is close to the low
    Sent only when the condition is NEW (see "repeat suppression").
ALERT 2 (MULTI-TIMEFRAME): the same symbol has the same signal (SELL or BUY)
    on at least `min_agree` of the watched timeframes at the same time (the
    ALERT2 setting in timeframes.py: by default any 2 of all 6). The message
    lists exactly which timeframes agree.

Repeat suppression ("edge triggering")
--------------------------------------
We remember which symbols were already near a level. An alert fires only when
a symbol ENTERS the zone, not while it stays there. When it leaves the zone the
memory is cleared, so the next entry alerts again. Same idea as a doorbell:
it rings when someone arrives, not for as long as they stand at the door.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from levels import SymbolLevels
from timeframes import ALERT2, TIMEFRAMES, Alert2Setting

# Assumptions (change them here or via the CLI).
ATR_PERIOD = 100   # smoothing length requested for this project
ATR_MULT = 2.0     # alert when price is within this many ATRs of a level

SELL = "SELL"      # price is near the HIGH
BUY = "BUY"        # price is near the LOW

# Alert 2 is evaluated by the run of the FASTEST timeframe in the setting (the
# "base"), which reads the saved results of the slower ones (the "partners").
# One evaluator = one writer of the "already alerted" memory, so there is
# nothing to race on. Which timeframes these are: the ALERT2 setting in timeframes.py.

# A partner's saved result older than this many of ITS candles is not trusted.
# (GitHub can delay or skip scheduled runs, so some slack is needed.)
STALE_FACTOR = 4


@dataclass(frozen=True)
class Alert:
    symbol: str
    signal: str          # SELL or BUY
    level: float         # the high (for SELL) or the low (for BUY)
    last: float
    distance: float      # high - last (SELL) or last - low (BUY)
    atr: float
    atr_multiple: float  # distance / atr, e.g. 1.4 means "1.4 ATRs away"


@dataclass(frozen=True)
class Confluence:
    symbol: str
    signal: str
    last: float | None   # newest price seen by the evaluator (None if it had no data)
    multiples: dict      # ONLY the timeframes that agree: name -> ATRs from the level (fastest first)


@dataclass
class Plan:
    """What one run decided: what to send, and what to remember afterwards."""
    new_alerts: list[Alert] = field(default_factory=list)
    confluences: list[Confluence] = field(default_factory=list)
    still_near: int = 0                  # in the zone but already alerted earlier
    state: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- ATR

def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range for every candle. The first candle has no previous close, so
    max() simply ignores that missing piece and uses High - Low."""
    prev_close = df["Close"].shift(1)
    parts = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    )
    return parts.max(axis=1)


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """Latest ATR value using Wilder's smoothing. Raises ValueError if it can't be trusted.

    Steps (this is the textbook method):
      1. Start with the plain average of the first `period` True Ranges.
      2. For every later candle:  atr = (previous_atr * (period - 1) + TR) / period
         so each new candle moves the ATR by only 1/period (slow, smooth).
    Seeding with a plain average (instead of the first candle alone) matters
    with a long period: otherwise one old candle would keep ~20% weight.
    With 150 candles and period 100: 100 for the seed + 50 smoothing steps.
    """
    missing = {"High", "Low", "Close"} - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    clean = df[["High", "Low", "Close"]].dropna()
    if len(clean) < period + 1:
        # Too few candles and the average is mostly noise.
        raise ValueError(f"need at least {period + 1} candles for ATR, got {len(clean)}")
    tr = true_range(clean).tolist()
    atr = sum(tr[:period]) / period
    for value in tr[period:]:
        atr = (atr * (period - 1) + value) / period
    if atr <= 0:
        raise ValueError("ATR is zero (no price movement)")
    return float(atr)


# ---------------------------------------------------------------- Alert 1

def find_alerts(result: SymbolLevels, atr: float, mult: float = ATR_MULT) -> list[Alert]:
    """SELL when the high is within `mult` ATRs above price; BUY when the low is
    within `mult` ATRs below it.

    By construction high >= last price >= low (the high/low come from the same
    candles that end at the last price), so these distances are never negative.
    """
    last = result.last_close
    if last is None:
        return []
    checks = (
        (SELL, result.high, result.high - last),
        (BUY, result.low, last - result.low),
    )
    alerts: list[Alert] = []
    for signal, level, distance in checks:
        if distance <= mult * atr:   # <= so "exactly 2 ATR" counts
            alerts.append(Alert(result.symbol, signal, level, last, distance, atr, distance / atr))
    return alerts


# ---------------------------------------------------------------- state helpers

def empty_state() -> dict:
    return {"updated": None, "zones": {}, "confluence": {}}


def zones_from_alerts(alerts: list[Alert]) -> dict:
    """{symbol: {signal: atr_multiple}} - who is near a level right now."""
    zones: dict = {}
    for a in alerts:
        zones.setdefault(a.symbol, {})[a.signal] = round(a.atr_multiple, 2)
    return zones


def fresh_alerts(alerts: list[Alert], previous_zones: dict) -> list[Alert]:
    """Keep only alerts whose (symbol, signal) was NOT already in the zone last run."""
    return [a for a in alerts if a.signal not in previous_zones.get(a.symbol, {})]


def is_usable(state: dict, tf_name: str, now: datetime) -> bool:
    """True if `state` was saved recently enough to trust for timeframe `tf_name`."""
    updated = state.get("updated")
    if not updated:
        return False
    try:
        stamp = datetime.fromisoformat(updated)
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp <= timedelta(minutes=STALE_FACTOR * TIMEFRAMES[tf_name].minutes)


# ---------------------------------------------------------------- Alert 2

def find_confluence(all_zones: dict, last_prices: dict, setting: Alert2Setting = ALERT2) -> list[Confluence]:
    """Find (symbol, signal) pairs that at least `setting.min_agree` timeframes agree on.

    all_zones: {timeframe name: zones}, where zones is {symbol: {signal: atr_multiple}}.
    Only timeframes that are actually available appear in it. A SELL on one
    timeframe and a BUY on another do NOT agree: votes are counted per signal.
    """
    votes: dict = {}
    for name in setting.timeframes:                 # fastest first
        for symbol, signals in all_zones.get(name, {}).items():
            for signal, multiple in signals.items():
                votes.setdefault((symbol, signal), {})[name] = multiple
    found = [Confluence(symbol, signal, last_prices.get(symbol), tfs)
             for (symbol, signal), tfs in sorted(votes.items())
             if len(tfs) >= setting.min_agree]
    return found


def _normalize_confluence_memory(memory: dict) -> dict:
    """{symbol: {signal: [timeframes]}}. Older files stored {symbol: [signals]}; read
    those too (with an unknown timeframe list) instead of crashing."""
    clean: dict = {}
    for symbol, value in memory.items():
        if isinstance(value, dict):
            clean[symbol] = {sig: list(tfs) for sig, tfs in value.items()}
        elif isinstance(value, list):
            clean[symbol] = {sig: [] for sig in value}
    return clean


# ---------------------------------------------------------------- the decision

def plan_alerts(tf_name: str, alerts_now: list[Alert], skipped: list[str], previous: dict,
                partner_states: dict | None = None, now: datetime | None = None,
                last_prices: dict | None = None, setting: Alert2Setting = ALERT2) -> Plan:
    """Decide what to send and what to remember. Pure: no I/O, so it is fully testable.

    previous        this timeframe's state saved by its last run
    partner_states  {timeframe name: state} of the other Alert 2 timeframes; only used by the base run
    skipped         symbols whose data failed this run: their memory is carried
                    over unchanged, otherwise they would re-alert when data returns
    last_prices     {symbol: newest price} from this run, shown in the Alert 2 message
    setting         which timeframes Alert 2 watches and how many must agree

    Alert 2 is "new" when a timeframe has JOINED the agreeing group since the last
    message for that symbol and signal. So you hear about the first time two agree,
    and again when a further timeframe joins, but not while the same group stays put.
    """
    now = now or datetime.now(timezone.utc)
    prev_zones = previous.get("zones", {})

    zones = zones_from_alerts(alerts_now)
    for symbol in skipped:
        if symbol in prev_zones:
            zones[symbol] = prev_zones[symbol]

    new = fresh_alerts(alerts_now, prev_zones)
    plan = Plan(new_alerts=new, still_near=len(alerts_now) - len(new),
                state={"updated": now.isoformat(), "zones": zones})

    if tf_name == setting.base:
        prev_conf = _normalize_confluence_memory(previous.get("confluence", {}))
        usable: dict = {}
        for name in setting.partners:
            st = (partner_states or {}).get(name)
            if st is not None and is_usable(st, name, now):
                usable[name] = st.get("zones", {})
            else:
                plan.notes.append(f"{name} result is missing or older than {STALE_FACTOR} candles: "
                                  f"not counted this run")
        available = 1 + len(usable)   # the base timeframe itself + the usable partners
        if available >= setting.min_agree:
            now_conf = find_confluence({setting.base: zones, **usable}, last_prices or {}, setting)
            conf_state: dict = {}
            for c in now_conf:
                conf_state.setdefault(c.symbol, {})[c.signal] = list(c.multiples)
            plan.confluences = [
                c for c in now_conf
                if any(tf not in prev_conf.get(c.symbol, {}).get(c.signal, []) for tf in c.multiples)]
        else:
            plan.notes.append(f"only {available} timeframe(s) available but {setting.min_agree} must agree: "
                              f"multi-timeframe check skipped this run")
            conf_state = prev_conf   # couldn't evaluate: keep memory so nothing repeats later
        plan.state["confluence"] = conf_state

    return plan
