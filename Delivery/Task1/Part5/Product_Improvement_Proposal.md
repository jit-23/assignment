# Support Engineer Assessment - Part 5: Product Improvement Proposal

These proposals are based on our use of the dxFeed demo services in Parts 1 to 4 (REST candle endpoint and dxLink WebSocket; GOOG, TSLA and EUR/USD on 1m, 5m and 1h). Every problem below was observed on the demo version, which is delayed (labelled "at least 15 min" by dxFeed; we observed about 15 minutes).

## 1. Technical Improvement: make the REST responses report their own limits

**Problem:** the REST endpoint returns HTTP 200 with no signal in three situations where the client needs one.

- **History truncated:** at most 8000 candles are returned per request, counted back from the request time. This limit is documented (2018 "Chart Server Data Availability" PDF), but the response gives no signal that it was applied. EUR/USD 1m started at [2026-10-01 02:49] instead of [2026-09-30 00:00].
- **Overload:** requests are processed at ~1.67 req/s, matching the documented limit of 100 requests/minute. But the limit is applied by queuing, not rejecting. With 50 workers, p95 latency grew from 761 ms to 30,219 ms, with 600/600 HTTP 200 and 0 HTTP 429. Clients with short timeouts fail and retry, which adds more load.
- **Partial candles:** the last candle is still open but has eventFlags = 0, so it looks the same as a closed one.

**Proposed solution:**

- **Truncation:** when the limit is reached, add a `"truncated": true` field and return a cursor for the next page (pagination).
- **Rate limit:** return HTTP 429 with a Retry-After header when a client exceeds its limit, instead of queuing the request for up to 30 seconds. Add X-RateLimit-Limit / X-RateLimit-Remaining headers.
- **Candle status:** add a "complete" flag to each candle, on REST and on dxLink.

HTTP 429 should be announced in advance, because it changes behaviour for clients that today wait instead of failing.

**Estimated complexity:** Medium

- Truncated flag and rate-limit headers: Low. The server already knows the limit and the queue.
- HTTP 429: Low-Medium. It needs a per-client limit at the gateway and a communication plan for existing clients.
- Pagination and the complete flag: Medium. Pagination changes the API contract; the complete flag needs the candle service to expose the period end.

**Business impact:**

- Removes the silent failures behind the Part 4 incident: the client's backtest would have run on a shorter history without anyone noticing, and its re-download failed with no signal to slow down.
- Fewer support tickets, because the response itself explains what happened.
- More trust in the data for trading clients, who need to know if a candle is final before acting on it.
- Protects the service: clients back off on 429 instead of adding more parallel requests.

## 2. Monitoring Improvement: content-based synthetic monitoring

**Problem:** HTTP status and uptime monitoring showed 100% healthy in Parts 1 and 2. However, the data had truncated history, partial candles, volume and VWAP mismatches, and 50x latency growth. There are no Cache-Control or Age headers, so a data freeze would look healthy too.

**Proposed solution:** turn the scripts that validate, compare and measure the problems above into scheduled checks on a set of reference symbols, with metrics and alerts.

**Estimated complexity:** Low-Medium

- Low: the check logic already exists in the scripts. The work is scheduling, storing the metrics and alerting.
- Medium: a trading calendar per instrument (US stock sessions, FX weekend), so that closed markets do not raise alerts, and tuning the baselines for a few weeks to avoid false alerts.

**Business impact:**

- We detect data and latency problems before clients do.
- Shorter time to resolve tickets: support checks the dashboard instead of reproducing the problem by hand.

## 3. Documentation Improvement: one candle data behaviour guide

**Problem:** the candle API rules are documented, but spread across sources a client is unlikely to find together, and the parts that matter most for integration are missing or only implied.

- **History limit:** the 8000-candle cap and the depth per period (43 days for 1-4m, 90 days for 5m+, 285 days for 1h+), counted back from the current time, are only in a 2018 PDF. They are not in the REST API reference, where clients set fromTime/toTime.
- **Partial candles:** the only guidance is a code comment ("skipping unfinished candle") in the KB sample. Nothing says that the REST response includes the open candle with eventFlags = 0.
- **Rate limits:** the REST page lists 100 requests/minute, but not what happens above it. We saw requests queued for up to 30 s with HTTP 200, so clients cannot tell throttling from an outage.
- **Demo delay:** the demo is described as "delayed" ("at least 15 min" on a quote table), but there is no stated policy for the delay or for market-hours behaviour.

In Parts 1-4 we found each of these rules by testing first and finding the documentation afterwards. A client will do the same, and the result is a support ticket.

**Proposed solution:** create one "Candle data behaviour guide" in the REST/dxLink API docs that consolidates and completes the above:

- **History limits:** the depth table and the 8000 cap, with an example showing why the same request returns less history later in the day (7829 -> 7744 candles in Part 1).
- **Partial candles:** how to detect the open candle on REST and dxLink (time + period + feed delay > now).
- **Rate limits:** the 100 requests/minute limit, what the server does above it, and recommended client timeout/retry settings.
- **Troubleshooting table:** symptom -> expected behaviour or real issue -> what to send to support.

Link it from the REST reference, the KB candle page and the support runbooks, and update or retire the 2018 PDF.

**Estimated complexity:** Low

- Most of the content already exists. The work is consolidating it and filling the gaps.
- Needs engineering review of the undocumented parts: overload behaviour and the current depth limits (the PDF is from 2018 and may be outdated).

**Business impact:**

- Clients find the limits before building on wrong assumptions, not after a failed backtest.
- Support can answer "missing history" and "slow API" tickets by linking one page.
- Expected behaviour stops looking like a bug: truncation and throttling are by design, but today nothing tells the client that.
