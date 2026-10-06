import csv
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://tools.dxfeed.com/webservice/rest/events.json"
#BASE_URL = "https://demo.dxfeed.com/webservice/rest/events.json"

INSTRUMENTS = {
    "US stock": "GOOG",
    "FX pair": "EUR/USD",
    "High volatility": "TSLA",
}

TIMEFRAMES = ("1m", "5m", "1h")
OUTPUT_FILES = {
    "US stock": Path(__file__).with_name("us_stock_candles.csv"),
    "FX pair": Path(__file__).with_name("fx_pair_candles.csv"),
    "High volatility": Path(__file__).with_name("high_volatility_candles.csv"),
}
CANDLE_ATTRIBUTES = {"EUR/USD": "price=bid"}


def extract_candles(data):
    if isinstance(data, dict):
        if all(field in data for field in ("open", "high", "low", "close")):
            yield data
        else:
            for value in data.values():
                yield from extract_candles(value)
    elif isinstance(data, list):
        for value in data:
            yield from extract_candles(value)

def fetch_candles(symbol, timeframe, start_date, end_date):
    candle_symbol = f"{symbol}{{={timeframe}"
    attributes = CANDLE_ATTRIBUTES.get(symbol)
    if attributes:
        candle_symbol += f",{attributes}"
    candle_symbol += "}"

    try:
        response = requests.get(
            BASE_URL,
            params={
                "events": "Candle",
                "symbols": candle_symbol,
                "fromTime": start_date,  	#YYYYMMDD
                "toTime": end_date,      	#YYYYMMDD
                "timeout": 60,
                "indent": "",
            },
            timeout=70,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"Request failed for {symbol} {timeframe}: {error}")
        return []

    payload = response.json()

    raw_candles = payload.get("Candle")
    if raw_candles is None:
        raw_candles = {
            key: value for key, value in payload.items() if key != "status"
        }
    candles = list(extract_candles(raw_candles))

    if not candles:
        print(f"No candles returned for {symbol} {timeframe}")
    return candles

start_date = "20261001-000000"
end_date =   "20261001-120000"

fieldnames = [
    "symbol", "event_symbol", "timeframe", "time_utc", "open", "high",
    "low", "close", "volume", "count", "vwap",
]
for label, symbol in INSTRUMENTS.items():
    output_file = OUTPUT_FILES[label]
    total_candles = 0

    with output_file.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for timeframe in TIMEFRAMES:
            candles = fetch_candles(symbol, timeframe, start_date, end_date)
            print(f"Fetched {len(candles)} candles for {label}, ({symbol}) {timeframe}")
            for candle in candles:
                timestamp = candle.get("time")
                time_utc = ""
                if timestamp is not None:
                    time_utc = datetime.fromtimestamp(
                        float(timestamp) / 1000, timezone.utc
                    ).isoformat()

                writer.writerow({
                    "symbol": symbol,
                    "event_symbol": candle.get("eventSymbol"),
                    "timeframe": timeframe,
                    "time_utc": time_utc,
                    "open": candle.get("open"),
                    "high": candle.get("high"),
                    "low": candle.get("low"),
                    "close": candle.get("close"),
                    "volume": candle.get("volume"),
                    "count": candle.get("count"),
                    "vwap": candle.get("vwap"),
                })

            total_candles += len(candles)
            print(f"{label} ({symbol}) {timeframe}: {len(candles)} candles")

    print(f"Wrote {total_candles} candle rows to {output_file}")