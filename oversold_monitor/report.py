"""Rendering of alerts into text, HTML and markdown."""

from __future__ import annotations

import html
from datetime import datetime

from .screener import DEEP, Reading

SEVERITY_LABEL = {
    "deeply_oversold": "DEEPLY OVERSOLD",
    "oversold": "OVERSOLD",
    "watch": "watch",
    "neutral": "neutral",
}
SEVERITY_EMOJI = {"deeply_oversold": "🔴", "oversold": "🟠", "watch": "🟡", "neutral": "⚪"}


def _fmt(value: float | None, spec: str = ".2f", suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:{spec}}{suffix}"


def _price(value: float) -> str:
    return f"{value:,.2f}" if value >= 1 else f"{value:,.4f}"


def headline(alerts: list[Reading], recoveries: list[Reading]) -> str:
    if alerts:
        deep = sum(1 for a in alerts if a.severity == DEEP)
        names = ", ".join(a.name for a in alerts[:4])
        if len(alerts) > 4:
            names += f" +{len(alerts) - 4} more"
        prefix = f"{len(alerts)} oversold"
        if deep:
            prefix += f" ({deep} deep)"
        return f"{prefix}: {names}"
    if recoveries:
        return f"{len(recoveries)} recovered: " + ", ".join(r.name for r in recoveries)
    return "No oversold signals"


def render_text(alerts: list[Reading], recoveries: list[Reading], kinds: dict[str, str]) -> str:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [f"Oversold monitor — {stamp}", "=" * 58, ""]

    if not alerts and not recoveries:
        lines.append("Nothing oversold. All watchlist instruments are in normal range.")
        return "\n".join(lines)

    for reading in alerts:
        emoji = SEVERITY_EMOJI.get(reading.severity, "")
        kind = kinds.get(reading.symbol, "new")
        tag = {"escalated": " [ESCALATED]", "reminder": " [STILL OVERSOLD]"}.get(kind, "")
        lines.append(
            f"{emoji} {reading.name} — {SEVERITY_LABEL[reading.severity]}{tag}"
            f"  (score {reading.score:.0f}/100)"
        )
        lines.append(
            f"   {_price(reading.price)}   1d {_fmt(reading.change_pct_1d, '+.2f', '%')}"
            f"   5d {_fmt(reading.change_pct_5d, '+.2f', '%')}"
            f"   20d {_fmt(reading.change_pct_20d, '+.2f', '%')}"
        )
        lines.append(
            f"   RSI {_fmt(reading.rsi, '.1f')}   Stoch {_fmt(reading.stoch_k, '.1f')}"
            f"   %B {_fmt(reading.percent_b, '.2f')}   MFI {_fmt(reading.mfi, '.1f')}"
            f"   off 52w high {_fmt(reading.pct_from_52w_high, '.1f', '%')}"
        )
        for trigger in reading.triggers:
            lines.append(f"     - {trigger}")
        if reading.position:
            lines.append(f"   You hold {reading.position:g} — adding here averages down.")
        if reading.falling_knife:
            lines.append("   ⚠ Below the 200d SMA and down >20% in a month — downtrend, not just a dip.")
        lines.append("")

    if recoveries:
        lines.append("Recovered (no longer oversold)")
        lines.append("-" * 58)
        for reading in recoveries:
            lines.append(
                f"✅ {reading.name} — RSI {_fmt(reading.rsi, '.1f')}, "
                f"{_price(reading.price)} ({_fmt(reading.change_pct_5d, '+.2f', '%')} 5d)"
            )
        lines.append("")

    lines.append("Signals are technical only — not investment advice.")
    return "\n".join(lines)


