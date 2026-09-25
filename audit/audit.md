# Improvement & Enhancement Audit

**AgentKthx v0.7.00 (R07.00)**

**Repository:** https://github.com/VTSTech/AgentKthx
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-26
**Status:** 8 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST

> **R07.01 delta (2026-09-26, unreleased):** Two findings closed. **MAINT-06** (Low): the 15 UTF-8 BOM-bearing files under `agentkthx/skills/` were stripped in place, `EXCLUDED_DIRS` was hoisted to module scope in `package_skill.py`, and a new `_find_bom_python_files()` guard now refuses to package any skill containing a BOM-bearing `.py` file (with a clear `sed -i '1s/^\xef\xbb\xbf//'` fix message). Four new regression tests in `tests/test_no_bom_in_skills.py` assert (a) no shipped `.py` under `skills/` begins with the BOM, (b) the packager rejects a BOM-bearing skill, and (c) the packager still accepts clean skills. **TEST-02** (Medium): `.github/workflows/ci.yml` now runs the full pytest suite on push and PR against a Python 3.12/3.13 matrix — the 988-test suite that previously executed only on maintainer machines now runs on every change, with `cancel-in-progress` concurrency to keep PR feedback fast. Suite: 980 → **984 passed / 9 skipped** in ~2.5s.
>
> **R07.00 delta (2026-09-26):** The two highest-severity structural findings are now **closed**. **MAINT-01** (High): the 4,079-line `cli.py` monolith is now a 23-file `agentkthx/cli/` package (largest file 310 lines) behind an import-compatible facade in `cli/__init__.py`. **MAINT-04** (Medium): the 3,119-line `agent.py` god-class is now a 51-line five-mixin composition (`AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin`) in `agentkthx/agent.py`. **PERF-03** (Low): the dead `think` parameter is gone — `_generate_stream()` takes no arguments and think-resolution precedence (explicit flag > model-family no-think directive) is explicit at `core/streaming.py:428-432`. New finding **ROB-08** was found and fixed within the cycle: `core/streaming.py:280` referenced `ResponseStateEvent` without importing it, so a KeyboardInterrupt during tool execution in streaming raised `NameError` instead of emitting the clean `RESPONSE_FAILED` SSE event — fixed with a regression test that reproduces the original failure. The R07.00 dead-code sweep removed ~1,292 verified zero-caller lines (whole `core/math_prompts.py`, 24 ACP plugin methods, legacy `tool_parse.py` helpers, dead params/fields) while deliberately preserving frozen public API (now smoke-tested). The update-check cache was **removed by design** (always-live results; `version --refresh` retired) — this supersedes R06.57's FIX-02. Test suite grew 822 → **963 passed / 9 skipped**. Version published to PyPI as `0.7.0` (PEP 440 normalization of 0.7.00). New findings this cycle: ROB-07, MAINT-06, MAINT-07, MAINT-08, MAINT-09, TEST-02. ROB-07 was subsequently fixed in the working tree (R07.01, unreleased) — see its entry below.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
  - [Security](#security)
  - [Robustness](#robustness)
  - [Maintainability](#maintainability)
  - [Performance](#performance)
  - [New Features](#new-features)
  - [Architecture](#architecture)
  - [Testing](#testing)
- [Priority Matrix](#priority-matrix)
- [Architecture Strengths](#architecture-strengths)

---

## Executive Summary

This audit covers AgentKthx at commit `acf1d72` (v0.7.00, published to PyPI as 0.7.0), with R07.01 in-tree fixes (unreleased) tracked in this document. At the R07.01 in-tree state: 114 Python files / 44,386 lines in the package plus 35 test files / ~11,600 lines, reviewed following the R07.00 modularization, dead-code cleanup, and update-check cache removal, plus the R07.01 ROB-07 attribution-guard fix and the MAINT-06 BOM strip + TEST-02 CI workflow. The suite passes **984 / 9 skipped in ~2.5 seconds** at the MAINT-06 + TEST-02 fix commit (980 from R07.01 ROB-07 + 4 new BOM regression tests). The headline is positive: both structural findings that dominated prior audits — the CLI monolith (MAINT-01, High) and the agent god-class (MAINT-04) — were closed by R07.00, and R07.01 closes the two highest-leverage near-term findings identified by the R07.00 audit (MAINT-06 and TEST-02). Eight findings remain open: three Medium (absent integration tests, OpenRouter routing preferences, Gemini thought-signature continuation) and five Low (PEP 639 license, version display inconsistency, test-only `PluginManager` API surface, hardcoded `/param` matrix, no coverage measurement). The next highest-leverage move is now TEST-01 (a thin record/replay integration tier) — with CI landed, an integration-test group is one CI matrix slot away from being part of the standard verification surface, and ARCH-02 (coverage measurement) is now nearly free to add to the same workflow. A recurring pattern worth noting: this codebase's remaining risk is concentrated in *process* (testing, reporting, packaging hygiene) rather than *structure* — the R-series discipline of audit-tracked findings with closure deltas is itself working and should continue.

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| ~~ROB-07~~ | ~~Medium~~ | Robustness | ✓ FIXED R07.01 (in tree) | `_get_git_short_hash()` attaches unrelated parent-repo hash to version string |
| ~~TEST-02~~ | ~~Medium~~ | Testing | ✓ CLOSED R07.01 (in tree) | No CI — 988-test suite runs only on maintainer machines |
| ~~MAINT-06~~ | ~~Low~~ | Maintainability | ✓ CLOSED R07.01 (in tree) | 15 files under `agentkthx/skills/` carry UTF-8 BOMs |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all tests are mocked unit tests |
| FEAT-01 | Medium | New Features | OPEN | No provider routing preferences for OpenRouter |
| FEAT-03 | Medium | New Features | OPEN | Gemini thought-signature stateful continuation not implemented |
| MAINT-07 | Low | Maintainability | OPEN | `license = {text = "MIT"}` emits PEP 639 deprecation warning on every build |
| MAINT-08 | Low | Maintainability | OPEN | Version display inconsistency: 0.7.00 in-repo vs 0.7.0 on PyPI |
| MAINT-09 | Low | Maintainability | OPEN | Test-only `PluginManager` API surface kept in production code |
| FEAT-02 | Low | New Features | OPEN | `/param` matrix hardcoded; Gemini excluded from some parameters |
| ARCH-02 | Low | Architecture | OPEN | No coverage measurement configured |
| ~~MAINT-01~~ | ~~High~~ | Maintainability | ✓ CLOSED R07.00 | `cli.py` 4,079-line monolith → 23-file package behind facade |
| ~~MAINT-04~~ | ~~Medium~~ | Maintainability | ✓ CLOSED R07.00 | `agent.py` 3,119-line god-class → 51-line 5-mixin composition |
| ~~ROB-08~~ | ~~Medium~~ | Robustness | ✓ CLOSED R07.00 | `ResponseStateEvent` NameError in streaming KeyboardInterrupt path |
| ~~PERF-03~~ | ~~Low~~ | Performance | ✓ CLOSED R07.00 | Dead `think` parameter on `_generate_stream()` |

Severity levels: **High** — correctness/security/data integrity. Medium — reliability, DX, or feature-gap impact. Low — nice-to-have.

---

## Detailed Findings

### Security

No findings in this category this cycle. This pass focused on architecture, maintainability, and robustness; no new externally reachable input surfaces were introduced in R07.00 (the release was restructural). The update checker's only outbound traffic is HTTPS to `pypi.org` and `github.com`, both parsed as JSON with size-bounded reads. Prior-cycle security findings were closed before R06.57 and remain closed. This is an observation of scope, not a clean bill of health — a dedicated security pass (plugin loading, ACP surface, eval paths) has not been re-run against the post-R07.00 module layout and should accompany any future work on `core/safe_eval.py` or the ACP plugin.

### Robustness

#### ~~ROB-07: `_get_git_short_hash()` attaches unrelated parent-repo hash to version string~~ ✓ FIXED R07.01 (in tree)

| Property | Value |
|----------|-------|
| **Severity** | Medium (at time of fix) |
| **Category** | Robustness |
| **File(s)** | `agentkthx/__init__.py`, `setup.py` (new) |

The version helper walks up to five parent directories looking for a `.git` folder, then runs `git rev-parse --short HEAD` and appends the result to `__version__`. When the package is executed from a location nested inside an *unrelated* git repository, it picks up that repository's hash. This was observed in practice during this audit cycle: a pip `--target` install executed under a different repository's tree reported itself as `R07.00-db152d9`, where `db152d9` belongs to the parent directory's repo, not AgentKthx. The consequence is corrupted version information in bug reports — the exact string maintainers ask users to paste — and it cannot be reproduced or resolved by the maintainer. The walk-up exists to serve source checkouts (running from a cloned repo should show the commit), so the fix constrains attribution. **Fixed in the working tree (R07.01, unreleased):** the resolver now (1) verifies `remote.origin.url` points at `VTSTech/AgentKthx` (https or ssh) before trusting any discovered `.git`, (2) caps the walk-up at 2 parents, (3) marks dirty trees via `git describe --always --dirty` (`acf1d72-dirty` = "not exactly what is on GitHub"), and (4) falls back to `SOURCE_COMMIT` baked into every wheel/sdist by new `setup.py` build hooks — so PyPI installs and `pip install git+https://…` installs report the exact commit they were built from (pip's temporary clone is discarded after the wheel is built; baking is the only way the hash survives). Verified empirically: a wheel installed inside a foreign git repo (`78b0377`) reports its baked commit `acf1d72`; a wheel built from an sdist (no `.git` present) preserves the baked value instead of clobbering it with None. 17 regression tests in `tests/test_git_meta.py`.

**Impact:** Bug reports stop containing plausible-but-wrong commit hashes, making user-reported issues actually actionable.

#### ~~ROB-08: `ResponseStateEvent` NameError in streaming KeyboardInterrupt path~~ ✓ CLOSED R07.00

| Property | Value |
|----------|-------|
| **Severity** | Medium (at time of fix) |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/streaming.py:280` |

Found and fixed within the R07.00 cycle. The KeyboardInterrupt-during-tool-execution handler referenced `ResponseStateEvent`, which was never imported in the module, so the intended clean `RESPONSE_FAILED` SSE event was replaced by an unhandled `NameError` — a crash while handling an interrupt. The fix imports the event class (matching the established `fail_event` pattern at `:171`), and a regression test reproduces the original failure mode on pre-fix code. The class of bug is worth remembering: except-blocks referencing unimported names fail only when the except path runs, which mocked happy-path tests never exercise. The R07.00 dead-code cross-referencer now also flags module-level names used but not imported, which closes the detection gap.

**Impact:** Interrupting the agent during tool execution now produces the designed failure event instead of a secondary crash.

### Maintainability

#### ~~MAINT-01: `cli.py` 4,079-line monolithic CLI~~ ✓ CLOSED R07.00

The monolith was decomposed into a 23-file `agentkthx/cli/` package: 8 top-level modules (`main.py` dispatch, `parser.py` with all 84 flags, `agent_factory.py`, `banner.py`, `utils.py`, `headers.py`) plus 15 command modules under `cli/commands/`. The largest file in the package is now 310 lines. Critically, the split preserved backward compatibility: `cli/__init__.py` is a facade re-exporting every public name, so existing imports and the test suite's monkeypatching continued to work unmodified across the restructure. This was the highest-severity finding in the R06.57 audit (then marked "worsened +278 lines"); it is now the audit's flagship closure.

**Impact:** CLI changes are now scoped to single-purpose files; the facade keeps a decade of muscle memory and test patches valid.

#### ~~MAINT-04: `agent.py` 3,119-line god-class with near-duplicate core paths~~ ✓ CLOSED R07.00

`agent.py` is now a 51-line composition: `class Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)` in `agentkthx/agent.py`, with the engine living in 21 modules under `agentkthx/core/` (9,154 lines total). The `_run_core()` / `_run_core_streaming()` near-duplication called out at R06.57 was dissolved by the streaming/tool-execution mixin split. MRO order in the composition is now a documented, load-bearing decision.

**Impact:** Agent capabilities are added as mixin methods in scoped modules instead of appending to a god-class.

#### ~~MAINT-06: 15 files under `agentkthx/skills/` carry UTF-8 BOMs~~ ✓ CLOSED R07.01 (in tree)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/skills/__init__.py`, `agentkthx/skills/skill-creator/scripts/*.py` (14 files) |

Fifteen package files began with a UTF-8 byte-order mark. Python itself executed them fine, but tooling did not: during the R07.00 dead-code analysis, `ast.parse` failed on these files until every read was switched to `encoding="utf-8-sig"`, and naive byte-level greps silently missed or mis-anchor matches. Since these files ship in the wheel, any downstream contributor running linters, cross-referencers, or codemods against an installed AgentKthx hit the same friction.

**Fixed in the working tree (R07.01, unreleased):** (1) The 15 BOMs were stripped in place via a one-time `strip_bom_in_skills.py` script (persisted in `/home/z/my-project/scripts/` for traceability — the same script is idempotent and can be re-run as a verification step). (2) `EXCLUDED_DIRS` was hoisted from a local variable inside `package_skill.package_skill()` to a module-level constant so it could be shared between the BOM scan and the zip write loop — they must agree on what "in the skill" means. (3) A new `_find_bom_python_files()` helper in `package_skill.py` scans every `.py` file about to be packaged; if any begins with `bytes([0xEF, 0xBB, 0xBF])`, the packager refuses to produce a `.skill` archive and prints a clear `sed -i '1s/^\xef\xbb\xbf//' <file>` fix message — forcing the source to be fixed rather than silently stripping. The decision to refuse-and-instruct rather than auto-strip is deliberate: silent stripping would let the issue recur on the next save. (4) Four regression tests in `tests/test_no_bom_in_skills.py` assert both the no-BOM invariant on shipped package files and the packager's reject/accept behavior.

**Impact:** Third-party tooling works against the shipped package without silent failures, and the packager prevents future BOM-bearing skills from being distributed.

#### MAINT-07: `license = {text = "MIT"}` emits PEP 639 deprecation warning on every build

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `pyproject.toml:10` |

Every `python -m build` run prints a setuptools deprecation warning because the license is declared in table form. The modern spelling is `license = "MIT"` (a SPDX expression string) with `license-files = ["LICENSE"]`. Purely cosmetic today, but deprecation warnings in builds train maintainers to ignore build output, which is exactly where real packaging errors surface. One-line change; the wheel metadata (`License: MIT`) is unchanged by the migration, so there is no PyPI-side effect.

**Impact:** Clean builds with no ignored-warning noise.

#### MAINT-08: Version display inconsistency: 0.7.00 in-repo vs 0.7.0 on PyPI

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `pyproject.toml:7`, `agentkthx/__init__.py:34`, `agentkthx/update_check.py` |

The project version is `0.7.00` in-repo, but PEP 440 normalization strips the trailing zero on PyPI, where it displays as `0.7.0`. The update comparator handles the equivalence correctly (verified against the live PyPI endpoint this cycle — a 0.7.00 install reports "up to date" against 0.7.0), so this is purely a human-factors issue: users comparing the banner against `pip show` see different strings, and issue reports may reference either. The project's R-style release naming (R07.00) makes the in-repo form intentional. Recommendation: keep the scheme, but add one line to the README's FAQ noting the normalization so the discrepancy is pre-answered — cheaper and more stable than changing versioning conventions mid-stream.

**Impact:** The most commonly asked version question is answered before it is asked.

#### MAINT-09: Test-only `PluginManager` API surface kept in production code

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/plugins/` (PluginManager), `tests/` |

The R07.00 dead-code audit identified a set of `PluginManager` methods with zero production callers that exist solely for the test harness (plugin enumeration and introspection used by the test suite). They were deliberately kept — the harness validates real plugin loading through them — but they remain an API surface with no production consumers, which future refactors may stumble over. The decision and the method list are recorded in the R07.00 progress notes. Recommendation: either annotate the test-facing methods as such in a docstring convention (`#: test-harness API`) or move the assertions to use the public plugin loading path, making the test surface explicit.

**Impact:** Future contributors can tell kept-by-design API from dead code without re-deriving the analysis.

### Performance

No open findings. ~~PERF-03~~ (dead `think` parameter accepted but never forwarded) is **closed R07.00**: `_generate_stream()` now takes no arguments, and think-resolution is explicit, ordered logic in `_generate_stream_chunks()` at `core/streaming.py:428-432` (explicit `--thinking` flag wins; else model-family no-think directives like qwen3/deepseek-r1 force it off). The R06.57 shared-backend work (429 retry, `num_ctx/32` context cap, `_calculate_safe_max_tokens` lifted into `OpenAICompatibleBackend`) remains in place and unchanged by the modularization. The update-check cache removal (R07.00) traded one cached network read per hour for at most one live read per process — a deliberate, documented decision, not a regression.

### New Features

#### FEAT-01: No provider routing preferences for OpenRouter

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Features |
| **File(s)** | `agentkthx/plugins/openrouter/` |

Carried forward from R06.57, unchanged. OpenRouter supports provider routing controls (order, allow/deny lists, fallback preferences) that let users trade price against latency or pin to specific inference providers, but AgentKthx exposes none of them — no routing keys appear anywhere in the OpenRouter plugin. Users who want routing currently cannot get it without writing their own plugin. The friction is real for the project's target audience (cost-sensitive local-LLM users on a cloud fallback). Implementation would be a `/param`-style pass-through of OpenRouter's `provider` routing object on model requests, following the existing PARAM_MATRIX pattern.

**Impact:** Cost- and latency-sensitive users gain provider control without abandoning the CLI.

#### FEAT-02: `/param` matrix hardcoded; Gemini excluded from some parameters

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Features |
| **File(s)** | `agentkthx/cli/commands/chat.py:607` |

Carried forward, unchanged. The `PARAM_MATRIX` in `chat.py` statically declares parameter support per backend, and Gemini is excluded from several entries. Two costs: (1) adding a backend requires editing the matrix by hand, and (2) the matrix can drift from what backends actually support — it is declaration, not discovery. The R06.57 MAINT-05 work replaced hardcoded backend *name lists* with the `is_cloud` attribute; the same discovery principle could apply here (backends declare their supported parameters, the matrix consults them). The Gemini exclusions specifically are worth re-verifying against current Gemini API capabilities, since several stem from pre-2.5 behavior.

**Impact:** Parameter support becomes self-reporting and drift-proof as backends evolve.

#### FEAT-03: Gemini thought-signature stateful continuation not implemented

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Features |
| **File(s)** | `agentkthx/plugins/gemini/gemini.py:50` |

Carried forward, unchanged. The plugin documents (at `gemini.py:50`) that thought-signature stateful continuation is not implemented: multi-turn agent loops re-derive reasoning from scratch each turn instead of resuming from the prior turn's encrypted thought signature, at a measured ~2-3x token cost on reasoning-heavy loops. The capability flags (`supports_thought_signatures`) are already declared in the plugin's model registry, so the plumbing exists on the discovery side — the continuation logic (persisting and replaying signatures across turns) is the missing piece. This is the single largest token-efficiency gap for Gemini users running autonomous agents.

**Impact:** Gemini agent loops stop paying repeated reasoning costs on every turn.

### Architecture

#### ARCH-02: No coverage measurement configured

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `pyproject.toml`, `tests/` |

Carried forward, unchanged. No pytest-cov or coverage configuration exists anywhere in the project. The suite is substantial (963 tests, 11,478 lines of test code) but its reach is unknown — nobody can say which modules are exercised and which are not. The R07.00 bug pattern (ROB-08: an except-path referencing an unimported name, invisible to happy-path tests) is exactly the kind of gap coverage numbers would have made discussable. This becomes nearly free once TEST-02 (CI) lands: add `pytest --cov=agentkthx --cov-report=term-missing` to the workflow and record the baseline, even if no threshold is enforced initially.

**Impact:** The suite's true reach becomes visible, guiding test investment to the actual gaps.

Note on the R07.00 facade: `cli/__init__.py`'s backward-compat re-export contract is a deliberate architectural decision, not a finding — it is documented in `docs/ARCH.md`, tested by the suite, and it successfully carried every import and monkeypatch through the modularization. New command modules must be re-exported there; this is recorded as a landmine in `audit/brief.md` rather than a defect.

### Testing

#### TEST-01: No integration tests — all tests are mocked unit tests

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (34 files) |

Carried forward from R06.57, unchanged in kind. The entire suite mocks backend HTTP; no test makes a real network call or exercises a real SSE stream end-to-end. History shows the cost: the streaming connection-cleanup bugs (ROB-05/ROB-06, closed R06.57) were found by manual VM testing, not the suite, and ROB-08 this cycle was an except-path no mocked test could reach. A thin integration tier — one or two tests per backend hitting a recorded/replayed HTTP fixture (vcr-style) or a local echo server — would cover the response-parsing and connection-lifecycle layers that unit mocks structurally cannot. The suite's 2.1s runtime leaves plenty of headroom for a slower-tagged integration group.

**Impact:** The bug classes that historically escaped to production become suite-detectable.

#### ~~TEST-02: No CI — 963-test suite runs only on maintainer machines~~ ✓ CLOSED R07.01 (in tree)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `.github/workflows/ci.yml` (new) |

The repository had no CI configuration at all — `.github/workflows/` did not exist. The project shipped 16 PyPI releases across 0.6.x–0.7.0 with substantial per-release churn (R07.00 alone: 8 files changed in the final cache-removal commit, on top of a 36-file cleanup), and the only thing standing between a bad push and a published release was a developer remembering to run pytest locally.

**Fixed in the working tree (R07.01, unreleased):** `.github/workflows/ci.yml` runs the full pytest suite on every push to `main`/`master` and every pull request, against a Python 3.12 / 3.13 matrix (matching `requires-python = ">=3.12"` and `[tool.black] target-version`). The workflow installs the package with `pip install -e .[dev]` (zero runtime deps — install is fast and deterministic), uses `actions/setup-python@v5` with `cache: pip` keyed on `pyproject.toml` for fast subsequent runs, and runs `python -m pytest tests/ -q` (the documented dev invocation). `concurrency: cancel-in-progress: true` cancels superseded runs on the same ref to keep PR feedback fast and avoid burning Actions minutes. The workflow is intentionally minimal — a 10-line job — so that the suite (now **988 tests: 984 passed / 9 skipped in ~2.5s** including the four new BOM regression tests) is verified on every change without depending on any one maintainer's machine.

**Impact:** Every push and PR is verified before it can reach a release, independent of any one machine. The highest-leverage single change identified by the R07.00 audit is now in place.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R07.0x)** | ~~TEST-02~~ (closed R07.01 — `.github/workflows/ci.yml`), ~~ROB-07~~ (closed R07.01 — attribution-guarded git hash), ~~MAINT-06~~ (closed R07.01 — 15 BOMs stripped + packager guard), MAINT-07 (one-line PEP 639 license migration) |
| **Short term (R07.1x – R08.0)** | TEST-01 (record/replay integration tier — now has a CI matrix slot waiting for it), FEAT-03 (Gemini thought-signature continuation — largest token-cost gap), FEAT-01 (OpenRouter routing preferences), ARCH-02 (coverage baseline — now rides free on the CI workflow) |
| **Medium term (R08.x+)** | FEAT-02 (self-reporting parameter support), MAINT-08 (README FAQ note on 0.7.00/0.7.0), MAINT-09 (annotate or absorb test-only PluginManager API) |

---

## Architecture Strengths

- **Zero runtime dependencies** (`dependencies = []` in `pyproject.toml`) — a full agentic framework with HTTP, SSE streaming, and a plugin system implemented on the standard library. This eliminates supply-chain exposure entirely and keeps the package installable anywhere Python 3.12 exists; it is the project's defining constraint and it is holding.
- **Mixin composition over god-class** — `agentkthx/agent.py` is 51 readable lines; capabilities live in five scoped mixins under `core/` (setup, compaction, tool execution, streaming, agentic loop). The MRO is the architecture, and it is legible at a glance.
- **The facade contract worked** — `cli/__init__.py` re-exports carried every legacy import path and every test monkeypatch through the R07.00 decomposition of a 4,079-line monolith without a single test rewrite. This is the pattern to keep for any future large restructure.
- **Plugin registry with a written spec** — 7 plugins including `test-plugin`, whose entire purpose is validating the plugin harness; `docs/PLUGIN_SPEC.md` (two versions) documents the contract. The `is_cloud` attribute (R06.57) means a new cloud backend is a one-line change.
- **A suite that gets run** — 984 tests in ~2.5 seconds with zero runtime deps, and as of R07.01 the suite is run on every push and PR via `.github/workflows/ci.yml` (closing the gap where the suite previously ran only on maintainer machines). The speed is a feature: it removes every excuse for skipping the suite before a commit, and the count is tracked per-release in the CHANGELOG.
- **BOM hygiene enforced** — the R07.01 BOM strip in `agentkthx/skills/` is enforced by a packager guard in `package_skill.py` that refuses to ship BOM-bearing `.py` files and four regression tests in `tests/test_no_bom_in_skills.py` that assert both the no-BOM invariant and the reject/accept behavior of the packager. The class of bug — "Python tolerates it, but tooling does not" — is now structurally guarded.
- **Audit discipline as process** — findings carry stable IDs across releases with closure deltas recorded at the top of this document; MAINT-01 was tracked from identification through "worsened" to closure across four releases. The tracker is doing its job.
- **Skills as instructions-as-docs** — bundled skills (including this `codebase-audit` skill) are declarative SKILL.md guidance with no code execution in the load path (`skills/loader.py`), keeping the attack surface of the skill system minimal.
- **Event-driven streaming contract** — the `ResponseEvent` family in `core/openresponses.py` gives every streaming path (including failure and interrupt paths, post ROB-08) a uniform SSE-compatible vocabulary, and the frozen OpenResponses public API (`create_response/get_response/add_tool`) is now pinned by smoke tests.
- **Honest dead-code hygiene** — R07.00 removed ~1,292 verified-dead lines using AST cross-referencing with explicit keep-lists for frozen API, rather than blind deletion; the KEEP decisions are documented rather than implicit.
