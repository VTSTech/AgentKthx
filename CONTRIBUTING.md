# Contributing to AgentKthx

> AgentKthx is a **stdlib-only** agentic framework — `dependencies = []` in `pyproject.toml`, and that's the load-bearing design decision behind almost every convention in this file. If your contribution would require adding a runtime dependency, please open a Discussion before opening a PR; the answer is almost always "find a stdlib way" or "make it a plugin."

This document is the short version. The long version is `audit/brief.md` — read the **Critical Files Index** and **Known Landmines** sections there before touching anything non-trivial.

---

## Code of Conduct

Be excellent to each other. Concretely:

- Assume good faith; ask before accusing.
- Disagree about code, not about people.
- Zero tolerance for harassment, discrimination, or doxxing.
- Repo owner (VTSTech) has final say on enforcement; bans are reversible on request.

We don't have a separate `CODE_OF_CONDUCT.md` because the above is the whole policy. If the project grows beyond a single maintainer, we'll adopt Contributor Covenant 2.1.

---

## Project ethos

1. **Zero runtime dependencies is the feature.** Every "just use `requests`" suggestion is wrong by default. `urllib`, `json`, `sqlite3`, `ast`, `subprocess`, `socket`, `ipaddress`, `threading`, `weakref` — that's the toolbox.
2. **Hackable over clever.** A 50-line straightforward implementation beats a 10-line metaprogramming trick. The codebase is meant to be read end-to-end by someone learning how agents work.
3. **Defense-in-depth, not sandbox.** The security primitives (`validate_path`, `sanitize_command`, `is_safe_url`, `safe_eval`, `sanitize_tool_output`) are best-effort guards, not isolation boundaries. Document new tools accordingly.
4. **The audit register is canonical.** Findings live in `audit/audit.md` (OPEN) and `audit/deltas.md` (CLOSED/WONTFIX). Cite finding IDs in commit messages and PR descriptions.

---

## Getting started

### Prerequisites

- Python **3.11+** (CI matrix covers 3.11, 3.12, 3.13, 3.14)
- `git`
- An editor that won't fight you on line endings (`.gitattributes` enforces LF)

### Setup

```sh
git clone https://github.com/VTSTech/AgentKthx.git
cd AgentKthx
pip install -e .           # editable install; zero runtime deps so this is fast
pip install -e ".[dev]"    # if you want pytest/black/ruff (see below)
```

There is no `[dev]` extra in `pyproject.toml` — dev tools are listed in `.github/workflows/ci.yml`:

```sh
pip install pytest black ruff
```

### Verify

```sh
python -m pytest tests/ -q          # ~13s, 2608 tests, 16 skipped
ruff check agentkthx/ tests/        # REQUIRED CI check
black --check agentkthx/ tests/     # REQUIRED CI check
```

If any of these fail locally, they will fail in CI. Fix before pushing.

---

## Code style

| Aspect | Convention |
|--------|-----------|
| Python version | 3.11+ — `match`/`case` and `\|` unions OK; **no** PEP 695 generics/`type` aliases, no backslash/multi-line f-string expressions (PEP 701, 3.12-only) |
| Formatter | `black` (defaults; line length 88) |
| Linter | `ruff` (config in `pyproject.toml`) |
| Imports | stdlib only at runtime; `pytest` / `black` / `ruff` are dev-only |
| File naming | `snake_case.py` modules, `PascalCase` classes, `SCREAMING_SNAKE` constants |
| Test files | `tests/test_*.py`; per-release regression files use `tests/test_r07_XX_<topic>.py` |
| Type hints | Encouraged for new public APIs; not enforced (no mypy yet — see TEST-04 audit finding) |
| Docstrings | Required on public classes and on mixin host-contract methods (the "host declares X" pattern) |

### What "stdlib-only" really means

Before adding an `import` statement, ask:

- **`import foo`** — is `foo` in the standard library for Python 3.11+? If yes, fine.
- **`from foo import bar`** where `foo` is a third-party package — **no.** Either find a stdlib equivalent, or move the code into a plugin (plugins may declare their own dependencies in their manifest, but the core package stays pure).
- **`__import__("foo")`** or `importlib.import_module("foo")` for an optional dep — **acceptable** if wrapped in `try/except ImportError` and the failure path degrades gracefully (see `agentkthx/__init__.py` for the pattern: `PersistentMemory`, `ACPPlugin`, `Soul` are all optional-import).

If you genuinely cannot solve the problem with the stdlib, open a Discussion describing the use case. We've found workarounds for HTTP, SSE, SQLite, AST eval, SSRF guards, plugin loading, and process lifecycle — chances are good we can find one for you.

