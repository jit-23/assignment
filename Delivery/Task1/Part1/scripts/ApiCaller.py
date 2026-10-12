import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

#BASE_URL = "https://tools.dxfeed.com/webservice/rest/events.json"
BASE_URL = "https://demo.dxfeed.com/webservice/rest/events.json"
HERE = Path(__file__).parent

# instrument -> output file
FILES = {"GOOG": "us_stock_candles.csv", "EUR/USD": "fx_pair_candles.csv", "TSLA": "high_volatility_candles.csv"}
TIMEFRAMES = {"1m": 60, "5m": 300, "1h": 3600}   # timeframe -> length in seconds
EXTRA = {"EUR/USD": ",price=bid"}                # FX has no trades, build candles from bid
DELAY = 15 * 60                                  # demo data is ~15 min behind
FIELDS = ["open", "high", "low", "close", "volume", "count", "vwap"]

start_date = "20260930-000000Z"
end_date =   "20261008-130000Z"	# covers RealTimeCandle data (2026-10-07 09:43 -> 2026-10-08 12:59 UTC)


def fetch_candles(symbol, timeframe):
    """Return (candles, fetch time in seconds); the raw JSON is saved to raw_json/."""
    try:
        response = requests.get(BASE_URL, timeout=70, params={
            "events": "Candle", "symbols": f"{symbol}{{={timeframe}{EXTRA.get(symbol, '')}}}",
            "fromTime": start_date, "toTime": end_date, "timeout": 60, "indent": ""})   # YYYYMMDD-HHMMSSZ
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Request failed for {symbol} {timeframe}: {error}")
        return [], time.time()

    (HERE / "raw_json").mkdir(exist_ok=True)
    (HERE / "raw_json" / f"{symbol.replace('/', '-')}_{timeframe}.json").write_text(response.text, encoding="utf-8")
    candles = [c for series in response.json().get("Candle", {}).values() for c in series]
    return candles, time.time()


for symbol, file in FILES.items():
    with open(HERE / file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol", "event_symbol", "timeframe", "time_utc", *FIELDS, "is_complete"])
        for timeframe, seconds in TIMEFRAMES.items():
            candles, fetched_at = fetch_candles(symbol, timeframe)
            for c in candles:
                start = c["time"] / 1000
                # complete = the candle's period (plus the demo delay) had ended when we fetched it
                complete = start + seconds + DELAY <= fetched_at
                writer.writerow([symbol, c["eventSymbol"], timeframe,
                                 datetime.fromtimestamp(start, timezone.utc).isoformat(),
                                 *[c.get(field) for field in FIELDS], "yes" if complete else "no"])
            incomplete = sum(c["time"] / 1000 + seconds + DELAY > fetched_at for c in candles)
            print(f"{symbol} {timeframe}: {len(candles)} candles, {incomplete} incomplete")
    print(f"Wrote {file}")
