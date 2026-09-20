# Technical Indicator Formulas

Reference for computing technical indicators using Python stdlib only.

## RSI — Relative Strength Index (14-period)

```python
import json, math

def compute_rsi(prices, period=14):
    """Compute RSI from a list of closing prices."""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Initial averages (simple)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        return 100.0

    # Smoothed averages (Wilder)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
```

Interpretation:
- RSI > 70: Overbought (potential sell signal)
- RSI < 30: Oversold (potential buy signal)
- RSI 30-70: Neutral zone
- RSI divergence with price = strong signal

## SMA — Simple Moving Average

```python
def compute_sma(prices, period=20):
    """Compute Simple Moving Average."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period
```

Interpretation:
- Price above SMA: Bullish trend
- Price below SMA: Bearish trend
- Price crossing SMA: Trend change signal
- SMA slope direction indicates trend strength

## EMA — Exponential Moving Average

```python
def compute_ema(prices, period=12):
    """Compute Exponential Moving Average."""
    if len(prices) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period  # Start with SMA
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema
```

Interpretation:
- EMA reacts faster than SMA to price changes
- EMA(12) crossing EMA(26) = MACD signal
- Price above EMA: Short-term bullish

## MACD — Moving Average Convergence Divergence

```python
def compute_macd(prices, fast=12, slow=26, signal_period=9):
    """Compute MACD line, signal line, and histogram."""
    if len(prices) < slow + signal_period:
        return None, None, None

    ema_fast = compute_ema(prices, fast)
    ema_slow = compute_ema(prices, slow)

    if ema_fast is None or ema_slow is None:
        return None, None, None

    macd_line = ema_fast - ema_slow

    # Compute signal line from MACD history
    # Approximate: use recent EMA of price difference
    macd_values = []
    for i in range(slow, len(prices) + 1):
        ef = compute_ema(prices[:i], fast)
        es = compute_ema(prices[:i], slow)
        if ef is not None and es is not None:
            macd_values.append(ef - es)

    if len(macd_values) < signal_period:
        return macd_line, macd_line, 0.0

    signal_line = compute_ema(macd_values, signal_period)
    if signal_line is None:
        signal_line = macd_line

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram
```

Interpretation:
- MACD > Signal (histogram > 0): Bullish momentum
- MACD < Signal (histogram < 0): Bearish momentum
- MACD crossing Signal: Trend change
- Histogram growing: Momentum strengthening
- Histogram shrinking: Momentum weakening

## Bollinger Bands (20, 2)

```python
def compute_bollinger_bands(prices, period=20, std_dev=2):
    """Compute Bollinger Bands."""
    if len(prices) < period:
        return None, None, None

    sma = compute_sma(prices, period)
    recent = prices[-period:]
    variance = sum((p - sma) ** 2 for p in recent) / period
    std = math.sqrt(variance)

    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, sma, lower

def bollinger_position(price, upper, lower):
    """Where price sits within Bollinger Bands (0.0 = lower, 1.0 = upper)."""
    if upper == lower:
        return 0.5
    return (price - lower) / (upper - lower)
```

Interpretation:
- Price near upper band (>0.8): Overbought, may revert down
- Price near lower band (<0.2): Oversold, may revert up
- Bands squeezing (narrow): Low volatility, breakout imminent
- Bands widening: High volatility, strong trend
- Price walking the band: Strong trend continuation

## Volume Analysis

```python
def volume_spike(current_vol, avg_vol, threshold=1.5):
    """Check if current volume exceeds average by threshold."""
    if avg_vol == 0:
        return False
    return current_vol > avg_vol * threshold
```

Interpretation:
- Volume spike + price up: Strong buying pressure
- Volume spike + price down: Strong selling pressure
- Declining volume + price up: Weak rally
- Declining volume + price down: Weak selloff

## All-in-One Computation

```python
def full_analysis(prices):
    """Compute all indicators at once. prices = list of floats."""
    result = {}
    result['rsi'] = compute_rsi(prices, 14)
    result['sma_20'] = compute_sma(prices, 20)
    result['ema_12'] = compute_ema(prices, 12)
    result['ema_26'] = compute_ema(prices, 26)
    macd, signal, hist = compute_macd(prices)
    result['macd'] = macd
    result['macd_signal'] = signal
    result['macd_histogram'] = hist
    upper, middle, lower = compute_bollinger_bands(prices)
    result['bb_upper'] = upper
    result['bb_middle'] = middle
    result['bb_lower'] = lower
    result['current_price'] = prices[-1] if prices else None
    return result
```
