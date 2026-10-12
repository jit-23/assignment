# Part 1: Data Consistency & Integrity

## 1. Methodology

In this report we use the demo data from the dxFeed API. The demo feed is delayed: dxFeed labels it "delayed at least by 15 min", and we observed a delay of about 15 minutes (see "Are partial candles returned?"). All times are in UTC.

The instruments used are GOOG (US stock), EUR/USD (FX pair) and TSLA (high-volatility instrument). These are some of the instruments available on the dxFeed demo. The timeframes are 1m, 5m and 1h, from 2026-09-30 00:00 to 2026-10-08 13:00.

The historical candles come from the dxFeed REST API `https://demo.dxfeed.com/webservice/rest/events.json`, requested with `fromTime=20260930-000000Z` and `toTime=20261008-130000Z`. The raw JSON of every response is saved in `raw_json/` and then converted to CSV.

For the FX pair (EUR/USD) we add `price=bid`, because FX has no trades, so the candles are built from bid quotes. Without this attribute the response is empty.

The real-time collection uses the dxLink WebSocket. We take a 24-hour snapshot first and then keep receiving live updates for about 3 hours (data from 2026-10-07 09:43 to 2026-10-08 12:30).

For the historical candles, `validate.py` checks:

- if each candle makes sense on its own (high/low fit open/close, no negative volume, VWAP between low and high)
- if every candle starts on the timeframe grid, and if there are duplicates
- if there are missing intervals between the first and the last candle
- if a 5m candle matches its 1m children (and 1h its 5m children): the open is the first child open, the close is the last child close, the high is the max, the low is the min, and volume and count are the sum of the children
- how many candles there are per day of the week

`compare.py` compares every candle that exists in both the real-time and the historical data.

## 2. Tools used

| Tool | Used for |
|---|---|
| `demo.dxfeed.com/webservice/rest/events.json` | Historical candles over a fixed UTC range |
| dxLink WebSocket `wss://demo.dxfeed.com/market-data/dxlink-ws` | Real-time candles with a 24-hour snapshot plus live updates |
| dxLink debug console | Watching candles update by hand |
| `ApiCaller.py` (Python) | Downloads historical 1m, 5m and 1h candles, saves the raw JSON and the CSV |
| `ApiCaller.js` (Node) | Captures real-time candles to one CSV per instrument using `@dxfeed/dxlink-api` |
| `validate.py` | Integrity, gap, aggregation and calendar checks. Writes every mismatch to `aggregation_mismatches.csv` |
| `compare.py` | Compares the stream with the REST data and shows the differences |

## 3. Findings

### Q: Are 5m candles consistent with aggregated 1m candles?

- **GOOG:** Yes. 1508/1509 match. The single mismatch [2026-10-08 13:00] is a boundary window: toTime includes the 5m candle but only its first 1m child.
- **EUR/USD:** Yes. 1577/1579 match; both mismatches are on the edges.
  - 321 of the 1900 5m candles have no 1m children, because the 1m history only starts at 2026-10-01 02:49, although we asked from 2026-09-30 00:00.
  - [2026-10-01 02:45] mismatches because it only has the children from 02:49.
  - [2026-10-08 13:00] is a boundary window.
- **TSLA:** Yes. 1540/1541 match. The single mismatch [2026-10-08 13:00] is a boundary window.

**Hypothesis:** The 5m candles are correct. The mismatches only happen where the 1m data is incomplete (see the timestamp irregularities question for both causes).

**Impact:** A client that builds 5m candles from 1m, or compares timeframes, will see a mismatch on the first and last candle of a request even though nothing is wrong.

### Q: Are 1h candles consistent with aggregated 5m candles?

