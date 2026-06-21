#!/usr/bin/env python3
"""
alert.py  --  Daily FINRA short-volume spike screener.

Two modes (set in config.json):
  - "universe": false  -> check only the watchlist tickers, per-ticker thresholds.
  - "universe": true   -> scan EVERY US ticker in the FINRA file, but keep only
                          liquid names (total-volume floor) and report the top N
                          by spike size. Without those filters the whole universe
                          is a daily firehose of micro-cap noise.

Spike = today's short volume / median of the previous `window` days (median is
robust to outliers). A baseline floor kills divide-by-tiny artifacts.

History is kept as a ROLLING window (last window+5 days only) so the repo file
stays small. On first run (empty history) it self-seeds by fetching recent files.

ATTENTION tool, not a signal: short-volume spikes are ~coin flips for direction.
"""

from __future__ import annotations

import io
import json
import os
from datetime import date, timedelta

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(ROOT, "data", "short_history.csv")
CFG = os.path.join(ROOT, "config.json")
URLS = [
    "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt",
    "http://regsho.finra.org/CNMSshvol{ymd}.txt",
]


def fetch_day(d, session):
    ymd = d.strftime("%Y%m%d")
    for tmpl in URLS:
        try:
            r = session.get(tmpl.format(ymd=ymd), timeout=45)
        except Exception:
            continue
        if r.status_code != 200 or "Symbol" not in r.text[:200]:
            continue
        df = pd.read_csv(io.StringIO(r.text), sep="|")
        cols = {c.lower().replace(" ", ""): c for c in df.columns}
        if not {"symbol", "shortvolume", "totalvolume"} <= set(cols):
            continue
        out = pd.DataFrame({
            "symbol": df[cols["symbol"]].astype(str),
            "short_volume": pd.to_numeric(df[cols["shortvolume"]], errors="coerce"),
            "total_volume": pd.to_numeric(df[cols["totalvolume"]], errors="coerce"),
        }).dropna()
        out = out[out["symbol"].str.match(r"^[A-Z.\-]+$", na=False)]
        out["date"] = pd.Timestamp(d).normalize()
        return d, out
    return None, None


def weekdays_back(n, end=None):
    end = end or date.today()
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def main():
    cfg = json.load(open(CFG))
    window = int(cfg.get("window", 20))
    universe = bool(cfg.get("universe", False))
    default_threshold = float(cfg.get("threshold", 3.0))
    per_ticker = {k.upper(): float(v) for k, v in cfg.get("thresholds_by_ticker", {}).items()}
    min_volume = float(cfg.get("min_volume", 50000))          # short-vol baseline floor (anti-artifact)
    liq_min = float(cfg.get("liquidity_min_volume", 1_000_000))  # total-vol floor (liquidity gate)
    top_n = int(cfg.get("top_n", 25))
    watch = [t.upper() for t in cfg.get("tickers", [])]

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (research-alert)"})

    if os.path.exists(HIST):
        hist = pd.read_csv(HIST, parse_dates=["date"])
    else:
        hist = pd.DataFrame(columns=["date", "symbol", "short_volume", "total_volume"])

    have = set(pd.to_datetime(hist["date"]).dt.normalize()) if len(hist) else set()
    targets = weekdays_back(window + 6)              # recent trading days we want
    missing = [d for d in targets if pd.Timestamp(d) not in have]

    latest_day = None
    frames = []
    for d in sorted(missing):                        # fetch only what we lack (usually just today)
        fd, df = fetch_day(d, session)
        if df is not None:
            frames.append(df); latest_day = max(latest_day or fd, fd)
    if latest_day is None and len(hist):
        latest_day = pd.to_datetime(hist["date"]).max().date()
    if latest_day is None:
        print("No FINRA data available; skipping."); _set_output(False); return

    if frames:
        hist = pd.concat([hist] + frames, ignore_index=True)
    if not universe and watch:
        hist = hist[hist["symbol"].isin(watch)]
    hist = hist.drop_duplicates(["date", "symbol"], keep="last")

    # rolling prune: keep only the last window+5 dates
    keep_dates = sorted(pd.to_datetime(hist["date"]).dt.normalize().unique())[-(window + 5):]
    hist = hist[pd.to_datetime(hist["date"]).dt.normalize().isin(keep_dates)].copy()
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    hist.sort_values(["symbol", "date"]).to_csv(HIST, index=False)

    today = pd.Timestamp(latest_day).normalize()
    print(f"Latest FINRA day: {latest_day} | mode: "
          f"{'UNIVERSE' if universe else 'watchlist'} | rows: {len(hist)}")

    # compute spikes
    alerts = []
    for sym, s in hist.sort_values("date").groupby("symbol"):
        if s["date"].max() != today or len(s) < window + 1:
            continue
        today_sv = s["short_volume"].iloc[-1]
        prev = s.iloc[-(window + 1):-1]
        base_sv = prev["short_volume"].median()
        base_tv = prev["total_volume"].median()
        if base_sv < min_volume:
            continue
        if universe and base_tv < liq_min:
            continue
        mult = today_sv / base_sv
        thr = per_ticker.get(sym, default_threshold)
        if mult >= thr:
            alerts.append((sym, mult, int(today_sv), int(base_sv), thr))

    alerts.sort(key=lambda x: -x[1])
    total = len(alerts)
    shown = alerts[:top_n] if universe else alerts

    if shown:
        head = (f"## Short-volume spike screen — {latest_day}" if universe
                else f"## Short-volume spike alert — {latest_day}")
        sub = (f"{total} liquid US names spiked today (median total vol >= "
               f"{liq_min:,.0f}); top {len(shown)} by size:" if universe
               else "Watchlist names at/above their trigger (today / "
                    f"{window}-day median):")
        lines = [head, "", sub, ""]
        for sym, mult, tv, base, thr in shown:
            lines.append(f"- **{sym}**: {mult:.1f}x  (>= {thr:g}x; today {tv:,} "
                         f"vs median {base:,})")
        lines += ["", "_Attention only — short-volume spikes are ~coin flips for "
                  "direction and include market-making. A list to LOOK at, not to trade._"]
        open(os.path.join(ROOT, "alerts.md"), "w").write("\n".join(lines))
        print("\n".join(lines))
        _set_output(True)
    else:
        print("No spikes above threshold today."); _set_output(False)


def _set_output(has):
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"has_alerts={'true' if has else 'false'}\n")


if __name__ == "__main__":
    main()
