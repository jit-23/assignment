// Real-time candles from the dxFeed demo, using the dxLink JS API (@dxfeed/dxlink-api).
// URL taken from the dxLink debug console: https://demo.dxfeed.com/market-data/dxlink-ws/debug/
import fs from 'node:fs'
import { DXLinkWebSocketClient, DXLinkFeed, FeedDataFormat } from '@dxfeed/dxlink-api'

// instrument -> output file; candles for each timeframe go in the same file
const FILES = { 'GOOG': 'us_stock_realtime_candles.csv', 'EUR/USD': 'fx_pair_realtime_candles.csv', 'TSLA': 'high_volatility_realtime_candles.csv' }
const TIMEFRAMES = ['1m', '5m', '1h']
const SECONDS = { '1m': 60, '5m': 300, '1h': 3600 }
const DELAY = 15 * 60 * 1000                         // demo data is ~15 min behind
const EXTRA = { 'EUR/USD': ',price=bid' }             // FX has no trades, build candles from bid
const FROM_TIME = Date.now() - 24 * 60 * 60 * 1000   // history from 24 hours ago
const RUN_SECONDS = 60 * 60 * 3                     // 3 hours
const REMOVE_EVENT = 0x02                            // eventFlags bit: candle deleted / empty snapshot

// The server shortens symbols in its answers (GOOG{=1m} comes back as GOOG{=m}),
// so remember which instrument/timeframe each answer belongs to.
const info = {}
const subscriptions = []
for (const symbol of Object.keys(FILES)) {
  for (const timeframe of TIMEFRAMES) {
    const extra = EXTRA[symbol] ?? ''
    info[`${symbol}{=${timeframe.replace(/^1(?=[mh])/, '')}${extra}}`] = { symbol, timeframe }
    subscriptions.push({ type: 'Candle', symbol: `${symbol}{=${timeframe}${extra}}`, fromTime: FROM_TIME })
  }
}

// 1. Connect and choose which Candle fields we want
const client = new DXLinkWebSocketClient()
client.connect('wss://demo.dxfeed.com/market-data/dxlink-ws')
const feed = new DXLinkFeed(client, 'AUTO')
feed.configure({
  acceptDataFormat: FeedDataFormat.FULL,
  acceptEventFields: { Candle: ['eventSymbol', 'time', 'eventFlags', 'open', 'high', 'low', 'close', 'volume', 'count', 'vwap'] },
})

// 2. Keep the latest version of every candle (a candle in progress is resent on every update)
const candles = new Map()
feed.addEventListener((events) => {
  for (const c of events) {
    const key = `${c.eventSymbol}|${c.time}`
    if (c.eventFlags & REMOVE_EVENT || !Number.isFinite(Number(c.open))) candles.delete(key)
    else candles.set(key, c)
  }
})

// 3. Subscribe
feed.addSubscriptions(subscriptions)

// 4. Stop after RUN_SECONDS (or on Ctrl+C) and write one file per instrument, sorted by timeframe then time
function save() {
  client.close()
  const savedAt = Date.now()
  for (const [symbol, file] of Object.entries(FILES)) {
    const rows = [...candles.values()]
      .filter((c) => info[c.eventSymbol]?.symbol === symbol)
      .sort((a, b) => TIMEFRAMES.indexOf(info[a.eventSymbol].timeframe) -
        TIMEFRAMES.indexOf(info[b.eventSymbol].timeframe) || a.time - b.time)
      .map((c) => [symbol, `"${c.eventSymbol}"`, info[c.eventSymbol].timeframe,
        new Date(c.time).toISOString().replace('.000Z', '+00:00'),
        c.open, c.high, c.low, c.close, c.volume, c.count, c.vwap,
        // complete = the candle's period (plus the demo delay) had ended when we saved
        c.time + SECONDS[info[c.eventSymbol].timeframe] * 1000 + DELAY <= savedAt ? 'yes' : 'no'].join(','))
    fs.writeFileSync(new URL(file, import.meta.url), ['symbol,event_symbol,timeframe,time_utc,open,high,low,close,volume,count,vwap,is_complete', ...rows].join('\n') + '\n')
    console.log(`Saved ${rows.length} candles to ${file}`)
  }
  process.exit(0)
}
setTimeout(save, RUN_SECONDS * 1000)
process.on('SIGINT', save)
console.log(`Collecting candles for ${RUN_SECONDS}s (Ctrl+C to stop early and save)...`)
