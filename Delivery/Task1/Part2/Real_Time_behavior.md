# Part 2 - Real-Time Behavior Under Load

## Methodology

I tested the dxFeed demo REST endpoint using 1m candles for GOOG, TSLA and EUR/USD. The EUR/USD requests use `{price=bid}`; without it, the request returns no candles at all. Every request uses the same fixed 1-hour UTC window [2026-10-09 12:00 to 13:00].

I sent 100 requests at each concurrency level, using a thread pool capped at 1, 2, 5, 10, 20 and 50 worker threads. This was a total of 600 requests (100 per level). Each level included 34 GOOG, 33 TSLA and 33 EUR/USD requests.

For every request I recorded the latency, HTTP status, candle count, latest candle timestamp, response size, a hash of the candle data and the cache-related headers.

The `cache_control` and `age_header` columns are empty in the CSV file, so the endpoint did not return cache headers in this test. The `error` column is empty because all requests completed without an error.

## Results: success rate and speed

| Concurrency | Success | p50 latency | p95 latency |
|---|---|---|---|
| 1 | 100% | 598.30 ms | 761.22 ms |
| 2 | 100% | 1,198.42 ms | 1,435.54 ms |
| 5 | 100% | 3,000.09 ms | 3,177.06 ms |
| 10 | 100% | 6,000.83 ms | 6,243.95 ms |
| 20 | 100% | 11,996.50 ms | 12,174.71 ms |
| 50 | 100% | 29,874.59 ms | 30,219.23 ms |

## Observed behavior

All 600 requests were successful at every concurrency level. No request failures and no HTTP 429 (Too Many Requests) responses were recorded.

Latency was the first thing to degrade:

- The median latency (p50) increased from 598.30 ms with 1 worker to 29,874.59 ms (about 29.9 seconds) with 50 workers.
- The 95th percentile latency (p95) increased from 761.22 ms with 1 worker to 30,219.23 ms (about 30.2 seconds) with 50 workers.
- Latency rose in proportion to the number of workers, so throughput stayed at about 1.67 requests/second at every level.

1.67 requests/second is exactly the REST rate limit documented by dxFeed (100 `events.json` requests per minute). This points to the requests being queued at that limit instead of being rejected. The documentation does not say what happens above the limit, and this test cannot show where the queue is.

For each symbol, all responses had the same candle-data hash at every level. This means the candle payloads were identical across repeated requests for that symbol. It does not prove that the data is correct, or that the server used a cache to serve them.

## Conclusion

Availability stayed stable during the whole test, but latency increased with concurrency, consistent with the documented 100 requests/minute limit. There were no rate-limit responses and no request errors, so a client gets no signal that it is being throttled. The responses had no `Cache-Control` or `Age` values, so caching behaviour cannot be evaluated.

This test used the dxFeed demo REST endpoint and one fixed 1-hour time window. The results do not establish the capacity or reliability of the production service.
