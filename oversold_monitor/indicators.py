"""Technical indicators used to detect oversold conditions.

Every function takes pandas Series/DataFrame of daily OHLCV data (oldest first)
and returns a Series aligned to the input index. Values before an indicator has
enough history are NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "rsi",
    "stochastic",
    "williams_r",
    "bollinger",
    "cci",
    "mfi",
    "sma",
    "atr",
    "pct_from_high",
]


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (the standard RSI).

    Returns 0-100. A flat-or-rising stretch with no losses yields 100.
    """
    delta = close.astype("float64").diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    # Wilder's smoothing == EMA with alpha = 1/period, seeded by a simple mean
    # of the first `period` values. `adjust=False` after seeding reproduces it.
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> RS is inf -> RSI 100; both zero (flat line) -> also 100.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 100.0)
    out[avg_gain.isna() | avg_loss.isna()] = np.nan
    return out


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder moving average: SMA seed, then (prev*(n-1) + x)/n."""
    values = series.to_numpy(dtype="float64", copy=True)
    out = np.full(values.shape, np.nan)
    if len(values) < period + 1:
        return pd.Series(out, index=series.index)

    # values[0] is NaN (from .diff()); seed with the mean of values[1..period].
    start = 1 if np.isnan(values[0]) else 0
    seed_slice = values[start : start + period]
    if len(seed_slice) < period or np.isnan(seed_slice).any():
        return pd.Series(out, index=series.index)

    prev = float(seed_slice.mean())
    out[start + period - 1] = prev
    for i in range(start + period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return pd.Series(out, index=series.index)


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Slow stochastic oscillator. Returns (%K, %D), both 0-100."""
    highest = high.rolling(period, min_periods=period).max()
    lowest = low.rolling(period, min_periods=period).min()
    span = highest - lowest
    raw_k = 100.0 * (close - lowest) / span.replace(0.0, np.nan)
    # A dead-flat range means price sits at both the high and the low; call it 50.
    raw_k = raw_k.where(span != 0, 50.0)
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(smooth_d, min_periods=smooth_d).mean()
    return k, d


def williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Williams %R, ranging -100 (at period low) to 0 (at period high)."""
    highest = high.rolling(period, min_periods=period).max()
    lowest = low.rolling(period, min_periods=period).min()
    span = highest - lowest
    out = -100.0 * (highest - close) / span.replace(0.0, np.nan)
    return out.where(span != 0, -50.0)


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger bands. Returns (middle, upper, lower, %B).

    %B is 0 at the lower band and 1 at the upper band; it goes negative when
    price closes below the lower band.
    """
    middle = close.rolling(period, min_periods=period).mean()
    # Population std is the convention for Bollinger bands.
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + num_std * std
    lower = middle - num_std * std
    width = upper - lower
    percent_b = (close - lower) / width.replace(0.0, np.nan)
    return middle, upper, lower, percent_b.where(width != 0, 0.5)


def cci(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.Series:
    """Commodity Channel Index. Below -100 is the classic oversold reading."""
    typical = (high + low + close) / 3.0
    ma = typical.rolling(period, min_periods=period).mean()
    mad = typical.rolling(period, min_periods=period).apply(
        lambda w: np.abs(w - w.mean()).mean(), raw=True
    )
    return (typical - ma) / (0.015 * mad.replace(0.0, np.nan))


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Money Flow Index — a volume-weighted RSI. Below 20 is oversold."""
    typical = (high + low + close) / 3.0
    raw_flow = typical * volume
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0)
    negative = raw_flow.where(direction < 0, 0.0)

    pos_sum = positive.rolling(period, min_periods=period).sum()
    neg_sum = negative.rolling(period, min_periods=period).sum()
    ratio = pos_sum / neg_sum.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + ratio))
    # No down-days in the window -> maximum reading.
    out = out.where(neg_sum != 0, 100.0)
    out[direction.isna()] = np.nan
    return out


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Average True Range (Wilder-smoothed)."""
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return _wilder(true_range, period)


def pct_from_high(close: pd.Series, period: int = 252) -> pd.Series:
    """Percent below the rolling high (negative number, e.g. -18.4)."""
    window = min(period, len(close))
    highest = close.rolling(window, min_periods=1).max()
    return 100.0 * (close - highest) / highest
