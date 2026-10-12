"""Validate dxFeed candle data produced by ApiCaller.py.

HOW TO RUN IT:
  python validate.py                       # check the three default CSV files
  python validate.py fx_pair_candles.csv   # check only the files you list
  python validate.py --requested-from 2026-10-01T00:00 --requested-to 2026-10-01T12:00

It also writes every aggregation mismatch to aggregation_mismatches.csv.
"""

# ---------------------------------------------------------------------------
# IMPORTS: ready-made tools that come with Python (no pandas needed here)
# ---------------------------------------------------------------------------
import argparse                                   # reads options typed on the command line
import csv                                        # reads and writes CSV files
import math                                       # maths helpers; we use math.isnan()
from collections import Counter                   # a dictionary that counts things for us
from datetime import datetime, timezone           # work with dates/times and the UTC time zone
from pathlib import Path                          # an easy way to work with file paths

# ---------------------------------------------------------------------------
# SETTINGS: fixed values used throughout the script
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent                      # the folder this script lives in

DEFAULT_FILES = [                                 # files checked when you give no file names
    HERE / "us_stock_candles.csv",                # a normal US stock (GOOG)
    HERE / "fx_pair_candles.csv",                 # a currency pair (EUR/USD)
    HERE / "high_volatility_candles.csv",         # a fast-moving stock (TSLA)
]

MISMATCH_FILE = HERE / "aggregation_mismatches.csv"   # where the mismatch report is saved

SECONDS_PER_TIMEFRAME = {                         # how long each candle type lasts, in seconds
    "1m": 60,                                     # 1 minute  = 60 seconds
    "5m": 300,                                    # 5 minutes = 300 seconds
    "1h": 3600,                                   # 1 hour    = 3600 seconds
}

AGGREGATIONS = [                                  # which (small, big) candle pairs we compare
    ("1m", "5m"),                                 # five 1-minute candles should make one 5-minute candle
    ("5m", "1h"),                                 # twelve 5-minute candles should make one 1-hour candle
]

NUMBER_COLUMNS = ["open", "high", "low", "close", "volume", "count", "vwap"]  # CSV columns holding numbers

TOLERANCE = 0.000001                              # numbers closer than this (relative) count as "equal"

MISMATCH_COLUMNS = [                              # column names for the mismatch CSV we write
    "symbol", "check", "parent_time_utc", "field", "parent_value",
    "aggregated_value", "diff", "diff_pct", "children_found",
    "children_expected", "boundary",
]


# ---------------------------------------------------------------------------
# SMALL HELPER FUNCTIONS
# ---------------------------------------------------------------------------
def to_text(seconds):
    """Turn a timestamp (seconds since 1970) into readable text like '2026-10-01 05:00'."""
    moment = datetime.fromtimestamp(seconds, timezone.utc)   # seconds -> a date/time object in UTC
    return moment.strftime("%Y-%m-%d %H:%M")                 # date/time object -> text


def to_number(text):
    """Turn text from the CSV into a number. Empty or broken values become NaN ("not a number")."""
    try:                                          # try the conversion...
        return float(text)                        # e.g. "350.81" -> 350.81
    except (TypeError, ValueError):               # ...and if it fails (empty cell, bad text)
        return float("nan")                       # use NaN to mean "no value"


def is_different(a, b):
    """Return True if two numbers are really different (ignoring tiny rounding noise)."""
    a_missing = math.isnan(a)                     # is a "no value"?
    b_missing = math.isnan(b)                     # is b "no value"?
    if a_missing and b_missing:                   # both missing -> treat as equal
        return False
    if a_missing or b_missing:                    # only one missing -> different
        return True
    allowed = TOLERANCE * max(1.0, abs(a), abs(b))   # how big a difference we tolerate
    return abs(a - b) > allowed                   # different if the gap is bigger than allowed


