# Agent Configuration

## Recommended Tools

Primary tools for trading analysis:

| Tool | Purpose |
|------|---------|
| `http_get` | Fetch market data from Yahoo Finance API |
| `python_repl` | Calculate technical indicators, run backtests, analyze data |
| `parse_json` | Parse API responses from Yahoo Finance |
| `web-search` | Find news, earnings dates, market events |
| `write_file` | Save portfolio state, trade logs, analysis reports |
| `read_file` | Load portfolio, trade history, saved strategies |
| `calculator` | Quick position sizing and risk math |
| `get_time` | Check current time for market hours |

## Model Recommendations

| Model | Notes |
|-------|-------|
| `qwen2.5:7b` | Best balance of reasoning + tool use for trading |
| `qwen2.5:3b` | Good for simple analysis, may struggle with complex multi-step |
| `qwen2.5:0.5b` | Very limited — simple price lookups only |
| `llama3.1:8b` | Strong reasoning, native tool support |
| `mistral:7b` | Good alternative with native tool support |

**Recommended:** Use at least a 7B parameter model. Trading analysis requires multi-step reasoning and reliable tool calling.

## Tool Choice

Use `auto` mode (default). The agent should decide when to fetch data vs. calculate vs. search based on the query.

## Context Settings

Increase context window for complex analysis:
```
--num-ctx 8192
```

This allows room for fetched data + indicator calculations + analysis output.

## Agent Mode

For autonomous periodic scanning, use agent mode:
```
agentnova agent -m qwen2.5:7b --soul nova-trading --session trading
```

For interactive analysis, use chat mode:
```
agentnova chat -m qwen2.5:7b --soul nova-trading --session trading
```

## Sessions

Always use `--session trading` (or a named session) to persist:
- Portfolio state between sessions
- Trade history
- Analysis preferences
- Conversation context about previous trades

## Dangerous Tool Handling

Enable confirm mode for file writes:
```
--confirm
```

This ensures the agent asks before overwriting portfolio files or trade logs.

## Progressive Disclosure

| Level | Content |
|-------|---------|
| 1 | Basic identity — name and purpose only |
| 2 | Identity + tool usage patterns |
| 3 | Full: identity, tools, indicator formulas, API endpoints, risk rules |
