# Support Engineer Assessment - Part 4: Incident Simulation (Based on Demo Observations)

## Scenario

INC-2026-1008-01, Thursday 2026-10-08. "Client X" runs a VWAP strategy on GOOG and TSLA 1h candles and backtests EUR/USD on 1m. Before the market opens, its weekly job downloads 2026-09-30 00:00 -> 2026-10-08 13:00 (the range used in Part 1), rebuilds 5m from 1m and 1h from 5m, checks "low <= vwap <= high" and checks range coverage.

The job reported 3 symptoms (S1-S3, referenced in the rest of this report):

- **S1:** GOOG/TSLA 1h candles have more volume and a different VWAP than the sum of their 5m candles. Some VWAP values are outside the candle low-high range, so the validator rejects them.
- **S2:** EUR/USD 1m history starts at 2026-10-01 02:49 instead of 2026-09-30 00:00 (~27h missing), with HTTP 200 and no warning.
- **S3:** Client X re-downloaded everything with 50 parallel workers and a 10 second timeout. Requests took up to 30 seconds, most timed out, retries added load and the job failed. No error and no HTTP 429 from the server.

Client X paused its VWAP strategy at the US open (13:30) and opened a ticket: "Candle data corrupted, REST timing out".

## Incident Analysis

**Summary:** all 3 symptoms were reproduced with the scripts from Parts 1 and 2. The prices are correct, no data was lost, and the service was healthy for normal traffic (1 worker probe: p50 latency ~600 ms).

- Severity: SEV3
- Priority: High. One client, prices not affected, workaround available, but trading is blocked.

### Timeline (2026-10-08, UTC)

- 13:10 - Client job runs and fails at 13:12 (S1, S2)
- 13:15 - Client re-downloads with 50 workers, times out (S3), aborted at 13:25
- 13:30 - Client pauses the VWAP strategy at the US open
- 13:34 - Ticket opened
- 13:40 - L2 acknowledges
- 13:45 - S1 and S2 reproduced with `ApiCaller.py` + `validate.py`
- 13:55 - S3 reproduced: 1 worker ~600 ms, 50 workers ~30 s, all HTTP 200
- 14:20 - Workarounds sent to the client
- 14:30 - Escalation to Data Quality (S1) and Engineering (S2, S3)
- 14:45 - Client backfill OK with 2 workers and a 30 s timeout. Strategy restarted

Detected by the client, not by our monitoring. Time to workaround: 46 min. Trading impact: 1h15m.

### S1 - Volume and VWAP

**Evidence:**

- 1h volume is bigger than the sum of the 5m candles (GOOG 86/130, TSLA 73/130).
- The mismatches happen only in extended hours (overnight, pre-market and after-hours), and OHLC always matches.
- VWAP was out of range on 1h candles for GOOG 9/130 and TSLA 8/130.

**Classification:** Open data question.

**Action and workaround:**

- Escalate to Data Quality.
- Build 1h candles from 5m.
- Log the VWAP range check instead of rejecting the candle.

### S2 - EUR/USD history

**Evidence:**
The API returns at most 8000 candles per request, counted back from the request time. This limit is documented in the dxFeed "Chart Server Data Availability" PDF (2018), but not in the REST API reference, and the response gives no signal when it is applied.

**Classification:** Documented product limit with no signal in the response.

**Action and workaround:**

- Escalate to Engineering.
- Request one day of 1m data per request to stay under the limit.

### S3 - Timeouts

**Evidence:**
~1.67 requests/second at any concurrency, which matches the documented REST limit of 100 requests/minute. p95 was 761 ms with 1 worker and 30,219 ms with 50. 600/600 requests succeeded with HTTP 200 and no HTTP 429.

**Classification:** Client concurrency above the documented rate limit.

**Action and workaround:**

- Use 1 to 2 workers (more do not help) and a 30 second timeout.
- Feature request: HTTP 429 with Retry-After.

### Root cause (hypotheses)

- **S1:** 1h candles include trades that the 1m/5m candles do not (extended-hours or late trades), and VWAP/volume use a wider set of trades than OHLC. EUR/USD is built from quotes, so it is not affected.
- **S2:** confirmed 8000 candle limit (documented only in the 2018 PDF) with no signal in the response.
- **S3:** requests are queued at ~1.67 req/s, the documented 100 requests/minute. 100 requests take ~60 s with either 2 or 50 workers. With a 10 second timeout only ~16 finish; the rest time out while the server logs success.

The limits in S2 and S3 are documented, but in places a client is unlikely to find, and the API gives no signal when they are applied (no truncated flag, no HTTP 429, no complete flag). The S1 behaviour is not documented.

