# Free Market Data APIs

No API keys required. Rate limits noted.

## Crypto APIs

### CoinGecko (Primary)

Base URL: `https://api.coingecko.com/api/v3`

**Simple Price:**
```
GET /simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true&include_market_cap=true
```

**Historical Chart:**
```
GET /coins/{id}/market_chart?vs_currency=usd&days=30&interval=daily
```

**Supported IDs:** bitcoin, ethereum, solana, cardano, dogecoin, etc.

**Rate Limit:** ~10-50 requests/minute (varies). Returns 429 when exceeded.
**Response Format:** JSON
**Reliability:** High. Occasional rate limiting during peak hours.

### CoinCap (Fallback)

Base URL: `https://api.coincap.io/v2`

**Assets:**
```
GET /assets?ids=bitcoin,ethereum
```

**Historical:**
```
GET /assets/{id}/history?interval=d1&start={epoch_ms}&end={epoch_ms}
```

**Rate Limit:** No strict limit, but be reasonable.
**Note:** Prices in USD, timestamps in milliseconds.

### Alternative Crypto APIs

- **CryptoCompare:** `https://min-api.cryptocompare.com/data/` (free tier: 100K/mo)
- **Blockchain.info:** `https://blockchain.info/ticker` (BTC only, simple)

## Stock APIs (COIN, MSTR)

### Yahoo Finance (Primary)

**Current Quote:**
```
GET https://query1.finance.yahoo.com/v8/finance/chart/COIN?range=1d&interval=1h
```

**Historical:**
```
GET https://query1.finance.yahoo.com/v8/finance/chart/MSTR?range=30d&interval=1d
```

**Tickers:** COIN, MSTR, BTC-USD, ETH-USD

**Note:** Yahoo may return 401 or redirect. If so, skip stock data and proceed with crypto-only.

### Alpha Vantage (Fallback, needs key)

Not recommended for this skill (requires free API key signup). Use only if Yahoo fails completely.

## Response Parsing Notes

### CoinGecko Price Response
```json
{
  "bitcoin": {
    "usd": 85000.0,
    "usd_24h_change": 2.5,
    "usd_24h_vol": 35000000000,
    "usd_market_cap": 1680000000000
  }
}
```

### CoinGecko Chart Response
```json
{
  "prices": [[1712000000000, 85000.0], [1712086400000, 85500.0], ...],
  "market_caps": [...],
  "total_volumes": [...]
}
```
Prices are `[timestamp_ms, price]` pairs. Extract just the price values for indicator computation.

### Yahoo Chart Response
```json
{
  "chart": {
    "result": [{
      "meta": {"regularMarketPrice": 250.0, ...},
      "indicators": {
        "quote": [{"close": [240, 245, 250, ...], "volume": [...]}]
      }
    }]
  }
}
```

## Error Handling

| Status | Action |
|--------|--------|
| 200 | Parse and use data |
| 429 | Rate limited. Switch to fallback API or wait 60s |
| 401/403 | Auth error. Try fallback API |
| 404 | Bad endpoint. Check URL |
| 500 | Server error. Retry once, then fallback |
| Timeout | Try next fallback |
| JSON parse error | Skip this data source, proceed with what you have |

## URL Construction in Python (stdlib only)

```python
import urllib.request
import json

def fetch_json(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'AgentNova-CryptoSignals/1.0',
        'Accept': 'application/json'
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())
```

Always include a User-Agent header. Some APIs reject requests without one.
