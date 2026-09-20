# CHANGELOG

All notable changes to AgentKthx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [R06.0] - 2026-09-20

### 🚀 **Project Rename: AgentNova → AgentKthx**

This release renames the project from `AgentNova` to `AgentKthx`. The rename was driven by name collisions in the AI agent space — multiple other projects, Instagram accounts, and PyPI packages were using the "AgentNova" name, making it difficult for users to find this project.

The new name `AgentKthx` honors the IRC-era slang "kthx" (OK, thanks), a callback to early internet culture. It's distinctive, memorable, and verified unused across PyPI, GitHub, and major social platforms as of September 2026.

### 📦 **Package Changes**

| Old | New |
|-----|-----|
| PyPI package: `agentnova` | `agentkthx` |
| CLI command: `agentnova` | `agentkthx` |
| Python import: `import agentnova` | `import agentkthx` |
| GitHub repo: `VTSTech/AgentNova` | `VTSTech/AgentKthx` |
| Source directory: `agentnova/` | `agentkthx/` |
| All internal imports: `from agentnova.X` | `from agentkthx.X` |

### ✅ **Backward Compatibility**

The rename preserves full backward compatibility with existing user setups:

- **CLI**: The `agentnova` command still works — it's installed by the `agentkthx` PyPI package as a redirect binary that calls `agentkthx` with a deprecation notice. Same for `localclaw`.
- **Python imports**: `import agentnova` still works — emits `DeprecationWarning` and re-exports everything from `agentkthx`. Existing scripts continue to function without changes.
- **Env vars**: All `AGENTNOVA_*` env vars (`AGENTNOVA_BACKEND`, `AGENTNOVA_MODEL`, `AGENTNOVA_DEBUG`, `AGENTNOVA_MAX_STEPS`, `AGENTNOVA_RETRY_ON_ERROR`, `AGENTNOVA_MAX_TOOL_RETRIES`, etc.) remain unchanged and continue to work.
- **Filesystem paths**: User data directories `~/.agentnova/`, `~/.cache/agentnova/` remain unchanged — existing SQLite memory sessions, turbo state files, and tool support caches continue to work.
- **PyPI redirect packages**: A new `agentnova-redirect/` package will be published to PyPI as a stub that depends on `agentkthx`. Same for `localclaw-redirect/` (updated to depend on `agentkthx`).

### 🏗️ **Repository Structure**

```
AgentKthx/                              # repo root (renamed from AgentNova/)
├── agentkthx/                          # main Python package (renamed from agentnova/)
│   ├── __init__.py                     # exports __version__ = "0.6.0"
│   ├── __main__.py
│   ├── agent.py
│   ├── cli.py
│   ├── backends/
│   ├── plugins/
│   ├── core/
│   └── ...
├── agentnova/                          # redirect stub (re-exports from agentkthx)
│   └── __init__.py
├── localclaw/                          # redirect stub (re-exports from agentkthx)
│   └── __init__.py
├── agentnova-redirect/                 # standalone PyPI package for agentnova redirect
│   ├── pyproject.toml
│   ├── README.md
│   └── agentnova/__init__.py
├── localclaw-redirect/                 # standalone PyPI package for localclaw redirect
│   ├── pyproject.toml
│   ├── README.md
│   └── localclaw/__init__.py
├── pyproject.toml                      # main package (name="agentkthx", version="0.6.0")
├── README.md
└── docs/
```

### 🐛 **Bug Fixes**
- **Fixed pre-existing `__all__` mismatch**: `agentkthx/__init__.py`'s `__all__` list referenced `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_DEFAULT_MODEL` but they were never actually imported from `config`. Fixed by adding them to the import statement. This was a latent bug that only surfaced when the redirect stub did `from agentkthx import *`.

### 🧪 **Tests**
- All 245 existing tests pass after the rename with zero code changes to test logic.
- Test files had their imports updated from `from agentnova` → `from agentkthx`.
- Verified: `tests/test_jev_api_mode.py` (33 tests), `tests/test_thinking_args.py` (43 tests), `tests/test_agent.py`, `tests/test_builtins.py`, `tests/test_skills.py`, `tests/test_spec_compliance.py` — all green.

### 📚 **Documentation**
- Updated `README.md` — bumped version to R06.0, added "Renamed from AgentNova" notice at the top, added "Migration from AgentNova" section at the bottom with what changed, what stays the same, migration steps, and rationale.
- Updated all `docs/*.md` files — replaced `AgentNova` → `AgentKthx` and `agentnova` → `agentkthx` throughout.
- Updated all docstrings in `agentkthx/` source files.
- Updated CLI command examples in docstrings (`agentnova run` → `agentkthx run`).
- Preserved `AGENTNOVA_*` env var references in docs (backward compat).
- Preserved filesystem path references (`~/.agentnova/`) in docs (backward compat with user data).

### 🔧 **Technical Details**
- Main `pyproject.toml`: name changed to `agentkthx`, version `0.6.0`. `[project.scripts]` now defines three CLI binaries: `agentkthx` (primary), `agentnova` (redirect), `localclaw` (redirect). `[tool.setuptools.packages.find]` includes `agentkthx`, `agentkthx.*`, `agentnova`, `localclaw` — all three packages ship in the same PyPI distribution.
- `[project.entry-points."agentkthx.backends"]`: new entry point namespace. Old `agentnova.backends` entry point kept for backward compat with any external code that references it.
- New `agentnova/` directory at repo root is a thin Python package (just `__init__.py`) that emits `DeprecationWarning` on import and re-exports everything from `agentkthx`. Mirrors the existing `localclaw/` redirect pattern.
- New `agentnova-redirect/` directory contains a standalone PyPI package (separate `pyproject.toml`) that depends on `agentkthx>=0.6.0`. This is what gets published to PyPI as the `agentnova` package — it's a stub that just installs the redirect binary.
- `localclaw-redirect/` updated: dependency changed from `agentnova>=0.0` to `agentkthx>=0.6.0`. CLI binary still emits the redirect notice.

### ⚠️ **What's NOT Changed (Intentionally)**
- **Env var names**: `AGENTNOVA_BACKEND`, `AGENTNOVA_MODEL`, `AGENTNOVA_DEBUG`, `AGENTNOVA_MAX_STEPS`, `AGENTNOVA_RETRY_ON_ERROR`, `AGENTNOVA_MAX_TOOL_RETRIES`, `AGENTNOVA_ACP`, `AGENTNOVA_ACP_URL`, `AGENTNOVA_BACKEND`, `AGENTNOVA_NUM_CTX`, `AGENTNOVA_NUM_PREDICT`, `AGENTNOVA_TEMPERATURE`, `AGENTNOVA_TOP_P`, `AGENTNOVA_FORCE_REACT`, `AGENTNOVA_USE_MF_SYS`, `AGENTNOVA_VERBOSE`, `AGENTNOVA_FAST`, `AGENTNOVA_API_MODE` — all unchanged. Users' existing env var configurations continue to work without any changes.
- **Filesystem paths**: `~/.agentnova/` (SQLite memory sessions, turbo state, audit log), `~/.cache/agentnova/` (tool support cache) — all unchanged. Users' existing data continues to be used.
- **Soul/skill file paths**: `agentkthx/souls/*/`, `agentkthx/skills/*/` — these are internal to the package, so they rename with the package, but user-created souls/skills outside the package continue to work.
- **Plugin manifest format**: `plugin.json` schema unchanged — existing plugins continue to work.
- **OpenResponses / OpenAI / JEV API modes**: All API modes work identically.
- **Thinking controls**: `--thinking` and `--think` flags work identically.
- **JEV decision envelope shape**: `{decision, probability, alternatives, usage, latency_ms, _jev}` unchanged.

### 🔄 **Migration Path for Users**

Most users need to do **nothing** — existing scripts, env vars, and CLI commands continue to work via the redirect stubs.

For users who want to update to the new naming:

```bash
# Uninstall old (optional — both can coexist)
pip uninstall agentnova

# Install new
pip install agentkthx

# Update scripts (optional)
# Old: from agentnova import Agent
# New: from agentkthx import Agent

# Update CLI invocations (optional)
# Old: agentnova run "..."
# New: agentkthx run "..."
```

