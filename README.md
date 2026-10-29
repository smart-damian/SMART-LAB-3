# market-levels

Finds each symbol's **most-defended high and low** (a simple support/resistance
estimate) for a 24-symbol watchlist (forex, metals, indices, energy, crypto).

Two ways to use it:

1. **`main.py`** – daily scan from the command line, with a local SQLite history.
2. **`scan_tf.py`** – multi-timeframe checker (5m, 15m, 30m, 1h, 4h, 1d). GitHub
   Actions runs each timeframe on its own schedule and Telegram is messaged only
   when there is a **new** BUY/SELL signal (two labelled alerts, described below).

## Requirements

- Python 3.10+ and an internet connection
- Packages: `yfinance`, `pandas` (plus `pytest` for tests). The Telegram sender and the
  alert memory use only Python's standard library.
- For the automatic mode: a free GitHub account and a Telegram account

## Setup (Termux Ubuntu)

```bash
apt update && apt install -y python3 python3-pip python3-venv python3-pandas python3-numpy git
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally

```bash
python scan_tf.py 1h --no-telegram        # DRY RUN: print only, nothing sent, nothing remembered
python scan_tf.py 15m                     # real run: alerts Telegram if there is something new
python scan_tf.py 1h --atr-mult 3         # signal within 3 ATR instead of 2
python scan_tf.py 1h --atr-period 50      # different ATR smoothing (default 100)
python main.py scan                       # original daily scan + SQLite
python -m pytest -v                       # tests (no internet needed)
```

To test Telegram from your phone:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."   # from BotFather
export TELEGRAM_CHAT_ID="123456789"
python scan_tf.py 1d
```

Local runs keep their memory in the `state/` folder (ignored by git). Delete a file in
`state/` to make that timeframe "forget" and alert again.

## The rules

**Lookback:** every timeframe uses the newest **150 candles** (`LOOKBACK` in
`timeframes.py`). The high, the low and the ATR all come from those same candles.

**ATR (Average True Range):** the typical size of one candle's move on that symbol and
timeframe. It makes distances comparable across symbols (EURUSD moves in thousandths, gold
in tens of dollars). It uses **Wilder smoothing with a period of 100**: the first 100
candles give a plain average, then the remaining 50 each nudge it by 1/100.

**ALERT 1 (LEVEL)** – on each timeframe:

| Signal | Condition | Meaning |
|---|---|---|
| **SELL** | high − last price ≤ 2 ATR | price is close to the high |
| **BUY** | last price − low ≤ 2 ATR | price is close to the low |

**ALERT 2 (MULTI-TIMEFRAME)** – the **same symbol with the same signal** is active on at
least **`min_agree`** of the watched timeframes at once. By default it watches **all six**
timeframes and **any 2** agreeing is enough. The message tells you **which timeframes agree**.
A SELL on one timeframe and a BUY on another do not agree, because votes are counted per signal.

**No repeats:** an alert is sent only when a signal is **new**, meaning the symbol just
entered the zone. While it stays there nothing more is sent. When it leaves the zone and
later comes back, it alerts again. (Think of a doorbell: it rings when someone arrives, not
for as long as they stand at the door.) For Alert 2, "new" means a timeframe has **joined**
the agreeing group: you get a message when two first agree, and another when a further
timeframe joins, but not while the same group stays put.

The full table is always printed, so you can read it in the GitHub Actions log.

## The Alert 2 setting (one place)

Open `timeframes.py` and edit this one block:

```python
ALERT2 = Alert2Setting(
    timeframes=tuple(TIMEFRAMES),   # all timeframes above
    min_agree=2,                    # any 2 of them agreeing is enough
)
```

Examples:

| You want | Set |
|---|---|
| Any 2 of all 6 (default) | `timeframes=tuple(TIMEFRAMES), min_agree=2` |
| At least 3 of all 6 | `timeframes=tuple(TIMEFRAMES), min_agree=3` |
| Only 5m + 1h + 4h, any 2 | `timeframes=("5m", "1h", "4h"), min_agree=2` |
| Slow version: all of 1h, 4h, 1d | `timeframes=("1h", "4h", "1d"), min_agree=3` |

Nothing else needs editing:

- The **fastest** watched timeframe becomes the evaluator (its run checks Alert 2 and reads the
  saved results of the others). The workflow finds that out by itself.
- The Telegram header follows the setting (e.g. `2+ of 6 agree`).
- A wrong setting (fewer than 2 timeframes, a repeated or unknown name, `min_agree` below 2 or
  above the number of timeframes) stops with a clear error instead of running with nonsense.

A saved result may be at most 4 of *its own* candles old (30m: 2 hours, 4h: 16 hours, 1d: 4 days).
A stale or missing timeframe is simply not counted that run, and the log says which one.

If you want a timeframe that does not exist yet (say `2h`), first add it to the `TIMEFRAMES`
table, to the cron list and the `case` block in the workflow, and to the workflow's dropdown
and its list of "Restore ... memory" steps.

## Automatic mode: GitHub Actions + Telegram

### 1. Create the Telegram bot (once)

1. In Telegram, open **@BotFather**, send `/newbot`, follow the prompts. It gives you a **token**.
2. Open your new bot and send it any message (e.g. "hi").
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. Find
   `"chat":{"id":123456789` – that number is your **chat id**.

The token is a password. Never put it in a file or in git.

### 2. Put the project on GitHub

Create an empty repo on github.com (see "Cost" below before choosing public/private), then in the project folder:

```bash
git init
git add .
git commit -m "Multi-timeframe market-levels"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

GitHub asks for a username and password: the password is a **personal access
token** (GitHub → Settings → Developer settings → Tokens). A classic token needs
the `repo` and `workflow` scopes; the `workflow` one is required to push files
under `.github/workflows/`.

### 3. Add the two secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the BotFather token |
| `TELEGRAM_CHAT_ID` | your chat id |

### 4. Test, then let it run

Repo → **Actions → Levels → Run workflow**, pick `1d`, then open the run to read the
table. A Telegram message arrives only if something is new. To force a test message from
your phone: `python scan_tf.py 1d --atr-mult 50` (the first run after clearing `state/`
alerts on everything within 50 ATR).

**Expect a burst on the very first runs:** with no memory yet, every symbol already near a
level counts as new and is sent once. After that you only get changes.

## Schedules (UTC)

| Timeframe | Cron | When it runs |
|---|---|---|
| 5m | `*/5 * * * *` | every 5 minutes |
| 15m | `4,19,34,49 * * * *` | :04, :19, :34, :49 |
| 30m | `1,31 * * * *` | :01 and :31 |
| 1h | `2 * * * *` | :02 each hour |
| 4h | `3 */4 * * *` | 00:03, 04:03, 08:03, ... |
| 1d | `6 0 * * *` | 00:06 daily |

Offsets of a few minutes make sure the candle has closed and avoid GitHub's busiest
moment (the top of the hour). To change or add a schedule, edit **both** the `cron:` line
and the matching line in the `case` block of `.github/workflows/levels.yml`.

## Example output (illustrative numbers)

Telegram message when something new happens (Alert 1 and Alert 2 can arrive together):

```
ALERT 1 (LEVEL) 5m  2026-10-26 14:05 UTC  (within 2 ATR)
SYMBOL   SIGNAL       LEVEL       LAST  ATRs
EURUSD=X SELL       1.10300    1.09800  1.61
GC=F     BUY     2,590.20   2,601.50    0.62

ALERT 2 (MULTI-TIMEFRAME)  2+ of 6 agree
2026-10-26 14:05 UTC  ATRs from level, limit 2

EURUSD=X SELL @ 1.09800  (3 agree)
  5m 1.61   15m 1.20   4h 0.70

