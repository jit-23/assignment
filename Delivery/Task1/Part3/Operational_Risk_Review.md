# Support Engineer Assessment - Part 3: Operational Risk Review

## 1. Scope

This review looks at the dxFeed demo services used in Parts 1 and 2:

- REST candle endpoint (`demo.dxfeed.com/webservice/rest/events.json`)
- dxLink WebSocket (`wss://demo.dxfeed.com/market-data/dxlink-ws`)

The instruments used are GOOG, TSLA and EUR/USD on the 1m, 5m and 1h timeframes. Every risk below is based on the behaviour we observed in Part 1, which analysed data consistency, and Part 2, which analysed behaviour under load.

The service used in Parts 1 and 2 is a demo version. It is delayed (dxFeed labels it "at least 15 min"; we observed about 15 minutes) and is not meant for production use, so production may behave differently.

This report shows what an L2 support team should watch for, and how to detect, mitigate and escalate each risk.

## 2. Risks

## R1 - Data Integrity Risk

### 1.1 - Volume and VWAP difference between timeframes

**Risk:**
The 1h candles of GOOG and TSLA have a different VWAP and more volume than the sum of their 5m children. The OHLC prices match, so the problem is not visible on a candle chart.

**Evidence (Part 1):**

- GOOG: volume and count mismatch on 86/130 1h candles, and VWAP on 66/130.
- TSLA: volume and count mismatch on 73/130 1h candles, and VWAP on 60/130.
- EUR/USD is not affected, because FX candles are built from quotes, not trades.

**Impact:**

- VWAP gives different values depending on the timeframe, so a VWAP strategy on 5m behaves differently on 1h.
- A client that reconciles timeframes will open tickets reporting "wrong data".

**Detection strategy:**

- Reconciliation jobs that rebuild 5m from 1m and 1h from 5m candles for a list of reference symbols, and compare OHLC, volume and count.
- Metric: % of mismatching candles per symbol, timeframe and session.
- Run once per hour during market hours and once after the market closes.

**Mitigation strategy:**

- Short term: tell clients to build higher timeframes from 1m when they need consistent volume, or to use only one timeframe for volume-based indicators.
- Long term: confirm with the data team which trades are included in each timeframe (extended hours, late reports).

**Escalation criteria:**

- Escalate to the Data Quality team on any OHLC mismatch (prices should never mismatch), when volume mismatches appear inside regular hours, or when the mismatch rate changes by more than 10% from the baseline.
- Priority: High; Critical if prices are compromised.
- Information to attach: symbol, timeframe, candle time, request URL, raw JSON and rows from `aggregation_mismatches.csv`.

### 1.2 - VWAP outside the candle price range

**Risk:**
Some GOOG and TSLA candles have a VWAP lower than the low or higher than the high. This should not be possible if VWAP and OHLC are computed from the same trades.

**Evidence (Part 1):**

- GOOG: 1376/6113 1m, 126/1509 5m, 9/130 1h candles.
- TSLA: 1274/6821 1m, 71/1541 5m, 8/130 1h candles.
- Example: GOOG 1h [2026-09-30 12:00] low=340.57, high=342.70, vwap=338.68

**Impact:**

- Execution benchmarks and VWAP strategies use a price the instrument never traded at during that candle.
- Clients that validate "low <= vwap <= high" will reject real data.

**Detection strategy:**

- Per-candle sanity checks: high >= max(open, close), low <= min(open, close), volume >= 0, low <= vwap <= high.
- Metric: count of VWAP-out-of-range candles per symbol per day.

**Mitigation strategy:**

- Short term: document the behaviour so clients do not hard-reject the candle; recommend logging instead of failing.
- Long term: investigate with the data team which trades go into OHLC, VWAP and volume for each timeframe.

**Escalation criteria:**

- Escalate to the Data Quality team when it appears on an instrument where it was not seen before, for example EUR/USD, which currently has 0 VWAP values out of range.
- Priority: Low if it is a known behaviour; High if it happens on a new instrument.
- Information to attach: symbol, candle time, OHLC and VWAP values, raw JSON.

### 1.3 - Boundary candle at toTime

**Risk:**
toTime includes every candle whose start time is <= toTime. The last 5m and 1h candles therefore include trades after the requested time, and a client that downloads consecutive ranges gets the boundary candle twice.

