"""
Compare real-time candles with historical candles.
Every candle in both files (same timeframe + time) should have the same
open, high, low, close, volume and count. Differences go to realtime_vs_historical.csv.

Run: python compare.py
"""
import csv
from pathlib import Path

HERE = Path(__file__).parent
FILES = {"GOOG": "us_stock", "TSLA": "high_volatility", "EUR/USD": "fx_pair"}
FIELDS = ["open", "high", "low", "close", "volume", "count"]


def load(path):
    with open(path, newline="") as f:
        return {(r["timeframe"], r["time_utc"]): r for r in csv.DictReader(f)}


def differs(a, b):
    a, b = float(a), float(b)
    return abs(a - b) > 1e-6 * max(1.0, abs(a), abs(b))   # ignore rounding noise


diffs = []
for symbol, name in FILES.items():
    realtime = load(HERE / "RealTimeCandle" / f".{name}_realtime_candles.csv")
    historical = load(HERE / "HistoricalCandle" / f".{name}_candles.csv")
    both = sorted(key for key in realtime if key in historical)

    bad = 0
    for tf, time in both:
        rt, hist = realtime[(tf, time)], historical[(tf, time)]
        changed = [f for f in FIELDS if differs(rt[f], hist[f])]
        if changed:
            bad += 1
            print(f"  DIFF {symbol} {tf} {time}: " + ", ".join(f"{f} {rt[f]} -> {hist[f]}" for f in changed))
            diffs += [[symbol, tf, time, f, rt[f], hist[f]] for f in changed]
    print(f"\n{symbol}: {len(both)} candles in both files, {len(both) - bad} identical, {bad} different\n")

with open(HERE / "realtime_vs_historical.csv", "w", newline="") as f:
    csv.writer(f).writerows([["symbol", "timeframe", "time_utc", "field", "realtime", "historical"], *diffs])
print(f"Wrote {len(diffs)} differing fields to realtime_vs_historical.csv")
