"""Indicator maths, checked against published reference values."""

import numpy as np
import pandas as pd
import pytest

from oversold_monitor import indicators as ind

# Wilder's own worked example from "New Concepts in Technical Trading Systems".
WILDER_CLOSES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245,
    45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028,
    46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515,
    45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628,
    43.1314,
]
# The RSI(14) series this data is known to produce (to 2dp, as published).
WILDER_RSI = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38,
    54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77,
]


def test_rsi_matches_wilder_reference():
    result = ind.rsi(pd.Series(WILDER_CLOSES), 14).dropna()
    assert len(result) == len(WILDER_RSI)
    np.testing.assert_allclose(result.to_numpy(), WILDER_RSI, atol=0.005)


def test_rsi_warmup_is_nan():
    result = ind.rsi(pd.Series(WILDER_CLOSES), 14)
    # No RSI until 14 changes exist, i.e. the first 14 rows.
    assert result.iloc[:14].isna().all()
    assert not np.isnan(result.iloc[14])


def test_rsi_bounds_and_extremes():
    rising = pd.Series(np.arange(100, 160, dtype=float))
    assert ind.rsi(rising).iloc[-1] == pytest.approx(100.0)

    falling = pd.Series(np.arange(160, 100, -1, dtype=float))
    assert ind.rsi(falling).iloc[-1] == pytest.approx(0.0, abs=1e-9)

    flat = pd.Series([50.0] * 60)
    assert ind.rsi(flat).iloc[-1] == pytest.approx(100.0)

    noisy = pd.Series(np.random.default_rng(0).normal(100, 5, 300).cumsum() / 10 + 100)
    values = ind.rsi(noisy).dropna()
    assert values.between(0, 100).all()


def test_rsi_too_short_returns_all_nan():
    assert ind.rsi(pd.Series([1.0, 2.0, 3.0]), 14).isna().all()


def _ohlc(closes):
    close = pd.Series(closes, dtype=float)
    return close + 0.5, close - 0.5, close


def test_stochastic_hits_zero_at_the_period_low():
    closes = list(np.linspace(100, 80, 30))
    high, low, close = _ohlc(closes)
    k, d = ind.stochastic(high, low, close, period=14, smooth_k=1, smooth_d=3)
    # Falling monotonically: every close is the lowest of its window, but the
    # low series sits 0.5 below it, so %K is small but positive.
    assert k.iloc[-1] < 10
    assert d.dropna().between(0, 100).all()


def test_stochastic_flat_range_is_midpoint():
    flat = pd.Series([25.0] * 40)
    k, _ = ind.stochastic(flat, flat, flat, period=14, smooth_k=1)
    assert k.iloc[-1] == pytest.approx(50.0)


def test_williams_r_endpoints():
    closes = list(np.linspace(50, 20, 40))
    high, low, close = _ohlc(closes)
    wr = ind.williams_r(high, low, close, 14)
    assert -100 <= wr.iloc[-1] <= 0
    assert wr.iloc[-1] < -80  # sitting at the bottom of the range


def test_bollinger_percent_b_geometry():
    rng = np.random.default_rng(7)
    close = pd.Series(100 + rng.normal(0, 2, 200).cumsum() * 0.1)
    middle, upper, lower, pb = ind.bollinger(close, 20, 2.0)
    assert (upper.dropna() > middle.dropna()).all()
    assert (lower.dropna() < middle.dropna()).all()
    # %B is defined so that price == lower band gives 0 and == upper gives 1.
    idx = close.index[-1]
    recomputed = (close[idx] - lower[idx]) / (upper[idx] - lower[idx])
    assert pb[idx] == pytest.approx(recomputed)


def test_percent_b_goes_negative_below_the_band():
    close = pd.Series([100.0] * 25 + [70.0])
    _, _, lower, pb = ind.bollinger(close, 20, 2.0)
    assert close.iloc[-1] < lower.iloc[-1]
    assert pb.iloc[-1] < 0


def test_cci_negative_on_a_sharp_drop():
    closes = [100.0] * 25 + [90.0, 85.0, 80.0]
    high, low, close = _ohlc(closes)
    assert ind.cci(high, low, close, 20).iloc[-1] < -100


def test_mfi_bounds_and_direction():
    rng = np.random.default_rng(3)
    closes = pd.Series(np.linspace(100, 60, 60))
    high, low, close = closes + 1, closes - 1, closes
    volume = pd.Series(rng.integers(1_000, 5_000, 60).astype(float))
    values = ind.mfi(high, low, close, volume, 14).dropna()
    assert values.between(0, 100).all()
    assert values.iloc[-1] < 20  # relentless selling


def test_atr_is_positive_and_tracks_range():
    rng = np.random.default_rng(11)
    close = pd.Series(100 + rng.normal(0, 1, 100).cumsum())
    high, low = close + 2, close - 2
    values = ind.atr(high, low, close, 14).dropna()
    assert (values > 0).all()
    assert values.iloc[-1] >= 3.0  # at least the intraday range


def test_pct_from_high():
    close = pd.Series([100.0, 120.0, 90.0])
    assert ind.pct_from_high(close).iloc[-1] == pytest.approx(-25.0)
    assert ind.pct_from_high(close).iloc[1] == pytest.approx(0.0)