GC=F BUY @ 2,601.50  (2 agree)
  1h 0.62   1d 1.10
```

`ATRs` is how many ATRs the price is from that level (smaller means closer). Alert 2 lists only the
timeframes that agree. Note that a timeframe's number is as of its own last run.

In the Actions log you also see the full table and lines such as
`3 signal(s) still active but already alerted earlier: not repeated.` or `No new alerts.`

## How the alert memory works

A GitHub runner is wiped after every run, so the script cannot remember anything by itself.
After each run, the workflow saves a small file (`state/<timeframe>.json`) to the **Actions
cache**, and the next run of that timeframe restores it.

```json
{"updated": "2026-10-26T14:05:00+00:00",
 "zones": {"EURUSD=X": {"SELL": 1.61}},
 "confluence": {"EURUSD=X": {"SELL": ["5m", "15m", "4h"]}}}
```

- `zones`: who is near a level right now (and how many ATRs away).
- `confluence`: only in the evaluator's file (5m by default); for each symbol and signal, which
  timeframes were in the group at the last evaluation.
- Each timeframe's file is written by that timeframe's run only. The evaluator run also **reads**
  the other watched timeframes' files. One writer per file means two jobs can never overwrite
  each other.
- If sending to Telegram fails, the memory is **not** saved, so the next run tries again.
- If the memory is ever lost (cache evicted after 7 days unused), the worst case is one repeated alert.

## Project structure

```
market-levels/
  scan_tf.py            multi-timeframe entry point (one timeframe per run)
  timeframes.py         timeframe table, LOOKBACK, the ALERT2 setting, 1h -> 4h builder
  alerts.py             ATR, BUY/SELL rule, no-repeat logic, multi-timeframe alert
  state.py              reads/writes the alert memory files
  notifier.py           Telegram sender (standard library only)
  main.py               original daily CLI (scan / history)
  levels.py             pure maths: find_level(), analyze()
  fetcher.py            yfinance download + safe per-symbol extraction
  storage.py            SQLite save/read (used by main.py only)
  symbols.txt           the watchlist (config, not code)
  requirements.txt      Python dependencies
  state/                alert memory (created while running, git-ignored)
  tests/                test_levels.py, test_timeframes.py, test_alerts.py, test_state.py
  .github/workflows/levels.yml   the six schedules + memory save/restore