def find_runs(timestamps, step):
    """Group sorted timestamps into runs of back-to-back values.

    Example with step=60: [0, 60, 120, 600] -> [[0, 120, 3], [600, 600, 1]]
    Each run is [start, end, how_many].
    """
    runs = []                                     # the list of runs we are building
    for ts in timestamps:                         # go through each timestamp in order
        if runs and ts == runs[-1][1] + step:     # does it directly follow the end of the last run?
            runs[-1][1] = ts                      #   yes -> extend that run's end
            runs[-1][2] += 1                      #          and add 1 to its length
        else:
            runs.append([ts, ts, 1])              #   no  -> start a new run of length 1
    return runs                                   # give the list of runs back


def parse_utc(text):
    """Read a date typed on the command line (e.g. 2026-10-01T00:00) as a UTC time."""
    moment = datetime.fromisoformat(text)         # text -> date/time object
    return moment.replace(tzinfo=timezone.utc)    # mark it as UTC


# ---------------------------------------------------------------------------
# LOADING THE CSV
# ---------------------------------------------------------------------------
def load(path):
    """Read one CSV file.

    Returns three things:
      symbol     - the instrument name, e.g. "GOOG"
      candles    - {timeframe: {timestamp: candle}}
                   e.g. candles["1m"][1790830800] = {"open": 350.81, "high": ..., ...}
      duplicates - a list of (timeframe, time) for rows that appeared more than once
    """
    candles = {}                                  # empty; filled in as we read rows
    duplicates = []                               # empty; filled when we see a repeat
    symbol = None                                 # will be set from the first row

    with path.open(newline="", encoding="utf-8") as csv_file:   # open the file for reading
        for row in csv.DictReader(csv_file):      # each row becomes a dictionary: {"symbol": "GOOG", ...}
            symbol = row["symbol"]                # remember the instrument name
            timeframe = row["timeframe"]          # "1m", "5m" or "1h"

            moment = datetime.fromisoformat(row["time_utc"])   # text time -> date/time object
            ts = int(moment.timestamp())          # date/time -> whole seconds since 1970 (easy to do maths with)

            candle = {}                           # build one candle as a dictionary
            for column in NUMBER_COLUMNS:         # for open, high, low, close, ...
                candle[column] = to_number(row[column])   # store the value as a number

            if timeframe not in candles:          # first time we see this timeframe?
                candles[timeframe] = {}           #   create an empty box for it

            if ts in candles[timeframe]:          # already have a candle at this time?
                duplicates.append((timeframe, to_text(ts)))   # remember it as a duplicate

            candles[timeframe][ts] = candle       # store the candle (a duplicate replaces the earlier one)

    return symbol, candles, duplicates            # hand the results back


# ---------------------------------------------------------------------------
# CHECK 1: INTEGRITY - does each candle make sense on its own?
# ---------------------------------------------------------------------------
def check_integrity(candles, duplicates):
    print("  Integrity")

    message = f"    duplicate timestamps: {len(duplicates)}"   # how many duplicates we found
    if duplicates:                                # if there are any...
        message += f"  e.g. {duplicates[:3]}"     #   show the first 3 as examples
    print(message)

    for timeframe, series in candles.items():     # check each timeframe separately
        step = SECONDS_PER_TIMEFRAME[timeframe]   # length of one candle in seconds

        off_grid = []                             # candles not starting on a clean boundary (e.g. 05:03 for 5m)
        bad_prices = []                           # candles where high/low don't fit open/close
        negative = []                             # candles with negative volume or count
        bad_vwap = []                             # candles where the average price is outside low..high

        for ts, c in series.items():              # look at every candle (ts = time, c = candle)
            when = to_text(ts)                    # readable time, used in the lists above

            if ts % step != 0:                    # time not an exact multiple of the step?
                off_grid.append(when)

            high_too_low = c["high"] < max(c["open"], c["close"], c["low"])   # high must be the biggest price
            low_too_high = c["low"] > min(c["open"], c["close"], c["high"])   # low must be the smallest price
            if high_too_low or low_too_high:
                bad_prices.append(when)

            if c["volume"] < 0 or c["count"] < 0:   # you can't trade a negative amount
                negative.append(when)

            vwap = c["vwap"]
            if not math.isnan(vwap):              # only check vwap if it has a value
                lowest_ok = c["low"] - 1e-9       # tiny margin for rounding
                highest_ok = c["high"] + 1e-9
                if not (lowest_ok <= vwap <= highest_ok):   # average price must sit between low and high
                    bad_vwap.append(when)

        print(f"    {timeframe}: off-grid={len(off_grid)} bad OHLC={len(bad_prices)} "
              f"negative vol/count={len(negative)} vwap outside [low,high]={len(bad_vwap)}")

        if off_grid:                              # show up to 5 examples of each problem found
            print(f"       off-grid e.g. {off_grid[:5]}")
        if bad_prices:
            print(f"       bad OHLC e.g. {bad_prices[:5]}")
        if bad_vwap:
            print(f"       vwap e.g. {bad_vwap[:5]}")


