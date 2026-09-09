"""Command line entry point: `python -m oversold_monitor ...`"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config, Instrument, load_config
from .notify import build_notifiers, build_payload, dispatch
from .providers import ProviderError, fetch_history
from .report import render_text
from .screener import Reading, evaluate, has_recovered, is_alertable
from .state import AlertState

log = logging.getLogger("oversold")


def _scan_one(instrument: Instrument, config: Config) -> Reading | None:
    try:
        history = fetch_history(
            instrument.symbol, config.lookback_days, tuple(config.providers)
        )
    except ProviderError as exc:
        log.warning("skipping %s: %s", instrument.name, exc)
        return None
    try:
        return evaluate(instrument, history.frame, config, source=history.source)
    except Exception as exc:
        log.warning("could not evaluate %s: %s", instrument.name, exc)
        return None


def scan(config: Config, workers: int = 8) -> list[Reading]:
    """Fetch and evaluate every enabled instrument, hardest-hit first."""
    instruments = config.active_instruments
    log.info("scanning %d instruments", len(instruments))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda i: _scan_one(i, config), instruments))
    readings = [r for r in results if r is not None]
    readings.sort(key=lambda r: r.score, reverse=True)
    return readings


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.cooldown_days is not None:
        config.cooldown_days = args.cooldown_days

    readings = scan(config, workers=args.workers)
    if not readings:
        log.error("no instrument returned usable data — check network access")
        return 2

    state = AlertState(config.state_path)
    alerts: list[Reading] = []
    recoveries: list[Reading] = []
    kinds: dict[str, str] = {}

    for reading in readings:
        recovered = has_recovered(reading, config)
        decision = state.decide(
            reading, config.cooldown_days, recovered, config.notify_recovery
        )
        if args.force and is_alertable(reading):
            decision.should_notify = True
            if decision.kind == "suppressed":
                decision.kind = "reminder"

        if decision.should_notify:
            kinds[reading.symbol] = decision.kind
            (recoveries if decision.kind == "recovered" else alerts).append(reading)
        if not args.dry_run:
            state.record(reading, decision, recovered)

    payload = build_payload(alerts, recoveries, kinds)

    if args.dry_run:
        print(render_text(alerts, recoveries, kinds))
        print(f"\n[dry run] {len(alerts)} alert(s) withheld; state not written.")
        return 0

    if payload.is_empty and not args.always_notify:
        log.info("nothing to report (%d instruments scanned)", len(readings))
        state.save()
        if args.summary:
            print(_summary_table(readings))
        return 0

    failed = dispatch(build_notifiers(config.notifiers), payload)
    state.save()

    if args.summary:
        print(_summary_table(readings))
    if failed:
        log.error("notifier(s) failed: %s", ", ".join(failed))
        return 1
    return 0


def _summary_table(readings: list[Reading]) -> str:
    header = f"{'INSTRUMENT':<10}{'PRICE':>12}{'SCORE':>7}{'RSI':>7}{'STOCH':>7}{'%B':>7}  STATE"
    lines = ["", header, "-" * len(header)]
    for r in readings:
        fmt = lambda v, s=".1f": "—" if v is None else format(v, s)
        lines.append(
            f"{r.name:<10}{r.price:>12,.2f}{r.score:>7.0f}{fmt(r.rsi):>7}"
            f"{fmt(r.stoch_k):>7}{fmt(r.percent_b, '.2f'):>7}  {r.severity}"
        )
    return "\n".join(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    """Read-only: show every instrument's reading, touching no state."""
    config = load_config(args.config)
    readings = scan(config, workers=args.workers)
    if not readings:
        return 2
    print(_summary_table(readings))
    alerts = [r for r in readings if is_alertable(r)]
    print(f"\n{len(alerts)} of {len(readings)} instruments are oversold.")
    return 0


def cmd_test_notify(args: argparse.Namespace) -> int:
    """Fire a sample alert through every configured channel."""
    config = load_config(args.config)
    sample = Reading(
        name="TEST", symbol="TEST", asset_class="equity", as_of="2026-01-01",
        price=123.45, change_pct_1d=-3.2, change_pct_5d=-9.4, change_pct_20d=-18.1,
        rsi=22.4, stoch_k=8.1, stoch_d=11.0, williams_r=-92.0, percent_b=-0.08,
        cci=-188.0, mfi=14.2, lower_band=127.0, sma50=150.0, sma200=160.0,
        atr_pct=4.1, pct_from_52w_high=-31.5, volume_vs_avg=2.3, score=84.0,
        severity="deeply_oversold",
        triggers=["RSI 22.4 <= 30", "Stoch %K 8.1 <= 20", "Below lower Bollinger band (%B -0.08)"],
        position=10, source="test",
    )
    payload = build_payload([sample], [], {"TEST": "new"})
    payload.subject = "[Oversold] TEST — notification check"
    failed = dispatch(build_notifiers(config.notifiers), payload)
    if failed:
        print(f"Failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("All configured notifiers delivered.")
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    state = AlertState(config.state_path)
    entries = state.data.get("instruments", {})
    if args.reset:
        state.data["instruments"] = {}
        state.save()
        print(f"Cleared {len(entries)} entries from {config.state_path}")
        return 0
    if not entries:
        print(f"No state recorded yet ({config.state_path}).")
        return 0
    print(f"{'SYMBOL':<10}{'SEVERITY':<18}{'ALERTING':<10}{'LAST NOTIFIED'}")
    for symbol, entry in sorted(entries.items()):
        print(
            f"{symbol:<10}{str(entry.get('severity')):<18}"
            f"{str(bool(entry.get('alerting'))):<10}{entry.get('last_notified_on') or '—'}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oversold_monitor",
        description="Watch a list of instruments and alert when they go oversold.",
    )
    parser.add_argument("-c", "--config", default="config.yml", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--workers", type=int, default=8, help="parallel fetches")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="scan and send notifications (the scheduled job)")
    run.add_argument("--dry-run", action="store_true", help="print, don't send or save")
    run.add_argument("--force", action="store_true", help="ignore cooldown/dedupe")
    run.add_argument("--always-notify", action="store_true",
                     help="send even when nothing is oversold")
    run.add_argument("--summary", action="store_true", help="also print the full table")
    run.add_argument("--cooldown-days", type=int, default=None)
    run.set_defaults(func=cmd_run)

    scan_cmd = sub.add_parser("scan", help="print readings for every instrument")
    scan_cmd.set_defaults(func=cmd_scan)

    test = sub.add_parser("test-notify", help="send a sample alert through each channel")
    test.set_defaults(func=cmd_test_notify)

    state_cmd = sub.add_parser("state", help="inspect or clear the dedupe state")
    state_cmd.add_argument("--reset", action="store_true")
    state_cmd.set_defaults(func=cmd_state)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