## Client Communication

**Subject:** [INC-2026-1008-01] Candle data and REST timeouts - findings and workarounds

Hello Client X,

Thank you for the detailed report. We reproduced what your job found. Prices (open, high, low, close) are correct on all timeframes and the service is working normally. Here is what you can do now:

1. **GOOG/TSLA 1h volume and VWAP:** the 1h candles include more volume than the 5m candles outside the regular session (overnight, pre-market and after-hours). We are confirming with our data team which trades go into each timeframe. Meanwhile, build 1h from 5m candles for volume-based indicators, and log the VWAP range check instead of rejecting the candle.
2. **EUR/USD history:** the REST endpoint returns up to 8000 candles per request, counted back from the request time (see our "Chart Server Data Availability" document). Please request one day of 1m data per request and check the first candle against your fromTime.
3. **Timeouts:** the REST API allows 100 requests per minute (about 1.67 per second), and requests above that are queued, so parallel requests do not speed up the download. Use 1-2 parallel requests, a timeout of at least 30 s and exponential backoff.

Also expected: the 5m/1h candle that starts exactly at toTime will not match its 1m candles, and the latest candle is still open and can change.

Next update by end of day 2026-10-09, once our data team confirms the volume/VWAP behaviour.

Best regards,
dxFeed Support

## Internal Escalation Note

**Title:** [INC-2026-1008-01] Candle volume/VWAP, silent truncation and no 429

- Priority: High (prices correct; Critical if OHLC mismatched)
- Client: Client X, trading paused from 13:30 to 14:45

**To Data Quality (S1):**

- 1h volume > sum of 5m (GOOG 86/130, TSLA 73/130), only 04:00-13:30 and 20:00-24:00 UTC. OHLC and 1m -> 5m match.
- VWAP outside low/high on GOOG and TSLA, 0 on EUR/USD.
- Escalated even though it is extended-hours only (Part 3 R1): first client report, no baseline, not documented.
- Questions: which trades go into OHLC, volume and VWAP per timeframe? Is it intended? Is it the same in production?

**To Engineering (S2, S3):**

- EUR/USD 1m cut at 8000 candles with HTTP 200. The limit is only documented in a 2018 PDF. Request: add it to the REST API reference, and add a truncated flag or pagination.
- Throughput ~1.67 req/s at 1-50 workers, matching the documented 100 requests/minute; p95 up to 30 s, 0 HTTP 429. Request: return 429 with Retry-After and document what happens above the limit.
- Question: is this queuing the documented 100 requests/minute limit, and is the limit per client, per IP or shared?

**Reproduce:** `events.json?events=Candle&symbols=GOOG{=1h}&fromTime=20260930-000000Z&toTime=20261008-130000Z` (also 5m, 1m, TSLA, and `EUR/USD{=1m,price=bid}`), then run the validation script (`validate.py`).

**Attached:** `raw_json/`, `aggregation_mismatches.csv`, `validate_OUTPUT.txt`, `traffic_results.csv`.

## Post-Mortem

Blameless format.

- **Summary and impact:** VWAP strategy paused for 1h15m. Other clients using 1h VWAP or requesting more than 8000 candles may be affected. No price errors, no data loss, no outage.
- **Timeline:** see "Timeline" above.
- **Root cause:** S1: <Data Quality answer>. S2: 8000 candle limit, documented only in a 2018 PDF, with no signal in the response. S3: requests queued at the documented 100 requests/minute with no 429, plus client retries without backoff.
- **Detection:** found by the client. Every request was HTTP 200, so monitoring stayed green.
- **What went well:** reproduced in 15 min with existing scripts; real issues separated from expected behaviour; workaround in 46 min.
- **What went poorly:** no monitoring caught it; the relevant documentation was scattered and hard to find; the API gave no signal to slow down.

### Action items

- Data Quality: confirm and document trade inclusion per timeframe (Document, High)
- Monitoring: hourly 1m -> 5m -> 1h reconciliation and VWAP sanity check (Detect, High)
- Monitoring: range coverage check, alert when count = 8000, p95 latency probe (Detect, High)
- Engineering: truncated flag/pagination, HTTP 429 with Retry-After, "complete" flag (Prevent, High/Medium)
- Support: one knowledge base article + onboarding that brings together the limits (8000 candles, 100 requests/minute), boundary/partial candles, FX `{price=bid}`, recommended concurrency and timeouts (Document, High)
- Support: notify other clients using 1h VWAP after Data Quality confirms (Communicate, Medium)

### Lesson

HTTP 200 does not mean the data is complete or correct. Monitoring must check the content (Part 3 R5).
