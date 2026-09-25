# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-26 | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `acf1d72` (R07.00, PyPI 0.7.0) + R07.01 in-tree fixes (ROB-07, MAINT-06, TEST-02)
> Supersedes: R06.57 brief (2026-09-25) — regenerated because R07.00 deleted or restructured every file the old brief referenced (`agent.py` 3,119-line and `cli.py` 4,079-line monoliths no longer exist).

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable agentic framework + CLI for autonomous agents with local and cloud LLMs (tool use, streaming, plugins, skills) |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib HTTP/SSE/argparse only); pytest/black/ruff for dev |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI) or `pip install -e .` from source; run `agentkthx chat`, `agentkthx version`, etc. (84 CLI flags) |
| **Test Command** | `python -m pytest tests/ -q` → **984 passed, 9 skipped, 0 failed in ~2.5s** (at the R07.01 MAINT-06 + TEST-02 fix commit). CI: `.github/workflows/ci.yml` runs the same command on push/PR against Python 3.12/3.13 (R07.01). |

---

## Architecture Map

```
agentkthx/agent.py        → Agent class = 5-mixin composition (51 lines; was the 3,119-line monolith)
agentkthx/core/           → 21 modules, 9,154 LOC — the engine: mixins, agentic loop, streaming, events
agentkthx/cli/            → 23-file CLI package (8 top-level + 15 commands); __init__.py is a facade
agentkthx/cli/commands/   → 15 command modules (chat, version, models, agent, tools, soul, ...)
agentkthx/plugins/        → 7 plugins: acp, bitnet, gemini, openrouter, test-plugin, turboquant, zai
agentkthx/skills/         → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py
agentkthx/update_check.py → always-live PyPI + GitHub version check (no cache since R07.00)
.github/workflows/ci.yml → GitHub Actions CI (R07.01) — runs full pytest suite on push/PR, Python 3.12/3.13 matrix
agentkthx/skills/skill-creator/scripts/package_skill.py → skill packager with BOM guard (R07.01 MAINT-06)
tests/                    → 35 files, ~11,600 LOC, 988 tests (mocked unit tests + 4 BOM regression tests)
docs/                     → ARCH.md (authoritative architecture map, ~1,871 lines), CHANGELOG, PLUGIN_SPEC v0.1/v0.2,
                            per-provider API references (ZAI, OpenRouter, Gemini, JEV)
audit/                    → this brief.md + audit.md (findings tracker, ID-stable across releases)
```

### Skip List

- `__pycache__/`, `.venv/`, `*.egg-info/`, `.pytest_cache/` — generated
- `docs/old_CHANGELOG.md` — historical
- `docs/*_API_TECHNICAL_REFERENCE.md` — provider API lore; read only when touching a specific backend
- Skill asset files (templates, examples) inside `agentkthx/skills/*/` — instructions-as-docs, not executable package code (exception: `skill-creator/scripts/`)

---

## Critical Files Index

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/agent.py` | `class Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)` :51 | Mixin MRO order is load-bearing; the whole R07.00 design in one readable page |
| `agentkthx/core/streaming.py` | `StreamingMixin` (862 lines) — `_generate_stream_chunks()` :409, `_generate_stream()` :508 | All LLM I/O flows through here; `think` resolution precedence (explicit flag > model-family directive) lives at :428-432; SSE event emission; the R07.00 `ResponseStateEvent` fix landed at :280 |
| `agentkthx/core/agentic_loop.py` | `AgenticLoopMixin` :125 (760 lines) | The agent turn loop — orchestration of generate → parse → tool → observe |
| `agentkthx/core/openresponses.py` | 1,053 lines — `ResponseEvent` family + OpenResponses public API | Event contract for SSE (RESPONSE_STREAMING, RESPONSE_FAILED, ...); `Agent.create_response/get_response/add_tool` are **frozen public API** (zero internal callers, kept by decision, smoke-tested in `tests/test_agent_openresponses_api.py`) |
| `agentkthx/core/helpers.py` | 1,114 lines — largest core module, shared utilities | Appears in most dependency chains; check here before writing new helpers (R07.00 already removed ~1,292 dead lines — don't reintroduce) |
| `agentkthx/core/error_recovery.py` | 909 lines — failure classification + recovery policy | Determines retry vs degrade vs fail behavior for every backend error |
| `agentkthx/cli/agent_factory.py` | `_build_agent(args, config)` :104 (310 lines) | Every CLI flag that changes agent construction lands here — adding a flag? This is the wiring point |
| `agentkthx/cli/parser.py` | 206 lines, 84 flags | `dest=` aliases are load-bearing (e.g. `--thinking` → `_think`); dead-code tools false-positive on them — all 84 flags are genuinely read |
| `agentkthx/cli/commands/chat.py` | Interactive chat REPL + `PARAM_MATRIX` :607 | The `/param` command matrix — per-backend support sets, statically declared |
| `agentkthx/cli/__init__.py` | **Facade** — re-exports every public name from the subpackage | Backward-compat contract: tests monkeypatch *through* this facade; a new command module must be re-exported here or import-compat breaks |
| `agentkthx/update_check.py` | `check_for_update()` — always live | Signature is `{timeout}` only (R07.00 removed cache/force params); queries pypi.org + GitHub main every invocation; per-process result stash lives in `cli/banner.py`, not here |
| `agentkthx/__init__.py` | `__version__ = "0.7.00"` + `_get_git_short_hash()` :39 (verified-remote live lookup → baked `_git_meta` fallback) | Version reporting; suffix resolution is attribution-guarded since R07.01 — see landmine below |

**Rule of thumb**: If a file appears in 3+ dependency chains, it belongs here. `core/helpers.py` and `core/openresponses.py` are the two most-traversed nodes.

---

## Request / Execution Lifecycle

```
1. `agentkthx <cmd>` → console script → cli/main.py → parser.py (84 flags, dest= aliases)
2. main.py dispatches → cli/commands/<cmd>.py
3. Agent commands → cli/agent_factory._build_agent(args, config)
      → Config.from_env() + PluginManager (loads 7 plugins) → backend selection
      → agent.py: Agent(5 mixins) — AgentSetupMixin.__init__ wires tools, parser,
        memory, thinking controls, compaction attrs
