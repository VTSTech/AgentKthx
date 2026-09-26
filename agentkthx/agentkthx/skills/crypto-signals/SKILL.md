---
name: crypto-signals
description: "Generate trading signals for cryptocurrency (BTC, ETH) and crypto-adjacent stocks (COIN, MSTR) using free market data APIs, technical analysis, and LLM interpretation. Use when asked to analyze crypto prices, generate buy/sell/hold signals, check portfolio status, or run the crypto signal workflow. Triggers on phrases like 'crypto signals', 'check my portfolio', 'analyze BTC', 'trading signals', 'market analysis', 'should I buy or sell'."
license: MIT
allowed-tools: shell read_file
---

# Crypto Trading Signals

Trading signal generation for a $10 crypto portfolio: $5 BTC, $5 ETH, plus crypto-adjacent stocks COIN and MSTR.

## Portfolio

```
ASSET  | TYPE    | VALUE
-------|---------|--------
BTC    | Crypto  | $5.00
ETH    | Crypto  | $5.00
COIN   | Stock   | Track only
MSTR   | Stock   | Track only
```

COIN and MSTR are stocks, not cryptocurrencies. They trade Mon-Fri 9:30AM-4PM ET.

## How It Works

This skill uses a **two-phase design** optimized for small models (0.5B-1B):

**Phase 1 — Data (script does everything):**
A Python script fetches prices, computes RSI/MACD/Bollinger indicators, generates BUY/HOLD/SELL signals, and writes the results to `crypto_report.json`.

**Phase 2 — Interpret (model reads JSON, writes summary):**
The model reads the pre-computed JSON and writes a human-readable report to `crypto_summary.txt`.

The script handles all the hard work. The model only needs to call 2 tools total.

## Workflow

### Step 1: Run the signal script

**IMPORTANT: Copy and paste this exact command into the shell tool. Do NOT modify the path.**

```
find /usr -name crypto_signal_agent.py -exec python {} run \;
```

This command searches for the script and runs it. The path will be found automatically. Do NOT guess paths like /path/to/ or /usr/bin/python. Use only the command above, exactly as written.

The script will:
- Fetch BTC and ETH prices from CoinGecko (fallback: CoinCap)
- Fetch COIN and MSTR prices from Yahoo Finance
- Compute RSI, SMA, EMA, MACD, Bollinger Bands from 30-day history
- Generate BUY/HOLD/SELL signals with confidence levels
- Write results to `crypto_report.json` in the current directory
- Append to `crypto_signals_log.json` for historical tracking

### Step 2: Read the report

Use the `read_file` tool to read `crypto_report.json`.

### Step 3: Write a human-readable summary

Based on the JSON data, use `write_file` to create `crypto_summary.txt` containing:

```
CRYPTO SIGNAL REPORT — {date}
========================

BTC: ${price} ({change_24h}% 24h)
  RSI(14): {rsi} | MACD: {macd} | BB Position: {pos}
  SIGNAL: {signal} ({confidence})

ETH: ${price} ({change_24h}% 24h)
  RSI(14): {rsi} | MACD: {macd} | BB Position: {pos}
  SIGNAL: {signal} ({confidence})

COIN: ${price} ({change}% 24h) — STOCK, not crypto
MSTR: ${price} ({change}% 24h) — STOCK, not crypto

PORTFOLIO: ${total} | 24h P&L: {pnl}%
ACTION: {one sentence recommendation}
```

Use the signal data from the JSON. Do not make up numbers.

### Step 4: Present to user

Read `crypto_summary.txt` and display it as your final answer.

## Quick Commands

- "Run signals" → Full 4-step workflow above
- "Check prices" → Run script, read report, show prices only
- "How's my portfolio" → Run script, read report, show portfolio section

## Other Script Commands

The script supports additional commands:

- `run` — Full analysis + JSON report
- `prices` — Current prices only
- `history` — Last 100 logged signals

Change `run` to `prices` or `history` in the command above.

## Constraints

- Zero dependencies — Python stdlib only
- Free APIs only — CoinGecko, CoinCap, Yahoo Finance (no keys)
- COIN/MSTR are stocks — note this in output
- Not financial advice — for educational/research purposes

## Troubleshooting

- If the script fails with a network error, the JSON will contain `_error` fields
- If CoinGecko rate-limits (429), the script auto-falls back to CoinCap
- If Yahoo Finance fails, stock data will be missing (crypto still works)
- The script writes to the current working directory

## Asset Reference

| Asset | CoinGecko ID | Yahoo Ticker | Type |
|-------|-------------|--------------|------|
| BTC | bitcoin | BTC-USD | Crypto |
| ETH | ethereum | ETH-USD | Crypto |
| COIN | — | COIN | Stock |
| MSTR | — | MSTR | Stock |