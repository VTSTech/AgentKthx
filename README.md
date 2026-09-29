# ⚛️ AgentKthx R07.15

**Status: Alpha**

A minimal, modular, python stdlib, agentic framework for tool calling AI agents. Runs **locally** with [Ollama](https://ollama.com), [BitNet](https://github.com/microsoft/BitNet), [TurboQuant](https://github.com/TheTom/llama-cpp-turboquant), **in the cloud** with [OpenRouter](https://openrouter.ai), [ZAI](https://api.z.ai), [HuggingFace](https://huggingface.co/), [OpenAI](https://openai.com), [OrcaRouter](https://www.orcarouter.ai), [Google Gemini](https://ai.google.dev/gemini-api/docs), [Pollinations](https://enter.pollinations.ai) and [Mistral](https://mistral.ai). Extensible via a manifest-based **plugin system** for additional backends and features.

Inspired by the architecture of OpenClaw, rebuilt from scratch for local-first operation.

**Written by [VTSTech](https://www.vts-tech.org)** · [GitHub](https://github.com/VTSTech/AgentKthx) [Discord](https://discord.gg/vSK3Ba2aQ)

> **ℹ️ Renamed from `AgentNova` (R06.0)**
>
> This project was previously named `AgentNova`. As of R06.0, the package has been renamed to `AgentKthx` (PyPI: `agentkthx`, CLI: `agentkthx`). The old `agentnova` PyPI package still works as a redirect — existing scripts and env vars (`AGENTKTHX_*`) continue to work without changes. See the [Migration Guide](#migration-from-agentnova) section below.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/VTSTech/AgentKthx/blob/main/AgentKthx.ipynb)
[![GitHub commits](https://badgen.net/github/commits/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![GitHub latest commit](https://badgen.net/github/last-commit/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![CI](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml/badge.svg)](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml)

[![pip - agentkthx](https://img.shields.io/badge/pip-agentkthx-2ea44f?logo=PyPi)](https://pypi.org/project/agentkthx/) [![PyPI version fury.io](https://badge.fury.io/py/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download month](https://img.shields.io/pypi/dm/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download day](https://img.shields.io/pypi/dd/agentkthx.svg)](https://pypi.org/project/agentkthx/)

[![License](https://img.shields.io/badge/License-MIT-blue)](#license) [![Go to Python website](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FVTSTech%2FAgentKthx%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&query=project.requires-python&label=python&logo=python&logoColor=white)](https://python.org)

<img width="854" height="645" alt="image" src="https://github.com/user-attachments/assets/c8daa2a6-0274-43b6-9364-2a604ad1aa4d" />
<img width="986" height="623" alt="image" src="https://github.com/user-attachments/assets/bbe21b8e-4f22-4dfb-8b82-1e95127643e7" />
<img width="768" height="428" alt="image" src="https://github.com/user-attachments/assets/daacd57c-4bd1-449b-9d0f-56a5058a3b9e" />
<img width="735" height="631" alt="image" src="https://github.com/user-attachments/assets/e36e916f-b815-4376-b8f1-f32394deae86" />
<img width="729" height="436" alt="image" src="https://github.com/user-attachments/assets/f5306ec6-77b1-4dee-8a20-d675e63814b2" />
<img width="848" height="316" alt="image" src="https://github.com/user-attachments/assets/86724683-b217-4788-9277-1e16b65d74c2" />
<img width="626" height="620" alt="image" src="https://github.com/user-attachments/assets/df0d91ec-8be5-47f1-a5fb-e89e57cdd38f" />
<img width="1219" height="662" alt="image" src="https://github.com/user-attachments/assets/063d6ece-ad1d-4ac6-901e-5634cc3222a9" />


## 📚 Documentation

| Document | Description |
|----------|-------------|
| [USAGE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/USAGE.md) | **Usage guide** — CLI commands, backend configurations (OpenRouter/Gemini/HuggingFace/ZAI/TurboQuant), Python API, persistent memory, security modes, environment variables, full CLI options table |
| [ARCH.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/ARCH.md) | Technical documentation for developers (directory structure, core design, orchestrator modes) |
| [CHANGELOG.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CHANGELOG.md) | Version history and release notes (includes LocalClaw history) |
| [TESTS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/TESTS.md) | Benchmark results, model recommendations, and testing guide |
| [PLUGIN_SPEC_v0.2.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/PLUGIN_SPEC_v0.2.md) | Plugin spec v0.2 — lifecycle hooks, plugin tools API, external plugin roots, dual-form manifests, migration guide from v0.1 |
| [JEV_API_MODE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/JEV_API_MODE.md) | JEV API mode — System-One decisions via any free LLM (Jev-compatible shape) |
| [docs/api/](https://github.com/VTSTech/AgentKthx/blob/main/docs/api/) | **API Technical References** — one deep-dive per provider, all in one folder: ZAI, OpenRouter, Gemini, Hugging Face Router, OpenAI, Mistral, Pollinations, OrcaRouter (auth & endpoints, request/response schemas, model catalogs, function calling, streaming, error codes & recovery, rate limits, free-tier behavior, AgentKthx `Backend` + `plugin.json` blueprints, troubleshooting matrices) |
| [CREDITS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CREDITS.md) | Acknowledges every project, inspiration, API, model creator, and specification that makes AgentKthx possible |

## Features

- **Zero dependencies** — Uses Python stdlib only (urllib for HTTP)
- **Plugin system** — Manifest-based plugin discovery, lazy loading, and dependency resolution (R05.0)
- **Plugin Spec v0.2 (R06.5)** — Lifecycle hooks (`on_init`/`on_run_start`/`on_run_end`/`on_error`/`on_shutdown`), plugin-provided tools, external plugin roots (`~/.agentkthx/plugins/`, `$AGENTKTHX_PLUGIN_PATH`), dual-form manifests (`extensions` block) with deprecation warnings for legacy fields, optional `sha256` content pinning (R07.05 SEC-06), `plugins --load/--unload/--reload/--json/--verbose` management
- **Native + plugin backends** — Ollama + llama-server built-in; OpenRouter, BitNet, ZAI, ACP, TurboQuant, Gemini, OrcaRouter, Mistral, HuggingFace, OpenAI, Pollinations as plugins
- **Multi-cloud support** — Access to 500+ models from OpenRouter, OpenAI, Anthropic, Google (Gemini + Gemma), Cohere, plus 11 upstream providers via OrcaRouter's zero-markup gateway
- **CloudBackend base class** (R07.05 MAINT-02) — shared cloud-backend boilerplate consolidated; new cloud backends are ~100 LOC instead of ~1500 LOC
- **Dual API support** — OpenResponses (`--api openre`) and OpenAI Chat-Completions (`--api openai`)
- **JEV decision mode** — System-One decisions via any free LLM (`--api jev`) — Jev-compatible shape, no TypeSafe API key required
- **Thinking controls** — `--thinking off|auto|low|medium|high` to control model reasoning effort, `--think` flag to display reasoning_content (chain-of-thought) in CLI output
- **Three-tier tool support** — Native, ReAct, or none (auto-detected)
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
- **Dangerous tool confirmation** — `--confirm` flag for interactive approval of destructive operations
- **Audit logging** — Automatic JSON-lines logging of shell, write, and edit operations
- **Argument normalization** — ~100+ tool argument aliases for small model compatibility
- **JSON structured output** — `--response-format json` for structured JSON responses
- **Self-update** — `agentkthx update` to update to latest version from GitHub
- **Update check** — Startup + post-run notice for both release tracks: **stable** (new package on PyPI) and **development** (new commits on GitHub main); `agentkthx version` shows both too; always live (no cache — every run queries PyPI + GitHub main directly), fails silently offline, opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`
- **Persistent status footer** — 2-line terminal footer with live model/backend/token info (R05.4, scroll-region based)
- **OpenRouter 429 retry** — Automatic retry with `Retry-After` header support for rate-limited providers (R05.4)
- **OrcaRouter Retry-After cap** (R07.07 ROB-16) — `Retry-After` honored up to 60s; longer waits capped (prevents malicious `Retry-After: 3600` from hanging the agent for an hour)
- **Tool-call visibility** — Tool calls and results displayed in chat mode (R05.4); per-response stats (`⏱️ N steps, M tool calls, Xms` + tools used) in both chat and agent modes (R07.15)
- **Audit-tracked development** — every release since R07.04 documents findings in `audit/audit.md` with stable IDs (SEC-XX, ROB-XX, MAINT-XX, PERF-XX, FEAT-XX, ARCH-XX, TEST-XX) and closure deltas. 68 CLOSED + 7 WONTFIX of 105 findings archived across R07.00 → R07.15 (71%). Run `/skill codebase-audit` to regenerate the brief against the current codebase.

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
variables, and the full CLI options table.

Quick reference:

```bash
# Single prompt
agentkthx run "What is 2+2?"

# Interactive chat
agentkthx chat

# Autonomous agent mode
agentkthx agent "Research the latest AgentKthx release"
```

For backend configs, security modes, environment variables, and the
complete CLI options table, see [docs/USAGE.md](docs/USAGE.md).

## LocalClaw Redirect

The `localclaw` command is provided for backward compatibility:

```bash
# Both work identically
localclaw run "What is 2+2?"
agentkthx run "What is 2+2?"
```

## Tests & Examples

AgentKthx includes a comprehensive suite of tests for validating agent capabilities across reasoning, knowledge, and tool usage:

```bash
# Basic agent test (no tools)
python -m agentkthx.examples.00_basic_agent

# Quick 5-question diagnostic
python -m agentkthx.examples.01_quick_diagnostic

# Tool usage tests (calculator, shell, datetime, file, python_repl)
python -m agentkthx.examples.02_tool_test

# Logic and reasoning tests (BBH-style)
python -m agentkthx.examples.03_reasoning_test

# GSM8K math benchmark (50 questions)
python -m agentkthx.examples.04_gsm8k_benchmark

# Common sense reasoning (BIG-bench)
python -m agentkthx.examples.05_common_sense

# Causal reasoning (BIG-bench)
python -m agentkthx.examples.06_causal_reasoning

# Logical deduction (BIG-bench)
python -m agentkthx.examples.07_logical_deduction

# Reading comprehension
python -m agentkthx.examples.08_reading_comprehension

# General knowledge (BIG-bench)
python -m agentkthx.examples.09_general_knowledge

# Implicit reasoning
python -m agentkthx.examples.10_implicit_reasoning

# Analogical reasoning
python -m agentkthx.examples.11_analogical_reasoning
```

### Test Categories

| Test | Questions | Focus |
|------|-----------|-------|
| Basic Agent | 1 | Single prompt, no tools |
| Quick Diagnostic | 5 | Calculator tool, multi-step reasoning |
| Tool Test | 10 | Calculator, shell, datetime, file, python_repl tools |
| Reasoning Test | 14 | Logic, deduction, patterns, spatial |
| GSM8K Benchmark | 50 | Math word problems |
| Common Sense | 25 | Physical properties, everyday reasoning |
| Causal Reasoning | 25 | Cause and effect relationships |
| Logical Deduction | 25 | Formal logic puzzles |
| Reading Comprehension | 25 | Passage-based Q&A |
| General Knowledge | 25 | Science, history, geography |
| Implicit Reasoning | 25 | Unstated assumptions and inference |
| Analogical Reasoning | 25 | Pattern matching and analogies |

### Benchmark Results (Quick Diagnostic)

| Model | Score | Time | Tool Support |
|-------|-------|------|-------------|
| functiongemma:270m | 5/5 (100%) | ~20s | native |
| granite4:350m | 5/5 (100%) | ~50s | native |
| qwen2.5:0.5b | 5/5 (100%) | 38s | native |
| qwen2.5-coder:0.5b | 5/5 (100%) | 93s | native |
| qwen3:0.6b | 5/5 (100%) | 70s | react |
| deepseek-r1:1.5b | 5/5 (100%) | ~305s | native |

All tested models achieve 100% on the Quick Diagnostic. Native models are ~2x faster than ReAct models due to direct API tool calling.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests (2051 passed / 16 skipped in ~15s)
pytest

# Format code (CI gates on ruff + black over agentkthx/ and tests/)
black agentkthx tests
ruff check agentkthx tests
```

### Audit Trail

AgentKthx is developed with an audit-tracked discipline: every release since R07.04 documents findings in `audit/audit.md` with stable IDs (`SEC-XX`, `ROB-XX`, `MAINT-XX`, `PERF-XX`, `FEAT-XX`, `ARCH-XX`, `TEST-XX`) and closure deltas.

- `audit/brief.md` — condensed intelligence brief for the current codebase (≤16K tokens; load this first when contributing)
- `audit/audit.md` — detailed findings report with severity, recommendations, and closure status per release
- The `codebase-audit` skill ships with the repo at `agentkthx/skills/codebase-audit/` — invoke via `/skill codebase-audit` in chat mode to regenerate the brief against the current codebase.

**Cumulative closure state: 68 CLOSED + 7 WONTFIX of 105 findings (75 archived, 71%)** across R07.00 → R07.15; test suite at 2051 passed / 16 skipped.

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
