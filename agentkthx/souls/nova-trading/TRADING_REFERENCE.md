# Trading Reference Guide

> Read this file with `read_file` when you need detailed formulas, API specs, or strategy definitions.

## Yahoo Finance API — Complete Reference

### Chart Data Endpoint

```
GET https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}
```

**Query Parameters:**

| Param | Values | Default |
|-------|--------|---------|
| `range` | `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `10y`, `ytd`, `max` | `1mo` |
| `interval` | `1m`, `2m`, `5m`, `15m`, `30m`, `60m`, `90m`, `1h`, `1d`, `5d`, `1wk`, `1mo`, `3mo` | `1d` |

**Range/Interval Constraints:**
- `1m` interval: max range `5d`
- `5m` interval: max range `10d` (some endpoints allow 60d)
- `15m`, `30m`, `60m`, `1h`: max range `60d`
- `1d` and above: max range `max`

**Response Structure (key paths):**
```
body.chart.result[0].meta.regularMarketPrice     # Current price
body.chart.result[0].meta.previousClose          # Previous close
body.chart.result[0].meta.symbol                 # Symbol
body.chart.result[0].meta.exchangeName           # Exchange (e.g., "TSX")
body.chart.result[0].meta.currency               # Currency (e.g., "CAD")
body.chart.result[0].meta.chartPreviousClose     # Chart previous close
body.chart.result[0].timestamp                   # Array of epoch timestamps
body.chart.result[0].indicators.quote[0].open    # Array of open prices
body.chart.result[0].indicators.quote[0].high    # Array of high prices
body.chart.result[0].indicators.quote[0].low     # Array of low prices
body.chart.result[0].indicators.quote[0].close   # Array of close prices
body.chart.result[0].indicators.quote[0].volume  # Array of volumes
```

**Required HTTP Headers:**
```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```
Without this header, Yahoo Finance may return 403 errors.

### Quote Summary Endpoint

```
GET https://query1.finance.yahoo.com/v10/finance/quoteSummary/{SYMBOL}?modules={MODULES}
```

**Available Modules:** `defaultKeyStatistics`, `financialData`, `earningsHistory`, `earningsTrend`, `incomeStatementHistory`, `balanceSheetHistory`, `cashflowStatementHistory`, `indexTrend`, `secFilings`, `price`, `summaryDetail`, `calendarEvents`

**Most useful for trading:** `defaultKeyStatistics,financialData,price,summaryDetail,calendarEvents`

**Key Data Points:**
```
body.quoteSummary.result[0].price.regularMarketPrice
body.quoteSummary.result[0].price.marketCap
body.quoteSummary.result[0].defaultKeyStatistics.forwardPE
body.quoteSummary.result[0].defaultKeyStatistics.beta
body.quoteSummary.result[0].financialData.totalRevenue
body.quoteSummary.result[0].summaryDetail.fiftyTwoWeekHigh
body.quoteSummary.result[0].summaryDetail.fiftyTwoWeekLow
body.quoteSummary.result[0].calendarEvents.earnings.earningsDate[0].fmt
```

### Search Endpoint

```
GET https://query1.finance.yahoo.com/v1/finance/search?q={QUERY}&quotesCount=10&region=CA
```

Returns symbol suggestions. Filter results where `quoteType == "EQUITY"` for stocks.

## Technical Indicator Formulas

### ATR (Average True Range)

```python
def atr(highs, lows, closes, period=14):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    return sma(tr, period)
```

### Stochastic Oscillator

```python
def stochastic(highs, lows, closes, k_period=14, d_period=3):
    k_vals = []
    for i in range(len(closes)):
        if i < k_period - 1:
            k_vals.append(None)
        else:
            window_high = max(highs[i-k_period+1:i+1])
            window_low = min(lows[i-k_period+1:i+1])
            k = ((closes[i] - window_low) / (window_high - window_low)) * 100 if window_high != window_low else 50
            k_vals.append(k)
    # %D is SMA of %K
    d_vals = []
    for i in range(len(k_vals)):
        if k_vals[i] is None:
            d_vals.append(None)
        elif i < d_period - 1:
            d_vals.append(None)
        else:
            valid = [k_vals[j] for j in range(i-d_period+1, i+1) if k_vals[j] is not None]
            d_vals.append(sum(valid) / len(valid))
    return k_vals, d_vals
