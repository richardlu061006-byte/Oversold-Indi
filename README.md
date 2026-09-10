# Oversold monitor

Watches your IBKR favourites and tells you when one of them becomes technically
oversold — so you find out on the day it happens instead of noticing a week
later.

It scans the whole watchlist once a day after the close, scores each instrument
on six oversold indicators, and pushes an alert through whichever channels you
turn on. It remembers what it already told you, so a stock that stays oversold
for three weeks doesn't generate fifteen identical messages.

```
🔴 AMD — DEEPLY OVERSOLD  (score 76/100)
   87.38   1d -4.98%   5d -18.16%   20d -47.20%
   RSI 13.1   Stoch 7.8   %B -0.04   MFI 10.3   off 52w high -12.9%
     - RSI 13.1 <= 30
     - Stoch %K 7.8 <= 20
     - Below lower Bollinger band (%B -0.04)
   ⚠ Below the 200d SMA and down >20% in a month — downtrend, not just a dip.
```

## Quick start

```bash
pip install -r requirements.txt

python -m oversold_monitor scan       # current readings for everything, no alerts
python -m oversold_monitor run        # the real thing: scan, alert, remember
```

`scan` is read-only and the best way to sanity-check the setup. `run` is what
you schedule.

## How "oversold" is decided

Six indicators are computed on daily bars, each with its own oversold line:

| Indicator | Oversold when | Weight |
|---|---|---:|
| RSI(14) — Wilder | ≤ 30 (≤ 20 is "deep") | 34 |
| Stochastic %K(14,3) | ≤ 20 | 16 |
| Bollinger %B(20,2) | ≤ 0.05 (below 0 = under the band) | 16 |
| Williams %R(14) | ≤ −80 | 12 |
| Money Flow Index(14) | ≤ 20 | 12 |
| CCI(20) | ≤ −100 | 10 |

Each one contributes its weight scaled by *how far past* its line the reading
sits, giving a composite **0–100 score**. One indicator dipping under its line
is noise; four agreeing is a signal, and the score reflects that.

Severity is then:

- **deeply oversold** — score ≥ 78, or RSI ≤ 20 with at least two other triggers
- **oversold** — score ≥ 55 with at least one trigger → **this is what alerts you**
- **watch** — something tripped but the score is low (logged, no alert)
- **neutral** — nothing tripped

Indicators that can't be computed are dropped and the rest are rescaled, so FX
pairs (which have no volume, hence no MFI) are still scored out of 100.

Every alert also carries context that changes what the signal *means*: 1/5/20-day
moves, distance from the 52-week high, position versus the 50- and 200-day SMAs,
ATR, and volume against its 20-day average. Anything below its 200-day SMA and
down more than 20% in a month gets a **falling knife** warning — oversold inside
a downtrend is not the same trade as oversold inside an uptrend.

If you hold a position (set in `config.yml`), the alert says so, because "add to
an existing position" and "open a new one" are different decisions.

## You won't get spammed

State lives in `state/alerts.json` and drives the repeat rules:

| Situation | What happens |
|---|---|
| First time it goes oversold | Alert |
| Severity escalates (oversold → deeply oversold) | Alert again immediately |
| Still oversold, nothing changed | Silent until `cooldown_days` (default 5) passes |
| RSI closes back above `rsi_exit` (default 45) | One "recovered" note, then reset |

So a stock that stays oversold for a month gets you roughly six messages, not
thirty — and the next time it dips it alerts fresh.

Inspect or clear it:

```bash
python -m oversold_monitor state
python -m oversold_monitor state --reset
```

## Where alerts go

Turn channels on in the `notifiers:` block of `config.yml`. Console and file are
on by default; the rest need a secret, read from the environment via `${VAR}` so
nothing sensitive lives in the config file.

| `type` | What it does | Needs |
|---|---|---|
| `console` | Prints to stdout | — |
| `file` | Writes an HTML report + JSON snapshot to `reports/` | — |
| `ntfy` | **Push to your phone.** Easiest option. | A topic name |
| `email` | HTML email via SMTP | `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL` |
| `webhook` | Slack, Discord, or raw JSON | `WEBHOOK_URL` |
| `desktop` | macOS/Linux notification | — |
| `github_summary` | Writes into the Actions run summary | — |