**Evidence (Part 1):**
`toTime=13:00` returned the 5m and 1h candles starting at 13:00, but only the 13:00 1m candle, without the other 4 children of the 5m candle. This causes the single aggregation mismatch on GOOG, TSLA and EUR/USD at [2026-10-08 13:00].

**Impact:**
Double-counted volume when ranges overlap, and false aggregation mismatches on every request edge.

**Detection strategy:**

- When downloading consecutive ranges, check for duplicate candle timestamps.
- Exclude the last candle of each request from reconciliation jobs, so it does not create false alerts.

**Mitigation strategy:**

- Short term: tell clients to use toTime = (next range start - 1 ms), or to de-duplicate by timestamp.
- Long term: document clearly that toTime is inclusive on the candle start time. Today the REST reference only says "the time to which to return events".

**Escalation criteria:**

- Once the behaviour is documented, no escalation is needed. Escalate only if it changes (e.g. toTime becomes exclusive), because that would affect existing clients.
- Priority: Low.
- Information to attach: request URL, list of returned timestamps.

### 1.4 - FX candles while the market is closed

**Risk:**
EUR/USD has single-tick candles during the weekend, and the Sunday data starts before the usual FX reopening (about 21:00 UTC).

**Evidence (Part 1):**
3 isolated candles on Saturday [2026-10-03 08:46], Saturday [2026-10-03 10:51] and Sunday [2026-10-04 01:54] UTC.

**Impact:**
Clients may think there was trading. Gap-detection tools split the weekend into several gaps for no reason.

**Detection strategy:**

- Calendar check: flag any FX candle between Friday 21:05 UTC and the Sunday reopening.

**Mitigation strategy:**

- Short term: tell clients to filter by trading calendar.
- Long term: ask the data team whether test or off-hours quotes should be excluded from candle building.

**Escalation criteria:**

- Escalate to the Data Quality team if the number of off-hours candles grows (e.g. more than 10 per weekend) or if they show prices far from the Friday close.
- Priority: Low (known behaviour).
- Information to attach: candle list with timestamps and prices.

## R2 - Latency Risk

**Risk:**
REST latency grows in direct proportion to the number of parallel requests. The service does not reject extra load (no HTTP 429); it queues it, so every request from that client becomes slower.

**Evidence (Part 2):**
100 requests per level, 1-hour window of 1m candles.

Throughput = number of workers / latency in seconds

| Concurrency | p50 | p95 | Throughput (p50) | Throughput (p95) |
|---|---|---|---|---|
| 1 | 598 ms | 761 ms | 1.67 req/s | 1.3 req/s |
| 2 | 1,198 ms | 1,435 ms | 1.67 req/s | 1.4 req/s |
| 5 | 3,000 ms | 3,177 ms | 1.67 req/s | 1.6 req/s |
| 10 | 6,000 ms | 6,243 ms | 1.67 req/s | 1.6 req/s |
| 20 | 11,997 ms | 12,175 ms | 1.67 req/s | 1.6 req/s |
| 50 | 29,875 ms | 30,219 ms | 1.67 req/s | 1.7 req/s |

Throughput stays at about 1.67 requests/second at every level, so adding parallel requests does not make anything faster. 1.67 req/s is exactly the rate limit documented on the dxFeed REST API page (100 `events.json` requests per minute). The documentation says dxFeed "may not receive requests" above this limit, but in practice the requests were queued, with no errors and no HTTP 429 responses.

**Impact:**

- Under load, every request takes many seconds.
- Dashboards and charts load very slowly, and clients with short timeouts fail. Because there is no HTTP 429, clients get no signal to slow down.

**Detection strategy:**

- A synthetic probe that sends the same small request every minute and checks latency.
- Metrics: p50 and p95 latency per endpoint, throughput (requests/second), number of in-flight requests.
- Alert when probe latency is above its baseline, not only when it fails.

**Mitigation strategy:**

- Short term: limit client concurrency to 1 or 2 parallel requests, since more workers do not increase throughput, and stay under 100 requests/minute. Set timeouts above the expected p95. Use the dxLink WebSocket for live data instead of polling REST.
- Long term: return HTTP 429 with Retry-After when the limit is reached, so a client can back off instead of waiting, and document what happens above the limit.

**Escalation criteria:**

- Escalate to Engineering if:
  - probe p95 > 1.5 seconds (about 2x baseline) for 10 consecutive minutes during market hours;
  - throughput drops below 1 request/second;
  - any 5xx error or timeout appears on the probe.