def render_markdown(alerts: list[Reading], recoveries: list[Reading]) -> str:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    out = [f"**Oversold monitor** — {stamp}", ""]
    if not alerts and not recoveries:
        return "\n".join(out + ["No oversold signals."])

    if alerts:
        out += ["| | Instrument | Price | Score | RSI | Stoch | %B | 20d | Triggers |",
                "|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in alerts:
            out.append(
                f"| {SEVERITY_EMOJI.get(r.severity, '')} | **{r.name}** | {_price(r.price)} "
                f"| {r.score:.0f} | {_fmt(r.rsi, '.1f')} | {_fmt(r.stoch_k, '.1f')} "
                f"| {_fmt(r.percent_b, '.2f')} | {_fmt(r.change_pct_20d, '+.1f', '%')} "
                f"| {len(r.triggers)} |"
            )
        out.append("")
    if recoveries:
        out.append("**Recovered:** " + ", ".join(r.name for r in recoveries))
    return "\n".join(out)


_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 24px; background: #0f1115; color: #e6e8ee; }
h1 { font-size: 19px; margin: 0 0 4px; }
.sub { color: #8b93a7; font-size: 13px; margin-bottom: 20px; }
.card { background: #171a21; border: 1px solid #262b36; border-left-width: 4px;
        border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.deeply_oversold { border-left-color: #ef4444; }
.oversold { border-left-color: #f59e0b; }
.recovered { border-left-color: #22c55e; }
.row { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.sym { font-size: 17px; font-weight: 650; }
.badge { font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
         padding: 3px 8px; border-radius: 999px; background: #262b36; color: #c3cad9; }
.price { font-variant-numeric: tabular-nums; font-size: 17px; }
.metrics { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 10px;
           font-size: 13px; color: #aab2c5; font-variant-numeric: tabular-nums; }
.metrics b { color: #e6e8ee; font-weight: 600; }
ul { margin: 10px 0 0; padding-left: 18px; color: #c3cad9; font-size: 13px; }
.note { margin-top: 10px; font-size: 12.5px; color: #f0b45e; }
.neg { color: #f87171; } .pos { color: #4ade80; }
.foot { color: #6b7386; font-size: 12px; margin-top: 22px; }
@media (prefers-color-scheme: light) {
  body { background: #f6f7f9; color: #11151c; }
  /* Only the non-accent borders; the severity left border must survive. */
  .card { background: #fff; border-top-color: #e3e6ec;
          border-right-color: #e3e6ec; border-bottom-color: #e3e6ec; }
  .sub, .metrics, ul { color: #5b6474; }
  .metrics b, .sym { color: #11151c; }
  .badge { background: #eef0f4; color: #475061; }
  .neg { color: #d02a2a; } .pos { color: #14803c; }
  .note { color: #a35a06; }
  .foot { color: #7a8291; }
}
"""


def _signed(value: float | None, spec: str = "+.2f") -> str:
    if value is None:
        return "<span>n/a</span>"
    cls = "pos" if value >= 0 else "neg"
    return f'<span class="{cls}">{value:{spec}}%</span>'


def render_html(alerts: list[Reading], recoveries: list[Reading], kinds: dict[str, str]) -> str:
    stamp = datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")
    parts = [
        "<style>", _CSS, "</style>",
        "<h1>Oversold monitor</h1>",
        f'<div class="sub">{html.escape(stamp)} — {len(alerts)} alert(s), '
        f"{len(recoveries)} recovery(ies)</div>",
    ]

    if not alerts and not recoveries:
        parts.append('<div class="card">Nothing oversold right now.</div>')

    for r in alerts:
        kind = kinds.get(r.symbol, "new")
        tag = {"escalated": "escalated", "reminder": "still oversold"}.get(kind, "new")
        parts.append(f'<div class="card {html.escape(r.severity)}">')
        parts.append(
            f'<div class="row"><span class="sym">{html.escape(r.name)} '
            f'<span class="badge">{html.escape(SEVERITY_LABEL[r.severity])} · {tag}</span></span>'
            f'<span class="price">{_price(r.price)}</span></div>'
        )
        parts.append(
            '<div class="metrics">'
            f"<span>score <b>{r.score:.0f}</b>/100</span>"
            f"<span>RSI <b>{_fmt(r.rsi, '.1f')}</b></span>"
            f"<span>Stoch <b>{_fmt(r.stoch_k, '.1f')}</b></span>"
            f"<span>%B <b>{_fmt(r.percent_b, '.2f')}</b></span>"
            f"<span>MFI <b>{_fmt(r.mfi, '.1f')}</b></span>"
            f"<span>1d {_signed(r.change_pct_1d)}</span>"
            f"<span>5d {_signed(r.change_pct_5d)}</span>"
            f"<span>20d {_signed(r.change_pct_20d)}</span>"
            f"<span>off 52w high <b>{_fmt(r.pct_from_52w_high, '.1f', '%')}</b></span>"
            "</div>"
        )
        if r.triggers:
            parts.append("<ul>" + "".join(f"<li>{html.escape(t)}</li>" for t in r.triggers) + "</ul>")
        if r.position:
            parts.append(f'<div class="note">Position: {r.position:g} held.</div>')
        if r.falling_knife:
            parts.append(
                '<div class="note">⚠ Below the 200-day SMA and down more than 20% '
                "in a month — this is a downtrend, not just a dip.</div>"
            )
        parts.append("</div>")

    for r in recoveries:
        parts.append(
            f'<div class="card recovered"><div class="row">'
            f'<span class="sym">{html.escape(r.name)} <span class="badge">recovered</span></span>'
            f'<span class="price">{_price(r.price)}</span></div>'
            f'<div class="metrics"><span>RSI <b>{_fmt(r.rsi, ".1f")}</b></span>'
            f"<span>5d {_signed(r.change_pct_5d)}</span></div></div>"
        )

    parts.append('<div class="foot">Technical signals only — not investment advice.</div>')
    return "\n".join(parts)