```

### ADX (Average Directional Index)

```python
def adx(highs, lows, closes, period=14):
    plus_dm = [0] * len(closes)
    minus_dm = [0] * len(closes)
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr_vals = [sum(tr[1:period+1])/period]
    smooth_plus = [sum(plus_dm[1:period+1])/period]
    smooth_minus = [sum(minus_dm[1:period+1])/period]
    for i in range(period, len(closes)):
        atr_vals.append((atr_vals[-1]*(period-1) + tr[i])/period)
        smooth_plus.append((smooth_plus[-1]*(period-1) + plus_dm[i])/period)
        smooth_minus.append((smooth_minus[-1]*(period-1) + minus_dm[i])/period)
    dx_vals = []
    for i in range(len(smooth_plus)):
        denom = smooth_plus[i] + smooth_minus[i]
        dx_vals.append(100 * abs(smooth_plus[i] - smooth_minus[i]) / denom if denom != 0 else 0)
    adx_val = sum(dx_vals[:period]) / period
    return adx_val, smooth_plus[-1], smooth_minus[-1]
```

## Position Sizing Calculator

```python
def calc_position_size(portfolio_value, risk_per_trade_pct, entry_price, stop_price):
    risk_amount = portfolio_value * (risk_per_trade_pct / 100)
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share == 0:
        return 0
    shares = int(risk_amount / risk_per_share)
    cost = shares * entry_price
    return max(0, shares), cost

# Example: $100K portfolio, 2% risk, entry $85, stop $82
shares, cost = calc_position_size(100000, 2, 85, 82)
# Result: shares=666, cost=$56,610
```

## Strategy Templates

### Strategy: RSI Mean Reversion

**Entry:** RSI crosses below 30, then crosses back above 30
**Exit:** RSI reaches 50, or stop-loss hit
**Stop-loss:** Recent swing low minus 1%

### Strategy: MACD Crossover

**Entry:** MACD line crosses above signal line (both below zero for stronger signal)
**Exit:** MACD line crosses below signal line
**Stop-loss:** Entry price minus 2x ATR(14)

### Strategy: Bollinger Band Bounce

**Entry:** Price closes below lower Bollinger Band, next candle closes above lower band
**Exit:** Price reaches middle band (SMA 20)
**Stop-loss:** Entry price minus 2x ATR(14)

### Strategy: Moving Average Golden Cross

**Entry:** SMA 50 crosses above SMA 200
**Exit:** SMA 50 crosses below SMA 200 (death cross)
**Stop-loss:** Entry price minus 3x ATR(14)

## Simple Backtest Engine (python_repl)

```python
def simple_backtest(closes, signals, initial_cash=100000, position_size_pct=10):
    """
    closes: list of close prices
    signals: list of 'BUY'/'SELL'/'HOLD' per bar, same length as closes
    Returns: final portfolio value, total return, trade count, win count
    """
    cash = initial_cash
    shares = 0
    trades = 0
    wins = 0
    entry_price = 0
    for i, (price, signal) in enumerate(zip(closes, signals)):
        if signal == 'BUY' and shares == 0:
            max_spend = cash * (position_size_pct / 100)
            shares = int(max_spend / price)
            cash -= shares * price
            entry_price = price
            trades += 1
        elif signal == 'SELL' and shares > 0:
            cash += shares * price
            if price > entry_price:
                wins += 1
            shares = 0
    # Final valuation
    final = cash + shares * closes[-1]
    total_return = ((final - initial_cash) / initial_cash) * 100
    return final, total_return, trades, wins
```

## TSX Market Hours

| Time (ET) | Status |
|-----------|--------|
| 9:30 AM - 4:00 PM ET, Mon-Fri | Market Open |
| 4:00 PM - 8:00 PM ET | After-hours (limited) |
| 8:00 PM - 9:30 AM ET | Closed |
| Saturday, Sunday | Closed |
| Statutory holidays | Closed |

**Canadian Statutory Holidays to check:**
New Year's Day, Family Day (3rd Monday Feb), Good Friday, Victoria Day, Canada Day (Jul 1), Civic Holiday (1st Monday Aug), Labour Day, Thanksgiving (2nd Monday Oct), Remembrance Day, Christmas Day, Boxing Day