# ---------------------------------------------------------------------------
# CHECK 2: GAPS - are there missing candles between the first and last one?
# ---------------------------------------------------------------------------
def check_gaps(candles):
    print("  Gaps (missing intervals inside the returned range)")

    for timeframe, series in candles.items():
        step = SECONDS_PER_TIMEFRAME[timeframe]   # seconds between candles
        times = sorted(series)                    # all candle times, oldest first
        first = times[0]                          # earliest candle
        last = times[-1]                          # latest candle

        expected = list(range(first, last + step, step))   # every time slot that SHOULD exist

        missing = []                              # slots that should exist but don't
        for ts in expected:
            if ts not in series:
                missing.append(ts)

        coverage = 100 * len(series) / len(expected)   # % of slots that actually have a candle

        print(f"    {timeframe}: {len(series)} candles {to_text(first)} -> {to_text(last)} UTC, "
              f"missing={len(missing)} ({coverage:.1f}% coverage)")

        runs = find_runs(missing, step)           # group missing slots into continuous gaps
        runs.sort(key=lambda run: run[2], reverse=True)   # longest gaps first (run[2] = length)
        for start, end, length in runs[:3]:       # show the 3 longest gaps
            print(f"       gap {to_text(start)} -> {to_text(end)} ({length} x {timeframe})")


# ---------------------------------------------------------------------------
# CHECK 3: AGGREGATION - do small candles add up to the big candle?
# ---------------------------------------------------------------------------
def combine(small_candles):
    """Build the big candle we EXPECT from a list of small candles (oldest first)."""
    volumes = []                                  # every small candle's volume
    counts = []                                   # every small candle's trade count
    highs = []                                    # every small candle's high
    lows = []                                     # every small candle's low
    price_times_volume = []                       # vwap * volume of each candle (for the average)

    for c in small_candles:                       # go through every small candle
        volumes.append(c["volume"])
        counts.append(c["count"])
        highs.append(c["high"])
        lows.append(c["low"])
        if not math.isnan(c["vwap"]):             # skip candles with no vwap
            price_times_volume.append(c["vwap"] * c["volume"])

    total_volume = sum(volumes)                   # sum() adds decimals more precisely than "+=" in a loop
    total_count = sum(counts)
    highest = max(highs)                          # biggest value in the list
    lowest = min(lows)                            # smallest value in the list

    if total_volume:                              # avoid dividing by zero
        vwap = sum(price_times_volume) / total_volume   # weighted average price
    else:
        vwap = float("nan")                       # no volume -> no average price

    return {
        "open": small_candles[0]["open"],         # open  = first small candle's open
        "high": highest,                          # high  = highest of all highs
        "low": lowest,                            # low   = lowest of all lows
        "close": small_candles[-1]["close"],      # close = last small candle's close
        "volume": total_volume,                   # volume = sum of volumes
        "count": total_count,                     # count  = sum of trade counts
        "vwap": vwap,                             # vwap   = volume-weighted average
    }