**Phone push in two minutes** — install [ntfy](https://ntfy.sh) (iOS/Android),
subscribe to an unguessable topic name (topics are public, so treat it as a
secret), then:

```yaml
- type: ntfy
  enabled: true
  topic: my-oversold-a7f3k9
  priority: high
```

**Gmail** needs an App Password (Google Account → Security → App passwords), not
your normal password.

Check everything is wired up before you rely on it:

```bash
python -m oversold_monitor test-notify
```

That sends one sample alert through every enabled channel.

## Running it automatically

### GitHub Actions (nothing to keep running)

`.github/workflows/oversold.yml` runs once each weekday from **09:50
America/New_York** — 20 minutes after the US open. It commits `state/alerts.json` back to
the repo, which is what makes the cooldown survive between runs.

Add your secrets under **Settings → Secrets and variables → Actions**
(`NTFY_TOPIC`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL`, `WEBHOOK_URL` —
only the ones you use). You can also trigger it by hand from the Actions tab,
with a "force" checkbox that ignores the cooldown.

**How the local time is held.** GitHub cron is UTC-only with no timezone
support, so a single entry drifts by an hour at each daylight-saving switch.
Two are scheduled instead — 13:50 UTC (09:50 EDT) and 14:50 UTC (09:50 EST).

GitHub's scheduler is also only best-effort: this workflow has been observed
starting **two hours behind** its scheduled slot, and delays of an hour are
routine on free runners. A narrow time window would therefore skip both entries
and silently do nothing, so the guard is "**not before 09:20 ET, at most once
per weekday**" instead. Whichever entry lands first past 09:20 ET does the work
and stamps the date in `state/last-run.txt`; the other sees the stamp and exits
in seconds. A run that fails leaves no stamp, so the other entry retries it.

Simulated over a year of firings with delays up to three hours, that produces
exactly one run on each of the 261 weekdays — never early, never twice, correct
on both sides of the DST switch.

**So: the alert lands at or after 09:50 ET, usually within an hour.** If you
need it at 09:50 on the dot, GitHub Actions cannot promise that no matter how
the cron is written — a `cron` entry on your own machine fires exactly on time
and is the only way to get it.

To move the time, shift both cron lines by the same amount and adjust the
floor in the "Decide whether this is today's run" step (`560` is minutes past
midnight, i.e. 09:20 ET).

Two things to check once, or it will look like nothing is happening:

1. **Scheduled workflows only run from the repository's default branch.** If
   you move this code to another branch, the cron stops firing.
2. **Settings → Actions → General → Workflow permissions** must be
   *Read and write*, or the step that commits the alert state fails and every
   run starts with an empty cooldown.

GitHub also disables scheduled workflows after 60 days with no repository
activity; the daily state commit normally counts, and if it ever does trigger
you'll get an email with a one-click re-enable.

### Local cron

```cron
15 22 * * 1-5 cd /path/to/Claude && /usr/bin/python3 -m oversold_monitor run >> monitor.log 2>&1
```

### Partial bars

Because 09:50 ET is inside the session, today's daily bar is only 20 minutes
old when the scan runs, and an RSI computed on it can swing on the opening
print and reverse by lunchtime. `exclude_partial_bar: true` (set in
`config.yml`) drops that in-progress bar so indicators use completed sessions
only — stable readings, at the cost of being one session behind. Set it to
`false` to score the live bar instead, which reacts faster and flip-flops more.

It has no effect on a run scheduled after 16:00 ET, where the day's bar is
already final.

### Intraday

Nothing stops you running it more often — replace the two cron lines with
`*/30 13-20 * * 1-5` for a half-hourly check through the session, and delete
the "Decide whether this is today's run" step. You'd also want `exclude_partial_bar: false`,
or every scan in the day would return the same completed-bar answer. The
cooldown keeps the alert volume sane.

## Configuration

Everything lives in `config.yml`, pre-populated with the 44 instruments from your
watchlist (including your position sizes for CRWV, CSIQ, AVGO, ORCL, GOOG, AAPL,
MSFT, BKSY and RDDT).

Add an instrument:

```yaml
instruments:
  - PLTR                                        # shorthand
  - {name: BTC, symbol: BTC-USD, asset_class: crypto}
  - {name: CRWV, symbol: CRWV, position: 70}    # position is context only
  - {name: OLD, symbol: OLD, enabled: false}    # keep but stop scanning
```

Symbols are Yahoo Finance tickers: `^GSPC` for the S&P index, `BTC-USD` for
crypto, `EURUSD=X` / `JPY=X` for FX, `CL=F` for front-month crude. Your `CL`
contract had expired — `CL=F` rolls automatically, so it won't expire on you again.

Tune the sensitivity:

```yaml
thresholds:
  score_alert: 55     # lower = more alerts, higher = only the extremes
  rsi_oversold: 30    # the classic line; 25 is stricter
cooldown_days: 5      # minimum gap between repeat alerts for the same name
notify_recovery: true # also tell me when something climbs back out
exclude_partial_bar: true  # ignore today's in-progress bar (see below)
```

Getting too many alerts? Raise `score_alert` to 65. Too few? Drop it to 45.

## Data

`yfinance` (free, no key) with automatic fallback to Stooq when Yahoo fails for a
symbol. Fetches run in parallel and a symbol that fails is skipped with a warning
rather than sinking the whole run.

## Tests

```bash
python -m pytest tests/ -q     # 76 tests
```

RSI is verified against Wilder's published worked example to two decimal places;
the rest covers scoring, severity, the de-duplication rules, config parsing,
provider fallback and normalisation, partial-bar trimming, notifier dispatch,
and end-to-end runs against stubbed data.

## Commands

| Command | Purpose |
|---|---|
| `run` | Scan, notify, record state — the scheduled job |
| `run --dry-run` | Print what *would* be sent; changes nothing |
| `run --force` | Ignore the cooldown |
| `run --always-notify` | Send even when nothing is oversold |
| `run --summary` | Also print the full table |
| `scan` | Read-only table of every instrument |
| `test-notify` | Sample alert through every channel |
| `state` / `state --reset` | Inspect or clear the dedupe state |

---

These are mechanical technical signals, not investment advice. An oversold
reading means a price has fallen quickly relative to its own recent range — it
is not a prediction that it will bounce, and things that are oversold routinely
get more so.
