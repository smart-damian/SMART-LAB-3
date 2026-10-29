#!/usr/bin/env python3
"""market-levels: command-line entry point.

Usage:
    python main.py scan                 # fetch, calculate, print, save
    python main.py scan --no-save       # same, but don't touch the database
    python main.py scan --csv out.csv   # also write a CSV file
    python main.py history EURUSD=X     # past results for one symbol
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import storage
from fetcher import FetchError, extract_symbol, fetch_ohlc
from levels import SymbolLevels, analyze

# Paths are built from this file's location, not from the current folder,
# so the app works no matter where you launch it from in Termux.
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOLS = BASE_DIR / "symbols.txt"
DEFAULT_DB = BASE_DIR / "data" / "levels.db"


def load_symbols(path: Path) -> list[str]:
    """Read one symbol per line. Blank lines and # comments are ignored.

    Keeping symbols in a text file means you change the watchlist without
    touching code (separating configuration from logic).
    """
    if not path.exists():
        sys.exit(f"Symbols file not found: {path}")
    symbols = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            symbols.append(line)
    if not symbols:
        sys.exit(f"No symbols found in {path}")
    return list(dict.fromkeys(symbols))  # remove duplicates, keep order


def fmt(value: float | None) -> str:
    """Forex prices need 5 decimals; indices and metals read better with 2."""
    if value is None:
        return "-"
    return f"{value:.5f}" if abs(value) < 10 else f"{value:,.2f}"


def print_table(results: list[SymbolLevels]) -> None:
    print(f"{'SYMBOL':<10}{'HIGH':>14}{'LOW':>14}{'LAST':>14}{'BARS':>6}")
    print("-" * 58)
    for r in results:
        print(f"{r.symbol:<10}{fmt(r.high):>14}{fmt(r.low):>14}"
              f"{fmt(r.last_close):>14}{r.bars:>6}")


def write_csv(path: str, results: list[SymbolLevels]) -> None:
    with open(path, "w", newline="") as f:  # newline="" avoids blank rows on some systems
        writer = csv.writer(f)
        writer.writerow(["symbol", "high", "low", "last_close", "bars"])
        for r in results:
            writer.writerow([r.symbol, r.high, r.low, r.last_close, r.bars])


def cmd_scan(args: argparse.Namespace) -> int:
    symbols = load_symbols(Path(args.symbols_file))
    print(f"Fetching {len(symbols)} symbols ({args.period} of daily data)...")

    try:
        raw = fetch_ohlc(symbols, period=args.period)
    except FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    results: list[SymbolLevels] = []
    skipped: list[str] = []
    for symbol in symbols:
        df = extract_symbol(raw, symbol)
        try:
            if df is None:
                raise ValueError("no data")
            results.append(analyze(symbol, df, args.lookback))
        except ValueError:
            # One bad ticker must not kill the other 23.
            skipped.append(symbol)

    if not results:
        print("Error: no symbol produced usable data.", file=sys.stderr)
        return 1

    print()
    print_table(results)
    if skipped:
        print(f"\nSkipped (no usable data): {', '.join(skipped)}")

    if args.csv:
        write_csv(args.csv, results)
        print(f"\nCSV written to {args.csv}")

    if not args.no_save:
        conn = storage.connect(args.db)
        storage.save_levels(conn, results)
        conn.close()
        print(f"\nSaved {len(results)} rows to {args.db}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    conn = storage.connect(args.db)
    rows = storage.history(conn, args.symbol, args.limit)
    conn.close()
    if not rows:
        print(f"No saved history for {args.symbol}. Run 'scan' first.")
        return 1
    print(f"{'DATE':<12}{'HIGH':>14}{'LOW':>14}{'LAST':>14}")
    print("-" * 54)
    for run_date, high, low, last in rows:
        print(f"{run_date:<12}{fmt(high):>14}{fmt(low):>14}{fmt(last):>14}")
    return 0


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find recent high/low levels for a watchlist.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="fetch data and calculate levels")
    scan.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS))
    scan.add_argument("--period", default="4mo", help="Yahoo period, e.g. 1mo, 4mo, 1y (default 4mo)")
    scan.add_argument("--lookback", type=positive_int, default=120, help="max candles to use (default 120)")
    scan.add_argument("--db", default=str(DEFAULT_DB))
    scan.add_argument("--no-save", action="store_true", help="don't write to the database")
    scan.add_argument("--csv", help="also write results to this CSV file")
    scan.set_defaults(func=cmd_scan)

    hist = sub.add_parser("history", help="show saved results for one symbol")
    hist.add_argument("symbol")
    hist.add_argument("--limit", type=positive_int, default=10)
    hist.add_argument("--db", default=str(DEFAULT_DB))
    hist.set_defaults(func=cmd_history)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


# This guard means importing main.py (e.g. from a test) doesn't run the app.
if __name__ == "__main__":
    sys.exit(main())
