"""Alert de-duplication.

Without this you get the same "AMD is oversold" message every single run for
as long as the condition lasts. The rules are:

  * first time an instrument goes oversold  -> alert
  * severity escalates (oversold -> deep)   -> alert again immediately
  * still oversold, nothing changed         -> stay quiet until the cooldown
                                               window has passed
  * recovers back above the exit line       -> optional all-clear, then reset
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .screener import SEVERITY_ORDER, Reading

SCHEMA_VERSION = 1


@dataclass
class Decision:
    should_notify: bool
    kind: str  # "new" | "escalated" | "reminder" | "recovered" | "suppressed"
    reason: str


class AlertState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {"version": SCHEMA_VERSION, "instruments": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError:
            # A corrupt state file must never stop the alerts; start clean.
            return
        if isinstance(loaded, dict) and loaded.get("version") == SCHEMA_VERSION:
            self.data = loaded
            self.data.setdefault("instruments", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def entry(self, symbol: str) -> dict[str, Any]:
        return self.data["instruments"].get(symbol, {})

    def decide(
        self, reading: Reading, cooldown_days: int, recovered: bool, notify_recovery: bool
    ) -> Decision:
        prior = self.entry(reading.symbol)
        prior_severity = prior.get("severity")
        prior_rank = SEVERITY_ORDER.get(prior_severity, 0)
        current_rank = SEVERITY_ORDER.get(reading.severity, 0)
        was_alerting = bool(prior.get("alerting"))

        if recovered:
            if was_alerting and notify_recovery:
                return Decision(True, "recovered", "RSI back above the exit level")
            return Decision(False, "suppressed", "not currently alerting")

        if current_rank < SEVERITY_ORDER["oversold"]:
            return Decision(False, "suppressed", f"severity {reading.severity}")

        if not was_alerting:
            return Decision(True, "new", "first oversold reading")

        if current_rank > prior_rank:
            return Decision(
                True, "escalated", f"{prior_severity} -> {reading.severity}"
            )

        last = prior.get("last_notified_on")
        if last:
            try:
                last_date = date.fromisoformat(last)
            except ValueError:
                return Decision(True, "reminder", "unreadable last-notified date")
            if date.today() - last_date >= timedelta(days=cooldown_days):
                days = (date.today() - last_date).days
                return Decision(True, "reminder", f"still oversold after {days}d")
            return Decision(False, "suppressed", f"cooldown until {last_date + timedelta(days=cooldown_days)}")

        return Decision(True, "reminder", "no record of a previous notification")

    def record(self, reading: Reading, decision: Decision, recovered: bool) -> None:
        """Persist the outcome for this instrument."""
        if recovered:
            self.data["instruments"][reading.symbol] = {
                "severity": reading.severity,
                "alerting": False,
                "score": reading.score,
                "last_seen_on": reading.as_of,
                "recovered_on": date.today().isoformat(),
            }
            return

        prior = self.entry(reading.symbol)
        alerting = SEVERITY_ORDER.get(reading.severity, 0) >= SEVERITY_ORDER["oversold"]
        record: dict[str, Any] = {
            "severity": reading.severity,
            "alerting": alerting,
            "score": reading.score,
            "rsi": reading.rsi,
            "last_seen_on": reading.as_of,
            "last_notified_on": prior.get("last_notified_on"),
            "first_alert_on": prior.get("first_alert_on"),
        }
        if decision.should_notify:
            record["last_notified_on"] = date.today().isoformat()
            record["first_alert_on"] = prior.get("first_alert_on") or date.today().isoformat()
        if not alerting:
            record["first_alert_on"] = None
        self.data["instruments"][reading.symbol] = record
