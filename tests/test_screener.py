"""Scoring, severity and the alert/no-alert decision."""

import numpy as np
import pandas as pd
import pytest

from oversold_monitor.config import Config, Instrument
from oversold_monitor.screener import (
    DEEP,
    NEUTRAL,
    OVERSOLD,
    evaluate,
    has_recovered,
    is_alertable,
)


def make_frame(closes, volume=1_000_000.0):
    """Build a plausible OHLCV frame from a close series."""
    index = pd.bdate_range("2024-01-01", periods=len(closes))
    close = pd.Series(closes, dtype=float, index=index)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": [volume] * len(close),
        },
        index=index,
    )


@pytest.fixture
def config():
    return Config(instruments=[])


def crashing(n=300, start=200.0):
    """A steady uptrend that rolls over into a sharp sell-off."""
    rise = np.linspace(start, start * 1.6, n - 40)
    fall = np.linspace(start * 1.6, start * 0.95, 40)
    return np.concatenate([rise, fall])


def climbing(n=300, start=100.0):
    return np.linspace(start, start * 1.8, n)


def test_steady_uptrend_is_neutral(config):
    reading = evaluate(Instrument("UP", "UP"), make_frame(climbing()), config)
    assert reading.severity == NEUTRAL
    assert reading.triggers == []
    assert reading.score < 55
    assert not is_alertable(reading)


def test_sharp_selloff_triggers_an_alert(config):
    reading = evaluate(Instrument("DOWN", "DOWN"), make_frame(crashing()), config)
    assert reading.severity in (OVERSOLD, DEEP)
    assert is_alertable(reading)
    assert reading.rsi < 30
    assert len(reading.triggers) >= 2
    assert reading.score >= 55


def test_score_is_bounded_and_ordered(config):
    calm = evaluate(Instrument("A", "A"), make_frame(climbing()), config)
    crash = evaluate(Instrument("B", "B"), make_frame(crashing()), config)
    assert 0 <= calm.score <= 100 and 0 <= crash.score <= 100
    assert crash.score > calm.score


def test_deeper_selloff_scores_higher(config):
    mild = np.concatenate([np.linspace(100, 160, 260), np.linspace(160, 150, 40)])
    harsh = np.concatenate([np.linspace(100, 160, 260), np.linspace(160, 95, 40)])
    a = evaluate(Instrument("M", "M"), make_frame(mild), config)
    b = evaluate(Instrument("H", "H"), make_frame(harsh), config)
    assert b.score > a.score


def test_missing_volume_still_scores_on_full_scale(config):
    """FX has no volume; MFI drops out and the rest must be rescaled."""
    frame = make_frame(crashing(), volume=0.0)
    reading = evaluate(Instrument("FX", "FX", asset_class="fx"), frame, config)
    assert reading.mfi is None
    assert reading.score >= 55
    assert is_alertable(reading)


def test_context_fields_are_populated(config):
    reading = evaluate(Instrument("X", "X", position=10), make_frame(crashing()), config)
    assert reading.pct_from_52w_high < 0
    assert reading.sma50 and reading.sma200
    assert reading.atr_pct > 0
    assert reading.position == 10
    assert reading.change_pct_20d < 0
    assert reading.as_of == "2025-02-21"


def test_falling_knife_flag(config):
    cliff = np.concatenate([np.linspace(100, 120, 260), np.linspace(120, 60, 40)])
    reading = evaluate(Instrument("K", "K"), make_frame(cliff), config)
    assert reading.falling_knife is True

    reading = evaluate(Instrument("U", "U"), make_frame(climbing()), config)
    assert reading.falling_knife is False


def test_short_history_does_not_crash(config):
    reading = evaluate(Instrument("S", "S"), make_frame(np.linspace(100, 90, 35)), config)
    assert reading.sma200 is None
    assert 0 <= reading.score <= 100


def test_recovery_detection(config):
    recovered = evaluate(Instrument("R", "R"), make_frame(climbing()), config)
    assert has_recovered(recovered, config)

    still_down = evaluate(Instrument("D", "D"), make_frame(crashing()), config)
    assert not has_recovered(still_down, config)


def test_thresholds_are_configurable(config):
    frame = make_frame(crashing())
    strict = Config(instruments=[], thresholds={**config.thresholds, "score_alert": 99})
    assert evaluate(Instrument("T", "T"), frame, strict).severity != OVERSOLD

    loose = Config(instruments=[], thresholds={**config.thresholds, "score_alert": 1})
    assert is_alertable(evaluate(Instrument("T", "T"), frame, loose))
