#!/usr/bin/env python3
"""
watchlist_spikes.py  --  For a hand-picked watchlist, measure the MATHEMATICAL
size of short-volume spikes ("X times the average of the previous candles") and
then HONESTLY show what actually followed every such spike -- winners AND losers.

Two things, deliberately kept separate:

  1) THE CRITERION (safe): for each stock, how big is a "notable" short-volume
     spike, measured as today's short volume / average of the previous W days.
     This is read off the stock's OWN spike distribution, NOT off price outcomes,
     so it is honest to use as an ALERT threshold ("unusual short activity").

  2) WHAT FOLLOWED (reality check): for every spike above each threshold, the
     forward 1w / 1m / 3m price move, with the up/down tally. This is where your
     memory of "interesting results" gets tested against ALL the cases, not the
     few you remember. Picking the stocks because they worked, then trusting the
     threshold, would be overfitting -- this section is the antidote.

Reuses finra_short.parquet (finra_fetch.py) + prices_daily.parquet (spike_study.py).

Usage:
  python3 watchlist_spikes.py
  python3 watchlist_spikes.py --window 20 --thresholds 2,3,4,5
  python3 watchlist_spikes.py --metric ratio          # use short/total instead of raw
  python3 watchlist_spikes.py --export-seed seed.csv   # seed for the GitHub alerts
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HZ = {"1w": 5, "1m": 21, "3m": 63}
DEFAULT = ["FI", "TSLA", "SSYS", "BA", "SIDU", "KO", "QCOM", "INTC", "AI", "REPL"]


def _norm(idx):
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--finra", default=os.path.join(SCRIPT_DIR, "finra_short.parquet"))
    ap.add_argument("--daily-cache", default=os.path.join(SCRIPT_DIR, "prices_daily.parquet"))
    ap.add_argument("--tickers", default=",".join(DEFAULT))
    ap.add_argument("--window", type=int, default=20,
                    help="how many previous candles for the baseline")
    ap.add_argument("--min-volume", type=float, default=50000.0,
                    help="floor on the trailing baseline: ignore days whose "
                         "median short volume is below this (kills divide-by-tiny artifacts)")
    ap.add_argument("--thresholds", default="2,3,4,5",
                    help="spike multiples to test (today / trailing average)")
    ap.add_argument("--metric", choices=["raw", "ratio"], default="raw",
                    help="raw short volume (what TradingView shows) or short/total ratio")
    ap.add_argument("--export-seed", default=None,
                    help="write recent short-volume history for the watchlist (alert seed)")
    args = ap.parse_args()

    if not os.path.exists(args.finra):
        sys.exit(f"{args.finra} not found -> run finra_fetch.py first.")
    panel = pd.read_parquet(args.finra); panel.index = _norm(panel.index)
    field = "short_volume" if args.metric == "raw" else "short_ratio"
    sv = panel[field]; sv = sv[~sv.index.duplicated(keep="last")].sort_index()

    px = None
    if os.path.exists(args.daily_cache):
        px = pd.read_parquet(args.daily_cache); px.index = _norm(px.index)
        px = px[~px.index.duplicated(keep="last")].sort_index()

    want = [t.strip().upper() for t in args.tickers.split(",")]
    missing = [t for t in want if t not in sv.columns]
    if missing:
        print(f"[warn] not in FINRA data (check symbol): {missing}")
    tickers = [t for t in want if t in sv.columns]
    if not tickers:
        sys.exit("None of the watchlist symbols are present in the FINRA file.")

    thresholds = [float(x) for x in args.thresholds.split(",")]
    print(f"Metric: {args.metric} short volume | trailing window: {args.window} candles\n")

    crit_rows = []
    for tk in tickers:
        s = sv[tk].dropna()
        if s.shape[0] < args.window + 70:
            print(f"{tk}: not enough history.\n"); continue
        trail = s.rolling(args.window).median().shift(1)
        mult = (s / trail).where(trail >= args.min_volume)
        mult = mult.replace([np.inf, -np.inf], np.nan)
        m = mult.dropna()
        if m.empty:
            print(f"{tk}: baseline always below floor ({args.min_volume:,.0f}); skipped.\n")
            continue
        p90, p95, p99 = m.quantile([0.90, 0.95, 0.99])
        biggest = m.max(); big_day = m.idxmax()
        crit_rows.append({"ticker": tk, "p90x": round(p90, 1), "p95x": round(p95, 1),
                          "p99x": round(p99, 1), "max_x": round(biggest, 1)})
        print(f"=== {tk} ===")
        print(f"  spike size (today / {args.window}-day median): typical big spike "
              f"~{p95:.1f}x (95th pct), rare ~{p99:.1f}x (99th). Biggest ever "
              f"{biggest:.1f}x on {big_day.date()}.")

        if px is not None and tk in px.columns:
            p = px[tk]
            fwd = {k: (p.shift(-(1 + H)) / p.shift(-1) - 1.0) for k, H in HZ.items()}
            for M in thresholds:
                days = mult.index[mult >= M]
                days = [d for d in days if d in p.index]
                if len(days) < 5:
                    continue
                line = f"   >= {M:.0f}x  ({len(days):>3} spikes): "
                parts = []
                for k in HZ:
                    v = np.array([fwd[k].get(d, np.nan) for d in days], float)
                    v = v[np.isfinite(v)]
                    if v.size == 0:
                        continue
                    up = int((v > 0).sum()); dn = int((v <= 0).sum())
                    parts.append(f"{k}: {up}up/{dn}dn avg {v.mean()*100:+.1f}%")
                print(line + "  |  ".join(parts))
        else:
            print("   (no price series to show what followed)")
        print()

    if crit_rows:
        df = pd.DataFrame(crit_rows)
        print("=== The criterion, across your watchlist ===")
        print(df.to_string(index=False))
        print(f"\nTypical 'notable spike' threshold = {df['p95x'].mean():.1f}x the "
              f"{args.window}-day median (mean of the 95th percentiles).")
        print(f"Rare/extreme threshold = {df['p99x'].mean():.1f}x (mean of 99th).")
        print("\nHONEST READING:")
        print("  - Use these multiples ONLY to define 'unusual short activity' for an")
        print("    alert. They are read off the spike sizes, not off price outcomes.")
        print("  - In the per-stock blocks above, check the up/dn tallies. If they are")
        print("    near 50/50, the 'interesting results' you remember were selection,")
        print("    not edge. A real signal would be clearly lopsided across ALL spikes.")

    if args.export_seed:
        recent = sv[tickers].tail(max(args.window * 4, 80))
        long = recent.stack().rename("short_volume").reset_index()
        long.columns = ["date", "symbol", "short_volume"]
        long.to_csv(args.export_seed, index=False)
        print(f"\nSeed written -> {args.export_seed} "
              f"({long.shape[0]} rows) for the GitHub alert repo.")


if __name__ == "__main__":
    main()
