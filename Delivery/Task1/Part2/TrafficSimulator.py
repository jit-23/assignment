"""Send a small, controlled burst of candle requests to the dxFeed demo API."""

import csv
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


# Keep the test bounded. Increase these only when the demo service permits it.
BASE_URL = "https://demo.dxfeed.com/webservice/rest/events.json"
SYMBOLS = 	{"GOOG": "",
			 "TSLA": "",
			 "EUR/USD": ",price=bid"
			}
TIMEFRAME = "1m"
CONCURRENCY_LEVELS = (1, 2, 5, 10, 20,50)
REQUESTS_PER_LEVEL = 100
TIMEOUT_SECONDS = 60
DEMO_DELAY_MINUTES = 15
OUTPUT_FILE = Path(__file__).with_name("traffic_results.csv")

def fetch_one(symbol, from_time, to_time, concurrency):
	"""Make one request and return its measurements, including any error."""
	params = {
		"events": "Candle",
		"symbols": f"{symbol}{{={TIMEFRAME}{SYMBOLS[symbol]}}}",
		"fromTime": from_time,
		"toTime": to_time,
		"timeout": TIMEOUT_SECONDS,
		"indent": "",
	}
	result = {
		"concurrency": concurrency,
		"symbol": symbol,
		"latency_ms": "",
		"status": "ERROR",
		"candle_count": 0,
		"latest_candle_utc": "",
		"response_bytes": 0,
		"sha256": "",
		"cache_control": "",
		"age_header": "",
		"error": "",
	}
	started = time.perf_counter()
	try:
		response = requests.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
		result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
		result["status"] = response.status_code
		result["response_bytes"] = len(response.content)
		result["cache_control"] = response.headers.get("Cache-Control", "")
		result["age_header"] = response.headers.get("Age", "")
		response.raise_for_status()

		candle_data = response.json().get("Candle", {})
		candles = [candle for series in candle_data.values() for candle in series]
		result["candle_count"] = len(candles)
		if candles:
			latest_ms = max(candle["time"] for candle in candles)
			latest = datetime.fromtimestamp(latest_ms / 1000, timezone.utc)
			result["latest_candle_utc"] = latest.isoformat()

		canonical_data = json.dumps(candle_data, sort_keys=True, separators=(",", ":"))
		result["sha256"] = hashlib.sha256(canonical_data.encode("utf-8")).hexdigest()
	except (requests.RequestException, ValueError, KeyError, TypeError) as error:
		result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
		result["error"] = f"{type(error).__name__}: {error}"

	return result


def percentile(values, percent):
	"""Return a simple nearest-rank percentile, or None for no measurements."""
	if not values:
		return None
	ordered = sorted(values)
	index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
	return ordered[index]


def print_summary(results):
	print("\nResults by concurrency")
	for level in CONCURRENCY_LEVELS:
		group = [row for row in results if row["concurrency"] == level]
		successful = [row for row in group if isinstance(row["status"], int) and row["status"] < 400]
		latencies = [row["latency_ms"] for row in successful]
		success_rate = 100 * len(successful) / len(group) if group else 0
		p50 = percentile(latencies, 50)
		p95 = percentile(latencies, 95)
		print(
			f"  workers={level:>2}: success={success_rate:5.1f}% "
			f"p50={p50 if p50 is not None else '-'} ms "
			f"p95={p95 if p95 is not None else '-'} ms"
		)

		for symbol in SYMBOLS:
			symbol_rows = [
				row for row in successful
				if row["symbol"] == symbol and row["sha256"]
			]
			hashes = {row["sha256"] for row in symbol_rows}
			if symbol_rows:
				print(f"    {symbol}: {len(hashes)} distinct response(s) across {len(symbol_rows)} successes")

	failures = [row for row in results if row["error"] or (isinstance(row["status"], int) and row["status"] >= 400)]
	if failures:
		print("\nErrors/statuses to inspect")
		for row in failures:
			print(f"  {row['symbol']} at workers={row['concurrency']}: HTTP {row['status']} {row['error']}")


def main():
	from_time = "20261009-120000Z"
	to_time = "20261009-130000Z"
	print(f"Testing {from_time} to {to_time} UTC, {REQUESTS_PER_LEVEL} requests per level")

	results = []
	symbols = list(SYMBOLS)
	for concurrency in CONCURRENCY_LEVELS:
		jobs = [
			(symbols[index % len(symbols)], from_time, to_time, concurrency)
			for index in range(REQUESTS_PER_LEVEL)
		]
		print(f"Running {len(jobs)} requests with at most {concurrency} in parallel...")
		with ThreadPoolExecutor(max_workers=concurrency) as pool:
			futures = [pool.submit(fetch_one, *job) for job in jobs]
			results.extend(future.result() for future in as_completed(futures))

	columns = [
		"concurrency", "symbol", "latency_ms", "status", "candle_count",
		"latest_candle_utc", "response_bytes", "sha256", "cache_control",
		"age_header", "error",
	]
	with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output:
		writer = csv.DictWriter(output, fieldnames=columns)
		writer.writeheader()
		writer.writerows(results)

	print_summary(results)
	print("result stored on traffic_results.csv")

if __name__ == "__main__":
	main()