- Priority: High; Critical if several clients are affected.
- Information to attach: latency graph, time window, concurrency, example request URL, client IP/region.

## R3 - Staleness Risk

### 3.1 - Partial candles look the same as closed candles

**Risk:**
Both REST and the WebSocket return the unfinished candle. Nothing in the event marks it as incomplete, so a client cannot tell an open candle from a closed one.

**Evidence (Part 1):**
1384/1387 GOOG candles were identical between stream and REST; the 3 that differ are the last candle of each timeframe. For example, GOOG 1m [2026-10-08 12:30] has volume 3,743 in the stream and 8,587 in REST.

**Impact:**
A database stores a candle that will still change. A strategy that acts on closed candles is triggered by an unfinished one. A client comparing stream with REST reports a data error when the difference is only timing.

**Detection strategy:**

- Mark candles as complete only when candle time + period + demo delay (about 15 min) <= request time.
- In reconciliation jobs, compare only complete candles, and count how many open candles are returned.

**Mitigation strategy:**

- Short term: document the timing rule for clients (today it only appears as a code comment in the dxFeed KB sample, "skipping unfinished candle"). Recommend dropping or overwriting the last candle.
- Long term: ask the Engineering team to add a "complete" flag.

**Escalation criteria:**

- Escalate to the Data Quality team if a candle that is complete still changes later (that would not be a partial candle, but a real data correction).
- Priority: High.
- Information to attach: REST and stream versions of the candle, time of the requests, raw JSON and stream log.

### 3.2 - Feed stops updating with no error

**Risk:**
The stream or REST endpoint can keep answering normally while the newest candle stops moving forward (data freeze). We did not test a freeze, but nothing in the response would show it: there are no cache headers and no "last update" field.

**Evidence (Part 2):**
The Cache-Control and Age headers were empty on all 600 requests. The candle hash for each symbol was identical on every request, as expected, because the time window was fixed. We cannot tell the difference between fresh and cached data.

**Impact:**

- A client sees old prices as new.
- A chart looks healthy but does not move.

**Detection strategy:**

- Freshness probe: for a liquid symbol, like EUR/USD during FX hours or GOOG during the regular session, compare the latest candle timestamp with the current time.
- For this demo service, expect a lag of about 15 min delay + 1 candle period, and alert above that value plus a tolerance. The 15 minutes is based on one observation in Part 1, so re-baseline it if the delay varies.
- For the stream, alert if there is no update for a liquid symbol for more than 2 minutes during market hours.

**Mitigation strategy:**

- Short term: a client-side heartbeat check (recurring ping) and automatic reconnect for the WebSocket.
- Long term: ask Engineering to expose cache headers or a last-update timestamp, so staleness can be measured.

**Escalation criteria:**

- Escalate to Engineering or Market Data Operations when the latest candle of a liquid symbol is older than 20 minutes (15 min delay + 5 min tolerance) during market hours.
- Priority: Critical. All clients see old data.
- Information to attach: symbols, latest candle timestamp vs. current time, endpoint, request time.

## R4 - Silent Failure Risk

### 4.1 - History truncated at 8000 candles with HTTP 200

**Risk:**
The API returns at most 8000 candles per request, counted back from the moment of the request. Older data is cut without any error or warning. The limit is documented, but only in a 2018 dxFeed PDF ("Chart Server Data Availability"), not in the REST API reference, and the response does not say when it was applied.

**Evidence (Part 1):**
EUR/USD 1m started at 2026-10-01 02:49 instead of 2026-09-30 00:00. A repeated request at 17:30 UTC without toTime returned exactly 8000 candles. With toTime=13:00 it returned 7744 candles starting at 04:14, instead of 7829 starting at 02:49 one hour earlier. GOOG and TSLA were not affected because they have fewer candles in the same period.

**Impact:**
Backtests and reports silently miss the oldest data. Results are not reproducible, because the same request returns different data at different times.

**Detection strategy:**

- Range coverage check: compare the first returned candle time with the requested fromTime. Alert if the gap is bigger than a normal market-closed gap.
- Alert when the candle count is exactly 8000.

**Mitigation strategy:**