- **GOOG:** No. Volume and count mismatch on 86/130 candles, and VWAP on 66/130. 85 of them are full windows (1 is the boundary [2026-10-08 13:00]).
- **EUR/USD:** Yes. 164/165 match. The single mismatch [2026-10-08 13:00] is a boundary window.
- **TSLA:** No. Volume and count mismatch on 73/130 candles, and VWAP on 60/130. 72 of them are full windows (1 is the boundary [2026-10-08 13:00]).

In every mismatch the 1h volume is bigger than the sum of its 5m children, while the prices (OHLC) match. The mismatches are all in extended hours: overnight and pre-market (04:00-13:30 UTC, which is 00:00-09:30 ET) and after-hours (20:00-24:00 UTC). The regular pre-market only starts at 08:00 UTC (04:00 ET), so the data also includes overnight trading. The hours fully inside the regular session (14:00-19:59 UTC) always match.

**Hypothesis:** The 1h candles include trades that the 5m and 1m candles do not, probably extended-hours or late-reported trades that are filtered differently per timeframe. 1m -> 5m always matches, and EUR/USD is built from quotes, not trades, so it is not affected.

**Impact:** Prices match, so the charts look correct, but volume indicators like VWAP differ by timeframe. A strategy tested on 5m can behave differently on 1h.

### Q: Are there gaps?

- **GOOG:** Yes. 5948 candles missing on 1m, 904 on 5m and 72 on 1h.
- **EUR/USD:** Yes. 2863 candles missing on 1m, 561 on 5m and 41 on 1h.
- **TSLA:** Yes. 5240 candles missing on 1m, 872 on 5m and 72 on 1h.

**Hypothesis:** Most gaps are not missing data. A candle is only created when there is a trade (or a quote for the FX pair), so a minute where nothing happens has no candle. The big gaps are nights and weekends, when the market is closed.

**Impact:** A client or tool that expects one candle per minute will treat empty minutes as missing data. Worse, if it fills the series by position instead of by time, it can mix candles from different hours.

### Q: Are there timestamp irregularities?

There are no irregularities inside the data of the 3 instruments: 0 duplicates and 0 candles starting outside of their grid. All gaps of GOOG and TSLA are overnight and on weekends, when the market is closed.

The only irregularities are on the edges of the data range:

1. EUR/USD 1m starts at [2026-10-01 02:49] instead of 2026-09-30 00:00. GOOG and TSLA 1m are not affected.
2. `toTime=13:00` returns the 1h and 5m candles that start at 13:00, but only the 13:00 1m child (boundary window).

**Hypothesis:**

1. The API returns at most 8000 candles per request, counted back from the moment of the request, and then cuts the result at toTime. EUR/USD trades 24 hours a day, so it hits the limit; GOOG and TSLA have fewer candles in the same period. To confirm it, we repeated the request later (17:30 UTC): without toTime it returned exactly 8000 candles, and with toTime=13:00 it returned 7744 candles starting at 04:14, instead of 7829 starting at 02:49 one hour earlier. This matches the limit documented by dxFeed in "Chart Server Data Availability" (2018 PDF): a maximum of 8000 candles per request, counted backwards from the current time. The limit is not mentioned in the REST API reference.
2. toTime includes every candle whose start time is <= toTime.

**Impact:**

1. The same request returns less history the later it runs, with status OK and no warning, so an FX backtest can silently miss its oldest data.
2. The last 5m and 1h candles include trades after the requested end time, and they never match their 1m children. This happens on every request, not just once. A client that downloads consecutive ranges gets the boundary candle twice and can count it twice.

### Q: Are weekends handled consistently?

- **GOOG and TSLA:** Yes. There are no candles between Friday 23:59 UTC and Monday 04:00 UTC in any timeframe. The gap in the CSV is from the market being closed.
- **EUR/USD:** In general, yes. The data stops on Friday at 21:05 UTC, but there are 3 isolated candles with a single tick each (Saturday 2026-10-03 08:46 and 10:51, Sunday 2026-10-04 01:54 UTC) while the FX market is closed. On Sunday the data starts at 18:21 UTC, before the usual FX reopening at 21:00 UTC.

