#!/usr/bin/env python3
"""Multi-timeframe scan: one timeframe per run; Telegram only on NEW alerts.

Usage:
    python scan_tf.py 1h                  # scan, print, alert Telegram if there is something new
    python scan_tf.py 4h --no-telegram    # dry run: print only, nothing sent, nothing remembered
    python scan_tf.py 15m --atr-mult 3    # alert within 3 ATR instead of 2

Two alerts (rules live in alerts.py):
    ALERT 1 (LEVEL)           SELL near the high, BUY near the low, on this timeframe
    ALERT 2 (MULTI-TIMEFRAME) same symbol + same signal on at least `min_agree` of the watched
                              timeframes (the ALERT2 setting in timeframes.py); the message lists
                              which timeframes agree. Evaluated by the fastest watched timeframe.

Design: GitHub Actions starts this script once per schedule tick with ONE
timeframe. The run is: fetch -> calculate -> decide (plan_alerts) -> send ->
remember. The full table is always printed (visible in the Actions log).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from alerts import (ATR_MULT, ATR_PERIOD, Alert, Confluence, compute_atr,
                    find_alerts, plan_alerts)
from fetcher import FetchError, extract_symbol, fetch_ohlc
from levels import SymbolLevels, analyze
from main import DEFAULT_SYMBOLS, fmt, load_symbols
from notifier import NotifyError, send_telegram
from state import load_state, save_state, state_path
from timeframes import ALERT2, TIMEFRAMES, Alert2Setting, Timeframe, get_timeframe, resample_ohlc

DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "state"


def run_scan(tf: Timeframe, symbols: list[str], atr_period: int = ATR_PERIOD,
             fetch: Callable = fetch_ohlc) -> tuple[list[SymbolLevels], dict[str, float], list[str]]:
    """Fetch and analyse every symbol for one timeframe.

    Returns (levels, atr per symbol, skipped symbols).
    `fetch` is a parameter so tests can pass a fake downloader instead of
    hitting the internet (dependency injection: pass in what a function needs).
    """
    raw = fetch(symbols, period=tf.period, interval=tf.interval)
    results: list[SymbolLevels] = []
    atrs: dict[str, float] = {}
    skipped: list[str] = []
    for symbol in symbols:
        df = extract_symbol(raw, symbol)
        try:
            if df is None:
                raise ValueError("no data")
            if tf.resample_to:
                df = resample_ohlc(df, tf.resample_to)
            # ATR uses the same newest-`lookback` candles as the levels, so
            # both numbers describe the same stretch of market.
            atr = compute_atr(df.tail(tf.lookback), atr_period)
            results.append(analyze(symbol, df, tf.lookback))
            atrs[symbol] = atr
        except ValueError:
            skipped.append(symbol)  # one bad ticker must not stop the others
    return results, atrs, skipped


def build_table(tf: Timeframe, results: list[SymbolLevels], atrs: dict[str, float],
                skipped: list[str], now: datetime | None = None) -> str:
    """Full table for the console / Actions log."""
    now = now or datetime.now(timezone.utc)
    lines = [
        f"LEVELS {tf.name}  {now:%Y-%m-%d %H:%M} UTC",
        f"{'SYMBOL':<9}{'HIGH':>11}{'LOW':>11}{'LAST':>11}{'ATR':>11}",
    ]
    for r in results:
        lines.append(f"{r.symbol:<9}{fmt(r.high):>11}{fmt(r.low):>11}"
                     f"{fmt(r.last_close):>11}{fmt(atrs[r.symbol]):>11}")
    if skipped:
        lines.append(f"skipped: {', '.join(skipped)}")
    return "\n".join(lines)


def build_alert_message(tf: Timeframe, alerts: list[Alert], mult: float,
                        now: datetime | None = None) -> str:
    """ALERT 1: short table for Telegram. SELL = near the high, BUY = near the low."""
    now = now or datetime.now(timezone.utc)
    lines = [
        f"ALERT 1 (LEVEL) {tf.name}  {now:%Y-%m-%d %H:%M} UTC  (within {mult:g} ATR)",
        f"{'SYMBOL':<9}{'SIGNAL':<7}{'LEVEL':>11}{'LAST':>11}{'ATRs':>6}",
    ]
    for a in alerts:
        lines.append(f"{a.symbol:<9}{a.signal:<7}{fmt(a.level):>11}{fmt(a.last):>11}"
                     f"{a.atr_multiple:>6.2f}")
    return "\n".join(lines)


def build_confluence_message(confluences: list[Confluence], mult: float,
                             now: datetime | None = None, setting: Alert2Setting = ALERT2) -> str:
    """ALERT 2: one entry per symbol and signal, listing ONLY the timeframes that agree,
    each with its distance from the level in ATRs. Entries are narrow (3 timeframes per
    line) so they fit a phone screen."""
    now = now or datetime.now(timezone.utc)
    lines = [
        f"ALERT 2 (MULTI-TIMEFRAME)  {setting.min_agree}+ of {len(setting.timeframes)} agree",
        f"{now:%Y-%m-%d %H:%M} UTC  ATRs from level, limit {mult:g}",
    ]
    for c in confluences:
        lines.append("")
        lines.append(f"{c.symbol} {c.signal} @ {fmt(c.last)}  ({len(c.multiples)} agree)")
        cells = [f"{tf} {m:.2f}" for tf, m in c.multiples.items()]
        for i in range(0, len(cells), 3):
            lines.append("  " + "   ".join(cells[i:i + 3]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan one timeframe; alert Telegram on new signals.")
    parser.add_argument("timeframe", choices=list(TIMEFRAMES))
    parser.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS))
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR),
                        help="where the alert memory (JSON) is kept")
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    parser.add_argument("--atr-mult", type=float, default=ATR_MULT,
                        help=f"alert when within this many ATRs of a high/low (default {ATR_MULT:g})")
    parser.add_argument("--no-telegram", action="store_true",
                        help="dry run: print only; nothing is sent and nothing is remembered")
    args = parser.parse_args()
    if args.atr_period < 1 or args.atr_mult <= 0:
        parser.error("--atr-period must be >= 1 and --atr-mult must be > 0")

    tf = get_timeframe(args.timeframe)
    symbols = load_symbols(Path(args.symbols_file))
    print(f"[{tf.name}] fetching {len(symbols)} symbols ({tf.period} of {tf.interval} candles)...")

    try:
        results, atrs, skipped = run_scan(tf, symbols, args.atr_period)
    except FetchError as exc:
        # Non-zero exit makes the GitHub run show red and email you.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not results:
        print("Error: no symbol produced usable data.", file=sys.stderr)
        return 1

    print(build_table(tf, results, atrs, skipped))

    # --- decide: who is near a level now, and which of those are NEW?
    now = datetime.now(timezone.utc)
    alerts_now = [a for r in results for a in find_alerts(r, atrs[r.symbol], args.atr_mult)]
    own_path = state_path(args.state_dir, tf.name)
    partners = {}
    if tf.name == ALERT2.base:  # only the fastest Alert 2 timeframe evaluates it
        partners = {n: load_state(state_path(args.state_dir, n)) for n in ALERT2.partners}
    last_prices = {r.symbol: r.last_close for r in results}
    plan = plan_alerts(tf.name, alerts_now, skipped, load_state(own_path), partners, now,
                       last_prices=last_prices)

    for note in plan.notes:
        print(f"Note: {note}")
    if plan.still_near:
        print(f"{plan.still_near} signal(s) still active but already alerted earlier: not repeated.")

    sections = []
    if plan.new_alerts:
        sections.append(build_alert_message(tf, plan.new_alerts, args.atr_mult, now))
    if plan.confluences:
        sections.append(build_confluence_message(plan.confluences, args.atr_mult, now))
    message = "\n\n".join(sections)  # ONE Telegram message, so it is sent or not as a whole
    print("\n" + (message if message else "No new alerts."))

    if args.no_telegram:
        print("(dry run: nothing sent, state not saved)")
        return 0

    # --- send first, remember second. If sending fails we exit WITHOUT saving,
    # so the next run tries again instead of silently losing the alert.
    if message:
        try:
            send_telegram(message)
        except NotifyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print("Sent to Telegram.")
    save_state(own_path, plan.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
