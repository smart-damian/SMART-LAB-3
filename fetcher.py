"""Network layer: download daily candles from Yahoo Finance via yfinance.

Kept separate from levels.py so the maths never needs internet to be tested.
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf


class FetchError(RuntimeError):
    """Raised when the whole download fails (no internet, Yahoo down, ...)."""


def fetch_ohlc(symbols: list[str], period: str = "4mo", interval: str = "1d") -> pd.DataFrame:
    """Download all symbols in ONE request.

    One batched call is much faster (and gentler on Yahoo's rate limits) than
    looping and calling download() 24 times.
    """
    try:
        data = yf.download(
            tickers=symbols,          # a list is safer than a space-joined string
            period=period,
            interval=interval,
            group_by="ticker",        # top column level = symbol
            threads=True,
            progress=False,           # progress bars make a mess of phone terminals
            auto_adjust=False,        # we want raw High/Low, not dividend-adjusted
        )
    except Exception as exc:  # yfinance raises many different error types
        raise FetchError(f"download failed: {exc}") from exc

    if data is None or data.empty:
        raise FetchError("Yahoo returned no data (check internet / try again later)")
    return data


def extract_symbol(raw: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """Pull one symbol's table out of the combined download.

    Returns None when the symbol is missing or has no rows. The original code
    did df[(symbol, "High")] directly, which crashes the whole run with a
    KeyError as soon as ONE ticker fails. Here one bad ticker is skipped.
    """
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        # yfinance versions differ on which level holds the ticker, so check both.
        if symbol in cols.get_level_values(0):
            df = raw[symbol]
        elif symbol in cols.get_level_values(1):
            df = raw.xs(symbol, axis=1, level=1)
        else:
            return None
    else:
        df = raw  # single-ticker downloads can come back flat

    df = df.dropna(how="all")
    return df if not df.empty else None