**Hypothesis:** The FX feed is built from quotes, not trades, so any quote sent while the market is closed (like test quotes) becomes a candle.

**Impact:** A client could see these candles and think there was trading. A gap-detection tool would split the weekend into pieces instead of one gap.

### Q: Are partial candles returned?

Yes. Both the real-time stream and the REST API return candles that are still being built. No field marks them as incomplete (eventFlags = 0), so a partial candle can only be identified by its time.

- **GOOG:** 1384/1387 identical candles. The 3 that differ are the last candle of each timeframe. For example, GOOG 1m [2026-10-08 12:30] has volume 3,743 in the stream and 8,587 in the REST data downloaded later.
- **TSLA:** 1462/1465 identical candles. The same 3 last candles differ.
- **EUR/USD:** 1954/1957 identical candles. The same 3 last candles differ.

For REST, we checked it with a request at 17:29:46 UTC: it returned the 17:14 EUR/USD 1m candle, still open because of the demo delay (about 15 minutes), with eventFlags = 0.

**Hypothesis:** dxFeed publishes the current state of a candle that is still open and keeps updating it until its period ends. High, low, close, volume and count change, while open stays the same. This is normal behaviour, but the candle event does not carry a "complete" flag. A candle is only complete when time + period + demo delay (about 15 min) <= time of the request.

**Impact:** This affects trading clients, because a partial candle looks exactly the same as a complete one.

- A database can store a candle that is still going to change, thinking it is closed.
- A strategy that acts on closed candles can be triggered by an unfinished one.
- A client comparing stream with REST data can report a "data error" when it is just a timing difference.

## 4. Anomalies and edge cases

### 1. VWAP outside the candle's own price range (volume inconsistencies)

GOOG and TSLA have candles where the VWAP is lower than the low or higher than the high, which should be impossible if VWAP and OHLC come from the same trades.

- **GOOG:** 1376/6113 1m, 126/1509 5m, 9/130 1h
- **TSLA:** 1274/6821 1m, 71/1541 5m, 8/130 1h
- **EUR/USD:** 0

Examples:

- GOOG 1m [2026-09-30 04:00] open=high=low=close=337.77, vwap=337.7491
- GOOG 1h [2026-09-30 12:00] low=340.57, high=342.70, vwap=338.68

**Hypothesis:** VWAP and volume are calculated over a wider set of trades than OHLC (other venues, odd lots, late trades). This is probably the same reason the 1h volume is bigger than the sum of the 5m.

**Impact:** Any VWAP-based strategy or execution benchmark is using a price the candle never traded at. A client validating "low <= vwap <= high" will reject real data.

### 2. Volume inconsistencies between timeframes

The 1h volume is bigger than the sum of its 5m candles for GOOG and TSLA. See "Are 1h candles consistent with aggregated 5m candles?".

### 3. Missing intervals: history limit

EUR/USD 1m is cut at the start because the API returns at most 8000 candles per request. See "Are there timestamp irregularities?".

### 4. Partial aggregation windows: boundary candle at toTime

The 5m and 1h candles that start at toTime are returned without most of their children. See "Are there timestamp irregularities?".

### 5. Partial candles have no complete flag

The last candle is still being built and nothing marks it. See "Are partial candles returned?".

### 6. Timestamp alignment: weekend FX candles

EUR/USD has single-tick candles while the market is closed. See "Are weekends handled consistently?".

We did not see inconsistent timezone handling (all timestamps are UTC and on the grid). A data freeze was not tested.

## Evidence files

- `*_candles.csv` - historical candles
- `*_realtime_candles.csv` - real-time candles
- `validate.py` - validation of the CSV files from `ApiCaller.py`
- `aggregation_mismatches.csv` - every aggregation mismatch found by `validate.py`
- `realtime_vs_historical.csv` - every difference found by `compare.py`
