# ⚛️ AgentKthx R07.27

**Status: Alpha**

A minimal, modular, python stdlib, agentic framework for tool calling AI agents. Runs **locally** with [BitNet](https://github.com/microsoft/BitNet), [Ollama](https://ollama.com), [TurboQuant](https://github.com/TheTom/llama-cpp-turboquant), **in the cloud** with [Cloudflare](https://developers.cloudflare.com/workers-ai/), [Google](https://ai.google.dev/gemini-api/docs), [HuggingFace](https://huggingface.co/), [Mistral](https://mistral.ai), [NVIDIA](https://build.nvidia.com), [OpenAI](https://openai.com), [OpenRouter](https://openrouter.ai), [OrcaRouter](https://www.orcarouter.ai), [Pollinations](https://enter.pollinations.ai) and [ZAI](https://api.z.ai). Extensible via a manifest-based **plugin system** for additional backends and features.

Inspired by the architecture of OpenClaw, rebuilt from scratch for local-first operation.

**Written by [VTSTech](https://www.vts-tech.org)** · [GitHub](https://github.com/VTSTech/AgentKthx) [Discord](https://discord.gg/vSK3Ba2aQ)

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/VTSTech/AgentKthx/blob/main/AgentKthx.ipynb)
[![GitHub commits](https://badgen.net/github/commits/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![GitHub latest commit](https://badgen.net/github/last-commit/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![CI](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml/badge.svg)](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml) ![coverage badge](./coverage.svg)

[![pip - agentkthx](https://img.shields.io/badge/pip-agentkthx-2ea44f?logo=PyPi)](https://pypi.org/project/agentkthx/) [![PyPI version fury.io](https://badge.fury.io/py/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download month](https://img.shields.io/pypi/dm/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download day](https://img.shields.io/pypi/dd/agentkthx.svg)](https://pypi.org/project/agentkthx/)

[![License](https://img.shields.io/badge/License-MIT-blue)](#license) [![Go to Python website](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FVTSTech%2FAgentKthx%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&query=project.requires-python&label=python&logo=python&logoColor=white)](https://python.org)

<img width="1648" height="996" alt="image" src="https://github.com/user-attachments/assets/42a7dfd6-ffb4-4e1a-844d-7984b4c8b59c" />
<img width="1295" height="1002" alt="image" src="https://github.com/user-attachments/assets/4264616b-1af7-44ad-bc74-24f78737f8e3" />
<img width="1498" height="721" alt="image" src="https://github.com/user-attachments/assets/f1f1e13b-c489-4fc3-b05c-9f6084818e7b" />
<img width="1117" height="882" alt="image" src="https://github.com/user-attachments/assets/d3e1a716-f3df-4383-ade2-c34882dbddb8" />
<img width="1247" height="622" alt="image" src="https://github.com/user-attachments/assets/f71e2221-57ac-4c15-850b-206039a95063" />
<img width="848" height="316" alt="image" src="https://github.com/user-attachments/assets/86724683-b217-4788-9277-1e16b65d74c2" />
<img width="1067" height="342" alt="image" src="https://github.com/user-attachments/assets/8b7870ca-5393-46b4-8d7a-0ea165cb2d41" />
<img width="1042" height="650" alt="image" src="https://github.com/user-attachments/assets/7743850a-634b-4fe6-96ea-977a96c26572" />
<img width="672" height="617" alt="image" src="https://github.com/user-attachments/assets/59b4a074-fd8a-4970-8289-5ec671324a94" />
<img width="1553" height="648" alt="image" src="https://github.com/user-attachments/assets/aeeb37b8-8f80-429b-b0e3-a31f8b3a482d" />



## 📚 Documentation

| Document | Description |
|----------|-------------|
| [ARCH.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/ARCH.md) | Technical documentation for developers (directory structure, core design, orchestrator modes) |
| [CHANGELOG.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CHANGELOG.md) | Version history and release notes (includes LocalClaw history) |
| [CREDITS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CREDITS.md) | Acknowledges every project, inspiration, API, model creator, and specification that makes AgentKthx possible |
| [docs/api/](https://github.com/VTSTech/AgentKthx/blob/main/docs/api/) | **API Technical References** — one deep-dive per provider, all in one folder: NVIDIA NIM, ZAI, OpenRouter, Gemini, Hugging Face Router, OpenAI, Mistral, Pollinations, OrcaRouter (auth & endpoints, request/response schemas, model catalogs, function calling, streaming, error codes & recovery, rate limits, free-tier behavior, AgentKthx `Backend` + `plugin.json` blueprints, troubleshooting matrices) |
| [JEV_API_MODE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/JEV_API_MODE.md) | JEV API mode — System-One decisions via any free LLM (Jev-compatible shape) |
| [mcp/ROADMAP.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/mcp/ROADMAP.md) | **MCP support** — Phase 1 (client mode, stdio) status + Phase 2 (`kthx-audit` MCP server) + Phase 3 (generic `mcp serve`) plan |
| [PLUGIN_SPEC_v0.2.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/PLUGIN_SPEC_v0.2.md) | Plugin spec v0.2 — lifecycle hooks, plugin tools API, external plugin roots, dual-form manifests, migration guide from v0.1 |
| [SUPPORT.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/SUPPORT.md) | Backend support tiers — Fully Supported vs Limited Support per-backend policy (R07.25) |
| [USAGE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/USAGE.md) | **Usage guide** — CLI commands, backend configurations (OpenRouter/Gemini/HuggingFace/ZAI/TurboQuant), Python API, persistent memory, security modes, environment variables, MCP client setup, full CLI options table |
| [TESTS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/TESTS.md) | Example scripts, test categories, benchmark results, and per-test deep-dive results |

## Features

- **Zero dependencies** — Uses Python stdlib only (urllib for HTTP)
- **Plugin system** — Manifest-based plugin discovery, lazy loading, dependency resolution (R05.0), plugin spec v0.2 lifecycle hooks (`on_init`/`on_run_start`/`on_run_end`/`on_error`/`on_shutdown`), plugin-provided tools, external plugin roots (`~/.agentkthx/plugins/`, `$AGENTKTHX_PLUGIN_PATH`), optional `sha256` content pinning (R07.05 SEC-06), `plugins --load/--unload/--reload/--json/--verbose` management
- **Native + plugin backends** — Ollama + TurboQuant built-in; Cloudflare, NVIDIA, OpenRouter, BitNet, ZAI, ACP, Gemini, OrcaRouter, Mistral, HuggingFace, OpenAI, Pollinations as plugins. (The TurboQuant backend uses llama.cpp's `llama-server` binary under the hood — `--backend turboquant` is the primary name; `--backend llama-server` remains as a backward-compat alias.)
- **Backend support tiers** (R07.25) — ZAI, OpenRouter, HuggingFace, Gemini, Mistral, NVIDIA NIM, Cloudflare Workers AI are Fully Supported (maintainer-tested before every release — Cloudflare promoted R07.27 after a 5/5 end-to-end smoke test); Pollinations, OrcaRouter, OpenAI are Limited Support (code-quality identical, but the maintainer's API key access has been unavailable for an extended period). See [docs/SUPPORT.md](docs/SUPPORT.md) for the full policy + what "Limited Support" means in practice. Local backends (TurboQuant, Ollama, BitNet) are always Fully Supported (no external API key needed).
- **Multi-cloud support** — Access to 500+ models from OpenRouter, OpenAI, Anthropic, Google (Gemini + Gemma), Cohere, plus 80+ models from NVIDIA NIM, 20+ open models (Llama, Mistral, Qwen, DeepSeek, Phi, Gemma, GPT-OSS) from Cloudflare Workers AI, and 11 upstream providers via OrcaRouter's zero-markup gateway
- **Dual API support** — OpenResponses (`--api openre`) and OpenAI Chat-Completions (`--api openai`)
- **JEV decision mode** — System-One decisions via any free LLM (`--api jev`) — Jev-compatible shape, no TypeSafe API key required
- **Thinking controls** — `--thinking off|auto|low|medium|high` to control model reasoning effort, `--think` flag to display reasoning_content (chain-of-thought) in CLI output. R07.26: streaming spinner starts even during streaming and stops on first chunk (thinking models that take 60-90+ seconds before the first token no longer look hung)
- **Tool support** — Native or ReAct, auto-detected from the server's own capabilities (a would-be `none` falls back to ReAct — no models classified none) plus a `think` column for thinking/reasoning support
- **Small model optimized** — Fuzzy matching, argument normalization, string-literal-aware Python-literal substitution (R07.07 MAINT-14 — `True`/`False`/`None` inside string values no longer mangled)
- **Built-in security** — Path validation, command blocklist (incl. shells + heredocs since R07.05), SSRF protection (DNS-resolving since R07.05), plugin sha256 pinning (R07.05), tool-output sanitization (R07.05), persistent-memory perms `0o700`/`0o600` (R07.05). Toggleable via `--security max|off`.
- **Multi-agent orchestration** — Router, pipeline, and parallel modes
- **Soul Spec v0.5** — Persona packages with progressive disclosure
- **AgentSkills spec** — Skill loading with SPDX license validation
- **Thinking models support** — Automatic handling of qwen3, deepseek-r1 thinking mode
- **Ctrl+C cancellation** — Graceful interrupt at backend, tool, and agent loop levels (R05.0; R07.06 ROB-01: half-cancelled run bug fixed)
- **Persistent memory** — SQLite-backed conversation persistence with session management (`--session`); writes are thread-safe (R07.05 ROB-03)
- **`/model` switch re-derives per-model state** (R07.06 ROB-14) — `num_ctx`, `num_predict`, `model_config`, `model_family` all follow the new model; values pinned via `--num-ctx`/`--num-predict`/`/param` survive the switch
- **17 built-in tools** — Calculator, shell, file ops (read/write/edit/list/find), HTTP, web search, JSON parse, Python REPL, todo list, datetime, word/char count. Load mid-session via `/tool shell,read_file`
- **MCP client support** (R07.22) — Connect to external [Model Context Protocol](https://modelcontextprotocol.io) servers via stdio JSON-RPC; their tools are bridged into the agent's registry with `<server>__<tool>` namespacing. See [docs/USAGE.md#mcp-model-context-protocol](docs/USAGE.md#mcp-model-context-protocol) for the full guide (`mcp init` / `mcp probe` / `mcp search` / `mcp install` / `chat --mcp [server...]`).
- **Dangerous tool confirmation** — `--confirm` flag for interactive approval of destructive operations
- **Audit logging** — Automatic JSON-lines logging of shell, write, and edit operations
- **Argument normalization** — ~100+ tool argument aliases for small model compatibility
- **JSON structured output** — `--response-format json` for structured JSON responses
- **Self-update** — `agentkthx update` to update to latest version from GitHub
- **Update check** — Startup + post-run notice for both release tracks: **stable** (new package on PyPI) and **development** (new commits on GitHub main); `agentkthx version` shows both too; always live (no cache — every run queries PyPI + GitHub main directly), fails silently offline, opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`
- **Persistent status footer** — 2-line terminal footer with live model/backend/token info (R05.4, scroll-region based)
- **Tool-call visibility** — Tool calls and results displayed in chat mode (R05.4); per-response stats (`⏱️ N steps, M tool calls, Xms` + tools used) in both chat and agent modes (R07.15)
- **Audit-tracked development** — every release since R07.04 documents findings in `audit/audit.md` with stable IDs (SEC / ROB / MAINT / PERF / FEAT / ARCH / TEST / MCP) and closure deltas. 101 CLOSED + 10 WONTFIX of 125 findings archived (~89%). See the [audit dashboard](https://kthx.vts-tech.org/audit-dash/) for the live register.

## Installation

```bash
# Latest Development Release
pip install git+https://github.com/VTSTech/AgentKthx.git --force-reinstall

# Last Stable (as stable as Alpha can be) Release
pip install agentkthx
```

## Usage

All usage instructions have moved to **[docs/USAGE.md](docs/USAGE.md)** —
CLI commands, backend configurations, Python API, persistent memory,
dangerous-tool confirmation, JSON structured output, TurboQuant server
management, tool support, model families, security modes, environment
variables, MCP client setup, and the full CLI options table.

Quick reference:

```bash
# Single prompt
agentkthx run "What is 2+2?"

# Interactive chat
agentkthx chat

# Autonomous agent mode
agentkthx agent "Research the latest AgentKthx release"

# Set API keys (interactive picker, persists to ~/.agentkthx/.env)
agentkthx auth

# MCP (Model Context Protocol) — see docs/USAGE.md#mcp-model-context-protocol
agentkthx mcp init
agentkthx chat --mcp filesystem
```

For backend configs, security modes, environment variables, MCP setup,
and the complete CLI options table, see [docs/USAGE.md](docs/USAGE.md).

## LocalClaw Redirect

The `localclaw` command is provided for backward compatibility:

```bash
# Both work identically
localclaw run "What is 2+2?"
agentkthx run "What is 2+2?"
```

## Tests & Examples

AgentKthx ships a comprehensive suite of regression tests + 12 example
scripts covering reasoning, knowledge, and tool usage. See
**[docs/TESTS.md](docs/TESTS.md)** for the example scripts, test
categories, and benchmark results.

```bash
# Regression test suite (3055 passed / 20 skipped in ~21s)
pytest

# Quick 5-question diagnostic example
python -m agentkthx.examples.01_quick_diagnostic
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests (3055 passed / 20 skipped in ~21s)
pytest

# Format code (CI gates on ruff + black over agentkthx/ and tests/)
black agentkthx tests
ruff check agentkthx tests
```

### Audit Trail

AgentKthx is developed with an audit-tracked discipline: every release since R07.04 documents findings in `audit/audit.md` with stable IDs (`SEC-XX`, `ROB-XX`, `MAINT-XX`, `PERF-XX`, `FEAT-XX`, `ARCH-XX`, `TEST-XX`, `MCP-XX`) and closure deltas.

- `audit/brief.md` — condensed intelligence brief for the current codebase (≤16K tokens; load this first when contributing)
- `audit/audit.md` — detailed findings report with severity, recommendations, and closure status per release
- `audit/deltas.md` — CLOSED + WONTFIX archive with closure prose per finding
- The `codebase-audit` skill ships with the repo at `agentkthx/skills/codebase-audit/` — invoke via `/skill codebase-audit` in chat mode to regenerate the brief against the current codebase.
- The [audit dashboard](https://kthx.vts-tech.org/audit-dash/) is regenerated by `audit/generate_audit_dash.py` (run manually before GitHub/CI per owner policy).

**Cumulative closure state: 101 CLOSED + 10 WONTFIX of 125 findings (111 archived, ~89%)** across R07.00 → R07.26; test suite at 3055 passed / 20 skipped.

## License

MIT License - See LICENSE file for details.

## Author

**VTSTech** — [https://www.vts-tech.org](https://www.vts-tech.org)

## Contributing

Contributions welcome!

## Changelog

See [docs/CHANGELOG.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CHANGELOG.md) for detailed version history and release notes.

## Migration from AgentNova

R06.0 renamed the project from `AgentNova` → `AgentKthx`. The rename was driven by name collisions in the AI agent space (multiple projects, Instagram accounts, etc. were using the AgentNova name).

### What changed

| Old | New |
|-----|-----|
| PyPI package: `agentnova` | `agentkthx` |
| CLI command: `agentnova` | `agentkthx` |
| Python import: `import agentnova` | `import agentkthx` |
| GitHub repo: `VTSTech/AgentNova` | `VTSTech/AgentKthx` |

### What stays the same (backward compat)

- ✅ The `agentnova` CLI command still works (redirects to `agentkthx`)
- ✅ `import agentnova` still works (re-exports from `agentkthx`, emits DeprecationWarning)
- ✅ All `AGENTKTHX_*` env vars still work unchanged
- ✅ `localclaw` CLI command still works (redirects through to `agentkthx`)
- ✅ All existing skills, souls, plugins, and configs continue to work
- ✅ SQLite persistent memory sessions remain compatible

### Migration steps (recommended but not required)

```bash
# Uninstall old package (optional — both can coexist)
pip uninstall agentnova

# Install new package
pip install agentkthx

# Update your scripts (optional — old imports still work with a warning)
# Old: from agentnova import Agent
# New: from agentkthx import Agent
```

### Why "AgentKthx"?

The name honors the IRC-era slang "kthx" (OK, thanks) — a callback to early internet culture. It's distinctive, memorable, and (most importantly) completely unused by any other AI agent project as of September 2026. The CLI binary `agentkthx` is short and easy to type.
