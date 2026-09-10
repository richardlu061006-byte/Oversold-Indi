"""Config loading, provider normalisation, notifiers and a full CLI run."""

import json
import os

import numpy as np
import pandas as pd
import pytest

from oversold_monitor import cli, notify, providers
from oversold_monitor.config import load_config
from oversold_monitor.providers import ProviderError

from .test_screener import crashing, climbing, make_frame

# --------------------------------------------------------------------- config


def write_config(tmp_path, body):
    path = tmp_path / "config.yml"
    path.write_text(body)
    return path


def test_config_parses_instruments_and_defaults(tmp_path):
    config = load_config(write_config(tmp_path, """
instruments:
  - AAPL
  - {name: BTC, symbol: BTC-USD, asset_class: crypto}
  - {name: OLD, symbol: OLD, enabled: false}
  - {name: CRWV, symbol: CRWV, position: 70}
"""))
    assert [i.symbol for i in config.instruments] == ["AAPL", "BTC-USD", "OLD", "CRWV"]
    assert [i.symbol for i in config.active_instruments] == ["AAPL", "BTC-USD", "CRWV"]
    assert config.instruments[1].asset_class == "crypto"
    assert config.instruments[3].position == 70
    assert config.thresholds["rsi_oversold"] == 30.0  # default filled in
    assert config.cooldown_days == 5


def test_config_interpolates_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOPIC", "s3cret-topic")
    config = load_config(write_config(tmp_path, """
instruments: [AAPL]
notifiers:
  - type: ntfy
    topic: ${MY_TOPIC}
"""))
    assert config.notifiers[0]["topic"] == "s3cret-topic"


def test_missing_env_var_becomes_empty_not_literal(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    config = load_config(write_config(tmp_path, """
instruments: [AAPL]
notifiers: [{type: ntfy, topic: "${NOPE}"}]
"""))
    assert config.notifiers[0]["topic"] == ""


def test_overrides_beat_defaults(tmp_path):
    config = load_config(write_config(tmp_path, """
instruments: [AAPL]
cooldown_days: 14
thresholds: {rsi_oversold: 25}
"""))
    assert config.cooldown_days == 14
    assert config.thresholds["rsi_oversold"] == 25
    assert config.thresholds["rsi_deep"] == 20.0  # untouched default survives


def test_empty_instrument_list_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="no instruments"):
        load_config(write_config(tmp_path, "instruments: []"))


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yml")


def test_shipped_config_is_valid():
    config = load_config("config.yml")
    assert len(config.active_instruments) > 30
    symbols = {i.symbol for i in config.instruments}
    assert {"AAPL", "NVDA", "BTC-USD", "^GSPC", "EURUSD=X"} <= symbols


# ------------------------------------------------------------------ providers


def test_normalise_flattens_yfinance_multiindex():
    index = pd.bdate_range("2024-01-01", periods=3)
    frame = pd.DataFrame(
        np.arange(15, dtype=float).reshape(3, 5),
        index=index,
        columns=pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], ["AAPL"]]
        ),
    )
    out = providers._normalise(frame, "AAPL")
    assert list(out.columns) == list(providers.REQUIRED_COLUMNS)
    assert len(out) == 3


def test_normalise_synthesises_volume_for_fx():
    index = pd.bdate_range("2024-01-01", periods=3)
    frame = pd.DataFrame(
        {"Open": [1.0] * 3, "High": [1.1] * 3, "Low": [0.9] * 3, "Close": [1.0] * 3},
        index=index,
    )
    out = providers._normalise(frame, "EURUSD=X")
    assert (out["Volume"] == 0).all()


def test_normalise_rejects_a_frame_missing_prices():
    frame = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    with pytest.raises(ProviderError):
        providers._normalise(frame, "X")


def test_normalise_sorts_oldest_first():
    index = pd.to_datetime(["2024-03-01", "2024-01-01", "2024-02-01"])
    frame = pd.DataFrame(
        {c: [3.0, 1.0, 2.0] for c in providers.REQUIRED_COLUMNS}, index=index
    )
    out = providers._normalise(frame, "X")
    assert out.index.is_monotonic_increasing


def test_fetch_history_falls_back_to_the_next_provider(monkeypatch):
    good = make_frame(climbing())

    def broken(symbol, days):
        raise ProviderError("yahoo is down")

    monkeypatch.setitem(providers.PROVIDERS, "yfinance", broken)
    monkeypatch.setitem(providers.PROVIDERS, "stooq", lambda s, d: good)

    history = providers.fetch_history("AAPL", 400)
    assert history.source == "stooq"


def test_fetch_history_raises_when_every_provider_fails(monkeypatch):
    def broken(symbol, days):
        raise ProviderError("nope")

    monkeypatch.setitem(providers.PROVIDERS, "yfinance", broken)
    monkeypatch.setitem(providers.PROVIDERS, "stooq", broken)
    with pytest.raises(ProviderError, match="all providers failed"):
        providers.fetch_history("AAPL", 400)