def check_aggregation(symbol, candles, mismatch_rows):
    print("  Aggregation consistency")

    for small_tf, big_tf in AGGREGATIONS:         # e.g. small_tf="1m", big_tf="5m"
        if small_tf not in candles or big_tf not in candles:   # can't compare if one is missing
            print(f"    {small_tf} -> {big_tf}: skipped (timeframe missing)")
            continue                              # go to the next pair

        small = candles[small_tf]                 # all small candles {time: candle}
        big = candles[big_tf]                     # all big candles   {time: candle}
        small_step = SECONDS_PER_TIMEFRAME[small_tf]
        big_step = SECONDS_PER_TIMEFRAME[big_tf]
        last_small_time = max(small)              # the latest small candle we have

        problems = Counter()                      # counts mismatches per column, e.g. {"volume": 3}
        partial = []                              # big candles where our small data ends part-way

        for big_time in sorted(big):              # go through big candles in time order
            big_candle = big[big_time]

            # The small-candle times that fall inside this big candle.
            # For a 5m candle at 05:00 that is 05:00, 05:01, 05:02, 05:03, 05:04.
            slots = list(range(big_time, big_time + big_step, small_step))

            inside = []                           # the small candles we actually have for those slots
            for slot in slots:
                if slot in small:
                    inside.append(small[slot])

            if not inside:                        # no small candles at all -> nothing to compare
                problems["no children"] += 1
                continue

            # "Boundary" = the big candle's last slot is after our last small candle,
            # i.e. the small data stops in the middle of this big candle.
            is_boundary = big_time + big_step - small_step > last_small_time
            if is_boundary:
                partial.append(to_text(big_time))

            expected = combine(inside)            # what the big candle SHOULD look like

            for column in NUMBER_COLUMNS:         # compare column by column
                actual_value = big_candle[column]
                expected_value = expected[column]
                if not is_different(actual_value, expected_value):
                    continue                      # they match -> nothing to record

                problems[column] += 1             # count one more problem in this column
                diff = actual_value - expected_value
                if actual_value:                  # percentage difference (skip if dividing by 0)
                    diff_pct = 100 * diff / actual_value
                else:
                    diff_pct = ""

                mismatch_rows.append({            # one row for the mismatch CSV
                    "symbol": symbol,
                    "check": f"{small_tf}->{big_tf}",
                    "parent_time_utc": to_text(big_time),
                    "field": column,
                    "parent_value": actual_value,
                    "aggregated_value": expected_value,
                    "diff": diff,
                    "diff_pct": diff_pct,
                    "children_found": len(inside),
                    "children_expected": len(slots),
                    "boundary": is_boundary,
                })

        if problems:                              # build a short summary like "volume=2, count=1"
            parts = [f"{column}={n}" for column, n in problems.items()]
            result = ", ".join(parts)
        else:
            result = "all match"
        print(f"    {small_tf} -> {big_tf} over {len(big)} candles: {result}")

        if partial:
            print(f"       boundary/partial windows (child data ends inside parent): {partial}")


def show_volume_mismatches(symbol, mismatch_rows):
    """Print volume mismatches for this symbol, ignoring the 'boundary' ones (those are expected)."""
    rows = []
    for r in mismatch_rows:
        if r["symbol"] == symbol and r["field"] == "volume" and not r["boundary"]:
            rows.append(r)

    if not rows:                                  # nothing to show
        return

    print("  Volume mismatches (non-boundary)")
    for r in rows:
        print(f"    {r['check']} {r['parent_time_utc']}: parent={r['parent_value']:.1f} "
              f"sum={r['aggregated_value']:.1f} diff={r['diff']:+.1f} ({r['diff_pct']:+.2f}%)")