- Short term: tell clients to split long ranges into smaller requests (e.g. one day of 1m per request), to check coverage after downloading, and point them to the documented limit.
- Long term: ask Engineering to add the limit to the REST API reference and to return a "truncated" flag or pagination when the limit is reached.

**Escalation criteria:**

- Escalate to Engineering when the limit changes, or when a client reports missing history that is inside the 8000 window.
- Priority: High.
- Information to attach: request URL, requested vs. returned range, candle count, request time.

### 4.2 - Wrong or missing attributes return empty instead of an error

**Risk:**
A request for EUR/USD candles without `{price=bid}` returns no candles and no error. A client cannot tell "no data" from "wrong request".

**Evidence (Parts 1 and 2):**
EUR/USD only returned candles with `{price=bid}`. Without it, the response was empty.

**Impact:**
New integrations think the symbol has no data. Monitoring that only checks the HTTP status will not show any error.

**Detection strategy:**

- Alert when a symbol that is known to be active returns 0 candles during its trading hours.
- Keep a documented list of symbols and their required attributes.

**Mitigation strategy:**

- Short term: add the FX attribute requirement to the knowledge base and the client onboarding docs.
- Long term: ask Engineering to return an error or warning when a symbol has no data for the requested price type.

**Escalation criteria:**

- Escalate to Engineering if a correct request, with the right attributes, returns 0 candles for an active symbol during trading hours.
- Priority: High; Critical if it affects several symbols.
- Information to attach: request URL with and without the attribute, response body.

### 4.3 - Slow responses on the client side

**Risk:**
Under load, the server returns HTTP 200, but only after about 30 seconds. A client with a shorter timeout gives up and sees a failure, while the server logs a success.

**Evidence (Part 2):**
100% success at every level, but p95 = 12.17 s at 20 workers and 30.22 s at 50 workers.

**Impact:**
A client reports outages that do not exist in the server metrics. It then retries, adds more load and makes the latency worse.

**Detection strategy:**

- Compare server-side latency with common client timeouts (e.g. 5 or 10 seconds).
- Track the % of requests slower than 5 and 10 seconds.

**Mitigation strategy:**

- Short term: recommend timeouts above p95 and exponential backoff on retries, and staying under the documented 100 requests/minute.
- Long term: return HTTP 429 with Retry-After when the limit is reached, so a client can back off instead of waiting.

**Escalation criteria:**

- Escalate to Engineering when more than 5% of requests take longer than 5 seconds for 10 minutes straight.
- Priority: High.
- Information to attach: latency histogram, client timeout value, request IDs.

## R5 - Monitoring Blind Spots

**Risk:**
HTTP status, success-rate and uptime monitoring showed 100% healthy in Parts 1 and 2, while the data had truncated history, partial candles, volume mismatches and 50x latency growth.

**Evidence:**

- Part 1: all requests returned HTTP 200, despite the data integrity, silent failure and staleness issues.
- Part 2: 600 successful requests with no HTTP 429, while latency grew from 0.6 seconds to 30 seconds.
- There are no Cache-Control or Age headers.

**Impact:**

- Clients find problems before monitoring does.
- Support cannot quickly confirm or reject tickets.

**What should be monitored (beyond the usual checks):**

- Beyond HTTP 200: the candle count and range coverage.
- Beyond 100% success: p50/p95 latency, % of requests above 5 s, throughput in req/s.
- Beyond endpoint up / WebSocket connected: latest candle age vs. wall clock, time since the last event per symbol.
- Beyond valid JSON: per-candle sanity checks and timeframe reconciliation.

**Mitigation strategy:**

- Short term: run the scripts that validate and compare candles and measure latency and throughput as scheduled synthetic checks.
- Long term: ask Engineering for cache headers, a last-update timestamp, a completeness flag, and HTTP 429 with Retry-After.

**Escalation criteria:**

- Escalate to Monitoring or Engineering when a ticket confirms a problem that no alert caught.
- Information to attach: ticket reference, what happened, which metric would have caught it.

## Conclusion

The demo services were always available, but availability does not mean the data is correct or fresh. The most important risks are the silent ones: history truncated with HTTP 200, partial candles that go undetected, and latency that grows with the number of workers without any rate-limit signal. Some of these limits are documented (8000 candles, 100 requests/minute), but the responses never show when they are applied. Monitoring based only on HTTP status misses all of these cases, so content checks are needed: range coverage, candle count, freshness, per-candle sanity checks and timeframe reconciliation.