### 🔗 **References**
- PyPI: https://pypi.org/project/agentkthx/
- GitHub: https://github.com/VTSTech/AgentKthx
- Author: [VTSTech](https://www.vts-tech.org)

## [R05.8] - 2026-09-20

### 🚀 **New Features**
- **`--thinking` CLI flag**: Added `--thinking off|auto|low|medium|high` to control model thinking behavior across all backends.
  - `off` → `think=False` (disable thinking entirely — fastest, recommended for JEV decisions)
  - `auto` → `think=None` (let model decide, default)
  - `low`/`medium`/`high` → `think=True` + forward `reasoning_effort` to OpenAI-compatible thinking models (o-series, GLM-5.x, etc.)
- **`--think` CLI flag**: Boolean flag to toggle display of `reasoning_content` (chain-of-thought) in CLI output. Off by default. When set, reasoning is printed under each step in a dim/indented style, visually distinct from the actual content.
- **`ThinkingLevel` enum**: New enum in `core/types.py` with members `OFF`, `AUTO`, `LOW`, `MEDIUM`, `HIGH`.
- **`parse_thinking_arg()` helper**: Maps a user-facing level string (or `ThinkingLevel` enum) to a `(think, reasoning_effort)` tuple. Used by `cli.py` to resolve `--thinking` before passing to `Agent()`. Tolerant — unknown values fall back to `AUTO` rather than raising.
- **`reasoning_content` capture**: `OllamaBackend.generate()` (both native `/api/chat` and OpenAI `/v1/chat/completions` paths) now captures `reasoning_content` from the response message (also `thinking` key for Ollama-native format). Surfaced as `response["reasoning_content"]` for callers / CLI to consume.
- **`StepResult.reasoning_content`**: New field on the `StepResult` dataclass carries the model's chain-of-thought for each step. Populated by the agent loop from `gen_response["reasoning_content"]`.
- **Agent thinking params**: `Agent.__init__()` accepts `thinking_level`, `think`, `reasoning_effort`, and `show_reasoning` kwargs. Explicit `think` / `reasoning_effort` kwargs override what `thinking_level` would have resolved to — lets programmatic callers bypass CLI parsing.
- **JEV decision envelope includes reasoning_content**: `generate_decision()` now surfaces `reasoning_content` on the returned dict, so `--think` works in JEV mode too.
- **Backend forwarding of `reasoning_effort`**: All three paths in `OllamaBackend` (native `/api/chat`, OpenAI `/v1/chat/completions`, streaming) now forward `reasoning_effort` to the underlying API when set. Silently ignored by models that don't recognize it.

### 🧪 **Tests**
- Added `tests/test_thinking_args.py` with 43 passing tests covering:
  - `ThinkingLevel` enum existence and values
  - `parse_thinking_arg()` for all 5 levels + None/empty/unknown/enum inputs
  - CLI `--thinking` accepts each valid value, rejects invalid, defaults to `auto`
  - CLI `--think` boolean flag (default False, sets True when present)
  - CLI `--thinking` and `--think` can combine
  - `Agent.__init__()` accepts all new params, resolves `thinking_level` correctly
  - Explicit `think` / `reasoning_effort` kwargs override `thinking_level`
  - `StepResult.reasoning_content` field exists, defaults to empty, accepts value
  - `_print_agent_steps()` accepts `show_reasoning` kwarg
  - Source-level verification that `OllamaBackend.generate()`, `OllamaBackend.generate_completions()`, and `generate_decision()` reference `reasoning_content`
  - End-to-end CLI → Agent flow tests for both `--thinking off` and `--thinking high`

### 📚 **Documentation**
- Updated `README.md` — bumped version to R05.8, added "Thinking controls" bullet to Features, added new "Thinking Controls" usage section with CLI examples and Python API, added `--thinking` and `--think` rows to CLI Options table.
- Updated `docs/JEV_API_MODE.md` — updated limitation #6 (reasoning models inflate latency) to mention the new `--thinking off` workaround as the proper CLI-level fix (replacing the previous "pass `think=False` programmatically" workaround).

### 🔧 **Backend Changes**
- **OllamaBackend.generate() (native /api/chat)**: Captures `reasoning_content` (or `thinking` key) from response message. Forwards `reasoning_effort` as top-level body field. Excluded `reasoning_effort` from the `options` dict to avoid double-send.
- **OllamaBackend.generate_completions() (OpenAI /v1/chat/completions)**: `_parse_choice()` now extracts `reasoning_content` from `message`. Response dict includes `reasoning_content` key. Forwards `reasoning_effort` to body when set.
- **OllamaBackend streaming path**: Forwards `reasoning_effort` to body when set.
- **OllamaBackend.generate_decision()**: Surfaces `reasoning_content` from underlying LLM response onto the decision envelope.
- **OllamaBackend._maybe_jev_dispatch()**: Forwards `reasoning_content` from decision envelope onto the generate()-shaped response.
- **Agent.__init__()**: Accepts and stores `thinking_level`, `think`, `reasoning_effort`, `show_reasoning` as instance attributes. Resolves `thinking_level` via `parse_thinking_arg()` with explicit kwargs taking precedence.
- **Agent loop**: Both `backend_kwargs = {"think": think}` blocks now: (1) honor `self._think` first, (2) fall back to model-family no-think directive if not set, (3) forward `self._reasoning_effort` when set. Captures `reasoning_content` from `gen_response` and stores it on the `StepResult`.
- **cli.py**: Resolves `--thinking` to `(think, reasoning_effort)` via `parse_thinking_arg()`. Passes both + `show_reasoning` to `Agent()`. `_print_agent_steps()` accepts `show_reasoning` kwarg and prints `reasoning_content` under each step when set.

### ⚠️ **Limitations**
- **Model compatibility** — `--thinking off` is honored by all thinking-capable models tested (GLM-4.5+, qwen3, deepseek-r1). `--thinking low/medium/high` (forwarded as `reasoning_effort`) is honored by OpenAI o-series and GLM-5.x. Other models silently ignore it. No error is raised in either case.
- **Display only** — `--think` only controls whether `reasoning_content` is displayed in the CLI step summary. It does not affect what the model emits, what gets stored in memory, or what gets sent to ACP logging. Future work could add `reasoning_content` to the persistent memory and ACP log.
- **Streaming** — When streaming, `reasoning_content` may arrive interleaved with content deltas. The current implementation captures it from the non-streaming `_generate()` path; streaming mode does not yet surface reasoning in real-time.

### 🔗 **References**
- [OpenAI Reasoning Models Documentation](https://platform.openai.com/docs/guides/reasoning) — `reasoning_effort` parameter spec
- [ZAI GLM-4.5 Documentation](https://docs.z.ai) — `reasoning_content` response field
- [Ollama Thinking Models](https://ollama.com/blog/thinking-models) — `think` parameter and `thinking` response field

## [R05.7] - 2026-09-20

### 🚀 **New Features**
- **JEV API Mode**: Added `--api jev` (sibling of `openre` / `openai`) — System-One decision mode that wraps any chat-capable LLM with a constrained decision prompt, returning a Jev-compatible envelope `{decision, probability, alternatives, usage}`. Works with free ZAI / OpenRouter / Ollama models — no TypeSafe API key required. See [JEV_API_MODE.md](JEV_API_MODE.md).
- **`generate_decision()` primitive**: New `BaseBackend.generate_decision(model, state, choices, ...)` method for programmatic decision calls. Default impl raises `NotImplementedError`; concrete impl on `OllamaBackend` uses TypeSafe's "System One LLM wrapper" pattern (constrained JSON output).
- **`_jev_call_completions()` hook**: Per-backend override point for the underlying LLM call. `ZaiBackend` routes through `_generate_with_auth` (Bearer auth + ZAI_FREE_ONLY), `OpenRouterBackend` routes through `generate()` (429 retry), default `OllamaBackend` routes through `generate_completions()`.
- **`_maybe_jev_dispatch()` helper**: Shared dispatch hook called from `generate()` — returns a generate()-shaped dict if `api_mode == JEV`, else None so normal OPENRE/OPENAI path runs. Lets ZAI/OpenRouter/Ollama all handle JEV mode uniformly.
- **JSON output mode**: JEV mode requests `response_format={"type": "json_object"}` from the underlying LLM. If the model doesn't honor it, output is still parsed best-effort (markdown fence stripping, fallback to raw text as decision).
- **Constrained-choice fuzzy matching**: When `choices` are provided, the parser snaps the model's decision to the canonical choice form (exact or substring match).
- **Probability clamping**: Probabilities are clamped to `[0.0, 1.0]`.
- **Tolerant JSON parsing**: Handles ```json fences, plain ``` fences, malformed JSON (fallback to raw text), and alternatives as string lists (not just dicts).

### 🧪 **Tests**
- Added `tests/test_jev_api_mode.py` with 33 passing tests covering: ApiMode.JEV enum, CLI `--api jev` choice, `_build_jev_messages()`, `_parse_jev_response()`, `_serialize_state()`, `_maybe_jev_dispatch()`, backend construction in JEV mode, mocked `generate_decision()` calls, and `BaseBackend.generate_decision()` default raising `NotImplementedError`.

### 📚 **Documentation**
- Added `docs/JEV_API_MODE.md` — comprehensive spec covering architecture, decision envelope shape, key methods, CLI usage, Python API, backend support table, recommended free models, limitations, and future work.
- Updated `README.md` — bumped version to R05.7, added JEV bullet to Features, added JEV usage section with CLI examples, added JEV Python API example, added `JEV_API_MODE.md` to documentation table, updated `--api` CLI options table.

### 🔧 **Backend Changes**
- **ZaiBackend.__init__**: Now accepts `api_mode="jev"` (previously only `"openai"` was accepted). OPENRE still falls back to OPENAI (ZAI has no native /api/chat endpoint).
- **OpenRouterBackend.__init__**: Now accepts `api_mode="jev"` (previously raised `ValueError`). OPENRE still raises `ValueError`.
- **ZaiBackend.generate()**: Calls `_maybe_jev_dispatch()` at the top so JEV mode works without bypassing ZAI's auth path.
- **OpenRouterBackend.generate()**: Same — calls `_maybe_jev_dispatch()` at the top.

### ⚠️ **Limitations**
- **Not a real Jev** — JEV mode emulates the Jev API shape using any LLM. It does not call TypeSafe's `api.typesafe.ai/v1/systemone` endpoint. Calibrated probabilities may be less accurate than native Jev.
- **Single-shot only** — JEV mode is designed for single decision calls, not multi-turn chat sessions. The agent loop expects text content; JEV mode stuffs JSON into `content` which may confuse multi-step reasoning.
- **No streaming** — `generate_decision()` does not stream. The decision is returned as a single envelope.
- **Tool calling disabled** — Decisions never call tools. `tools=None` is always passed to the underlying LLM call.

### 🔬 **Known Issues**
- **Reasoning models inflate latency** — Thinking-capable models (e.g. GLM-4.5-flash) emit a `reasoning_content` field before the final JSON envelope. This reasoning is billable (counts toward `completion_tokens`) but not used by the decision parser. In the verified smoke test (see below), GLM-4.5-flash produced 319 reasoning tokens vs ~80 tokens of actual decision JSON, inflating latency to ~22s for a trivial classification. Future fix: pass `think=False` explicitly when the model supports it, or strip reasoning_content from the token accounting.

### ✅ **Verified**
- **Smoke test passed** (2026-09-20) on ZAI free tier:
  ```bash
  agentkthx run "Is 'You won a prize' spam or inbox?" \
      --api jev --backend zai -m glm-4.5-flash
  ```
  Result: `decision="spam"`, `probability=0.95`, `alternatives=[{"value":"inbox","probability":0.05}]`, `_parse_ok=true`, `usage={input:292, output:319, total:611}`, `latency_ms=22139`. Probabilities correctly summed to 1.0.
- **Unit test suite** — 33/33 tests pass in `tests/test_jev_api_mode.py` (no network required).
- **Combined suite** — 202/202 tests pass (33 new JEV + 169 existing) with zero regressions vs R05.6 baseline. The 9 pre-existing failures in `tests/test_r048_changes.py` and `tests/test_security.py` (ZAI module path mismatch from R04.8, IPv6 loopback test) were confirmed present in baseline R05.6 — not caused by JEV changes.

### 🔗 **References**
- [TypeSafe AI — Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (Sep 16, 2026)
- [LangChain — What Is Jev?](https://www.langchain.com) (Sep 18, 2026)
- [DataCamp — Jev: TypeSafe's System One Model](https://www.datacamp.com) (Sep 17, 2026)
- [OpenRouter — Jev 1.13](https://openrouter.ai/typesafe/jev-1.13) (Sep 18, 2026)

## [R05.6] - 2026-09-19 3:00:48 PM

### 🚀 **New Features**
- **Added ZAI API Technical Reference**: Created comprehensive technical documentation at `/docs/ZAI_API_TECHNICAL_REFERENCE.md` for improved ZAI backend integration
- **Default Streaming for Cloud Providers**: ZAI and OpenRouter backends now default to streaming mode (`--stream` flag automatically enabled), while local providers (Ollama, BitNet, TurboQuant) default to non-streaming
- **Configurable Max Steps**: Added `--max-steps` CLI parameter with default increased from 5 to 10 steps to allow agents more time for complex tasks
- **Environment Variable Support**: Max steps can be configured via `AGENTNOVA_MAX_steps` environment variable
- **Enhanced Terminal Input**: Added readline support for proper arrow key navigation (← → ↑ ↓) and message history browsing

### 🐛 **Bug Fixes**
- **Fixed Max Steps Default Error**: Resolved `TypeError: 'NoneType' object cannot be interpreted as an integer` when `--max-steps` not specified, including defensive null-check in Agent constructor
- **Removed Duplicate Argument**: Eliminated duplicate `--max-steps` definition in `shared_args.py` that was causing `argparse.ArgumentError`
- **Fixed Truncation Logic**: Removed debug mode truncation condition that was hiding step-by-step progress in regular chat mode
- **Resolved Backend Variable Scoping**: Fixed `UnboundLocalError: cannot access local variable 'backend' where it is not associated with a value`
- **Fixed Timeout Variable**: Resolved `NameError: name 'timeout' is not defined` error
- **Enhanced Error Handling**: Added full traceback display for unexpected errors to improve debugging experience

### 🔧 **Enhancements**
- **Improved Step Display Logic**: Agent step summary is now hidden when only 1 step exists (just final answer), but shown for multi-step reasoning (2+ steps)
- **Enhanced Agent Steps Visibility**: Step-by-step progress is now visible in regular chat mode (not just debug mode) for better user experience
- **Backend Auto-Detection**: Improved model configuration auto-detection for ZAI model families
- **Error Handling**: Enhanced retry logic with exponential backoff for rate limits and server errors
- **Memory Management**: Improved context window management with proper truncation handling

### 📋 **CLI Changes**
- **New `--max-steps` Parameter**: Added to control maximum reasoning steps (default: 10)
- **Streaming Behavior**: Cloud providers default to streaming, local providers default to non-streaming
- **Help Output**: `--max-steps` now properly documented in CLI help
- **Argument Parsing**: Fixed argument conflicts and improved parser stability
- **Enhanced Terminal Input**: Integrated Python `readline` module for proper arrow key handling
- **Message History Navigation**: UP/DOWN arrows now browse through previous user messages with persistent history storage in `~/.agentkthx_history`

### 🔌 **Backend Improvements**
- **ZAI Backend**: 
  - Fixed truncation handling in debug mode
  - Improved model family detection and configuration
  - Enhanced error handling and retry logic
  - Added support for thinking parameters
- **All Backends**: 
  - Improved rate limiting and concurrency control
  - Enhanced error recovery mechanisms
  - Better timeout handling

### 📖 **Documentation**
- **ZAI API Reference**: Created complete technical implementation guide
- **Developer Documentation**: Added implementation notes for AgentKthx integration
- **API Documentation**: Enhanced with code examples and troubleshooting guides

### 🛠️ **Internal Changes**
- **Code Structure**: Refactored shared argument parsing to eliminate duplicates
- **Configuration**: Centralized model default handling
- **Testing**: Improved argument validation and error handling
- **Performance**: Optimized context window management and token counting
- **Terminal Enhancement**: Integrated Python readline module for improved terminal input handling and escape sequence processing

---

## [0.5.5] - 2026-09-18 3:21:26 PM

### Added
- **ZAI Model Accuracy Improvements**
  - Added comprehensive max_tokens documentation to ZAI backend with confirmed model specifications
  - Implemented FREE_ONLY filtering for ZAI backend to show only free models (glm-4.5-flash, glm-4.7-flash)
  - Added intelligent tool support defaults for cloud providers (no expensive API calls needed)
- **Intelligent CLI Defaults for Cloud Providers**
  - Auto-populates `--num-ctx` with catalog values when not specified by user
  - Auto-populates `--num-predict` with catalog values when not specified by user
  - Enables streaming mode by default for cloud providers (OpenRouter, ZAI) for optimal UX

### Changed
- **Cloud Provider Model Listing**
  - Fixed model list display for cloud providers (ZAI, OpenRouter) to remove redundant family column
  - Improved context size formatting with proper "K" suffix display (128K → 128K, not 125K)
  - Fixed cloud provider backend detection using BackendType enum instead of isinstance checks
  - Set cloud model size display to "unknown" (accurate since cloud APIs don't provide this info)
- **OpenRouter Backend Enhancement**
  - Now uses live API data for max_tokens and context length instead of static catalog
  - Added cache population on backend initialization to ensure live data is always available
  - Improved `_get_model_defaults()` to check both cache and catalog for accurate defaults

### Fixed
- **ZAI Context Length Accuracy**
  - Updated ZAI catalog context lengths based on official API documentation:
    - glm-4.5, glm-4.5-flash: 128K → 132K (for proper 128K display)
    - glm-4.7, glm-4.7-flash: 128K → 204800 (for proper 200K display)
  - Removed orphaned ZAI backend code from `backends/zai.py` (was duplicated in plugin system)
  - Fixed FREE_ONLY filtering for ZAI backend using `ZAI_FREE_ONLY=true` environment variable
- **Cloud Provider Max Token Accuracy**
  - **OpenRouter**: Fixed max_tokens fallback from 4096 to live API values (e.g., 32K for poolside/laguna-xs-2.1:free)
  - **ZAI**: Fixed incorrect 131072 max_tokens to model-specific limits (98304 for GLM-4.5 series, 131072 for GLM-4.6/5)
  - ZAI models now respect official API limits: GLM-4.5 series (96K max), GLM-4.6/5 (128K max)
  - Fixed cache population issue where `list_models()` wasn't called before `run()`
- **CLI Streaming Defaults**
  - OpenRouter and ZAI now automatically enable streaming mode for better interactive experience
  - Run and Chat commands now auto-detect cloud providers and enable streaming without explicit `--stream` flag

### Documentation
- Added cache refresh endpoint comments to cloud provider backends:
  - OpenRouter: GET /v1/models (1-hour cache timeout)
  - ZAI: GET /api/paas/v4/models (1-hour cache timeout)
- Added comprehensive ZAI documentation with context lengths, max tokens, and pricing information for all GLM models
  - Includes official context sizes: 128K, 200K, and 1M variants
  - Shows per-model pricing and FREE_ONLY behavior
- **API Integration Documentation**
  - Added OpenRouter live API integration details with max_completion_tokens and context_length fields
  - Documented CLI auto-defaults behavior for cloud providers
  - Updated ZAI model specifications with official API documentation reference

---

## [R05.4] - 09-11-2026 7:21:00 PM

### OpenRouter Tool-Calling Fix, CLI Visibility, Security Modes & 429 Retry

Fixes a critical defect in the OpenRouter backend where native tool calls were never sent to the API and never parsed from the response, causing the agent to silently fall back to treating the model's text output as a final answer. Also adds a runtime-toggleable security mode (`/security max|off`), automatic 429 retry with `Retry-After` support, a 2-line persistent terminal status footer, CLI visibility for tool-call execution, and proper error surfacing for empty responses and provider-side rate limits. This release makes the OpenRouter backend fully usable for agentic workflows with cloud models.

### Fixed

#### Critical: OpenRouter Backend Never Sent or Parsed Tool Calls
- **Bug**: `OpenRouterBackend.generate()` built the request body without the `tools` field — OpenRouter had no idea what tools existed, so the model hallucinated its own non-standard text format (`shellcommand\`echo ...\``) instead of using proper function-calling JSON. The agent's ToolParser couldn't recognize this format, so every tool request was silently accepted as a final answer.
- **Root Cause**: The original `generate()` method hardcoded `"tool_calls": []` in its return dict and never read `message.get("tool_calls")` from the API response. It also used `max_completion_tokens` (the newer OpenAI field) instead of `max_tokens` (universally supported), causing silent failures on some `:free` providers.
- **Fix**: Rewrote `generate()` to:
  - Build the request body with `tools` in OpenAI function-calling schema (`t.to_openai_schema()`)
  - Send `max_tokens` for maximum provider compatibility
  - Parse `choices[0].message.tool_calls` from the response, normalizing arguments (JSON string → dict, handling malformed JSON gracefully with `_raw` fallback)
  - Synthesize `finish_reason` when providers omit it (some do)
  - Return `latency_ms` for performance tracking

#### OpenRouter HTTP Errors Not Surfaced to User
- **Bug**: `_make_api_request()` called `response.raise_for_status()` which raised a bare `requests.exceptions.HTTPError` with no upstream error message. The chat loop caught this as a generic `RuntimeError` and printed a vague "Error:" line, or worse — when OpenRouter returned HTTP 200 with a top-level `error` field (provider-side rate limit), the parser silently returned an empty response and the user saw a blank "Agent Nova: " line.
- **Fix**:
  - `_make_api_request()` now extracts the upstream error message from any 4xx/5xx response (handles `{"error": {"message": ...}}`, `{"error": "..."}`, and `{"message": "..."}` shapes) and raises `RuntimeError("OpenRouter API error {status}: {message}")` so callers can pattern-match on the text.
  - `_parse_openai_response()` now checks for a top-level `error` field *before* looking at `choices`. If found, raises `RuntimeError("OpenRouter provider error: {message} (code={code})")`. Also raises on missing `choices` instead of silently returning empty.
  - Both errors propagate to the chat loop's existing `except RuntimeError` handler, which prints the real message in red.

#### ReAct Fallback Safety Net for Free Models
- **Bug**: Some `:free` / fine-tuned models on OpenRouter reject the `tools` field at runtime with HTTP 400 ("Model does not support tools"), even though the cloud provider nominally supports native tool calling. The original code had no fallback for this case.
- **Fix**: `generate()` now detects "does not support tools" / "tools are not supported" / "function calling is not supported" in the error message and automatically retries the request once *without* the `tools` field. This lets the model fall back to text-format (ReAct) tool calls that the Agent's `ToolParser` can still parse from the response content. The fallback is logged in debug mode.

#### OpenRouter 429 Rate Limit Retry (`plugins/openrouter/openrouter.py`)
- **Bug**: When the upstream provider returned HTTP 429 (rate limited), `_make_api_request()` immediately raised a `RuntimeError`. The user saw a rate-limit error or, worse, a blank "Agent Nova: " response if the 429 came back as a provider-side error field with HTTP 200.
- **Fix**: `_make_api_request()` now retries up to 3 times on HTTP 429, honoring the `Retry-After` header (capped at 60 seconds to avoid hanging). In debug mode, prints: `[OpenRouter] 429 rate limited (attempt 1/4): <message>. Retrying in 10s...`. Only after exhausting all retries does it raise the `RuntimeError` that surfaces as `Error: OpenRouter rate limit: ...` in the chat loop. This dramatically reduces the number of "empty response" errors the user sees with `:free` models that have aggressive rate limits.

#### Empty Response Detection (`plugins/openrouter/openrouter.py`, `cli.py`)
- **Bug**: When the model returned empty/whitespace-only content with no tool calls (often caused by provider-side rate limiting, content filtering, or model issues), the agent silently accepted it as a final answer and the user saw a blank `Agent Nova: ` line.
- **Fix — Backend layer**: `generate()` now raises `RuntimeError("OpenRouter returned an empty response (no content, no tool_calls)...")` when the API response has no content AND no tool calls. This surfaces as a visible error in the chat loop instead of a silent blank.
- **Fix — CLI layer**: The chat loop now checks `result.final_answer` after the agent run completes. If empty/whitespace, displays:
  ```
  Agent Nova: (empty response)
    The model returned no content. This is likely a rate limit (429) or content filter.
    Try again in a few seconds, or use /debug to see what happened.
  ```
  instead of the blank `Agent Nova: ` line.

### Added

#### Chat Mode Status Footer — Restored with 2-Line Scroll Region (`cli.py`)
- **History**: R05.1 introduced a persistent emoji status footer bar drawn below the `You:` input prompt every turn, showing version (⚛️), model (🧠), prompt size (📝), context window (📦), max response tokens (💬), temperature (🌡️), backend (🔌), and cumulative session token usage with ↑/↓ arrows (📈). R05.2 removed it because the ANSI cursor-up rendering approach caused visual stacking — each turn's footer was never erased from terminal scrollback, accumulating one extra line per turn.
- **R05.4 Attempt 1**: Printed the footer once per turn after the agent's response. This prevented stacking, but old footer text still appeared in the chat log as it scrolled by — the user wanted ONLY the current footer visible at all times.
- **R05.4 Final Fix**: Uses a terminal **scroll region** (DECSTBM — `Set Top and Bottom Margins`) to reserve the bottom **two** lines of the terminal for the footer (the single-line footer was getting cut off on narrower terminals):
  - **Line 1**: version (⚛️), model (🧠), prompt size (📝), context window (📦), max response tokens (💬), temperature (🌡️)
  - **Line 2**: backend (🔌), session token usage (📈 ↑in ↓out), debug indicator (🐛)
  - On chat start: `\033[1;{height-2}r` sets the scroll region to lines 1 through (height-2). The bottom 2 lines are excluded from scrolling and reserved for the footer.
  - The conversation (user input, agent responses, slash command output) all scroll within the region above. The footer stays fixed at the bottom.
  - The footer is redrawn in place via save-cursor (`\033[s`), move-to-footer-line, clear-line (`\033[2K`), write-footer, restore-cursor (`\033[u`). No footer text EVER enters the scrollback history — exactly one footer (2 lines) visible at all times.
  - On every exit path (quit, EOF, Ctrl+C, exception): a `try/finally` block calls `_teardown_footer_region()` which resets the scroll region (`\033[r`) and clears both footer lines, so the terminal is never left in a broken state.
  - `_position_for_input()` moves the cursor to the bottom of the scroll region (one line above the footer) before each `input("You: ")` call, so the prompt always appears in the right place.
  - Handles terminal resize: re-queries `shutil.get_terminal_size()` on each footer update and re-establishes the scroll region if dimensions changed.
  - Graceful fallback: if stdout is not a TTY (piped output) or the terminal is smaller than 6 lines, the scroll region is skipped entirely — no garbage ANSI codes in piped output.
  - Footer is refreshed at the top of each loop iteration (before `input()`) and after each agent response (to update token counts).
  - Backward-compatible `_footer_text()` wrapper kept for legacy tests that expect a single function.

#### Security Mode Toggle (`core/helpers.py`, `cli.py`, `shared_args.py`)
- **Feature**: Added a runtime-toggleable security mode with two settings:
  - `max` (default) — all security checks enabled: shell injection patterns, blocked commands, path traversal, SSRF protection
  - `off` — ALL security checks disabled: the model can run any command, read/write any path, and fetch any URL
- **Implementation**:
  - New `SecurityMode` type (`Literal["max", "off"]`) and global `_security_mode` flag in `core/helpers.py`
  - `set_security_mode(mode)` / `get_security_mode()` API for programmatic access
  - `sanitize_command()`, `validate_path()`, and `is_safe_url()` all check the flag and skip ALL checks when mode is `"off"` (empty inputs are still rejected — that's input validation, not security)
  - Exported from `agentkthx.core.__init__` as part of the public API
- **CLI integration**:
  - New `/security` slash command in chat mode: `/security` (show current), `/security max`, `/security off`
  - New `--security max|off` CLI flag for startup (in `shared_args.py`, wired in `_build_agent()`)
  - Security mode now displayed in `/status` output
  - `/help` updated to list the new command
- **Use case**: When using a fine-tuned model that legitimately uses `&&`, `|`, `$()`, etc. in shell commands, the user can switch to `off` mode to allow these without hitting false-positive injection rejections. The default `max` mode preserves the strict security posture for untrusted models.
- **Warning**: `--security off` disables ALL safety checks. Only use when you trust the model and need unrestricted access.

#### CLI Tool-Call Visibility (`_print_agent_steps`)
- **Feature**: The chat loop previously only printed `result.final_answer` — the user saw nothing about what the agent actually *did*. Now a new `_print_agent_steps(result, debug)` helper prints a compact summary of each tool call between the user prompt and the final answer:
  ```
  [1] tool shell {"command": "echo hi"}
      → hi
  [2] tool read_file {"file_path": "/tmp/x"}
      → file contents...
  ```
- **Behavior**:
  - Wired into both `cmd_chat` and `cmd_run`
  - Suppressed when `agent.debug=True` (debug mode already prints verbose step output)
  - Long args (>120 chars) and long results (>200 chars) truncated with `...`
  - No-op when the run had no tool calls (just a text answer)

#### OpenRouter Backend Test Suite (`tests/test_openrouter_backend.py`)
- **39 tests** covering:
  - Response parsing: native tool_calls, object args, malformed args, missing choices, provider error field, text-only response
  - Request body construction: tools included/omitted, optional params, `max_tokens` vs `max_completion_tokens`
  - Error-text matching for ReAct fallback (`_is_tools_not_supported_error`)
  - Full `generate()` flow with mocked HTTP: happy path, fallback retry, unrelated-error propagation, missing finish_reason synthesis, empty response detection, whitespace-only response detection, empty content + tool_calls (valid)
  - `test_tool_support()` returns NATIVE without any API call (cloud provider handles detection)
  - `_print_agent_steps` CLI helper: prints calls when present, silent in debug mode, silent when no tool calls, truncates long args/results
  - Security mode toggle: default mode, set max/off, invalid mode rejection, shell injection allowed/blocked per mode, path traversal allowed/blocked per mode, SSRF localhost allowed/blocked per mode

### Changed

#### OpenRouter `test_tool_support()` Simplified
- **Before**: Returned `NATIVE` for most models, `UNTESTED` for a hardcoded blocklist (`anthropic/claude-3-haiku`). Did not actually probe the model.
- **After**: Always returns `ToolSupportLevel.NATIVE` without any API call. OpenRouter is a cloud aggregator that only exposes models which already support native function calling on their underlying provider, so probing is unnecessary. The signature now accepts `model`, `family`, and `force_test` parameters for API compatibility with other backends (all ignored). The defensive ReAct fallback in `generate()` handles the rare runtime rejection case.

#### Refactored Request/Response Helpers
- **`_build_openai_body()`** — new method that centralizes OpenAI Chat-Completions request body construction. Adds optional fields (`top_p`, `top_k`, `seed`, `n`, `presence_penalty`, `frequency_penalty`, `stop`, `response_format`, `tool_choice`) only when explicitly provided. Keeps `generate()` and `generate_stream()` in sync.
- **`_parse_openai_response()`** — new static method that parses an OpenAI-format response into the dict shape AgentKthx's agent loop expects (`content`, `tool_calls`, `finish_reason`, `usage`, `raw`). Handles arguments as JSON string (OpenAI spec) or object (some providers), with `_raw` fallback for malformed JSON.

### File Changes Summary

| Action | File | Changes |
|--------|------|:-------:|
| Updated | `pyproject.toml` | Version: 0.5.3 → 0.5.4 |
| Updated | `agentkthx/__init__.py` | Version 0.5.3 → 0.5.4, docstring R05.3 → R05.4 |
| Updated | `agentkthx/cli.py` | Docstring R05.3 → R05.4, added `_print_agent_steps()` + 2-line scroll-region footer (`_setup_footer_region`, `_teardown_footer_region`, `_update_footer`, `_position_for_input`), wired into `cmd_chat` + `cmd_run`, added `RuntimeError` handler in `cmd_run`, added `/security` slash command, `--security` flag wiring in `_build_agent()`, `/status` shows security mode, empty-answer detection |
| Updated | `agentkthx/core/helpers.py` | Added `SecurityMode` type, `_security_mode` flag, `set_security_mode()` / `get_security_mode()` / `_security_enabled()`; `sanitize_command()`, `validate_path()`, `is_safe_url()` now skip checks when mode is "off" |
| Updated | `agentkthx/core/__init__.py` | Exported `set_security_mode`, `get_security_mode`, `SecurityMode` |
| Updated | `agentkthx/shared_args.py` | Added `--security max\|off` CLI argument |
| Updated | `agentkthx/plugins/openrouter/openrouter.py` | Rewrote `generate()` (sends tools, parses tool_calls, ReAct fallback, empty-response detection), rewrote `_make_api_request()` (429 retry with Retry-After, extracts upstream error messages), rewrote `_parse_openai_response()` (surfaces `error` field, raises on missing choices), simplified `test_tool_support()` (returns NATIVE without probe), added `_build_openai_body()` + `_is_tools_not_supported_error()` helpers |
| Updated | `README.md` | Title R05.3 → R05.4, added security mode docs, `--security` CLI option, R05.4 feature list |
| Added | `tests/test_openrouter_backend.py` | 39 tests for OpenRouter backend, CLI helper, and security mode |
| Updated | `docs/CHANGELOG.md` | Added R05.4 entry |
| **Total** | **10 files** | **Bug fixes, security mode, 429 retry, 2-line footer, CLI visibility, tests, version bump** |

---

## [R05.3] - 09-11-2026 4:22:00 PM

### Documentation Updates & Version Bump for OpenRouter Plugin Release

Updates all documentation and version banners to reflect the completed OpenRouter plugin implementation. This release is a documentation update that ensures consistency across all user-facing materials following the successful OpenRouter plugin integration in R05.2.

### Updated

#### Version Numbers and Banners
- **pyproject.toml** — Updated version from `0.5.2` to `0.5.3` for release preparation
- **README.md** — Updated title from "R05.2" to "R05.3" and enhanced OpenRouter documentation
- **CLI Banner** — Updated `agentkthx/cli.py` docstring to show "AgentKthx R05.3" 
- **Main Module** — Updated `agentkthx/__init__.py` docstring and `__version__` to "0.5.3"

#### Documentation Enhancements
- **OpenRouter Examples** — Added comprehensive usage examples showing different model types (OpenAI, Anthropic, Google)
- **Environment Variables** — Documented all OpenRouter configuration options with examples
- **Backend Examples** — Updated backend options section to include OpenRouter alongside existing backends
- **Changelog Link** — Added link to docs/CHANGELOG.md in README for easy version tracking

### Configuration

The OpenRouter plugin configuration remains the same as R05.2:
```bash
export OPENROUTER_API_KEY="your_api_key_here"
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
export OPENROUTER_DEFAULT_MODEL="anthropic/claude-3.5-sonnet"
export OPENROUTER_FREE_ONLY="1"
```

### File Changes Summary

| Action | File | Changes |
|--------|------|:-------:|
| Updated | `pyproject.toml` | Version: 0.5.2 → 0.5.3 |
| Updated | `README.md` | Title, OpenRouter docs, changelog link |
| Updated | `agentkthx/cli.py` | Banner docstring |
| Updated | `agentkthx/__init__.py` | Version and docs |
| Updated | `docs/CHANGELOG.md` | Added R05.3 entry |
| **Total** | **5 files** | **Version and documentation updates** |

---

## [R05.2] - 09-11-2026 2:37:37 PM

### OpenRouter Plugin Implementation & Backend API Mode Improvements

Adds the OpenRouter cloud backend as a first-class alternative to Ollama, enabling access to 500+ models from Anthropic, OpenAI, Google, Cohere, and other providers via an OpenAI Chat-Completions compatible API. Implements comprehensive model discovery with 1-hour caching, free model filtering via `OPENROUTER_FREE_ONLY`, and proper error handling for rate limits and authentication. Fixes critical issues with API mode defaults, backend type display, and response parsing.

### Added

#### OpenRouter Plugin (`plugins/openrouter/`)
- **`OpenRouterPlugin`** — complete cloud backend implementation providing access to OpenRouter's API. Located in `plugins/openrouter/openrouter.py` with full plugin manifest (`plugin.json`). Inherits from `OllamaBackend` to reuse proven OpenAI Chat-Completions logic while adding OpenRouter-specific features.
- **OpenAI Chat-Completions API** — OpenRouter only supports the OpenAI Chat-Completions format (`--api openai`). The backend automatically validates this at construction time and raises `ValueError` if an incompatible API mode is attempted.
- **Authentication** — uses `OPENROUTER_API_KEY` environment variable for Bearer token authentication. Added to request headers as `Authorization: Bearer <key>` along with OpenRouter-specific headers (`HTTP-Referer`, `X-Title`) for proper API access.
- **Model Discovery** — `list_models()` queries `GET /models` endpoint with proper authentication and caching. Returns model information including name, size, provider, and context length. Cache timeout is 1 hour (3600 seconds) to balance performance and API usage.
- **Model Catalog** — `OPENROUTER_MODELS` dictionary with metadata for popular models including pricing, context length, and provider information. Used for fallback when the API is unavailable.
- **Free Model Filtering** — `OPENROUTER_FREE_ONLY` environment variable support. When enabled, `list_models()` filters to show only models with ":free" suffix or common free model patterns ("flash", "mini", "haiku", "tiny").
- **Rate Limit Handling** — comprehensive 429 error detection with `Retry-After` header support and descriptive error messages showing recommended wait times. Gracefully handles upstream provider rate limits without hard failures.
- **Authentication Errors** — 401 errors produce clear messages directing users to check their `OPENROUTER_API_KEY` environment variable.
- **CLI Integration** — `--backend openrouter` works across all subcommands (`chat`, `run`, `agent`, `models`). Backend choices dynamically include OpenRouter when the plugin is loaded.
- **Public API Export** — `OpenRouterBackend` exported from `agentkthx.__init__` for use in Python applications.
- **Configuration Variables** — `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_DEFAULT_MODEL`, `OPENROUTER_FREE_ONLY` with environment variable fallbacks.
- **Endpoints**: `GET /models` (model discovery), `POST /chat/completions` (generation).

#### Default API Mode Change (`shared_args.py`, `cli.py`)
- **Global OpenAI Default** — changed default API mode from `"openre"` to `"openai"` for better cloud provider compatibility. Most cloud APIs (OpenRouter, OpenAI, Anthropic) use Chat-Completions format, making this the more practical default.
- **OpenRouter Auto-Detection** — when `--backend openrouter` is used without an explicit `--api` flag, the system automatically defaults to `openai` API mode since OpenRouter only supports Chat-Completions.

#### Backend Type Enumeration Fixed (`plugins/openrouter/openrouter.py`)
- **`BackendType.OPENROUTER`** — added new enum value `OPENROUTER = "openrouter"` to properly identify OpenRouter backends. Fixed footer display to show "🔌 openrouter" instead of "🔌 zai".
- **Proper Inheritance** — OpenRouter backend now correctly implements `backend_type` property returning `BackendType.OPENROUTER` instead of reusing `BackendType.ZAI`.

### Fixed

#### API Mode Default for OpenRouter (`cli.py`)
- **Issue**: OpenRouter backend required explicit `--api openai` flag, failing with confusing error when using default `--api openre`.
- **Fix**: OpenRouter backend now automatically defaults to OpenAI API mode. The `OPENROUTER_API_KEY` environment variable is the only requirement for basic usage.
- **Impact**: Users can now run `agentkthx chat --backend openrouter --model poolside/laguna-xs-2.1:free` without specifying API mode.

#### Backend Footer Display (`plugins/openrouter/openrouter.py`)
- **Issue**: Footer incorrectly showed "🔌 zai" instead of "🔌 openrouter" for OpenRouter backends.
- **Fix**: Corrected `backend_type` property to return `BackendType.OPENROUTER` instead of `BackendType.ZAI`.
- **Impact**: Footer now correctly displays the backend type for better user feedback.

#### Response Parsing (`plugins/openrouter/openrouter.py`)
- **Issue**: Custom `generate()` method returned raw OpenRouter API response, causing empty content display despite successful API calls.
- **Fix**: Modified `generate()` method to parse OpenRouter response and extract content from `choices[0].message.content`, returning the response in AgentKthx's expected format.
- **Response Format**: Returns `dict` with `content`, `tool_calls`, `usage`, and `raw` fields matching OllamaBackend's `generate_completions()` output format.
- **Impact**: Model responses now display correctly in chat mode with proper content extraction.

#### Debug Output Cleanup (`plugins/openrouter/openrouter.py`)
- **Issue**: Debug print statements remained in the code, showing `[DEBUG]` output even without `--debug` flag.
- **Fix**: Removed all `print(f"[DEBUG] ...")` statements from OpenRouter backend methods.
- **Impact**: Clean output in normal operation, debug output only appears when `--debug` flag is explicitly used.

### Configuration

#### Environment Variables
- `OPENROUTER_API_KEY` — OpenRouter API key (required for generation)
- `OPENROUTER_BASE_URL` — OpenRouter API base URL (default: `https://openrouter.ai/api/v1`)
- `OPENROUTER_DEFAULT_MODEL` — Default model name (default: `anthropic/claude-3.5-sonnet`)
- `OPENROUTER_FREE_ONLY` — Set to `1` to filter models to free tier only

#### Usage Examples
```bash
# Basic chat with OpenRouter
agentkthx chat --backend openrouter --model poolside/laguna-xs-2.1:free

# Chat with different model
agentkthx chat --backend openrouter --model openai/gpt-4o

# Run command
agentkthx run "What is 15 * 8?" --backend openrouter --model deepseek/deepseek-chat

# List available models
agentkthx models --backend openrouter

# Free models only
OPENROUTER_FREE_ONLY=1 agentkthx models --backend openrouter
```

### File Changes Summary

| Action | File | Changes |
|--------|------|:-------:|
| Created | `agentkthx/plugins/openrouter/__init__.py` | +23 |
| Created | `agentkthx/plugins/openrouter/plugin.json` | +25 |
| Created | `agentkthx/plugins/openrouter/openrouter.py` | +678 |
| Updated | `agentkthx/shared_args.py` | +1 −1 |
| Updated | `agentkthx/cli.py` | +2 −0 |
| Updated | `agentkthx/__init__.py` | +1 −0 |
| **Total** | **4 files** | **+730 −2** |

---

## [R05.1] - 04-27-2026 8:07:20 PM

### Fixed

#### Missing import (`cli.py`)

## [R05.0] - 04-15-2026 1:54:57 PM

### Plugin System, Ctrl+C Cancellation & Documentation Restructure

The largest architectural change since R04.0. Introduces a full plugin system with manifest-based discovery, topological dependency resolution, and lazy loading. Backends previously compiled into the framework (BitNet, ZAI, ACP, TurboQuant) are now pluggable, reducing the native surface to just Ollama and llama-server. Adds Ctrl+C cancellation at three layers (backend HTTP call, tool execution, agent step loop). Consolidates all documentation under `/docs/` and publishes a Plugin Specification. Version bumped to 0.5.0.

### Added

#### Plugin System (`plugins/_loader.py`, `plugins/__init__.py`)
- **`PluginManager`** — central singleton registry for plugin discovery, loading, dependency resolution, backend registration, CLI extension, and config aggregation. Plugin backends, CLI commands, and config defaults are all registered through the manager and merged transparently with native functionality.
- **Manifest-based discovery** — each plugin ships a `plugin.json` with name, version, type, entrypoint, dependencies, config defaults, and capabilities (`provides.backends`, `provides.cli_commands`, `provides.cli_flags`). Plugins are discovered by scanning `agentkthx/plugins/` for subdirectories containing a manifest.
- **Topological dependency resolution** — the `depends` field in plugin.json is respected via Kahn's algorithm. Missing hard dependencies cause the dependent plugin to be skipped with a warning. Circular dependencies are detected and rejected.
- **Lazy loading** — plugins are loaded on first use. The `_ensure_plugin()` function in `backends/__init__.py` resolves backend names to plugin directories via manifest scan (e.g. `test-backend` maps to `test-plugin`), then loads only the required plugin.
- **`register_backend(name, cls)`** — plugins register backend classes that integrate seamlessly with `get_backend()` and `--backend` CLI choices. Backend name is independent of plugin directory name, enabling a single plugin to provide multiple backends.
- **`register_cli_command(name, handler, setup_parser)`** — plugins add subcommands to the `agentkthx` CLI. Commands are wired into argparse at runtime in `main()` via the stashed `_SubParsersAction`, and plugin commands are marked with a `*` suffix in `--help` output.
- **`register_config_defaults(env_prefix, defaults)`** — plugins contribute default environment variable values that are aggregated by the PluginManager for framework-wide access.
- **`find_plugin_for_backend(backend_name)`** — reverse-maps a backend name to its plugin directory by scanning manifest `provides.backends`, solving the case where the backend name differs from the plugin directory name.
- **Plugin types** — `backend` (registers inference backends), `feature` (extends framework with CLI commands, config), `tools` (future), `hook` (future). Lifecycle: `discover()` → `load(name)` → `register(manager)` → `[active]` → `unregister()` → `unload()`.

#### Plugin: BitNet (`plugins/bitnet/`)
- **`BitNetPlugin`** — extracts the former `BitNetBackend` (269 lines) into a self-contained plugin. Provides the `bitnet` backend via `register_backend("bitnet", BitNetPlugin)`. Config defaults (`BITNET_BASE_URL`, `BITNET_TUNNEL`) moved from `config.py` to `plugin.json`.
- **`plugin.json`** — type `backend`, depends on nothing, provides `bitnet` backend with llama-server mode flag.

#### Plugin: ZAI (`plugins/zai/`)
- **`ZaiPlugin`** — extracts the former `ZaiBackend` (835 lines) into a self-contained plugin. Full ZAI API integration including dynamic model discovery, native function calling, pricing metadata, free-only mode, and auto-fallback on 429/1113 errors. Config defaults (`ZAI_BASE_URL`, `ZAI_API_KEY`, `ZAI_FREE_ONLY`, `ZAI_FREE_FALLBACK_MODEL`) moved from `config.py` to `plugin.json`.
- **`plugin.json`** — type `backend`, depends on nothing, provides `zai` backend.

#### Plugin: ACP (`plugins/acp/`)
- **`ACPPlugin`** — extracts the former `acp_plugin.py` (2397 lines) into a self-contained plugin. Full Agent Control Panel integration for audit logging, session monitoring, and agent telemetry. Config defaults (`ACP_URL`, `ACP_USERNAME`, `ACP_PASSWORD`) moved from `config.py` to `plugin.json`.
- **`plugin.json`** — type `feature`, provides no backends, extends framework with ACP logging capabilities.

#### Plugin: TurboQuant (`plugins/turboquant/`)
- **`TurboQuantPlugin`** — extracts the former `turbo.py` (694 lines) into a self-contained plugin. TurboQuant server lifecycle management (start/stop/status/list), Ollama model registry, GGUF binary header parsing, and KV cache configuration. Config defaults (`TURBOQUANT_SERVER_PATH`, `TURBOQUANT_PORT`, `TURBOQUANT_CTX`) moved from `config.py` to `plugin.json`.
- **`plugin.json`** — type `feature`, provides the `turbo` CLI subcommand.

#### Plugin: Test-Plugin (`plugins/test-plugin/`)
- **Validation plugin** — exercises every plugin system feature: discovery, backend registration, CLI command registration, config defaults, dependency resolution, and lifecycle (register/unregister). Provides the `plugin-test` CLI command and `test-backend` backend for integration testing.
- **`TestBackend`** — minimal stub backend that returns a fixed validation response, implementing all abstract methods from `BaseBackend`.
- **`plugin.json`** — type `feature`, no dependencies, provides `test-backend` backend and `plugin-test` CLI command.

#### Ctrl+C Cancellation (`agent.py`)
- **Three-layer cancellation** — graceful Ctrl+C handling at every level of the agent execution stack:
  1. **Backend HTTP call** — `KeyboardInterrupt` during `backend.generate()` returns a cancelled response dict with `_cancelled: True` and empty content, preventing connection leaks.
  2. **Tool execution** — `KeyboardInterrupt` during `_execute_tool()` marks the tool call as `FAILED`, appends a cancellation step, and breaks the agent step loop. In streaming mode, yields a `RESPONSE_FAILED` SSE event and returns immediately.
  3. **Agent step loop** — cancelled responses from layer 1 are detected via the `_cancelled` flag, appending an error step and breaking the loop.
- **Tool execution re-raise** — `_execute_tool()` now re-raises `KeyboardInterrupt` (was previously caught by `except Exception`), allowing the step loop to handle cancellation cleanly.

#### Documentation
- **`docs/PLUGIN_SPEC.md`** — Plugin Specification v0.1 (462 lines). Covers manifest format (`plugin.json` schema), plugin types, entrypoint contract (`register(manager)` / `unregister(manager)`), lifecycle, dependency resolution, backend registration API, CLI extension API, config defaults API, and a complete example plugin walkthrough.
- **Documentation restructure** — moved `ARCH.md`, `CHANGELOG.md`, `CREDITS.md`, `audit.md`, `brief.md` from repository root to `docs/` directory. All links updated in README.md and ARCH.md.
- **`docs/TESTS.md`** — moved from root to `docs/` to consolidate documentation.

### Changed

#### Backend Registry Simplified (`backends/__init__.py`)
- **Native registry reduced to 2 backends** — only `ollama` and `llama-server` remain hardcoded. All other backends (bitnet, zai) are loaded dynamically via the plugin system.
- **`_ensure_plugin(name)`** — new lazy-loading function that resolves backend names to plugins and loads them on first access. Uses `find_plugin_for_backend()` for reverse name mapping.
- **`get_backend()`** updated — checks native registry first, then delegates to `_ensure_plugin()` + `PluginManager.get_backend_class()` for plugin backends. Error messages distinguish between native and plugin backend failures.
- **Removed**: hardcoded `BitNetBackend`, `ZaiBackend`, `BitNetBackend` imports. These are now provided by their respective plugins.

#### Config Module Decentralized (`config.py`)
- **Plugin-owned config moved to manifests** — `BITNET_BASE_URL`, `BITNET_TUNNEL`, `ZAI_BASE_URL`, `ZAI_API_KEY`, `ZAI_FREE_ONLY`, `ZAI_FREE_FALLBACK_MODEL`, `ACP_URL`, `ACP_USERNAME`, `ACP_PASSWORD`, `TURBOQUANT_SERVER_PATH`, `TURBOQUANT_PORT`, `TURBOQUANT_CTX` now have their defaults defined in each plugin's `plugin.json` rather than in the central config module.
- **Backward compatibility** — config variables are still importable from `config.py` for existing code. They read from environment variables with plugin-defined defaults as fallbacks.
- **Module-level variables kept as thin wrappers** — `BITNET_BASE_URL = os.environ.get("BITNET_TUNNEL") or os.environ.get("BITNET_BASE_URL", "http://localhost:8765")` etc.

#### Public API Exports (`__init__.py`)
- **Removed**: `BitNetBackend`, `ZaiBackend` exports (available via plugin system).
- **Added**: `LlamaServerBackend`, `get_backend_choices` exports.
- **ACP import path updated** — `from .acp_plugin import ACPPlugin` → `from .plugins.acp.acp_plugin import ACPPlugin` (graceful import with fallback).
- **Version bumped**: `0.4.8` → `0.5.0`.

#### CLI Plugin Integration (`cli.py`, `shared_args.py`)
- **Plugin CLI subcommands** — `main()` discovers plugin commands from manifests, adds them as argparse subparsers with `* [plugin]` help text, loads plugins, and dispatches to registered handlers when native command lookup fails.
- **`_subparsers_action` stashed on parser** — `create_parser()` saves the `_SubParsersAction` return value as `parser._subparsers_action` for later dynamic subparser addition. (The private `parser._subparsers` attribute is an `_ArgumentGroup`, not the subparsers registry.)
- **`--backend` choices dynamic** — `get_backend_choices()` now merges native backends with plugin-provided values via `PluginManager._cli_flag_values["--backend"]`.
- **Plugin command help marking** — existing native subcommands that overlap with plugin commands (e.g. `turbo`) have their help text updated via `_choices_actions` to show `* ... [plugin]`.
- **Warning on plugin discovery failure** — replaced silent `except Exception: pass` with a descriptive warning to stderr, aiding debugging.

#### `pyproject.toml`
- **Plugin manifest packaging** — added `"plugins/*/plugin.json"` to `package-data` glob so manifests are included in the wheel distribution. Required for plugin discovery in installed environments.

### Fixed

#### Plugin CLI Subparser Registration (`cli.py`)
- **Bug**: `parser._subparsers` is an `_ArgumentGroup`, not the `_SubParsersAction` that holds `.choices`. The previous fix used `parser._subparsers` to dynamically add plugin CLI subparsers, which caused an `AttributeError` silently swallowed by `except Exception: pass`. Plugin commands like `plugin-test` never appeared in the argparse subparser registry.
- **Fix**: Stash the return value of `parser.add_subparsers()` as `parser._subparsers_action` in `create_parser()`. Use `getattr(parser, "_subparsers_action", None)` in `main()` to access the real `_SubParsersAction` with `.choices`.
- **Bug**: Help text for existing subparsers lives in `_choices_actions`, not on the `ArgumentParser` object. Setting `subparsers_action.choices[name].help` raised `AttributeError` for the `turbo` command, aborting the entire loop before `plugin-test` could be added.
- **Fix**: Iterate `subparsers_action._choices_actions` to find and update help text for existing subcommands.
- **Bug**: Silent `except Exception: pass` masked the above errors, making the issue invisible during debugging.
- **Fix**: Replaced with `except Exception as e: print(..., file=sys.stderr)`.

#### Backend-to-Plugin Name Resolution (`backends/__init__.py`)
- **Bug**: `get_backend("test-backend")` tried to load a plugin directory named `test-backend`, which didn't exist (the plugin is named `test-plugin`). The backend name and plugin directory name are independent, but the loader assumed they were the same.
- **Fix**: Added `PluginManager.find_plugin_for_backend(name)` which scans discovered manifests' `provides.backends` to reverse-map backend name → plugin directory name. `_ensure_plugin()` now calls this before falling back to treating the backend name as the plugin name.

### File Changes Summary

| Action | File | Changes |
|--------|------|:-------:|
| Moved | `TESTS.md` → `docs/TESTS.md` | — |
| Created | `agentkthx/plugins/__init__.py` | +40 |
| Created | `agentkthx/plugins/_loader.py` | +563 |
| Created | `agentkthx/plugins/bitnet/__init__.py` | +20 |
| Created | `agentkthx/plugins/bitnet/bitnet.py` | +63 |
| Created | `agentkthx/plugins/bitnet/plugin.json` | +31 |
| Created | `agentkthx/plugins/zai/__init__.py` | +20 |
| Created | `agentkthx/plugins/zai/zai.py` | +835 |
| Created | `agentkthx/plugins/zai/plugin.json` | +33 |
| Created | `agentkthx/plugins/acp/__init__.py` | +31 |
| Created | `agentkthx/plugins/acp/acp_plugin.py` | +2397 |
| Created | `agentkthx/plugins/acp/plugin.json` | +28 |
| Created | `agentkthx/plugins/turboquant/__init__.py` | +36 |
| Created | `agentkthx/plugins/turboquant/turbo.py` | +694 |
| Created | `agentkthx/plugins/turboquant/plugin.json` | +28 |
| Created | `agentkthx/plugins/test-plugin/__init__.py` | +92 |
| Created | `agentkthx/plugins/test-plugin/plugin.json` | +31 |
| Created | `agentkthx/plugins/test-plugin/test_backend.py` | +52 |
| Created | `docs/PLUGIN_SPEC.md` | +462 |
| Moved | `ARCH.md` → `docs/ARCH.md` | +12 |
| Moved | `CREDITS.md` → `docs/CREDITS.md` | — |
| Moved | `audit.md` → `docs/audit.md` | — |
| Moved | `brief.md` → `docs/brief.md` | — |
| Updated | `agentkthx/__init__.py` | +14 −8 |
| Updated | `agentkthx/agent.py` | +68 −4 |
| Updated | `agentkthx/agent_mode.py` | +8 −0 |
| Updated | `agentkthx/backends/__init__.py` | +121 −46 |
| Updated | `agentkthx/cli.py` | +146 −8 |
| Updated | `agentkthx/config.py` | +89 −62 |
| Updated | `agentkthx/core/openresponses.py` | +8 −0 |
| Updated | `agentkthx/shared_args.py` | +3 −1 |
| Updated | `pyproject.toml` | +5 −1 |
| Updated | `README.md` | +6 −2 |
| Updated | `docs/CHANGELOG.md` | changelog entry |
| Updated | `docs/ARCH.md` | tree update |
| **Total** | **35 files** | **+5805 −131** |

---
