"""Turn price history into a graded oversold reading."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any

import pandas as pd

from . import indicators as ind
from .config import Config, Instrument

# Severity ladder, weakest first.
NEUTRAL = "neutral"
WATCH = "watch"
OVERSOLD = "oversold"
DEEP = "deeply_oversold"
SEVERITY_ORDER = {NEUTRAL: 0, WATCH: 1, OVERSOLD: 2, DEEP: 3}


@dataclass
class Reading:
    """Everything we computed for one instrument on one day."""

    name: str
    symbol: str
    asset_class: str
    as_of: str
    price: float
    change_pct_1d: float | None
    change_pct_5d: float | None
    change_pct_20d: float | None
    rsi: float | None
    stoch_k: float | None
    stoch_d: float | None
    williams_r: float | None
    percent_b: float | None
    cci: float | None
    mfi: float | None
    lower_band: float | None
    sma50: float | None
    sma200: float | None
    atr_pct: float | None
    pct_from_52w_high: float | None
    volume_vs_avg: float | None
    score: float
    severity: str
    triggers: list[str] = field(default_factory=list)
    position: float | None = None
    source: str = ""
    falling_knife: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _last(series: pd.Series) -> float | None:
    """Final non-NaN value of a series, as a plain float."""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def _pct_change(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods:
        return None
    prior = float(close.iloc[-1 - periods])
    if prior == 0:
        return None
    return 100.0 * (float(close.iloc[-1]) / prior - 1.0)


def _ramp(value: float, start: float, full: float) -> float:
    """0.0 at `start`, 1.0 at `full`, linear between. Handles either direction."""
    if start == full:
        return 1.0 if value <= start else 0.0
    fraction = (value - start) / (full - start)
    return max(0.0, min(1.0, fraction))


def evaluate(
    instrument: Instrument, frame: pd.DataFrame, config: Config, source: str = ""
) -> Reading:
    """Compute indicators and the composite oversold score for one instrument."""
    thresholds = config.thresholds
    weights = config.weights

    close, high, low = frame["Close"], frame["High"], frame["Low"]
    volume = frame["Volume"]

    rsi_series = ind.rsi(close, int(thresholds["rsi_period"]))
    stoch_k, stoch_d = ind.stochastic(high, low, close)
    wr = ind.williams_r(high, low, close)
    _, _, lower, percent_b = ind.bollinger(close)
    cci_series = ind.cci(high, low, close)
    has_volume = bool(volume.abs().sum() > 0)
    mfi_series = ind.mfi(high, low, close, volume) if has_volume else None

    rsi_v = _last(rsi_series)
    stoch_v = _last(stoch_k)
    wr_v = _last(wr)
    pb_v = _last(percent_b)
    cci_v = _last(cci_series)
    mfi_v = _last(mfi_series) if mfi_series is not None else None

    # --- composite score -------------------------------------------------
    # Each indicator contributes its weight scaled by how far past its own
    # oversold line it sits. Weights of indicators we could not compute are
    # dropped and the rest are rescaled, so a volume-less FX pair is still
    # scored on a 0-100 basis.
    contributions: dict[str, float] = {}
    available = 0.0

    if rsi_v is not None:
        available += weights["rsi"]
        contributions["rsi"] = weights["rsi"] * _ramp(
            rsi_v, float(thresholds["rsi_oversold"]), float(thresholds["rsi_deep"])
        )
    if stoch_v is not None:
        available += weights["stochastic"]
        contributions["stochastic"] = weights["stochastic"] * _ramp(
            stoch_v, float(thresholds["stoch_oversold"]), 5.0
        )
    if wr_v is not None:
        available += weights["williams"]
        contributions["williams"] = weights["williams"] * _ramp(
            wr_v, float(thresholds["williams_oversold"]), -95.0
        )
    if pb_v is not None:
        available += weights["percent_b"]
        contributions["percent_b"] = weights["percent_b"] * _ramp(
            pb_v, float(thresholds["percent_b_oversold"]), -0.25
        )
    if cci_v is not None:
        available += weights["cci"]
        contributions["cci"] = weights["cci"] * _ramp(
            cci_v, float(thresholds["cci_oversold"]), -250.0
        )
    if mfi_v is not None:
        available += weights["mfi"]
        contributions["mfi"] = weights["mfi"] * _ramp(
            mfi_v, float(thresholds["mfi_oversold"]), 5.0
        )

    total_weight = sum(weights.values())
    raw_score = sum(contributions.values())
    # Rescale onto the full weight budget when some indicators were unavailable.
    score = raw_score * (total_weight / available) if available > 0 else 0.0
    score = round(min(100.0, score), 1)

    # --- which lines were actually crossed -------------------------------
    triggers: list[str] = []
    if rsi_v is not None and rsi_v <= float(thresholds["rsi_oversold"]):
        triggers.append(f"RSI {rsi_v:.1f} <= {thresholds['rsi_oversold']:g}")
    if stoch_v is not None and stoch_v <= float(thresholds["stoch_oversold"]):
        triggers.append(f"Stoch %K {stoch_v:.1f} <= {thresholds['stoch_oversold']:g}")
    if wr_v is not None and wr_v <= float(thresholds["williams_oversold"]):
        triggers.append(f"Williams %R {wr_v:.1f} <= {thresholds['williams_oversold']:g}")
    if pb_v is not None and pb_v <= float(thresholds["percent_b_oversold"]):
        triggers.append(f"Below lower Bollinger band (%B {pb_v:.2f})")
    if cci_v is not None and cci_v <= float(thresholds["cci_oversold"]):
        triggers.append(f"CCI {cci_v:.0f} <= {thresholds['cci_oversold']:g}")
    if mfi_v is not None and mfi_v <= float(thresholds["mfi_oversold"]):
        triggers.append(f"MFI {mfi_v:.1f} <= {thresholds['mfi_oversold']:g}")

    # --- severity ---------------------------------------------------------
    deep_rsi = rsi_v is not None and rsi_v <= float(thresholds["rsi_deep"])
    if score >= float(thresholds["score_deep"]) or (deep_rsi and len(triggers) >= 2):
        severity = DEEP
    elif score >= float(thresholds["score_alert"]) and triggers:
        severity = OVERSOLD
    elif triggers:
        severity = WATCH
    else:
        severity = NEUTRAL

    # --- context ----------------------------------------------------------
    price = float(close.iloc[-1])
    sma50 = _last(ind.sma(close, 50))
    sma200 = _last(ind.sma(close, 200))
    atr_v = _last(ind.atr(high, low, close))
    change_20d = _pct_change(close, 20)
    avg_volume = _last(volume.rolling(20, min_periods=5).mean()) if has_volume else None

    return Reading(
        name=instrument.name,
        symbol=instrument.symbol,
        asset_class=instrument.asset_class,
        as_of=frame.index[-1].date().isoformat(),
        price=price,
        change_pct_1d=_pct_change(close, 1),
        change_pct_5d=_pct_change(close, 5),
        change_pct_20d=change_20d,
        rsi=rsi_v,
        stoch_k=stoch_v,
        stoch_d=_last(stoch_d),
        williams_r=wr_v,
        percent_b=pb_v,
        cci=cci_v,
        mfi=mfi_v,
        lower_band=_last(lower),
        sma50=sma50,
        sma200=sma200,
        atr_pct=(100.0 * atr_v / price) if atr_v and price else None,
        pct_from_52w_high=_last(ind.pct_from_high(close, 252)),
        volume_vs_avg=(float(volume.iloc[-1]) / avg_volume)
        if avg_volume
        else None,
        score=score,
        severity=severity,
        triggers=triggers,
        position=instrument.position,
        source=source,
        # Oversold in a confirmed downtrend is a different animal from a dip
        # inside an uptrend; flag it rather than filtering it out.
        falling_knife=bool(
            sma200 is not None
            and price < sma200
            and change_20d is not None
            and change_20d < -20.0
        ),
    )


def is_alertable(reading: Reading) -> bool:
    return SEVERITY_ORDER[reading.severity] >= SEVERITY_ORDER[OVERSOLD]


def has_recovered(reading: Reading, config: Config) -> bool:
    """True once RSI closes back above the exit line and nothing else trips."""
    if reading.rsi is None:
        return False
    return reading.rsi >= float(config.thresholds["rsi_exit"]) and not reading.triggers


def today_iso() -> str:
    return date.today().isoformat()