---

## Architecture in one screen

```
cli/commands/*  →  cli/agent_factory  →  Agent (agent.py)
                                             │
               ┌───────────────────────────────┤
               ▼                               ▼
      AgentSetupMixin                  AgenticLoopMixin ── ToolExecutionMixin ── CompactionMixin ── StreamingMixin
      (agent_setup.py)                 (agentic_loop.py)  (tool_execution.py)  (compaction.py)   (streaming.py)
               │                               │                                   │
               ▼                               ▼                                   ▼
       soul/loader.py                  core/error_recovery.py             core/tool_parse.py
               │                               │                                   │
               ▼                               ▼                                   ▼
       core/models.py ◄──── core/helpers.py ◄────────────────────────────── core/api_resilience.py
                                 ▲
                                 │
               ┌─────────────────┴┴─────────────────┐
               │                                    │
       backends/base.py ◄── backends/cloud_base.py ◄── plugins/{zai,openrouter,gemini,openai,huggingface,mistral,pollinations,orcarouter}
               │                                             ▲
               ▼                                             │
       backends/openai_compat.py ◄───────────────────────────┘
               │
               ▼
       backends/ollama.py ◄── backends/llama_server.py ◄── plugins/turboquant (lifecycle: TurboState, _build_command)
```

The full version with file-by-file commentary is in `audit/brief.md`. The one thing to internalize: **the Agent is a 5-mixin composition**, not a god-class. Mixins access the host via `self.X` with a docstring-declared "host contract." If you add a new mixin or change what the host must provide, update every affected docstring.

---

## Critical files — touch with care

These files have outsize blast radius. A bad change here will break many things.

| File | Why it's hot |
|------|--------------|
| `agentkthx/core/helpers.py` (~1,384 LOC) | Imported by **18+ modules**. Any change to `validate_path`, `sanitize_command`, `is_safe_url`, `sanitize_tool_output`, or `normalize_args` affects every tool, every backend, every plugin. |
| `agentkthx/backends/cloud_base.py` | Shared base for **8 cloud plugins**. A bug here is a bug × 8. |
| `agentkthx/cli/agent_factory.py` (~820 LOC) | Touched by every chat startup path (catalog ladder + tool detection + quant detection) AND the model-switch callback. Changes here affect every backend launch. |
| `agentkthx/shared_args.py` (~563 LOC) | CLI flag definitions must be wired in **3 surfaces** (`add_agent_args`, `add_shared_args`, `test` parser) + `parse_shared_args` + `SharedConfig`. Miss one and the flag is silently ignored somewhere. |
| `agentkthx/plugins/_loader.py` (~1,530 LOC) | PluginManager singleton loads at startup. A failure cascades to all backends. |
| `agentkthx/backends/llama_server.py` | The `_agent_internal` exclusion tuple is **duplicated** in `_generate_completion` and `_stream_completion`. Add a new agent-internal kwarg to only one → the other leaks it into `/completion` request bodies. |
| `agentkthx/core/agentic_loop.py` | The unified loop body. Both streaming and non-streaming paths funnel through `_generate_with_retry` (line ~264). New instrumentation should hook there. |

---

## Landmines (read before changing these areas)

1. **`os.kill(pid, 0)` kills on Windows** (ROB-33, `plugins/turboquant/turbo.py:159-183`). On Windows, any signal other than CTRL_*_EVENT is `TerminateProcess`. Do not probe process liveness this way on Windows paths.
2. **`--force-react` is `store_true`** (MAINT-25). `--force-react=False` is an argparse error. If you need an opt-out for the local-backend default-to-ReAct behavior, add a new flag — don't try to extend the existing one.
3. **`Agent.add_tool` is deprecated but silent** (MAINT-16). Use `register_tool` for mid-session tool addition. `add_tool` clears conversation memory for backward compat and emits no `DeprecationWarning`.
4. **`_agent_internal` tuple is duplicated** across two `llama_server.py` methods. If you add an agent-internal kwarg, add it to BOTH.
5. **New CLI param → 3 surfaces + `parse_shared_args` + `SharedConfig`**, use `is not None` (not `or` — see ROB-35), mirror the `_explicit` pin pattern.
6. **`AGENTKTHX_NO_UPDATE_CHECK=1`** skips the 3-request startup check. Don't add new network calls to the startup path without an env-var escape hatch.
7. **`MemoryConfig.max_tokens=0` default** — token tier is opt-in. Don't change this default casually.
8. **BitNet `repeat_penalty=1.3` is a default, not a hardcode.** The kwargs loop in `llama_server.py` deliberately does NOT skip `repeat_penalty`. Don't "restore" the hardcode; explicit kwargs and `/param repeat_penalty` must override it.
9. **`BUILTIN_REGISTRY` todo store is a module-level singleton.** Two `Agent` instances in one process share todos unless `set_todo_session()` is called. Document this if you add new session-scoped tool state.
10. **`normalize_args` strategy 5 matches substrings** (MAINT-03). `{"e": ...}` will bind to `expression`. Prefer exact keys in tool args.
11. **`Memory.sanitize_history` mutates `_messages` in place** (PERF-01). Don't call it inside nested iteration.