4. AgenticLoopMixin drives turns:
      StreamingMixin._generate_stream_chunks(prompt)  → backend HTTP/SSE stream
        (zai / openrouter / gemini / bitnet plugins; OpenAICompatibleBackend
         provides shared 429-retry, num_ctx/32 cap, safe-max-tokens — R06.57 ARCH-03)
      → tool-call parsing → ToolExecutionMixin._execute_tool(tool_name, args)
      → context growth → CompactionMixin compacts when threshold hit
5. Progress/errors surface as core/openresponses.ResponseEvent family
      → SSE-compatible (RESPONSE_STREAMING / RESPONSE_FAILED / ...) — the same
        events the KeyboardInterrupt path must emit (see ROB-08, closed R07.00)
6. `agentkthx version` → update_check.check_for_update() (live PyPI + GitHub),
      banner caches per-process so one invocation = one network round max
```

---

## Dependency Graph

```
cli/main.py → cli/parser.py → cli/commands/* → cli/agent_factory.py → agent.py (Agent)
Agent → core mixins → core/{helpers, models, types, prompts, tool_cache, ...}
plugins/* (self-contained) ← registered/loaded via PluginManager during agent setup
skills/loader.py → skills/*/SKILL.md          (declarative; no code execution)
cli/__init__.py facade ← re-exports cli.* public names; tests monkeypatch THROUGH it
update_check.py ← cli/commands/version.py + cli/banner.py   (standalone; no core deps)
```

Blast-radius notes: `core/helpers.py` and `core/openresponses.py` sit under nearly everything — changes there need the full suite. Plugin changes are isolated (each plugin is self-contained; `test-plugin` exists to validate the harness). The facade means a rename inside `cli/` is a two-file change (module + facade re-export) or tests silently keep patching stale paths.

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| Agent capability | Mixin method on the right mixin — never grow `agent.py` itself |
| CLI compat | New command module ⇒ must re-export in `cli/__init__.py` facade |
| Plugins | `docs/PLUGIN_SPEC.md` (v0.1/v0.2); backends subclass `OpenAICompatibleBackend`; `is_cloud` class attribute gates cloud-only UX (R06.57 MAINT-05 — a new cloud backend is a 1-line change) |
| Events | `ResponseEvent` dataclass family in `core/openresponses.py`; streaming failure paths must emit events, not raise |
| Config | `Config.from_env()` constructor; no mirror fields (removed R07.00) |
| Testing | pytest, fully mocked (no network); monkeypatch via the facade; regression test accompanies every bug fix |
| Versioning | R-style (R07.00) in-repo ↔ PEP 440 `0.7.00` in pyproject; PyPI displays `0.7.0` (normalization — expected) |
| Tooling quirk | ~~15 files under `agentkthx/skills/` carry UTF-8 BOMs~~ **FIXED R07.01**: BOMs stripped, `package_skill.py` enforces the no-BOM invariant, regression tests in `tests/test_no_bom_in_skills.py`. Plain `utf-8` reads work everywhere now. |

---

## Known Landmines

- **Facade monkeypatch contract** — `cli/__init__.py` re-exports are what tests patch. Add a command without re-exporting it and you get confusing test failures or silently stale patches. Import-compat is tested; keep it that way.
- **UTF-8 BOMs in `skills/`** — ~~15 files (mostly `skill-creator/scripts/`, `skills/__init__.py`) started with `\xef\xbb\xbf`~~ **FIXED R07.01**: BOMs stripped, `package_skill.py` now refuses to package BOM-bearing skills, and `tests/test_no_bom_in_skills.py` asserts the invariant. Tooling can now read `skills/*.py` with plain `encoding="utf-8"` (no `utf-8-sig` needed).
- **`_get_git_short_hash()` is attribution-guarded + build-baked (R07.01)** — a discovered `.git` is trusted only when its `remote.origin.url` is `VTSTech/AgentKthx` (walk-up capped at 2 parents); wheels/sdists carry `agentkthx/_git_meta.py` (`SOURCE_COMMIT`, generated by `setup.py` at build time, .gitignored, auto-removed from the tree after sdist builds). Edit `setup.py` to change baking; a new wheel flow that skips `build_py` would silently lose the suffix.
- **Version display mismatch** — code says `0.7.00`, PyPI normalizes to `0.7.0`. The update comparator handles the equivalence correctly (verified live), but humans comparing banner to `pip show` will do a double-take. Intentional; just don't "fix" the comparator.
- **Update check is always live (R07.00)** — every process pays up to 1 timeout per source when offline (PyPI + GitHub). Opt-out env var unchanged. There is no cache to bypass; `version --refresh` was retired.
- **argparse `dest=` aliases** — static analysis reports false-positive unused flags; all 84 are read. Verify with grep before deleting any "dead" flag.
- **`_execute_tool(tool_name, args)`** — 2-arg signature since R07.00 (dead `user_prompt` param removed). Old 3-arg calls fail loudly; tests were updated in the same commit.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Modularization strategy | Split monoliths but keep import-compatible facade (R07.00) | Tests and user monkeypatches survived the restructure untouched |
| Update check | Cache removed entirely — always live (R07.00) | The R06.57 hourly cache hid fresh releases from users (its original bug); simplicity won; supersedes R06.57 FIX-02 |
| Dependencies | Zero runtime deps — stdlib HTTP/SSE | Hackability + no supply-chain surface; deliberate identity of the project |
| Frozen public API | `Agent.create_response/add_tool/get_response` kept despite zero internal callers | Public API stability; now covered by smoke tests instead of deleted |
| Test-only API | `PluginManager` test-facing methods kept | Harness validation uses them; flagged as future judgment calls |
| Dead code | ~1,292 lines excised R07.00 (whole `core/math_prompts.py`, 24 ACP methods, legacy `tool_parse` helpers, ...) | Zero-caller, verified by AST cross-reference + vulture + manual dispatch analysis |

---

## What's Missing / Incomplete

- ~~**No CI** — `.github/workflows/` does not exist; the 988-test suite runs only on maintainer machines~~ **FIXED R07.01**: `.github/workflows/ci.yml` runs `python -m pytest tests/ -q` on push and PR against a Python 3.12/3.13 matrix.
- **No coverage measurement** — no pytest-cov / coverage config anywhere (ARCH-02). With CI now in place, this is a one-line `--cov` flag away from being part of the standard workflow.
- **No integration tests** — every test is mocked; historically some bugs (streaming connection cleanup, R06.57) were caught only by manual VM testing (TEST-01)
- **OpenRouter routing preferences** — no provider-order/routing controls (FEAT-01, open since R06.57)
- **Gemini thought-signature continuation** — multi-turn loops re-derive reasoning (~2-3x token cost); documented as unimplemented at `plugins/gemini/gemini.py:50` (FEAT-03)
- **PEP 639 license migration** — `license = {text = "MIT"}` in pyproject emits a setuptools deprecation warning on every build (MAINT-07)

---

## Quick Start for Developer

1. Read the Critical Files Index above — start with `agent.py` (one page tells you the whole design), then `core/streaming.py` for the I/O path
2. Understand the Execution Lifecycle — that is the system
3. Check Known Landmines — especially the facade contract and BOM quirk before writing tooling
4. Follow Patterns & Conventions — mixin methods, facade re-exports, plugin spec
5. Run `python -m pytest tests/ -q` before and after any change — 2.5 seconds, no excuse; expect exactly 984 passed / 9 skipped at the R07.01 MAINT-06 + TEST-02 fix commit. CI runs the same command on push/PR.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. `docs/ARCH.md` is the deep-dive companion when you need module-level detail.
