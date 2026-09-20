#!/usr/bin/env python3
"""
crypto_signal_agent.py — Zero-dependency crypto signal engine.

Designed for the two-phase small-model workflow:
  Phase 1 (this script): Fetch data, compute indicators, generate signals, write JSON
  Phase 2 (LLM): Read JSON, write human-readable summary

Usage:
  python crypto_signal_agent.py run         # Full analysis → crypto_report.json
  python crypto_signal_agent.py prices      # Current prices only → stdout
  python crypto_signal_agent.py history     # Last 100 log entries → stdout

No pip dependencies — stdlib only (urllib, json, math, time, os).
"""

import json
import math
import os
import sys
import time
import urllib.request
import urllib.error

USER_AGENT = "AgentKthx-CryptoSignals/1.0"
COINGECKO = "https://api.coingecko.com/api/v3"
COINCAP = "https://api.coincap.io/v2"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"

REPORT_FILE = os.path.join(os.getcwd(), "crypto_report.json")
LOG_FILE = os.path.join(os.getcwd(), "crypto_signals_log.json")

# ─── HTTP ────────────────────────────────────────────────────────────────────

def fetch_json(url, timeout=15):
    """Fetch JSON from URL. Returns dict with _error key on failure."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": True, "_status": e.code, "_url": url}
    except Exception as e:
        return {"_error": True, "_status": 0, "_message": str(e)}

# ─── Data Fetching ──────────────────────────────────────────────────────────

def fetch_crypto_prices():
    """Fetch current BTC + ETH prices. CoinGecko primary, CoinCap fallback."""
    url = (
        f"{COINGECKO}/simple/price"
        f"?ids=bitcoin,ethereum"
        f"&vs_currencies=usd"
        f"&include_24hr_change=true"
        f"&include_24hr_vol=true"
        f"&include_market_cap=true"
    )
    data = fetch_json(url)
    if data.get("_error"):
        # Fallback to CoinCap
        data2 = fetch_json(f"{COINCAP}/assets?ids=bitcoin,ethereum")
        if not data2.get("_error"):
            result = {}
            for asset in data2.get("data", []):
                sym = asset.get("symbol", "").upper()
                result[sym] = {
                    "usd": _float(asset.get("priceUsd", 0)),
                    "usd_24h_change": _float(asset.get("changePercent24Hr", 0)),
                    "usd_24h_vol": _float(asset.get("volumeUsd24Hr", 0)),
                    "usd_market_cap": _float(asset.get("marketCapUsd", 0)),
                    "_source": "coincap"
                }
            return result
        return data

    result = {}
    for key, val in data.items():
        sym = "BTC" if key == "bitcoin" else "ETH"
        result[sym] = {**val, "_source": "coingecko"}
    return result


def fetch_history(coin_id, days=30):
    """Fetch 30-day price history from CoinGecko."""
    url = (
        f"{COINGECKO}/coins/{coin_id}/market_chart"
        f"?vs_currency=usd&days={days}&interval=daily"
    )
    data = fetch_json(url)
    if data.get("_error"):
        return data
    prices = [p[1] for p in data.get("prices", [])]
    volumes = [v[1] for v in data.get("total_volumes", [])]
    return {"prices": prices, "volumes": volumes, "count": len(prices)}


def fetch_stock(ticker):
    """Fetch stock price from Yahoo Finance."""
    url = f"{YAHOO}/{ticker}?range=1d&interval=1h"
    data = fetch_json(url)
    if data.get("_error"):
        return data
    try:
        meta = data["chart"]["result"][0]["meta"]
        quotes = data["chart"]["result"][0]["indicators"]["quote"][0]
        closes = [c for c in quotes.get("close", []) if c is not None]
        prev = closes[-2] if len(closes) >= 2 else (closes[-1] if closes else 0)
        current = meta.get("regularMarketPrice", closes[-1] if closes else 0)
        change = _pct_change(current, prev)
        return {
            "symbol": ticker,
            "price": _round2(current),
            "change_pct": change,
            "market_cap": meta.get("marketCap"),
            "_source": "yahoo"
        }
    except (KeyError, IndexError, TypeError) as e:
        return {"_error": True, "_message": f"Parse error: {e}"}

# ─── Technical Indicators ──────────────────────────────────────────────────

def compute_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    if al == 0:
        return 100.0
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return _round2(100.0 - (100.0 / (1.0 + ag / al)))


def compute_sma(prices, period=20):
    if len(prices) < period:
        return None
    return _round2(sum(prices[-period:]) / period)


def compute_ema(prices, period=12):
    if len(prices) < period:
        return None
    m = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * m + ema
    return _round2(ema)


def compute_macd(prices, fast=12, slow=26, sig=9):
    if len(prices) < slow + sig:
        return None, None, None
    ef = compute_ema(prices, fast)
    es = compute_ema(prices, slow)
    if ef is None or es is None:
        return None, None, None
    macd_line = _round4(ef - es)
    # Build MACD history for signal line
    vals = []
    for i in range(slow, len(prices) + 1):
        e1 = compute_ema(prices[:i], fast)
        e2 = compute_ema(prices[:i], slow)
        if e1 is not None and e2 is not None:
            vals.append(e1 - e2)
    signal = compute_ema(vals, sig) if len(vals) >= sig else macd_line
    hist = _round4(macd_line - (signal or 0))
    return macd_line, _round4(signal) if signal else 0, hist


def compute_bollinger(prices, period=20, dev=2):
    if len(prices) < period:
        return None, None, None
    sma = compute_sma(prices, period)
    recent = prices[-period:]
    var = sum((p - sma) ** 2 for p in recent) / period
    std = math.sqrt(var)
    return _round2(sma + dev * std), _round2(sma), _round2(sma - dev * std)


def full_analysis(prices):
    """Compute all indicators for a list of closing prices."""
    bb_upper, bb_mid, bb_lower = compute_bollinger(prices)
    bb_pos = None
    if bb_upper and bb_lower and bb_upper != bb_lower:
        bb_pos = _round3((prices[-1] - bb_lower) / (bb_upper - bb_lower))

    macd, macd_sig, macd_hist = compute_macd(prices)

    return {
        "price": _round2(prices[-1]) if prices else None,
        "data_points": len(prices),
        "rsi_14": compute_rsi(prices, 14),
        "sma_20": compute_sma(prices, 20),
        "ema_12": compute_ema(prices, 12),
        "ema_26": compute_ema(prices, 26),
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_histogram": macd_hist,
        "bb_upper": bb_upper,
        "bb_middle": bb_mid,
        "bb_lower": bb_lower,
        "bb_position": bb_pos,
    }

# ─── Signal Generation ──────────────────────────────────────────────────────

def generate_signal(analysis):
    """Score-based signal: STRONG BUY / BUY / HOLD / SELL / STRONG SELL."""
    if not analysis or analysis.get("rsi_14") is None:
        return {"signal": "HOLD", "confidence": "LOW", "reason": "Insufficient data"}

    rsi = analysis["rsi_14"]
    bb_pos = analysis.get("bb_position")
    macd_hist = analysis.get("macd_histogram", 0)
    price = analysis["current_price"] if "current_price" in analysis else analysis.get("price")
    sma = analysis.get("sma_20")

    buy = 0
    sell = 0

    # RSI
    if rsi < 25: buy += 3
    elif rsi < 30: buy += 2
    elif rsi < 35: buy += 1
    elif rsi > 75: sell += 3
    elif rsi > 70: sell += 2
    elif rsi > 65: sell += 1

    # Bollinger position
    if bb_pos is not None:
        if bb_pos < 0.1: buy += 2
        elif bb_pos < 0.2: buy += 1
        elif bb_pos > 0.9: sell += 2
        elif bb_pos > 0.8: sell += 1

    # MACD histogram
    if macd_hist is not None:
        if macd_hist > 0: buy += 1
        elif macd_hist < 0: sell += 1

    # Price vs SMA
    if sma and price:
        if price < sma * 0.98: buy += 1
        elif price > sma * 1.02: sell += 1

    if buy >= 4: sig, conf = "STRONG BUY", "HIGH"
    elif buy >= 2: sig, conf = "BUY", "MEDIUM"
    elif sell >= 4: sig, conf = "STRONG SELL", "HIGH"
    elif sell >= 2: sig, conf = "SELL", "MEDIUM"
    else: sig, conf = "HOLD", "MEDIUM"

    return {
        "signal": sig,
        "confidence": conf,
        "buy_score": buy,
        "sell_score": sell
    }

# ─── Logging ────────────────────────────────────────────────────────────────

def append_log(signals_dict, portfolio_value):
    """Append signal entry to crypto_signals_log.json."""
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "signals": signals_dict,
        "portfolio_value": portfolio_value,
    }
    existing = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = []
    existing.append(entry)
    existing = existing[-100:]  # Keep last 100
    with open(LOG_FILE, "w") as f:
        json.dump(existing, f, indent=2)

def read_log():
    """Read signal history from crypto_signals_log.json."""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

# ─── Main Commands ──────────────────────────────────────────────────────────

def cmd_run():
    """Full analysis: fetch → compute → signal → write crypto_report.json"""
    print("Fetching prices...", file=sys.stderr)
    prices = fetch_crypto_prices()

    print("Fetching stocks...", file=sys.stderr)
    stocks = {}
    for ticker in ["COIN", "MSTR"]:
        s = fetch_stock(ticker)
        if not s.get("_error"):
            stocks[ticker] = s

    print("Computing indicators...", file=sys.stderr)
    analysis = {}
    for coin_id in ["bitcoin", "ethereum"]:
        hist = fetch_history(coin_id, 30)
        if hist.get("prices"):
            a = full_analysis(hist["prices"])
            s = generate_signal(a)
            analysis[coin_id] = {"analysis": a, "signal": s}
        else:
            analysis[coin_id] = {"error": "Failed to fetch history"}

    # Build the report
    btc_price = prices.get("BTC", {}).get("usd", 0)
    eth_price = prices.get("ETH", {}).get("usd", 0)
    btc_change = prices.get("BTC", {}).get("usd_24h_change", 0)
    eth_change = prices.get("ETH", {}).get("usd_24h_change", 0)

    # Estimate portfolio value ($5 each at purchase, track current)
    # We don't know purchase price, so show allocation as $5 flat
    btc_signal = analysis.get("bitcoin", {}).get("signal", {}).get("signal", "N/A")
    eth_signal = analysis.get("ethereum", {}).get("signal", {}).get("signal", "N/A")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "prices": prices,
        "stocks": stocks,
        "analysis": analysis,
        "portfolio": {
            "BTC": {"allocated": 5.00, "signal": btc_signal},
            "ETH": {"allocated": 5.00, "signal": eth_signal},
            "total_allocated": 10.00
        }
    }

    # Write report
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to {REPORT_FILE}", file=sys.stderr)

    # Log
    signals_log = {}
    for cid, sym in [("bitcoin", "BTC"), ("ethereum", "ETH")]:
        signals_log[sym] = analysis.get(cid, {}).get("signal", {})
    append_log(signals_log, 10.00)
    print(f"Log appended to {LOG_FILE}", file=sys.stderr)

    # Print to stdout too
    print(json.dumps(report, indent=2))


def cmd_prices():
    """Quick price check."""
    prices = fetch_crypto_prices()
    stocks = {}
    for ticker in ["COIN", "MSTR"]:
        s = fetch_stock(ticker)
        if not s.get("_error"):
            stocks[ticker] = s
    print(json.dumps({"crypto": prices, "stocks": stocks}, indent=2))


def cmd_history():
    """Show signal history."""
    log = read_log()
    print(json.dumps(log, indent=2))

# ─── Helpers ────────────────────────────────────────────────────────────────

def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def _round2(v):
    return round(float(v), 2) if v is not None else None

def _round3(v):
    return round(float(v), 3) if v is not None else None

def _round4(v):
    return round(float(v), 4) if v is not None else None

def _pct_change(current, previous):
    if not previous:
        return 0.0
    return round((current - previous) / previous * 100, 2)

# ─── Entry Point ────────────────────────────────────────────────────────────

COMMANDS = {
    "run": cmd_run,
    "prices": cmd_prices,
    "history": cmd_history,
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "Usage: python crypto_signal_agent.py <command>",
            "commands": list(COMMANDS.keys())
        }))
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(json.dumps({
            "error": f"Unknown command: {cmd}",
            "available": list(COMMANDS.keys())
        }))
        sys.exit(1)