# ---------------------------------------------------------------------------
# CHECK 4: CALENDAR - how many candles per day of the week?
# ---------------------------------------------------------------------------
def check_calendar(candles):
    print("  Calendar (candles per weekday, UTC)")

    for timeframe, series in candles.items():
        days = Counter()                          # e.g. {"Thu": 500, "Fri": 400}
        for ts in series:
            day_name = datetime.fromtimestamp(ts, timezone.utc).strftime("%a")   # "Mon", "Tue", ...
            days[day_name] += 1

        weekend = days["Sat"] + days["Sun"]       # candles on Saturday + Sunday (Counter gives 0 if missing)
        print(f"    {timeframe}: {dict(days)}  weekend candles={weekend}")


# ---------------------------------------------------------------------------
# CHECK 5: TIME WINDOW - did we get the time range we asked for?
# ---------------------------------------------------------------------------
def check_window(candles, requested_from, requested_to):
    if requested_from is None:                    # only runs if --requested-from was given
        return

    print("  Requested vs returned window")
    asked_start = int(requested_from.timestamp())   # requested start, in seconds
    asked_end = None
    if requested_to is not None:
        asked_end = int(requested_to.timestamp())   # requested end, in seconds

    for timeframe, series in candles.items():
        first = min(series)                       # first candle we got
        last = max(series)                        # last candle we got

        start_offset_hours = (first - asked_start) / 3600   # positive = data starts later than asked
        line = f"    {timeframe}: first candle offset {start_offset_hours:+.2f}h"

        if asked_end is not None:
            end_offset_hours = (last - asked_end) / 3600     # positive = data ends later than asked
            line += f", last candle offset {end_offset_hours:+.2f}h"
        print(line)


# ---------------------------------------------------------------------------
# CHECK 6: FX VOLUME - currencies have no real "traded volume"
# ---------------------------------------------------------------------------
def check_fx_volume(candles):
    """If volume always equals count, the 'volume' is probably just a tick count."""
    for timeframe, series in candles.items():
        same = 0                                  # how many candles have volume == count
        for c in series.values():
            if not is_different(c["volume"], c["count"]):
                same += 1

        if same:
            print(f"  Volume == count for {same}/{len(series)} {timeframe} candles "
                  "(volume likely represents ticks, not traded size)")


# ---------------------------------------------------------------------------
# MAIN: what actually runs when you type "python validate.py"
# ---------------------------------------------------------------------------
def main():
    # 1) Describe the command-line options this script accepts.
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path, default=DEFAULT_FILES)   # zero or more CSV files
    parser.add_argument("--requested-from", type=parse_utc, help="UTC start you intended to request, e.g. 2026-10-01T00:00")
    parser.add_argument("--requested-to", type=parse_utc, help="UTC end you intended to request, e.g. 2026-10-01T12:00")
    args = parser.parse_args()                    # read what the user actually typed

    # 2) Run every check on every file.
    mismatch_rows = []                            # collects mismatches from ALL files
    for path in args.files:
        symbol, candles, duplicates = load(path)  # read the CSV
        print(f"\n===== {symbol} ({path.name}) =====")
        check_window(candles, args.requested_from, args.requested_to)
        check_integrity(candles, duplicates)
        check_gaps(candles)
        check_aggregation(symbol, candles, mismatch_rows)
        show_volume_mismatches(symbol, mismatch_rows)
        check_calendar(candles)
        check_fx_volume(candles)

    # 3) Save all mismatches to a CSV file for the report.
    with MISMATCH_FILE.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=MISMATCH_COLUMNS)
        writer.writeheader()                      # first line: the column names
        writer.writerows(mismatch_rows)           # then one line per mismatch
    print(f"\nWrote {len(mismatch_rows)} mismatch rows to {MISMATCH_FILE.name}")


# Only run main() when this file is run directly (not when imported by another script).
if __name__ == "__main__":
    main()
