"""Configuration loading with ${ENV_VAR} interpolation for secrets."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "rsi_period": 14,
    "rsi_oversold": 30.0,
    "rsi_deep": 20.0,
    "rsi_exit": 45.0,          # recovery is declared once RSI closes back above this
    "stoch_oversold": 20.0,
    "williams_oversold": -80.0,
    "cci_oversold": -100.0,
    "mfi_oversold": 20.0,
    "percent_b_oversold": 0.05,
    "score_alert": 55.0,       # composite 0-100 needed to raise an alert
    "score_deep": 78.0,        # composite needed for "deeply oversold"
}

# How much each confirming indicator contributes to the composite score.
DEFAULT_WEIGHTS: dict[str, float] = {
    "rsi": 34.0,
    "stochastic": 16.0,
    "williams": 12.0,
    "percent_b": 16.0,
    "cci": 10.0,
    "mfi": 12.0,
}


@dataclass
class Instrument:
    """One watchlist row: display name plus the symbol we actually fetch."""

    name: str
    symbol: str
    asset_class: str = "equity"
    position: float | None = None  # shares held, purely for alert context
    enabled: bool = True

    @classmethod
    def parse(cls, raw: Any) -> "Instrument":
        if isinstance(raw, str):
            return cls(name=raw, symbol=raw)
        if not isinstance(raw, dict):
            raise ValueError(f"cannot parse instrument: {raw!r}")
        symbol = raw.get("symbol") or raw.get("name")
        name = raw.get("name") or symbol
        if not symbol:
            raise ValueError(f"instrument needs a name or symbol: {raw!r}")
        return cls(
            name=str(name),
            symbol=str(symbol),
            asset_class=str(raw.get("asset_class", "equity")),
            position=raw.get("position"),
            enabled=bool(raw.get("enabled", True)),
        )


@dataclass
class Config:
    instruments: list[Instrument]
    thresholds: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    notifiers: list[dict[str, Any]] = field(default_factory=list)
    providers: tuple[str, ...] = ("yfinance", "stooq")
    lookback_days: int = 400
    cooldown_days: int = 5
    notify_recovery: bool = True
    state_path: Path = Path("state/alerts.json")
    report_dir: Path = Path("reports")

    @property
    def active_instruments(self) -> list[Instrument]:
        return [i for i in self.instruments if i.enabled]


def _interpolate(value: Any) -> Any:
    """Replace ${VAR} with the environment value, recursively."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    raw = _interpolate(yaml.safe_load(path.read_text()) or {})

    instruments = [Instrument.parse(i) for i in raw.get("instruments", [])]
    if not instruments:
        raise ValueError(f"{path}: no instruments configured")

    thresholds = {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})}
    weights = {**DEFAULT_WEIGHTS, **(raw.get("weights") or {})}

    return Config(
        instruments=instruments,
        thresholds=thresholds,
        weights=weights,
        notifiers=list(raw.get("notifiers") or []),
        providers=tuple(raw.get("providers") or ("yfinance", "stooq")),
        lookback_days=int(raw.get("lookback_days", 400)),
        cooldown_days=int(raw.get("cooldown_days", 5)),
        notify_recovery=bool(raw.get("notify_recovery", True)),
        state_path=Path(raw.get("state_path", "state/alerts.json")),
        report_dir=Path(raw.get("report_dir", "reports")),
    )