The full list with severity is in `audit/audit.md` (37 OPEN findings) and `audit/brief.md` (top 22 in the "Known Landmines" section).

---

## Common contribution types

### Adding a new cloud backend

Subclass `CloudBackend` (~100 LOC). Override:

- Class attributes: `provider_name`, `base_url`, `is_cloud = True`, `auth_header_name`, `auth_header_prefix`
- `_get_models_url()`, `_get_chat_url()`, `_parse_models_response()`, `_parse_chat_response()`
- `test_tool_support(model)` — return a `ToolSupportLevel` (capabilities-first probe preferred over sampled inference)
- `test_thinking_support(model)` — return a `ThinkingSupport`

Register in `backends/__init__.py`. Add a `plugins/<provider>/<provider>.py` shim if you want it auto-discovered as a plugin.

**Beware the retry-loop family** (ROB-29 / MAINT-23, open). The skeleton has not yet been lifted to the base class. If your backend needs retry-on-transient, you'll end up copy-pasting the loop from `mistral/` or `pollinations/`. Please contribute a refactor to lift it into `cloud_base.py` rather than adding a third copy.

### Adding a new local backend

Subclass `BaseBackend` directly. Implement `generate()`, `_generate_stream()`, `list_models()`, `test_tool_support()`, `test_thinking_support()`. Use `urllib.request` for HTTP (see `llama_server.py` for the SSE buffering pattern). Do not add a network library as a dependency.

### Adding a new CLI flag

This is the most error-prone common contribution. The checklist:

1. Add the flag to `add_agent_args()` in `shared_args.py` (covers `chat`, `run`, `agent`).
2. Add the flag to `add_shared_args()` (covers example scripts).
3. Add the flag to the `test` subcommand parser (in `cli/parser.py`).
4. Add the field to `SharedConfig`.
5. Read it in `parse_shared_args()` using `is not None` — never `or` (ROB-35).
6. If the param has an env fallback, use `_env_int` / `_env_float` and document the env var name in the help text.
7. If the param should survive `/model` switches, set an `_explicit` pin in `_build_agent` and clear it on `/param reset`.
8. Add a test in `tests/test_<topic>.py` — for parameter parsing, the convention is one test per flag in a `test_r07_XX_<topic>.py` regression file.

### Adding a new tool

Use `@agent.tool()` decorator or call `agent.register_tool(...)` mid-session. **Do not** use `agent.add_tool()` — it's deprecated, clears memory, and emits no warning (MAINT-16).

Tool args go through `helpers.py:normalize_args` (5 strategies, last is permissive — MAINT-03). Prefer exact param names; the fuzzy matcher is a footgun.

Tool output is automatically wrapped by `sanitize_tool_output` — do not pre-sanitize.

If your tool has side effects (shell, file write, network), mark it sequential via the tool registry so it's excluded from parallel batches. See `tools/builtins.py` for the pattern.

### Adding a plugin

Read `docs/PLUGIN_SPEC.md` and `schemas/v0.2/plugin.schema.json` (note: the schema is declared but not validated — SEC-13 is open — so dict-shape checks in `_parse_manifest` are the actual contract).

Minimum viable plugin:

```
my_plugin/
├── manifest.json    # name, version, hooks, optional sha256
└── __init__.py      # register_<your_thing>() function called by the loader
```

The loader does Kahn topological sort on declared dependencies, then `exec_module`s each plugin. If a sha256 pin is present, it's verified fail-closed BEFORE `exec_module`. If absent, the plugin loads silently (SEC-13).

### Adding a test

- Co-located in `tests/`, named `test_<topic>.py`
- **All current tests are mocked unit tests** — there is no integration tier (TEST-01, open). If you add a test that hits a real network endpoint or real local backend, mark it `@pytest.mark.integration` and add a fixture that's skipped when the endpoint isn't reachable. Don't break the "13-second test suite" invariant.
- For per-release regressions, use `tests/test_r07_XX_<topic>.py` (e.g., `test_r07_19_zai_probe_transient.py`). The naming convention is enforced by convention, not by code.
- Per-release exemplars from R07.19 are the house style: small, focused, one behavior per test, mocked backend.

