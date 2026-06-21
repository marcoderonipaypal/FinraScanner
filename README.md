# FINRA short-volume alerts / screener

Daily check that opens a GitHub issue when short volume spikes. Two modes (set in
`config.json`):

- **Watchlist** (`"universe": false`): only your tickers, each with its own trigger.
- **Universe** (`"universe": true`): scans every US ticker in the FINRA file, keeps
  only liquid names (total-volume floor), reports the top N by spike size.

Spike = today's short volume / **median** of the previous `window` days. The median
is robust to outliers; a baseline floor removes divide-by-tiny artifacts.

**Attention tool, not a signal.** Short-volume spikes are ~coin flips for direction
and include market-making. An alert means "go look", nothing more.

## Why the filters matter (universe mode)
Without a liquidity floor, the whole universe throws 100+ micro-cap junk spikes every
day and the alert becomes noise. `liquidity_min_volume` keeps only tradable names;
`top_n` caps the list so the email stays readable.

## Setup (once)
1. Create a GitHub repo, add these files (keep the folder layout).
2. **Settings -> Actions -> General -> Workflow permissions -> Read and write.**
3. Done. Runs every weekday 23:00 UTC. First run self-seeds the history by fetching
   the last ~25 trading days (no manual seed needed). Test now: **Actions tab ->
   Run workflow.**

## config.json
```json
{
  "universe": true,
  "window": 20,
  "threshold": 3.0,
  "min_volume": 50000,
  "liquidity_min_volume": 1000000,
  "top_n": 25,
  "tickers": ["FI","TSLA","SSYS","BA","SIDU","KO","QCOM","INTC","AI","REPL"],
  "thresholds_by_ticker": { "TSLA": 2.5, "KO": 2.5, "REPL": 4.5, "SIDU": 8.0 }
}
```
- `universe`: true = scan everything; false = watchlist only.
- `window`: days for the median baseline (recomputed daily, so triggers self-adapt).
- `threshold`: default spike multiple. `thresholds_by_ticker` overrides per name
  (used in watchlist mode; also honored in universe mode for listed names).
- `min_volume`: short-vol baseline floor (kills fake 800x spikes on dead names).
- `liquidity_min_volume`: median TOTAL volume a name must have to appear in universe
  mode. Raise it to cut more micro-cap noise (e.g. 5000000 = only big names).
- `top_n`: max names listed per day in universe mode.

## Cost
Public repo = unlimited free Actions minutes. Private = ~40 min/month (the job is
~1-2 min/day; the FINRA file is downloaded whole in both modes). History is kept to a
rolling window (last window+5 days) so the repo never bloats.

## Honest note
The universe screener is a discovery list, not edge. Tested on ~4,600 stocks, short
spikes had no reliable tradable direction. Use it to notice unusual activity on names
you then judge for yourself.

## Email to a custom address (SMTP)
The workflow also emails the alert to any address via SMTP. Set these in
**Settings -> Secrets and variables -> Actions -> New repository secret**:

| secret | example | notes |
|--------|---------|-------|
| `MAIL_SERVER` | `smtp.gmail.com` | your provider's SMTP host |
| `MAIL_PORT` | `465` | 465 (SSL). Outlook: `smtp-mail.outlook.com` / `587` |
| `MAIL_USERNAME` | `you@gmail.com` | the sending account |
| `MAIL_PASSWORD` | `abcd efgh ijkl mnop` | **app password**, not your login password |
| `MAIL_TO` | `other@address.com` | where alerts are delivered |

Gmail: enable 2-factor auth, then create an **App password** (Google Account ->
Security -> App passwords) and use that as `MAIL_PASSWORD`. The recipient
(`MAIL_TO`) can be any address, no GitHub account needed.

The GitHub issue step still runs too (a log in the repo); delete that step from the
workflow if you only want the email.