def test_stooq_symbol_mapping():
    assert providers._stooq_symbol("AAPL") == "aapl.us"
    assert providers._stooq_symbol("BTC-USD") == "btcusd"
    assert providers._stooq_symbol("EURUSD=X") == "eurusd"
    with pytest.raises(ProviderError):
        providers._stooq_symbol("^GSPC")


# ------------------------------------------------------------------ notifiers


def sample_payload():
    from .test_state import reading

    return notify.build_payload([reading()], [], {"AMD": "new"})


def test_payload_renders_every_format():
    payload = sample_payload()
    assert "AMD" in payload.text and "RSI" in payload.text
    assert "<style>" in payload.html and "AMD" in payload.html
    assert "AMD" in payload.markdown
    assert payload.subject.startswith("[Oversold]")
    assert not payload.is_empty


def test_empty_payload_is_flagged():
    assert notify.build_payload([], [], {}).is_empty


def test_html_escapes_instrument_names():
    from .test_state import reading

    evil = reading(symbol="<script>x</script>")
    html = notify.build_payload([evil], [], {}).html
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_file_notifier_writes_html_and_json(tmp_path):
    notifier = notify.FileNotifier({"directory": str(tmp_path), "filename_stamp": "latest"})
    notifier.send(sample_payload())
    assert (tmp_path / "oversold-latest.html").exists()
    data = json.loads((tmp_path / "oversold-latest.json").read_text())
    assert data["alerts"][0]["symbol"] == "AMD"


def test_webhook_bodies_per_format(monkeypatch):
    sent = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, json=None, timeout=None, **kwargs):
        sent["url"], sent["body"] = url, json
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    notify.WebhookNotifier({"url": "https://x/y", "format": "slack"}).send(sample_payload())
    assert "text" in sent["body"]

    notify.WebhookNotifier({"url": "https://x/y", "format": "discord"}).send(sample_payload())
    assert len(sent["body"]["content"]) <= 2000

    notify.WebhookNotifier({"url": "https://x/y", "format": "json"}).send(sample_payload())
    assert sent["body"]["alerts"][0]["symbol"] == "AMD"


def test_build_notifiers_skips_disabled_and_unknown():
    built = notify.build_notifiers([
        {"type": "console"},
        {"type": "file", "enabled": False},
        {"type": "does_not_exist"},
    ])
    assert [n.name for n in built] == ["console"]


def test_build_notifiers_defaults_to_console():
    assert [n.name for n in notify.build_notifiers([])] == ["console"]


def test_one_broken_notifier_does_not_stop_the_others():
    class Boom(notify.Notifier):
        name = "boom"

        def send(self, payload):
            raise RuntimeError("kaboom")

    delivered = []

    class Works(notify.Notifier):
        name = "works"

        def send(self, payload):
            delivered.append(payload)

    failed = notify.dispatch([Boom({}), Works({})], sample_payload())
    assert failed == ["boom"]
    assert len(delivered) == 1


# ------------------------------------------------------------------- full run


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A config plus a stubbed provider: NVDA crashing, AAPL climbing."""
    frames = {"NVDA": make_frame(crashing()), "AAPL": make_frame(climbing())}
    monkeypatch.setattr(
        cli,
        "fetch_history",
        lambda symbol, days, order: providers.History(symbol, frames[symbol], "stub"),
    )
    config_path = tmp_path / "config.yml"
    config_path.write_text(f"""
instruments: [NVDA, AAPL]
state_path: {tmp_path / 'state.json'}
notifiers:
  - type: file
    directory: {tmp_path / 'reports'}
""")
    monkeypatch.chdir(tmp_path)
    return config_path, tmp_path


def test_run_alerts_then_suppresses_the_repeat(wired, capsys):
    config_path, tmp_path = wired

    assert cli.main(["-c", str(config_path), "run"]) == 0
    report = json.loads((tmp_path / "reports" / "oversold-latest.json").read_text())
    assert [a["symbol"] for a in report["alerts"]] == ["NVDA"]

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["instruments"]["NVDA"]["alerting"] is True
    assert state["instruments"]["AAPL"]["alerting"] is False

    # Second run on the same day: still oversold, but inside the cooldown.
    (tmp_path / "reports" / "oversold-latest.json").unlink()
    assert cli.main(["-c", str(config_path), "run"]) == 0
    assert not (tmp_path / "reports" / "oversold-latest.json").exists()


def test_force_ignores_the_cooldown(wired):
    config_path, tmp_path = wired
    cli.main(["-c", str(config_path), "run"])
    (tmp_path / "reports" / "oversold-latest.json").unlink()

    assert cli.main(["-c", str(config_path), "run", "--force"]) == 0
    assert (tmp_path / "reports" / "oversold-latest.json").exists()


def test_dry_run_writes_no_state(wired, capsys):
    config_path, tmp_path = wired
    assert cli.main(["-c", str(config_path), "run", "--dry-run"]) == 0
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "reports").exists()
    assert "dry run" in capsys.readouterr().out


def test_scan_is_read_only(wired, capsys):
    config_path, tmp_path = wired
    assert cli.main(["-c", str(config_path), "scan"]) == 0
    out = capsys.readouterr().out
    assert "NVDA" in out and "AAPL" in out and "1 of 2" in out
    assert not (tmp_path / "state.json").exists()


def test_state_command_lists_and_resets(wired, capsys):
    config_path, tmp_path = wired
    cli.main(["-c", str(config_path), "run"])

    cli.main(["-c", str(config_path), "state"])
    assert "NVDA" in capsys.readouterr().out

    cli.main(["-c", str(config_path), "state", "--reset"])
    assert json.loads((tmp_path / "state.json").read_text())["instruments"] == {}


def test_run_reports_failure_when_no_data(tmp_path, monkeypatch):
    def broken(symbol, days, order):
        raise ProviderError("offline")

    monkeypatch.setattr(cli, "fetch_history", broken)
    path = tmp_path / "config.yml"
    path.write_text("instruments: [AAPL]\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["-c", str(path), "run"]) == 2


def test_a_dead_symbol_does_not_sink_the_whole_run(tmp_path, monkeypatch):
    def half_broken(symbol, days, order):
        if symbol == "DEAD":
            raise ProviderError("delisted")
        return providers.History(symbol, make_frame(crashing()), "stub")

    monkeypatch.setattr(cli, "fetch_history", half_broken)
    path = tmp_path / "config.yml"
    path.write_text(f"""
