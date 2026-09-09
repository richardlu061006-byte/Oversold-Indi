"""The de-duplication rules — the part that stops daily alert spam."""

import json
from datetime import date, timedelta

import pytest

from oversold_monitor.screener import Reading
from oversold_monitor.state import AlertState


def reading(severity="oversold", score=70.0, rsi=25.0, symbol="AMD"):
    return Reading(
        name=symbol, symbol=symbol, asset_class="equity", as_of="2026-01-05",
        price=100.0, change_pct_1d=-2.0, change_pct_5d=-8.0, change_pct_20d=-15.0,
        rsi=rsi, stoch_k=12.0, stoch_d=14.0, williams_r=-88.0, percent_b=0.01,
        cci=-140.0, mfi=18.0, lower_band=99.0, sma50=110.0, sma200=120.0,
        atr_pct=3.0, pct_from_52w_high=-25.0, volume_vs_avg=1.4,
        score=score, severity=severity, triggers=["RSI 25.0 <= 30"],
    )


@pytest.fixture
def state(tmp_path):
    return AlertState(tmp_path / "alerts.json")


def test_first_oversold_reading_notifies(state):
    decision = state.decide(reading(), cooldown_days=5, recovered=False, notify_recovery=True)
    assert decision.should_notify and decision.kind == "new"


def test_repeat_within_cooldown_is_suppressed(state):
    first = reading()
    decision = state.decide(first, 5, False, True)
    state.record(first, decision, False)

    again = state.decide(reading(), 5, False, True)
    assert not again.should_notify
    assert again.kind == "suppressed"


def test_escalation_breaks_through_the_cooldown(state):
    first = reading()
    state.record(first, state.decide(first, 5, False, True), False)

    deeper = reading(severity="deeply_oversold", score=85.0, rsi=17.0)
    decision = state.decide(deeper, 5, False, True)
    assert decision.should_notify and decision.kind == "escalated"


def test_de_escalation_does_not_re_alert(state):
    deep = reading(severity="deeply_oversold", score=85.0, rsi=17.0)
    state.record(deep, state.decide(deep, 5, False, True), False)

    milder = reading(severity="oversold", score=60.0, rsi=28.0)
    assert not state.decide(milder, 5, False, True).should_notify


def test_reminder_after_the_cooldown_expires(state):
    first = reading()
    state.record(first, state.decide(first, 5, False, True), False)
    stale = (date.today() - timedelta(days=6)).isoformat()
    state.data["instruments"]["AMD"]["last_notified_on"] = stale

    decision = state.decide(reading(), 5, False, True)
    assert decision.should_notify and decision.kind == "reminder"


def test_watch_severity_never_notifies(state):
    assert not state.decide(reading(severity="watch", score=40.0), 5, False, True).should_notify


def test_recovery_notifies_only_if_we_were_alerting(state):
    quiet = state.decide(reading(severity="neutral", rsi=55.0), 5, True, True)
    assert not quiet.should_notify

    first = reading()
    state.record(first, state.decide(first, 5, False, True), False)
    back = state.decide(reading(severity="neutral", rsi=55.0), 5, True, True)
    assert back.should_notify and back.kind == "recovered"


def test_recovery_can_be_switched_off(state):
    first = reading()
    state.record(first, state.decide(first, 5, False, True), False)
    decision = state.decide(reading(severity="neutral", rsi=55.0), 5, True, False)
    assert not decision.should_notify


def test_recovery_resets_so_the_next_dip_alerts_again(state):
    first = reading()
    state.record(first, state.decide(first, 5, False, True), False)

    recovery = reading(severity="neutral", rsi=55.0)
    state.record(recovery, state.decide(recovery, 5, True, True), True)

    fresh = state.decide(reading(), 5, False, True)
    assert fresh.should_notify and fresh.kind == "new"


def test_state_round_trips_to_disk(tmp_path):
    path = tmp_path / "nested" / "alerts.json"
    first = AlertState(path)
    entry = reading()
    first.record(entry, first.decide(entry, 5, False, True), False)
    first.save()

    assert path.exists()
    reloaded = AlertState(path)
    assert reloaded.entry("AMD")["alerting"] is True
    assert reloaded.entry("AMD")["last_notified_on"] == date.today().isoformat()


def test_corrupt_state_file_starts_clean_instead_of_crashing(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text("{ this is not json")
    state = AlertState(path)
    assert state.data["instruments"] == {}
    assert state.decide(reading(), 5, False, True).should_notify


def test_unknown_schema_version_is_discarded(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({"version": 999, "instruments": {"AMD": {"alerting": True}}}))
    assert AlertState(path).entry("AMD") == {}


def test_instruments_are_tracked_independently(state):
    amd = reading(symbol="AMD")
    state.record(amd, state.decide(amd, 5, False, True), False)

    intc = state.decide(reading(symbol="INTC"), 5, False, True)
    assert intc.should_notify and intc.kind == "new"