### Adding a soul or skill

- **Souls** (`agentkthx/soul/` + `agentkthx/souls/`): YAML/markdown persona packages. Mostly safe. Follow the spec in `docs/` (Soul Spec v0.5). If your soul uses ReAct format, ship the `Action:`/`Action Input:` block explicitly — MAINT-24 means custom souls without their own block produce prompts with zero format instructions.
- **Skills** (`agentkthx/skills/`): Python code that runs with full process privileges. Be careful. The bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) are the house style.

---

## Audit workflow

The audit is part of the project, not bolted on. Before adding work:

1. **Read `audit/audit.md`** (37 OPEN findings, ID-indexed, priority matrix). If your work touches an open finding, reference its ID in your PR.
2. **Read `audit/deltas.md`** (77 archived: 70 CLOSED + 7 WONTFIX). Don't reintroduce a closed design decision without discussing it.
3. **The audit tooling is in `agentkthx/skills/codebase-audit/`** — read its `SKILL.md` for the re-audit workflow and the dashboard parser contract.

When you close a finding with your PR, add a delta entry to `audit/deltas.md` with the format used by existing entries (ID, status, closure prose, commit SHA).

---

## Commit messages and PRs

### Commit message format

```
<short summary, imperative mood, ≤72 chars>

<optional body, wrapped at 72, explaining why not what>

<optional footer>
Closes <finding ID or issue #>
```

Examples from the existing history:

```
Fix #13: cloud thinking name heuristics match model segment only

The R07.19 heuristics matched against the full model slug including
vendor prefixes, so `thinkingmachines/*` cached `YES` under
`thinking:<model>` keys incorrectly. Scope the regex to the model
segment after the last `/`.

Closes ROB-13 (audit/deltas.md)
```

### PR checklist

- [ ] Tests pass: `python -m pytest tests/ -q`
- [ ] Lint passes: `ruff check agentkthx/ tests/`
- [ ] Format passes: `black --check agentkthx/ tests/`
- [ ] No new runtime dependencies added (or Discussion approved)
- [ ] If touching a critical file, the change is minimal and explained
- [ ] If closing an audit finding, `audit/deltas.md` updated
- [ ] If adding a CLI flag, wired in all 3 surfaces + `SharedConfig` + `parse_shared_args`
- [ ] If adding a backend, `test_tool_support` and `test_thinking_support` implemented
- [ ] If adding a tool, registered via `register_tool` (not `add_tool`)
- [ ] New tests added; if integration, marked and skip-by-default
- [ ] `audit/brief.md` updated if the architecture map or critical files index changed materially

### PR size

Prefer small, focused PRs. If a PR touches more than ~10 files or adds more than ~500 LOC, expect review to take longer and consider splitting. The exception is a per-release PR that closes a cluster of related findings — those are expected to be larger.

---

## Issue triage

- **Bugs** → GitHub Issues, with a reproducer
- **Feature requests** → GitHub Discussions → "Ideas" category
- **Questions** → GitHub Discussions → "Q&A" category
- **Security reports** → see `SECURITY.md` (do NOT use public issues)

Issues that lack a reproducer or a clear ask will be closed with a "needs more info" label after 14 days.

---

## Release process (maintainer-only, but documented for transparency)

1. All tests pass on `main`
2. Bump version in `pyproject.toml` and `agentkthx/__init__.py` (`scripts/bump-version.sh` automates this)
3. Update `docs/CHANGELOG.md` with R07.XX section
4. Tag: `git tag -s v0.7.XX` (signed; if you don't have a signing key, annotated `git tag -a` is acceptable)
5. Push tag → triggers release workflow → PyPI publish
6. Add delta entries for any findings closed in the release

The release workflow lives in `.github/workflows/`. There is no auto-release on every push; releases are deliberate.

---

## Recognition

Contributors are credited in `docs/CHANGELOG.md` per release. If you'd prefer not to be named, say so in your PR.

---

## Final notes

- The codebase is small enough to read end-to-end. If you're stuck on where something lives, `audit/brief.md` has the map; `grep` is your friend; don't be afraid to ask in Discussions.
- The owner (VTSTech) is a single maintainer with limited bandwidth. PRs that match the conventions in this document will move faster than ones that don't.
- The audit register is the source of truth for known issues. If you find a bug, check the register first — it may already be tracked.

Thanks for contributing.