```

Outdated versions of files are kept in `projects/TRASH/market-levels/` (see its `ARCHIVE_LOG.md`).

## How it works

1. A cron schedule fires. The workflow works out which timeframe that schedule means
   (`github.event.schedule` holds the cron text that triggered it) and restores that
   timeframe's memory. If this is the Alert 2 evaluator, it also restores the other watched
   timeframes' memory (it asks `timeframes.py` which ones).
2. `scan_tf.py` downloads all symbols in one request. For 4h it merges 1h candles into 4h.
3. For each symbol, `levels.py` finds the high and low and `alerts.py` computes the ATR, both
   from the newest 150 candles.
4. `alerts.py` marks SELL (near the high) and BUY (near the low), then keeps only signals that
   are new compared with the memory. The evaluator run also counts, per symbol and signal, how many
   watched timeframes agree (its own fresh result plus the others' saved results).
5. The full table is printed. If anything is new, one Telegram message is sent with the
   labelled alert(s).
6. Only after a successful send, the memory is updated and saved. The runner is then thrown away.

## Why it is built this way

| Decision | Reason |
|---|---|
| One script run per timeframe | Each schedule is independent: if the 5m job fails, 1d is unaffected |
| Settings in one table (`timeframes.py`) | Tuning is a one-line edit, not a logic change |
| Alert 2 as one setting object (`ALERT2`) | One fact lives in one place; code, workflow and messages read it instead of repeating it |
| Setting validated when it is created | A typo fails loudly right away instead of silently never alerting |
| Votes counted per signal, then compared with `min_agree` | "Any 2 of 6" is one rule that also covers "all 3 of 3" |
| Rules in pure functions (`alerts.py`) | Testable with pencil-sized numbers, no internet needed |
| Distance measured in ATRs, not price | "2 ATR" means the same thing for EURUSD and gold |
| Alert only on entering the zone / joining the group | Otherwise the same symbol would message you every run |
| Send first, save memory second | A failed send is retried next run instead of being lost |
| One writer per memory file | Two jobs running together can't overwrite each other |
| Fastest watched timeframe evaluates Alert 2 | One evaluator keeps the "already sent" memory in one place, and runs often enough to be timely |
| Memory in the Actions cache, not git commits | No commit spam, no extra permission, loss is harmless |
| 1d downloads `1y`, not `4mo` | 4 months is only ~85 weekday candles, fewer than the 101+ needed |
| Token in GitHub Secrets | Secrets are encrypted and hidden in logs; code in git is not private |
| Telegram errors never print the URL | The URL contains the token |
| `permissions: contents: read`, `timeout-minutes: 5` | Least privilege; a hung job can't burn your minutes |

## Cost, limits and honest warnings

- **Free minutes:** GitHub gives unlimited minutes on **public** repos, but only
  2,000 minutes/month on private ones. The 5m job alone is about 8,600 runs a month and
  each run is billed at least one minute, so it cannot fit in a private repo. A public repo
  means your symbols.txt and code are visible (the secrets stay hidden).
- **Slow timeframes are "as of their last run":** the 1h, 4h and 1d numbers in Alert 2 were
  calculated when those jobs last ran (up to an hour, 4 hours or a day ago), while the 5m number
  is fresh. A 1d vote can therefore be up to a day old. Treat Alert 2 as "these timeframes recently
  said the same thing", not "all of them agree this very second".
- **Alert 2 can be late:** only the evaluator run (the fastest timeframe) checks it, so a match can
  show up up to one of its runs after the last timeframe enters the zone. GitHub can also delay or skip runs.
- **More timeframes means more chances to agree:** with 6 timeframes and min_agree 2, Alert 2 will fire
  often, partly by chance, because nearby timeframes use overlapping price history. Raise `min_agree`
  if it is too noisy.
- **Timing is not exact:** GitHub may delay scheduled runs by several minutes under load and
  can skip some. Treat "5m" as "roughly every 5 to 15 minutes".
- **Memory is best-effort:** the Actions cache can be cleared by GitHub, which causes one repeated alert.
- **Inactive repos:** GitHub disables scheduled workflows on a public repo after 60 days
  without repo activity. Push a small commit or re-enable them in the Actions tab.
- **Symbols may be skipped:** an ATR of period 100 needs at least 101 candles. Thin symbols
  (e.g. `ALI=F`) may have fewer and are listed as `skipped`.
- **Data:** Yahoo's free data (via unofficial `yfinance`) is limited to about 60 days for
  5m/15m/30m, can be delayed, may rate-limit GitHub's shared IPs, and can differ from your
  broker's prices.
- **4h candles** are aligned to Yahoo's timestamps and may not match your broker's 4h candles.
- The newest, still-forming candle is included as it stands.
- BUY/SELL here only mean "price is close to the low/high of the last 150 candles". They are
  not trading advice and have not been tested for profitability.
- `main.py` is unchanged: it still uses 4 months of daily data and a 120-candle lookback.

## Next steps

- Backtest the idea before trusting it: on past data, how often does a symbol that 2 (or 3, or 4)
  timeframes flagged actually reverse, compared with one timeframe alone? That tells you the right
  `min_agree`.
- Exercise: make Alert 2 count only timeframes that are **different lengths apart** (for example, ignore
  5m + 15m agreeing on their own, because they share most of their price history). Which function in
  `alerts.py` would you change, and what would the setting look like?