instruments: [DEAD, NVDA]
state_path: {tmp_path / 'state.json'}
notifiers: [{{type: file, directory: {tmp_path / 'r'}}}]
""")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["-c", str(path), "run"]) == 0
    report = json.loads((tmp_path / "r" / "oversold-latest.json").read_text())
    assert [a["symbol"] for a in report["alerts"]] == ["NVDA"]


def test_normalise_handles_a_tz_aware_index():
    index = pd.date_range("2024-01-01", periods=3, tz="America/New_York")
    frame = pd.DataFrame(
        {c: [1.0, 2.0, 3.0] for c in providers.REQUIRED_COLUMNS}, index=index
    )
    out = providers._normalise(frame, "X")
    assert out.index.tz is None
    assert out.index[0].isoformat().startswith("2024-01-01")


def test_normalise_handles_a_naive_index():
    index = pd.bdate_range("2024-01-01", periods=3)
    frame = pd.DataFrame(
        {c: [1.0, 2.0, 3.0] for c in providers.REQUIRED_COLUMNS}, index=index
    )
    assert providers._normalise(frame, "X").index.tz is None


def test_force_labels_a_suppressed_alert_as_a_reminder(wired, tmp_path):
    config_path, tmp_path = wired
    cli.main(["-c", str(config_path), "run"])
    (tmp_path / "reports" / "oversold-latest.json").unlink()

    cli.main(["-c", str(config_path), "run", "--force"])
    html = (tmp_path / "reports" / "oversold-latest.html").read_text()
    assert "still oversold" in html


# ------------------------------------------------------- partial-bar handling


def _frame_ending(last_day: str, days: int = 40):
    index = pd.bdate_range(end=pd.Timestamp(last_day), periods=days)
    return pd.DataFrame(
        {c: np.linspace(100, 90, days) for c in providers.REQUIRED_COLUMNS}, index=index
    )


def test_partial_bar_is_dropped_during_the_session():
    frame = _frame_ending("2026-09-09")
    now = pd.Timestamp("2026-09-09 09:50", tz="America/New_York")
    out = providers.drop_partial_bar(frame, now)
    assert len(out) == len(frame) - 1
    assert out.index[-1].date().isoformat() == "2026-09-08"


def test_completed_bar_is_kept_after_the_close():
    frame = _frame_ending("2026-09-09")
    now = pd.Timestamp("2026-09-09 18:15", tz="America/New_York")
    assert len(providers.drop_partial_bar(frame, now)) == len(frame)


def test_yesterdays_last_bar_is_never_dropped():
    """Data already lagging a day must not be trimmed a second time."""
    frame = _frame_ending("2026-09-08")
    now = pd.Timestamp("2026-09-09 09:50", tz="America/New_York")
    assert len(providers.drop_partial_bar(frame, now)) == len(frame)


def test_drop_partial_bar_handles_an_empty_frame():
    empty = pd.DataFrame(columns=list(providers.REQUIRED_COLUMNS))
    assert providers.drop_partial_bar(empty).empty


def test_exclude_partial_bar_flag_reaches_the_scan(tmp_path, monkeypatch):
    seen = {}

    def stub(symbol, days, order):
        return providers.History(symbol, make_frame(crashing()), "stub")

    def spy(frame):
        seen["called"] = True
        return frame

    monkeypatch.setattr(cli, "fetch_history", stub)
    monkeypatch.setattr(cli, "drop_partial_bar", spy)
    path = tmp_path / "config.yml"
    path.write_text(f"""
instruments: [NVDA]
exclude_partial_bar: true
state_path: {tmp_path / 'state.json'}
notifiers: [{{type: file, directory: {tmp_path / 'r'}}}]
""")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["-c", str(path), "run"]) == 0
    assert seen.get("called") is True


def test_exclude_partial_bar_defaults_to_off(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("instruments: [AAPL]\n")
    assert load_config(path).exclude_partial_bar is False
