"""Price history providers.

`yfinance` is the default (free, no API key). Stooq is a no-key CSV fallback
used automatically when Yahoo fails for a symbol.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


class ProviderError(RuntimeError):
    """Raised when a provider cannot return usable history for a symbol."""


@dataclass
class History:
    symbol: str
    frame: pd.DataFrame
    source: str


def _normalise(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Coerce a provider frame into oldest-first OHLCV with a clean index."""
    if isinstance(frame.columns, pd.MultiIndex):
        # yfinance returns (field, ticker) when handed a list of tickers.
        levels = [lvl for lvl in range(frame.columns.nlevels)]
        for level in levels:
            if symbol in frame.columns.get_level_values(level):
                frame = frame.xs(symbol, axis=1, level=level)
                break
        else:
            frame = frame.droplevel(-1, axis=1)

    frame = frame.rename(columns={c: str(c).title() for c in frame.columns})
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        # FX and index series often have no volume; synthesise it as zero so
        # volume-based indicators degrade gracefully instead of blowing up.
        if missing == ["Volume"]:
            frame["Volume"] = 0.0
        else:
            raise ProviderError(f"{symbol}: missing columns {missing}")

    frame = frame[list(REQUIRED_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["Close"])
    index = pd.to_datetime(frame.index)
    # Daily bars come back naive from some sources and tz-aware from others;
    # tz_localize(None) rejects an already-naive index on pandas 2.x.
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    frame.index = index
    return frame.sort_index()


def fetch_yfinance(symbol: str, lookback_days: int) -> pd.DataFrame:
    import yfinance as yf

    period = f"{max(lookback_days, 90)}d"
    frame = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if frame is None or frame.empty:
        raise ProviderError(f"{symbol}: yfinance returned no rows")
    return _normalise(frame, symbol)


# Yahoo suffixes Stooq does not share; map what we can and give up on the rest.
_STOOQ_SUFFIX = {"": ".us"}


def _stooq_symbol(symbol: str) -> str:
    s = symbol.lower()
    if s.startswith("^") or s.endswith("=f"):
        raise ProviderError(f"{symbol}: no Stooq equivalent")
    if s.endswith("-usd"):  # crypto, e.g. btc-usd -> btcusd
        return s.replace("-usd", "usd")
    if s.endswith("=x"):  # fx, e.g. eurusd=x -> eurusd
        return s[:-2]
    return s + _STOOQ_SUFFIX[""]


def fetch_stooq(symbol: str, lookback_days: int) -> pd.DataFrame:
    import requests

    mapped = _stooq_symbol(symbol)
    url = f"https://stooq.com/q/d/l/?s={mapped}&i=d"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    text = response.text.strip()
    if not text or text.lower().startswith("<"):
        raise ProviderError(f"{symbol}: Stooq returned no data")

    frame = pd.read_csv(io.StringIO(text))
    if "Date" not in frame.columns:
        raise ProviderError(f"{symbol}: unexpected Stooq payload")
    frame = frame.set_index("Date")
    return _normalise(frame, symbol).tail(max(lookback_days, 90))


PROVIDERS = {"yfinance": fetch_yfinance, "stooq": fetch_stooq}


def fetch_history(
    symbol: str, lookback_days: int = 400, order: tuple[str, ...] = ("yfinance", "stooq")
) -> History:
    """Try each provider in turn; return the first usable history."""
    errors = []
    for name in order:
        fetcher = PROVIDERS.get(name)
        if fetcher is None:
            errors.append(f"{name}: unknown provider")
            continue
        try:
            frame = fetcher(symbol, lookback_days)
        except Exception as exc:  # providers fail in many creative ways
            log.debug("%s failed for %s: %s", name, symbol, exc)
            errors.append(f"{name}: {exc}")
            continue
        if len(frame) >= 30:
            return History(symbol=symbol, frame=frame, source=name)
        errors.append(f"{name}: only {len(frame)} rows")

    raise ProviderError(f"{symbol}: all providers failed ({'; '.join(errors)})")


def drop_partial_bar(frame: pd.DataFrame, now: "pd.Timestamp | None" = None) -> pd.DataFrame:
    """Drop today's still-forming daily bar.

    A scan during market hours sees a bar built from only part of the session,
    so its RSI can swing on the opening print and flip back by lunchtime.
    Dropping it means indicators are computed on completed sessions only —
    stable readings, at the cost of being one session behind.
    """
    from zoneinfo import ZoneInfo

    if frame.empty:
        return frame

    eastern = ZoneInfo("America/New_York")
    now = pd.Timestamp.now(tz=eastern) if now is None else now
    # Before 16:00 ET the US session has not settled; after it, today is final.
    if now.hour >= 16:
        return frame

    last = frame.index[-1]
    if last.date() == now.date():
        return frame.iloc[:-1]
    return frame
