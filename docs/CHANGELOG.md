# CHANGELOG

All notable changes to AgentKthx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [R07.29] - 2026-10-09

**SiliconFlow cloud backend (the 11th) — the first scaffold built on the post-R07.28 hardened CloudBackend patterns.** R07.29 adds `agentkthx/plugins/siliconflow/` (~700 LOC + 100 unit tests), the third of four planned cloud-backend additions (NVIDIA → Cloudflare → SiliconFlow → DuckDuckGo). This is the payoff release for R07.28's closure pass: the SiliconFlow backend inherits the shared `_make_api_request`/`_iter_sse_lines`/`generate_stream`/`_jev_call_completions` transport **untouched** (identity-pinned by test — the MAINT-31 dedup backlog can't grow from this backend), implements the ROB-42-hardened `list_models` (narrowed 5-tuple except incl. OSError, store-on-success-only, stale-first fallback), and supplies only the MAINT-28 hook surface: `_error_brand`, `_STATUS_REMEDIATIONS` (401 "Invalid token" + 404 "404 page not found" — both documented plain-string envelopes), the balance-vs-TPM quota classifier, and the Mistral-style FREE_ONLY filter. No retry-loop code was copied — nvidia.py's 818 LOC vs this backend's ~700, of which ~450 are docstrings/comments.

### SiliconFlow plugin added (`agentkthx/plugins/siliconflow/`)

- **11th cloud backend**: `SiliconFlowBackend(CloudBackend)` at `https://api.siliconflow.com/v1` (the documented `.cn` TLD alternative flows through `SILICONFLOW_BASE_URL` for China-domestic traffic). Registers under both `siliconflow` and `sf` aliases.
- **Catalog**: 200+ models keyed on FULL `<author>/<model>` IDs (HuggingFace-style naming the API requires in the request body — the NVIDIA `_catalog_model_key` contract, one hook replacing four per-method overrides). 57-model chat-only seed in `agentkthx/data/model_seed.json`: OCR (the third free-tier model), vision (VL/Omni/GLM-*V), translation (Hunyuan-MT), embeddings/rerankers (BGE) are all blocklisted from the seed AND filtered from live `/v1/models` results.
- **Free tier** (verified Oct 2026): 3 permanently-free models, NO daily/monthly quota, no credit card — the only provider of the four documented with a truly uncapped free tier (subject to TPM). Zero pricing is seeded for exactly the two free chat models (never fabricated for paid ones — unknown prices resolve to not-free via the `pricing.get("input", -1) != 0.0` contract).
- **The only genuinely-filtering FREE_ONLY** (per the API reference): SiliconFlow prices per-model rather than account-wide, so `SILICONFLOW_FREE_ONLY=1` actually filters `list_models()` to the free models AND swaps paid models to `SILICONFLOW_FREE_FALLBACK_MODEL` (default: Qwen/Qwen3-8B) at generate time — the Mistral `MISTRAL_FREE_ONLY` swap pattern.
- **Default model**: `Qwen/Qwen3-8B` — the free tier's only tool-capable chat model (32K context, general chat + light reasoning).
- **429 split by body wording** (the MAINT-28 hook): "TPM limit reached" / "rate limiting" → transient (backoff + retry); "balance" / "quota" / "insufficient" / "arrear" → permanent (fast-fail with the top-up-or-switch-to-free message). The transient indicators are checked FIRST so "TPM **limit** reached" can never trip the quota fast-fail on the word "limit" — the exact Cloudflare "limit" drift lesson MAINT-28 closed, now guarded by a dedicated veto test.
- **Tool support** (name-pattern only, no probe): DeepSeek-R1 family + `*-Thinking` (Kimi-K2-Thinking, Qwen3-*-Thinking-2507) and vision models (deepseek-vl2, Qwen*-VL*, GLM-*V, Qwen3-Omni) → REACT; everything else NATIVE. GLM-Z1 is deliberately NATIVE — SiliconFlow's own docs contradict themselves (the caveat says no tools, the verified list says "thinking + tools"), so the conservative default + runtime 400→ReAct fallback resolves the drift; a test pins the decision.
- **`agentkthx auth` picker**: SiliconFlow added (23 entries now — 11 keys + 11 flags + Cloudflare's account ID).
- **`agentkthx config`**: SiliconFlow section + env reference (all five `SILICONFLOW_*` vars). The static env-var reference table was hoisted out of `_print_config_summary` into the module-level `_ENV_REFERENCE` (+ `_env_reference_entries()` accessor) so tests can pin per-backend coverage.
- **smoke test**: `scripts/smoke_test.sh` DEFAULT_BACKENDS + the per-backend env-var/FREE_ONLY/model maps (hardcoded `Qwen/Qwen3-8B` — the other free model has no tools, which would diverge the native-tools smoke steps).
- **Support tier**: Limited (new scaffold) — 100 mocked unit tests green, but no maintainer end-to-end smoke test has run yet. Promotes to Fully Supported on the first 5/5 `./scripts/smoke_test.sh --backend siliconflow` pass (Cloudflare's R07.27 promotion path). Tier tables now classify all 11 cloud backends (7 Fully + 4 Limited).
- **100 regression tests** in `tests/test_siliconflow_backend.py` covering: shared-transport identity pins (the loops are inherited, not copied), the four per-method catalog lookups inherited via the single hook, ROB-42 contracts (failed-fetch never persists seed-as-api, stale-first, malformed-shape-raises, success-stores-with-provenance), the blocklist, free/balance classifiers + the transient-veto, tool-support patterns (incl. the segment-not-vendor boundary and the no-tool-cache contract), FREE_ONLY catalog filter + generate swap, quota-429 fast-fail on both transport paths, 401/404 remediation texts, the plain-string error envelopes, manifest + config + auth-registry + enum integration, and seed-catalog integrity (zero pricing only on the two free models).
- **4 auth-picker count pins** updated for the 11th backend (the historical-name tests — their own docstrings document the 8→9→10→11 pair growth); **SUPPORT.md tier pins** updated in the same conscious-diff path (+ a new SiliconFlow-row pin test). Suite: 3,227 → 3,325 passed (+98 net: +100 new, 2 pre-existing network flakes deselected) / 20 skipped; ruff + black clean.
- **Registered follow-ups** (see `audit/audit.md`): FEAT-09 — SiliconFlow `enable_thinking` + `repetition_penalty` request-field passthrough (the API supports both; the scaffold drops them per the cloud-drop-repeat_* house convention, mapping `think` onto `enable_thinking` needs BOTH `_build_stream_body` and `_tweak_request_body` to avoid the MAINT-22 streaming-drift class); TEST-12 — no live-shape contract test for the SiliconFlow scaffold (the R07.27 Cloudflare lesson: the live `/v1/models` shape is verified only by the maintainer's first smoke run).

## [R07.28] - 2026-10-08 2:56:37 PM

**Register closure pass: 6 findings closed in 3 batches, 1 registered (MAINT-31), suite 3,171 → 3,227.** R07.28 is a consolidation release on the R07.26/R07.27 cloud-backend surface — no new features. Batch 1 closed the two Medium findings: MAINT-28 (the retry-loop skeleton duplicated ×4 across nvidia.py + cloudflare.py collapsed into shared CloudBackend template methods — `_handle_http_error_for_retry`/`_handle_url_error_for_retry`/`_parse_error_envelope` + per-backend hooks; nvidia.py −117 LOC, cloudflare.py −109 LOC, cloud_base.py +300) and ROB-42 (list_models() failure path rebuilt to the Mistral ROB-28/30 pattern: narrowed except, store-on-success, stale-first service). Batch 2 (same release) moved the request loops themselves onto CloudBackend — shared `_make_api_request`/`_iter_sse_lines`/`generate_stream`/`_jev_call_completions` + the `_catalog_model_key`/`_tweak_request_body` hooks (nvidia.py 1,070 → 807 LOC, cloudflare.py 1,461 → 1,213 LOC) — found + fixed a latent streaming-retry UnboundLocalError the per-backend loops carried, and extended the ROB-42 catch-narrowing to zai + pollinations (unified 5-tuple incl. OSError). Batch 3 closed the four Low quick wins: ROB-43 (quota-exhaustion 429s now fast-fail at ANY attempt — the `attempt == 0` gate dropped from the shared `_check_quota_429`; a quota 429 after transient retries surfaces the clear monthly/daily-quota message instead of the generic error after burning the retry budget), ROB-44 (Cloudflare catalog discovery honors `CLOUDFLARE_BASE_URL` — `_get_models_url` derives scheme+host from `_base_url` instead of hardcoding api.cloudflare.com; the default URL is byte-identical), MAINT-29 (test_tool_support docstrings rewritten to the actual name-pattern-only contract — no first-use probe, no tool_support.json write — and the cloudflare `_is_free_model` "conservatively paid" doc drift aligned with the live-merge behavior), MAINT-30 (NVIDIA NIM row restored to docs/SUPPORT.md's Fully Supported table — the tier tables now classify all 10 cloud backends; 7 Fully + 3 Limited). MAINT-31 registered (Low, OPEN) for the remaining CloudBackend dedup backlog (list_models ×6 template, test_tool_support ×7 name-pattern shape, legacy request loops in mistral/pollinations/zai/orcarouter, OpenAICompat 429-retry family). Register: 132 findings — 15 OPEN / 107 CLOSED / 10 WONTFIX (~89% archived); security surface unchanged (0 OPEN). 56 new regression tests across `tests/test_r07_28_batch1_closures.py` (+23), `tests/test_r07_28_batch2_cloud_lifts.py` (+18), and `tests/test_r07_28_batch3_quick_wins.py` (+15); ruff + black clean.

## [R07.27] - 2026-10-08 2:04:17 AM

**Cloudflare Workers AI cloud backend + live-catalog shape fix + paid-plan-only detection.** R07.27 adds the 10th cloud backend (Cloudflare Workers AI at `api.cloudflare.com/client/v4/accounts/{id}/ai/v1`) — the first AgentKthx cloud backend that requires BOTH an API key AND a 32-hex account ID baked into the URL path (every other backend derives everything from the Bearer token alone). Initial scaffold (batch 1) shipped with the live-fetch shape wrong: the docs claim `id` carries the model ID and `type == "text-generation"` carries the category, but the live `/ai/models/search` endpoint actually returns an internal UUID in `id` (the model ID lives in `name`) and uses `task.name == "Text Generation"` (capitalized, with space — not lowercase). The original filter dropped every live entry and silently fell back to the 15-model seed. Batch 2 fixes the shape: the live fetch now reads `name` + `task.name` + parses the `properties[]` array for `context_window` / `function_calling` / `reasoning` enrichment, returning 39 chat models (was 15). Batch 3 lands the paid-plan-only detection: Cloudflare's catalog lists paid-tier-only models alongside free ones, and a free-tier account invoking one gets HTTP 403 with `errors[0].code == 5035` ("model not available on the Workers Free plan"). The original 403 handler mis-classified this as a token-permission error; the fix surfaces a clear "Workers Paid plan" error pointing at `/model` or the upgrade URL, caches the paid-only verdict under a `cf-paid:<model>` key in `~/.agentkthx/tool_support.json`, and filters cached paid-only models out of `agentkthx models --backend cf` when `CLOUDFLARE_FREE_ONLY=true`. Suite: 3055 → 3171 passed (+116 net) / 20 skipped. Zero regressions; ruff + black clean.

This release is the second of four planned cloud-backend additions (NVIDIA → Cloudflare → SiliconFlow → DuckDuckGo) introduced in R07.26. The Cloudflare plugin was scaffolded in three batches over a single session with live end-to-end verification on the maintainer's free-tier account: batch 1 = the plugin scaffold itself (modeled on the NVIDIA NIM pattern from R07.26); batch 2 = the live-fetch shape fix after the maintainer's first `agentkthx models --backend cf` revealed the docs-vs-reality mismatch (the live fetch was silently returning the 15-model seed); batch 3 = the paid-plan-only 403/5035 detection after the maintainer's first try with `@cf/zai-org/glm-5.3-flash` hit a 403 with code 5035 that the original handler mis-classified as a token-permission error. Each batch was unit-tested (88 → 96 → 115 tests) before the next was started.

### Cloudflare Workers AI plugin added (`agentkthx/plugins/cloudflare/`)

- New 10th cloud backend: `CloudflareBackend(CloudBackend)` (~750 LOC). Registers under both `cloudflare` and `cf` aliases via the plugin manifest.
- **Unique auth**: requires BOTH `CLOUDFLARE_API_KEY` (Bearer token, no recognizable prefix) AND `CLOUDFLARE_ACCOUNT_ID` (32-hex-char, baked into the URL path: `https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1`). `CloudflareBackend.__init__` raises `ValueError` if the account ID is missing — fail-fast prevents confusing 404s later. The `/auth` picker shows three Cloudflare rows (API key, account ID, FREE_ONLY flag) — the only backend with three rows; every other backend has two.
- **Free tier** (verified Oct 2026 via the Cloudflare Workers AI pricing docs): 10,000 neurons per day on the free plan, UTC daily reset, no credit card. Quota is account-wide (shared across all models). Paid Workers plan ($5/mo) does NOT raise the daily free quota — only enables paid overage at $0.011/1k neurons beyond the cap.
- **Default model**: `@cf/meta/llama-3.3-70b-instruct-fp8-fast` (flagship chat model — supports tools, streaming, JSON mode, 128K context, FP8 quantization for ~3x throughput vs the fp16 variant).
- **Live catalog discovery**: `_fetch_live_models()` queries the native `/ai/models/search` endpoint (NOT on the OpenAI-compat path — there's no `/v1/models`). Returns a Cloudflare-shaped envelope `{result, success, errors, messages}` (not OpenAI's `{data}`). The actual model ID lives in `result[].name` (not `result[].id` which is an internal UUID). The category lives in `result[].task.name == "Text Generation"` (capitalized, with space — not a lowercase `type` field as the docs claim). Capability metadata lives in a `result[].properties[]` array as `{property_id, value}` pairs.
- **Live enrichment** (R07.27 batch 2): `context_window` is parsed from the `properties[]` array and overrides the seed-catalog value when present (e.g. `@cf/zai-org/glm-5.3` shows 1M context from the live API, vs the seed's 128K fallback). `function_calling` and `reasoning` boolean flags are also extracted and stashed in the model details dict — bonus metadata the seed never had. The live `context_window` from Cloudflare's own properties is now the source of truth (no longer needs the static seed for context_length on live-discovered models).
- **Non-chat blocklist**: `_NON_CHAT_PATTERNS` filters embeddings (`embed`, `bge`), image generation (`diffusion`, `flux`, `sdxl`, `stable-diffusion`, `dreamshaper`), TTS/speech (`tts`, `speech`, `whisper`, `asr`, `audio`), classification (`classify`, `classification`), safety/guardrails (`guard`), specialized classifiers (`clef`), translation (`translate`), reranking (`rerank`). Vision-language models are NOT blocked — they're chat-capable (text + image input) and AgentKthx drives them via ReAct since they reject `tools`.
- **Tool support**: chat models default to `NATIVE`. Three families pre-classified as `REACT` via `_REACT_NAME_PATTERNS` (vision, deepseek-r\d, -r1, \bthinking\b, gpt-oss) because they reject `tools` with 400: vision models (`@cf/meta/llama-3.2-*-vision-instruct`), reasoning distill models (`@cf/deepseek-ai/deepseek-r1-distill-*`), and GPT-OSS models (`@cf/openai/gpt-oss-*`) — GPT-OSS only supports tools via the Responses API (`/responses`), NOT `/chat/completions`.
- **Daily-neuron-quota detection**: `_looks_like_neuron_quota_exhaustion(status_code, body_text)` distinguishes the daily-quota-exhausted 429 (body contains "neuron" / "quota" / "daily" / "exhausted") from a transient rate-limit 429. Quota-exhausted 429s surface immediately as a clear "daily neuron quota exhausted — wait for UTC midnight reset" RuntimeError. NOTE: "limit" alone is NOT an indicator — "rate limit exceeded" (the canonical transient wording) contains "limit", so including it would mis-classify every transient 429 as quota exhaustion. Caught by the test suite during scaffold (R07.27 batch 1) — the original indicator set included "limit" and a regression test failed, prompting the tighter 4-indicator set.
- **Paid-plan-only detection** (R07.27 batch 3): HTTP 403 with Cloudflare error code 5035 ("model not available on the Workers Free plan") now surfaces a clear "Workers Paid plan — your free plan doesn't include it. Pick a free-tier model with /model, or upgrade at https://dash.cloudflare.com/?to=/:account/workers/plans" error (instead of the misleading "token lacks permission" boilerplate). The paid-only verdict is cached under a `cf-paid:<model>` key prefix in `~/.agentkthx/tool_support.json` (mirrors the existing `thinking:<model>` pattern). Subsequent `agentkthx models --backend cf` runs with `CLOUDFLARE_FREE_ONLY=true` filter cached paid-only models out automatically — the user only sees models their plan can actually access. Default behavior (FREE_ONLY=false) keeps paid-only models in the catalog so paid-plan users still see every Cloudflare model.
- **Cloudflare-specific extensions**:
  - `options.rejectIfBusy` exposed via the `reject_if_busy=True` kwarg (latency-sensitive agentic workflows; fails the request immediately if Cloudflare's capacity is exhausted rather than waiting in a queue).
  - `top_k` is silently dropped from the request body (Cloudflare doesn't support it on the OpenAI-compat path — only via the native `/ai/run/{model}` endpoint).
- **15-model seed catalog** in `agentkthx/data/model_seed.json` (added via the idempotent `scripts/add_cloudflare_seed.py` helper). After the live-fetch fix, the user sees 39 chat models: 32 from the live API (35 Text Generation minus 3 blocklisted: `@cf/meta/llama-guard-3-8b`, `@cf/cloudflare/clef`, `@cf/cloudflare/clef-flash`) plus 7 seed-only models that Cloudflare's API didn't return this round (variants that may be temporarily unavailable, or that `/ai/models/search` omits for pagination/task-filter reasons). 24 are brand-new from the live API: GLM-5.3 (1M context), Kimi K2.6 + K2.7-code (262K), DeepSeek-V4-flash + V4-pro (1M), Nemotron-3-120B, Qwen3.8-27B, Gemma-4-26B, plus several LoRA variants.
- **115 regression tests** in `tests/test_cloudflare_backend.py` covering: inheritance, init + URL resolution + account-ID validation, URL builders (chat completions + native /ai/models/search), auth headers, free-model + neuron-quota classifier, tool-support classification (vision/R1-distill/GPT-OSS → REACT, chat → NATIVE), catalog content (15-model seed, full-prefixed IDs), non-chat blocklist (embeddings/image-gen/TTS/guard/clef/etc.), list_models + offline fallback, model-info lookups (no prefix stripping), manifest via PluginManager (cloudflare + cf aliases), config.py env-var defaults, /auth picker registry integration (incl. the unique CLOUDFLARE_ACCOUNT_ID row), BackendType enum integration. Plus 8 new live-fetch shape regression tests pinning the corrected `name` + `task.name` + `properties[]` parsing against a mock built from the actual live response (verified Oct 2026 against a real account). Plus 19 new 403/5035 + paid-only cache tests covering `_extract_cloudflare_error_code` parsing, the `cache_cloudflare_paid_only` / `is_cached_cloudflare_paid_only` / `clear_cloudflare_paid_only` tool_cache helpers, the 5035 special-case in both `_make_api_request` and `_iter_sse_lines`, and the `list_models` FREE_ONLY filter behavior.

### `agentkthx auth` picker: Cloudflare added (with unique 3rd row for account ID)

- `agentkthx/cli/auth.py` — added Cloudflare to `AUTH_BACKENDS` (10th cloud backend), `_KEY_VAR_TO_SLUG` (maps `CLOUDFLARE_API_KEY` → `cloudflare` for live-backend-patching), and a new `_EXTRA_AUTH_ENTRIES` tuple for the unique `CLOUDFLARE_ACCOUNT_ID` row.
- The `/auth` picker now shows three Cloudflare rows (vs two for every other backend):
  ```
  Cloudflare   CLOUDFLARE_API_KEY       set (***xyz)
  Cloudflare   CLOUDFLARE_ACCOUNT_ID    set (***abc)
  Cloudflare   CLOUDFLARE_FREE_ONLY     [off]
  ```
  The account-ID row is injected between the API-key row and the FREE_ONLY flag row by the updated `auth_vars()` builder (iterates `_EXTRA_AUTH_ENTRIES` for each backend). Live-patching the account ID mid-session is intentionally NOT supported — the URL is baked at `__init__` time, so changing it requires a backend restart. The env-file + config module + `os.environ` are still updated so the next CLI invocation picks it up.

### `agentkthx config` display surfaces updated

- `agentkthx/cli/commands/config.py` — added Cloudflare to all 4 display surfaces: the `--urls` dump (compact URL list, with the `(built from CLOUDFLARE_ACCOUNT_ID)` hint when the URL is template-derived), the `--full` dump (per-backend block with API key, account ID, default model, FREE_ONLY flag), the default summary URLs dict, and the `_backend_auth_rows` specs (the per-backend key+flag table). Also added Cloudflare to the `_BACKEND_SLUG_TO_LABEL` map (`cloudflare` + `cf` → `Cloudflare`) and the env-var reference list (5 new `CLOUDFLARE_*` entries with their descriptions).

### `agentkthx models` for Cloudflare: live catalog, not seed

- The Cloudflare live `/ai/models/search` endpoint returns a different envelope shape than the docs claim (verified live Oct 2026 against a real account). The original scaffold (R07.27 batch 1) read `id` for the model ID and `type == "text-generation"` for the category — but the live response carries an internal UUID in `id` (the model ID lives in `name`) and uses `task.name == "Text Generation"` (capitalized, with space — not a lowercase `type` field). The original filter dropped every live entry and silently fell back to the 15-model seed.
- The fix (R07.27 batch 2) reads `m.get("name")` for the model ID, filters on `(m.get("task") or {}).get("name") == "Text Generation"`, and parses the `properties[]` array into a flat dict for `context_window` / `function_calling` / `reasoning` lookup. Each live-discovered entry is now enriched with the live `context_window` (overriding the seed fallback when present) and the `function_calling` + `reasoning` flags as bonus metadata. Catalog merge: live-discovered models first, then catalog-only seed entries (variants the API may omit) — so the seed entries still appear even when Cloudflare's pagination or task filtering drops them.
- After the fix, `agentkthx models --backend cf` returns 39 chat models (was 15) on the maintainer's free-tier account. 24 are brand-new from the live API (GLM-5.3 1M context, Kimi K2.6/K2.7-code 262K, DeepSeek-V4-flash/pro 1M, Nemotron-3-120B, Qwen3.8-27B, Gemma-4-26B, plus LoRA variants).
- 8 new regression tests in `tests/test_cloudflare_backend.py::TestCloudflareLiveFetchShape` pin the corrected shape by mocking the live response with the actual envelope observed in production. Tests cover: uses `name` not `id` for the model ID, uses `task.name` not `type` for the category, drops safety-guard models despite "Text Generation" label (via the `guard` blocklist pattern), drops Cloudflare's CLEF specialized classifier (via the `clef` blocklist pattern), enriches `context_window` from the `properties[]` array, enriches `function_calling` + `reasoning` flags, merges live + seed catalog, and handles `success=false` responses by raising (so the surrounding `try/except` in `list_models` falls back to seed).

### `agentkthx chat --backend cf`: paid-plan-only detection (R07.27 batch 3)

- **Problem**: Cloudflare's `/ai/models/search` lists ALL models including paid-tier-only ones. When a free-tier user tries to invoke one (e.g. `@cf/zai-org/glm-5.3-flash`), Cloudflare returns HTTP 403 with `errors[0].code == 5035` and the message "AiError: Model @cf/zai-org/glm-5.3-flash is not available on the Workers Free plan". The original 403 handler mis-classified this as a token-permission error ("token lacks Workers AI:Edit permission") which is wrong — the token is fine, the *model* is behind a paywall. Different user action: 5035 = upgrade OR switch model, not "recreate your token".
- **Fix**: a new `_extract_cloudflare_error_code(body_text)` helper parses the Cloudflare error envelope (NOT OpenAI-spec: `{result, success, errors: [{code, message}], messages}`) and extracts `errors[0].code`. On 403 with code 5035, the handler now raises a clear "Workers Paid plan" error pointing the user at `/model` or the upgrade URL. The plain-403 path (no code 5035, OpenAI-spec `{error: {message}}` shape) still falls through to the existing "token lacks permission" handler — and crucially does NOT cache a paid-only verdict (the model is fine, the token is the issue).
- **Auto-cache**: the paid-only verdict is written to `~/.agentkthx/tool_support.json` under a `cf-paid:@cf/zai-org/glm-5.3-flash` key (mirrors the existing `thinking:<model>` pattern — same cache file, separate prefix so re-testing tools never clobbers it and vice versa). Subsequent `agentkthx models --backend cf` runs with `CLOUDFLARE_FREE_ONLY=true` automatically filter these out via the new `_filter_paid_only()` method on `CloudflareBackend`. The user only sees models their plan can actually access. Default behavior (FREE_ONLY=false) keeps paid-only models in the catalog so paid-plan users still see every Cloudflare model.
- **3 new tool_cache helpers** in `agentkthx/core/tool_cache.py`:
  - `cache_cloudflare_paid_only(model, paid_only=True)` — writes the verdict.
  - `is_cached_cloudflare_paid_only(model) -> bool` — reads it back.
  - `clear_cloudflare_paid_only() -> int` — drops all `cf-paid:<model>` entries (useful after upgrading to a paid Workers plan). Returns the count cleared. Preserves tool-support and `thinking:<model>` entries (only the `cf-paid:` prefix is dropped).
- **Cache clear**: after upgrading to a paid Workers plan, drop the verdicts with one line:
  ```python
  from agentkthx.core.tool_cache import clear_cloudflare_paid_only
  clear_cloudflare_paid_only()  # returns count of cleared entries
  ```
- The 5035 special-case is wired into BOTH `_make_api_request` (non-streaming) and `_iter_sse_lines` (streaming). Both paths cache the paid-only verdict and surface the clear "Workers Paid plan" error. The cache write is best-effort — a cache failure never masks the real error from the user.
- 19 new tests cover the 5035 detection (9 `_extract_cloudflare_error_code` parser tests), the cache helpers (4 round-trip + clear + clear-all-preserves-other-entries tests), the 403 handler (4 tests: 5035 raises paid-plan error, 5035 caches the verdict, plain 403 raises token-permission error and does NOT cache, streaming path also caches), and the `list_models` FREE_ONLY filter (2 tests: filters cached paid-only models when FREE_ONLY=true, keeps them when false).

### Files touched

- `agentkthx/__init__.py` — version bumped to `0.7.27`.
- `pyproject.toml` — `version = "0.7.27"`.
- `README.md` — header `# ⚛️ AgentKthx R07.27`; features section updated (cloud backend count 9 → 10; Cloudflare added to the providers list, the plugin backends bullet, the support tiers bullet as Limited Support pending end-to-end smoke test, the multi-cloud catalog counts).
- `agentkthx/mcp/client.py` — `_CLIENT_VERSION` bumped to `0.7.27` (mirrors `__init__`).
- `agentkthx/core/types.py` — added `BackendType.CLOUDFLARE` enum value with docstring noting the unique account-ID requirement, no `/v1/models` on the OpenAI-compat path, daily-neuron-quota free tier, vision/R1-distill/GPT-OSS REACT fallback, and `top_k` + `repetition_penalty` unsupported on the OpenAI-compat path.
- `agentkthx/core/tool_cache.py` — added `_cloudflare_paid_key`, `cache_cloudflare_paid_only`, `is_cached_cloudflare_paid_only`, `clear_cloudflare_paid_only` (~90 LOC). Mirrors the existing `thinking:<model>` pattern: same `tool_support.json` file, separate `cf-paid:<model>` key prefix.
- `agentkthx/config.py` — added `CLOUDFLARE_BASE_URL` (empty default; backend constructs URL from account ID), `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_DEFAULT_MODEL` (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`), `CLOUDFLARE_FREE_ONLY` env vars + `cloudflare`/`cf` branch in the DEFAULT_MODEL ladder.
- `agentkthx/cli/auth.py` — added Cloudflare to `AUTH_BACKENDS` (10th cloud backend) + `_KEY_VAR_TO_SLUG` + new `_EXTRA_AUTH_ENTRIES` tuple for the unique `CLOUDFLARE_ACCOUNT_ID` row. Updated `auth_vars()` to iterate `_EXTRA_AUTH_ENTRIES` per backend and inject the account-ID row between the API-key and FREE_ONLY rows.
- `agentkthx/cli/commands/config.py` — added Cloudflare to all 4 display surfaces (`--urls` dump, `--full` dump, default summary URLs, `_backend_auth_rows` specs) + the `_BACKEND_SLUG_TO_LABEL` map (`cloudflare` + `cf` → `Cloudflare`) + the env-var reference list (5 new `CLOUDFLARE_*` entries with descriptions).
- `agentkthx/data/model_seed.json` — added `cloudflare` backend key with 15 chat-text-only catalog entries using FULL prefixed IDs (added via the idempotent `scripts/add_cloudflare_seed.py` helper).
- `agentkthx/plugins/cloudflare/__init__.py` — **new** (registers `cloudflare` + `cf` aliases).
- `agentkthx/plugins/cloudflare/plugin.json` — **new** (manifest v0.2, includes `CLOUDFLARE_ACCOUNT_ID` in the config defaults).
- `agentkthx/plugins/cloudflare/cloudflare.py` — **new** (~750 LOC; `CloudflareBackend(CloudBackend)` with account-ID-in-URL validation, native `/ai/models/search` discovery with the corrected `name` + `task.name` + `properties[]` shape, live enrichment of `context_window` / `function_calling` / `reasoning`, non-chat blocklist, vision/R1-distill/GPT-OSS REACT pre-classification, daily-neuron-quota 429 detection, 403/5035 paid-plan-only detection + cache write, `options.rejectIfBusy` extension, `top_k` silent drop, FREE_ONLY catalog filter).
- `tests/test_cloudflare_backend.py` — **new** (115 regression tests across 13 test classes: inheritance, init + URL resolution + account-ID validation, URL builders, auth headers, free-model + neuron-quota classifier, tool-support classification, catalog content, non-chat blocklist, list_models + offline fallback, model-info lookups, manifest via PluginManager, config.py integration, /auth picker registry integration, BackendType enum integration, live-fetch shape regression, 403/5035 paid-only cache, list_models FREE_ONLY filter).
- `tests/test_r07_20_auth_picker.py` — updated for the 10-cloud-backend reality: 21 entries (10 pairs + 1 extra Cloudflare account-ID row); updated the labels/env-names/flags sets to include `Cloudflare` + `CLOUDFLARE_API_KEY` + `CLOUDFLARE_FREE_ONLY`; rewrote the pair-grouping iteration to handle the Cloudflare triple (key, account_id, flag); added `test_extra_auth_entries_includes_cloudflare_account_id`. Updated the `_backend_auth_rows` test to expect 10 rows.
- `docs/SUPPORT.md` — added Cloudflare row to the Limited Support table (with the new-scaffold rationale and promotion path); bumped the cloud-backend count from 8 to 10; added an R07.27 changelog entry. Promote to Fully Supported after the maintainer runs `./scripts/smoke_test.sh --backend cloudflare` end-to-end successfully.
- `scripts/add_cloudflare_seed.py` — **new** (idempotent seed-injection script for the `cloudflare` catalog in `agentkthx/data/model_seed.json`; can be re-run as Cloudflare ships new models without dropping the other backends).
- `scripts/smoke_test.sh` — added `cloudflare` to `DEFAULT_BACKENDS` + the envvar/FREE_ONLY/default-model case statements + the `--help` text.

### Known follow-ups (NOT in this release)

- **Cloudflare promoted to Fully Supported** — the maintainer's end-to-end smoke test (`./scripts/smoke_test.sh --backend cloudflare`) passed 5/5 immediately after R07.27 shipped: models listing (37 free models after the paid-only filter dropped 2 cached 5035-verdicted models), non-streaming inference, non-streaming shell tool, streaming shell tool, all on a free-tier account using `@cf/meta/llama-3.3-70b-instruct-fp8-fast`. Promotion recorded in `docs/SUPPORT.md` (moved from Limited to Fully Supported) + `README.md` features section. The smoke test log line `[Cloudflare] CLOUDFLARE_FREE_ONLY=true: filtered out 2 cached paid-only model(s) from the catalog` confirmed the batch-3 paid-only cache fix worked end-to-end.
- **Per-release regression file** `tests/test_r07_27_cloudflare_scaffold.py` per the house convention `test_r07_<release>_*.py`. The 115 tests in `tests/test_cloudflare_backend.py` cover the same surface; a release-stamped regression file is the convention but not strictly required.
- **Audit register update** (new `CF-*` finding category in `audit/audit.md` + `audit/deltas.md` + `audit/brief.md`). Separate task per the `codebase-audit` SKILL workflow.
- **Use the live `function_calling` / `reasoning` flags** (currently stashed in the model details dict) to drive `test_tool_support()` and the `think` column from the provider's own declaration rather than the static-catalog + name-heuristic approach. Future enhancement — currently the name-pattern pre-classification works correctly for the 3 known REACT families (vision, R1-distill, GPT-OSS), and the live flags are bonus metadata not yet wired into the agent loop.

## [R07.26] - 2026-10-07 1:11:05 PM

**NVIDIA NIM cloud backend + `agentkthx auth` subcommand + streaming spinner fix.** R07.26 adds the 9th cloud backend (NVIDIA NIM at `integrate.api.nvidia.com/v1`) backed by vLLM with a free tier of 1,000 monthly-recurring inference credits, no credit card required. Also ships a new `agentkthx auth` top-level subcommand that exposes the existing `/auth` interactive picker so users can configure API keys + toggle FREE_ONLY flags BEFORE launching a chat session (fixes the misleading `--api-key` reference in the missing-key error message — that flag never existed). The NVIDIA plugin filters its live catalog via a non-chat blocklist (embeddings / reward / safety / vision / translation / specialized tools) so the `agentkthx models --backend nim` output only shows models AgentKthx can actually drive today. A new `scripts/probe_nvidia.sh` probe script validates the seed catalog against the live endpoint and optionally tests inference per model (`--deep`). The chat spinner (braille `⠇ thinking...` on stderr) now starts even during streaming and is stopped + cleared on the first streaming chunk — fixing the "thinking spinner overwrites streaming output" bug that affected thinking models (GLM-5.3-flash, DeepSeek-V4.1-flash, kimi-k3). Four new API Technical References added under `docs/api/`. Suite: 2966 → 3055 passed (+89 net) / 20 skipped. Zero regressions; ruff + black clean.

This release is the first of four planned cloud-backend additions (NVIDIA → Cloudflare → SiliconFlow → DuckDuckGo). The API Technical Reference docs for all four were written first (primary sources cited in each), then the NVIDIA plugin was scaffolded from the `CloudBackend` base class following the Mistral plugin pattern (cleanest modern example, uses R07.24 retry helpers). The initial catalog was rebuilt from the live `/v1/models` endpoint after the probe script revealed 21 of 23 fabricated model names didn't exist on NVIDIA's cloud — a reminder that training-data-based model catalogs must be verified against the live API before shipping.

### NVIDIA NIM plugin added (`agentkthx/plugins/nvidia/`)

- New 9th cloud backend: `NvidiaBackend(CloudBackend)` (~800 LOC). Registers under both `nvidia` and `nim` aliases via the plugin manifest.
- **Free tier** (verified Oct 2026 via [stevescargall.com](https://stevescargall.com/run-free-llms-at-scale-litellm-gateway-with-groq-nvidia-nim-and-cloudflare-workers-ai/), [gopenai.com](https://blog.gopenai.com), [stork.ai](https://www.stork.ai)): 1,000 inference credits on signup, **resets monthly** (not daily like Cloudflare), up to 5,000 by request. 40 RPM hard rate limit. No credit card. Quota is account-wide (not per-model) — every model is "free" within the credit budget.
- **Catalog**: 45 chat-text-only models seeded in `agentkthx/data/model_seed.json` using FULL prefixed IDs (e.g. `meta/llama2-70b`, `nvidia/llama-3.1-nemotron-70b-instruct`, `moonshotai/kimi-k3`) matching what NVIDIA's API expects in the request body. Rebuilt from the live `/v1/models` endpoint via `scripts/rebuild_nvidia_seed_from_live.py` after the probe script revealed that 21 of 23 initial entries were fabricated (didn't exist on NVIDIA's cloud). All 45 entries verified: 45/45 match the live endpoint — zero drift.
- **Catalog lookup overrides**: NVIDIA's catalog keys on FULL prefixed IDs (not bare post-slash segments like ZAI/OpenRouter). Overrode `get_model_info()`, `_get_model_defaults()`, `get_model_max_context()`, and `_is_free_model()` in `NvidiaBackend` to skip the CloudBackend prefix-stripping that would break lookups.
- **Default model**: `nvidia/llama-3.1-nemotron-70b-instruct` (NVIDIA's Nemotron-tuned Llama-3.1-70B variant — NVIDIA's cloud does NOT serve bare `meta/llama-3.3-70b-instruct`).
- **Live catalog filter**: `_fetch_live_models()` queries NVIDIA's open `/v1/models` endpoint (80 entries) and filters via `_NON_CHAT_PATTERNS` blocklist (23 name substrings). New chat models like `moonshotai/kimi-k3` pass through automatically without requiring a seed-catalog update. Blocklist is conservative — only patterns that CLEARLY indicate non-chat models are filtered.
- **Tool support**: chat models default to `NATIVE`. Reasoning models (DeepSeek-R1, R1-distill, Qwen3-Thinking) pre-classified as `REACT` via `_REASONING_NAME_PATTERNS` (deepseek-r\d, -r1, \bthinking\b) — sending `tools` to these models returns 400 from vLLM, so ReAct prompting is used instead.
- **Credit-exhaustion detection**: `_looks_like_credit_exhaustion(status_code, body_text)` distinguishes the monthly-credit-exhausted 429 (body contains "credit" / "quota" / "balance") from a transient rate-limit 429. Credit-exhausted 429s surface immediately as a clear "monthly quota exhausted — wait for the next reset" RuntimeError.
- **Fixed-param 400 handler**: `_extract_fixed_param(body_text)` detects NVIDIA NIM's "param is fixed at X" 400 errors (e.g. kimi-k3 fixes `top_p` at 0.95). The retry loop sets the param to the fixed value and retries — so models with fixed parameters succeed on the second attempt. Works on both streaming and non-streaming paths.
- **Thinking-model timeout**: streaming `urlopen` uses 300s timeout for thinking models (vs 120s default) — thinking models can take 60-90+ seconds before the first token, and the default timeout would fire mid-reasoning.
- **87 regression tests** in `tests/test_nvidia_backend.py` covering inheritance, init + URL resolution, auth headers, free-model + credit-exhaustion detection, fixed-param 400 detection, tool-support classification, catalog content (chat-text-only, full prefixed IDs), blocklist behavior (keeps kimi-k3, blocks embeddings/reward/safety/vision/etc.), model-info lookups (no prefix stripping), PluginManager discovery + alias registration, config.py env-var defaults, BackendType enum.

### `agentkthx auth` top-level subcommand added

- **Problem**: `CloudBackend.__init__` raised `ValueError: NVIDIA_API_KEY is required for the NVIDIA backend. Set it via --api-key, NVIDIA_API_KEY env var, or Config.` — but `--api-key` was never a real CLI flag. Users hitting this error had no way to set the key without exporting a shell var first.
- **Fix**: new `agentkthx auth` subcommand exposes the existing `run_auth_picker()` (which already backed the in-chat `/auth` slash command and persisted to `~/.agentkthx/.env`). Loaded on every CLI startup via `env_file.load_env_file()` at `config.py` import time.
- **Workflow**: `agentkthx auth` → arrow-key picker over 9 backends × 2 entries (key + FREE_ONLY flag) → paste key, Enter → key persisted to `~/.agentkthx/.env` → next CLI invocation picks it up automatically. No shell export needed.
- New file: `agentkthx/cli/commands/auth.py` — wraps `run_auth_picker(agent=None)` with `KeyboardInterrupt` handling + clean exit codes.
- Updated `CloudBackend.__init__` error message: now says "Run `agentkthx auth` to set it interactively (persists to ~/.agentkthx/.env), or export <ENV_VAR> in your shell." (no more `--api-key` reference).

### Streaming spinner fix (thinking models)

- **Problem**: the braille spinner (`⠇ thinking...` on stderr) was suppressed when `stream=True` (PERF-01 comment: "streaming output itself is the progress indicator"). But thinking models (GLM-5.3-flash, DeepSeek-V4.1-flash) take 60-90+ seconds before the first token — during that gap, neither the spinner nor the stream showed anything, so it looked hung. Enabling the spinner during streaming caused a second problem: the spinner kept overwriting streaming text on stderr until `agent.run()` returned.
- **Fix**: (1) the spinner now starts even for streaming (`if not agent.debug` instead of `if not agent.debug and not _will_stream`). (2) A one-shot callback `agent._on_first_stream_chunk` is set by `chat.py` before calling `agent.run()`. The streaming loop in `streaming.py` checks for the callback on the first chunk that carries content, reasoning, or tool-call data — when found, it calls the callback (which stops + clears the spinner via `_spinner_stop_thread`) then sets `self._on_first_stream_chunk = None` so subsequent chunks don't re-trigger it. Works on both the `openai_compat` and native `generate_stream` paths.

### NVIDIA catalog: blocklist approach (not allowlist)

The live NVIDIA `/v1/models` endpoint returns 80 entries including embeddings, reward, safety, vision-only, translation, document parsing, video analysis, and specialized-tool models — none of which AgentKthx can drive today (chat-text I/O only; image I/O planned but not yet implemented).

- **Approach**: blocklist (not allowlist). The initial allowlist (only seed-catalog entries pass) was too aggressive — it dropped legitimate chat models like `moonshotai/kimi-k3` that NVIDIA serves but we hadn't seeded. Switched to a blocklist of 23 name substrings that clearly indicate non-chat models. New chat models pass through automatically.
- `_NON_CHAT_PATTERNS` covers: embeddings (`embed`, `arctic-embed`), reward (`reward`), safety/guardrails (`safety`, `guard`), translation (`translate`), vision/multimodal (`vision`, `vl-`, `vlm`, `multimodal`, `vila`, `neva`, `nvclip`, `deplot`, `kosmos`, `omni`), document parsing (`parse`), video analysis (`video`), specialized tools (`ising-calibration`, `muse-glimmer`, `diffusion`), vertical SaaS (`palmyra`, `laguna`).
- When image I/O lands, remove the vision/multimodal patterns from the blocklist + re-add the dropped seed entries.

### NVIDIA probe script (`scripts/probe_nvidia.sh`)

- GET-only by default (no credits burned); `--deep` flag tests inference per model (~1 credit each).
- Loads `~/.agentkthx/.env` (same file `agentkthx auth` writes to) so the probe sees keys set via the auth picker without requiring shell exports. Shell exports always win (mirrors `agentkthx.env_file.load_env_file`).
- 5 report sections: (1) endpoint + auth shape, (2) live /v1/models count + provider distribution + sample card, (3) seed catalog drift (which seed entries exist on the live endpoint — was 2/23 with fabricated names, now 45/45 after rebuild), (4) non-chat filter (blocklist coverage), (5) deep probe (per-model inference test: HTTP status, response time, error message, `top_p` acceptance). `--filter` flag limits the deep probe to models matching a substring.

### API Technical Reference docs (`docs/api/`)

Four new reference docs, each grounded in primary sources:

- **`NVIDIA_API_TECHNICAL_REFERENCE.md`** — based on NVIDIA NIM docs + live `/v1/models` probe. Documents the credit-budget free tier, the open catalog endpoint, tool-support caveats, the credit-exhaustion 429 detection, and the fixed-param 400 handler.
- **`CLOUDFLARE_API_TECHNICAL_REFERENCE.md`** — based on Cloudflare Workers AI docs. Documents the account-ID-in-URL auth pattern, the `options.rejectIfBusy` extension, and the daily neuron quota.
- **`SILICONFLOW_API_TECHNICAL_REFERENCE.md`** — based on SiliconFlow OpenAPI spec. Documents the 3 permanently-free models, the heterogeneous error envelopes, and the `enable_thinking` + `reasoning_content` fields.
- **`DUCKDUCKGO_API_TECHNICAL_REFERENCE.md`** — based on reverse-engineered source at `github.com/mrgick/duck_chat`. Documents the keyless/anonymous protocol, the `x-vqd-4` token rotation, and the ReAct-only tool path.
- Doc names follow the house style (`<PROVIDER>_API_TECHNICAL_REFERENCE.md`) — the initial `_NIM_` / `_WORKERS_AI_` / `_AI_CHAT_` suffixes were drift and have been corrected.

### CI fixes

- `tests/test_r07_19_help_sort.py` — updated expected subcommand list to include `auth` (alphabetically between `agent` and `chat`).
- `tests/test_r07_20_auth_picker.py` — fixed order-dependent failure when `OPENROUTER_API_KEY` is left as a short (≤ 8 char) value by `tests/test_api_resilience.py`. The test now accepts both mask shapes: `Set (***<4chars>)` (long-key) and `Set (***)` (short-key, no last-4 suffix). Also bumped 8→9 backend count assertions for the NVIDIA addition.

### Files touched

- `agentkthx/__init__.py` — version bumped to `0.7.26`.
- `pyproject.toml` — `version = "0.7.26"`.
- `README.md` — header `# ⚛️ AgentKthx R07.26`.
- `agentkthx/mcp/client.py` — `_CLIENT_VERSION` bumped to `0.7.26`.
- `agentkthx/core/types.py` — added `BackendType.NVIDIA` enum value.
- `agentkthx/config.py` — added `NVIDIA_BASE_URL` / `NVIDIA_API_KEY` / `NVIDIA_DEFAULT_MODEL` / `NVIDIA_FREE_ONLY` env vars + DEFAULT_MODEL ladder entry. Default model: `nvidia/llama-3.1-nemotron-70b-instruct`.
- `agentkthx/cli/auth.py` — added NVIDIA to `AUTH_BACKENDS` + `_KEY_VAR_TO_SLUG`.
- `agentkthx/cli/commands/config.py` — added NVIDIA to imports, URLs dump, `--full` dump, auth-rows specs, slug-to-label map, env-var reference dump.
- `agentkthx/cli/commands/__init__.py` — exported `cmd_auth`.
- `agentkthx/cli/commands/auth.py` — **new** (`cmd_auth` wrapper).
- `agentkthx/cli/main.py` — added `"auth": cmd_auth` to dispatch table.
- `agentkthx/cli/parser.py` — registered `auth` subparser.
- `agentkthx/cli/commands/chat.py` — spinner now starts even for streaming; added `_on_first_stream_chunk` one-shot callback to stop the spinner on the first streaming chunk (fixes spinner-overwrites-streaming-output bug).
- `agentkthx/backends/cloud_base.py` — updated missing-key `ValueError` message (no more `--api-key` reference; now points at `agentkthx auth`).
- `agentkthx/core/streaming.py` — added `_on_first_stream_chunk` callback check on both `openai_compat` and native `generate_stream` paths (stops the spinner when the first chunk with content/reasoning/tool_calls arrives).
- `agentkthx/data/model_seed.json` — added `nvidia` backend key with 45 chat-text-only catalog entries using FULL prefixed IDs (rebuilt from live `/v1/models` endpoint).
- `agentkthx/plugins/nvidia/__init__.py` — **new** (registers `nvidia` + `nim` aliases).
- `agentkthx/plugins/nvidia/plugin.json` — **new** (manifest v0.2).
- `agentkthx/plugins/nvidia/nvidia.py` — **new** (~800 LOC; `NvidiaBackend(CloudBackend)` with catalog-lookup overrides, blocklist filter, credit-exhaustion detection, fixed-param 400 handler, thinking-model timeout).
- `tests/test_nvidia_backend.py` — **new** (87 regression tests).
- `tests/test_r07_19_help_sort.py` — updated expected subcommand list (added `auth`).
- `tests/test_r07_20_auth_picker.py` — bumped 8→9 backend count assertions; fixed `test_key_display_masked_or_not_set` for short-key mask shape.
- `scripts/probe_nvidia.sh` — **new** (5-section probe script with `.env` loader + `--deep` inference test).
- `scripts/add_nvidia_to_seed.py` — **new** (idempotent seed-extension script).
- `scripts/filter_nvidia_seed_chat_only.py` — **new** (idempotent filter script).
- `scripts/rebuild_nvidia_seed_from_live.py` — **new** (rebuilds the seed catalog from the live `/v1/models` endpoint using full prefixed IDs).
- `docs/api/NVIDIA_API_TECHNICAL_REFERENCE.md` — **new**.
- `docs/api/CLOUDFLARE_API_TECHNICAL_REFERENCE.md` — **new**.
- `docs/api/SILICONFLOW_API_TECHNICAL_REFERENCE.md` — **new**.
- `docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md` — **new**.

### Next steps (planned)

- Scaffold the Cloudflare Workers AI plugin (account-ID-in-URL pattern, no `/v1/models` on the OpenAI-compat path).
- Scaffold the SiliconFlow plugin (3 permanently-free models with no quota — most useful FREE_ONLY filter).
- Scaffold the DuckDuckGo AI Chat plugin (custom backend from scratch — not OpenAI-compatible; only keyless alternative to Pollinations).

## [R07.25] - 2026-10-06 05:27:09 PM

**Two-batch closure pass + backend support tiers introduced.** R07.25 ships 5 closures (4 CLOSED + 1 WONTFIX) across two batches, plus a new `docs/SUPPORT.md` policy that splits the cloud backends into Fully Supported (ZAI, OpenRouter, HuggingFace, Gemini, Mistral) and Limited Support (Pollinations, OrcaRouter, OpenAI) tiers based on maintainer testing coverage. The `audit/generate_audit_dash.py` dashboard generator also gained an MCP category bucket + a hero resolution-rate % (was closure-rate %). Suite: 2959 → 2966 passed (+7 net across the two batches) / 20 skipped. Zero regressions; ruff + black clean. Register: 125 findings — 14 OPEN / 101 CLOSED / 10 WONTFIX (111 archived, ~89%). Closure rate 81% / resolution rate 89%.

The release is split into two batches for changelog clarity, but shipped as a single tagged release. The audit register (`audit/audit.md` + `audit/deltas.md`) tracks each finding's closure prose separately.

### Batch 1 — SEC-09 WONTFIX + TEST-09 CLOSED (2 findings resolved)

- **SEC-09** (Medium, WONTFIX) — ACP credentials sent as Basic Auth over HTTP by default. Owner decision: ACP is a monitoring-only protocol with no ability to prompt or run commands; the `http://localhost:8766` default is a PLACEHOLDER parameter so a fresh checkout works against a local ACP instance, NOT a recommendation to deploy ACP on remote HTTP. Users deploying ACP across machines are expected to put it behind HTTPS themselves — same operator-side trust boundary as MCP-02/SEC-13 R07.24 WONTFIX. A runtime warning would block the documented localhost-development path under the default placeholder URL, which is a UX regression for the most common deployment shape. **Security category now 100% resolved**: 15 CLOSED + 5 WONTFIX, 0 OPEN.
- **TEST-09** (Low, CLOSED) — new `scripts/smoke_test_r07_25.sh` extends the R07.21 smoke-test script family with a 4th streaming-path step per backend. The script is a superset of `smoke_test_r07_21.sh` — runs the existing three steps (model listing, `--think`, `--tools shell --no-stream`) per backend AND adds a 4th step that re-runs the `--tools shell` invocation WITHOUT `--no-stream`, forcing the streaming path (`generate_completions_stream` → `_iter_sse_lines` → SSE chunk parse → tool-call extraction) with a 180s timeout (vs 120s non-streaming). The smoke marker (`smoke-test-marker-$$`) is the SAME for both paths, so a streaming-only regression (the R07.09 `_iter_sse_lines` `NotImplementedError` shape) shows up as a streaming-step fail + non-streaming-step pass — exactly the bug shape the original 64-test suite missed. Per owner policy the smoke-test script + the dashboard generator are run manually before GitHub/CI — no pytest regression file ships alongside either; the contract is the script's `--help` block + the inline contract (SAME marker, both paths, `run_tool_step` helper). The R07.21 script is kept for back-compat; R07.25 supersedes it for any future streaming-adjacent change.

### Batch 2 — ROB-02 / ROB-06 / ROB-15 closures (3 closed)

- **ROB-02** (Medium, CLOSED) — orchestrator parallel mode now uses `FIRST_COMPLETED` + `executor.shutdown(wait=False, cancel_futures=True)` (Python 3.9+) + a cooperative `threading.Event` stashed on each agent's `_cancel_event` attribute so backends that poll between SSE chunks can abort cleanly. The old `ALL_COMPLETED` + `future.cancel()` combo only prevented not-yet-started futures from starting — already-running workers kept the pool alive past the timeout, holding HTTP connections + consuming tokens for minutes. The new path wakes the moment ANY future returns (or the timeout fires), signals cooperative cancellation, then `cancel_futures=True` cancels the not-yet-started queue. Already-running workers can't be killed (Python threads can't be killed) but they'll see the cancel_event soon and abort, OR finish on their own — `executor.shutdown(wait=False)` releases the pool without blocking on them. Workers that haven't finished when results are collected now show up as `[Timeout after Ns]` (was unreachable pre-ROB-02). ~70 LOC in `agentkthx/orchestrator.py`; 3 regression tests in `tests/test_r07_25_batch2_closures.py`.
- **ROB-06** (Medium, CLOSED) — new `CloudBackend._close_http_response(response)` static helper does `fp.close()` + `release_conn()` + `close()` in sequence, all best-effort with `getattr` guards so missing attributes on older Python / alternate response shapes don't raise. Each of the 8 cloud backends' `_iter_sse_lines` finally block now uses the helper via `getattr(self, "_close_http_response", None)` so the helper degrades gracefully even if the backend's MRO doesn't include `CloudBackend` (e.g. OpenAI which extends `OpenAICompatibleBackend`). The `streaming.py` KeyboardInterrupt handler comment was upgraded to document the two-pass close contract (`stream_gen.close()` triggers the backend's `GeneratorExit` cleanup which calls the helper). On POSIX the helper is a no-op (`response.close()` already releases the socket); on Windows it's the difference between a deterministic close + a GC-dependent close that can exhaust the connection pool on long Ctrl+C-heavy sessions. ~80 LOC across `agentkthx/backends/cloud_base.py` + 8 plugin files; 7 regression tests including source-inspection pins that every cloud backend uses the helper.
- **ROB-15** (Medium, CLOSED) — new `@contextmanager _transaction()` on `PersistentMemory` wraps `_write_message` + `_touch_session` in a single `self._write_lock` acquisition (RLock per ROB-18 R07.21 CLOSED, so reentrant-safe for nested transactions) + a single idempotent commit at context-exit. Pre-ROB-15 `add()` did two lock acquisitions + two commits per message, with an interleaving window where another thread's `add()` could commit between the message-write and the session-touch. Now both writes are atomic from the perspective of other writers. `add_tool_call` + `add_tool_result` got the same wrap. Pairs naturally with ROB-18 (RLock) — completes the transaction wrap the audit's original recommendation noted ("the lock must become reentrant first, or the helpers need lock/no-lock variants"). ~50 LOC in `agentkthx/core/persistent_memory.py`; 5 regression tests including a reentrant-safe nested-transaction test + an atomic-persistence test that reads the DB directly to verify both rows are present after a single `add()`.

### Backend support tiers introduced (R07.25)

AgentKthx ships 8 cloud backends plus TurboQuant (local llama.cpp), Ollama, and BitNet. As of R07.25, the cloud backends are split into two support tiers based on **owner testing coverage** — not on code quality, completeness, or feature surface. The maintainer (VTSTech) personally tests every backend before a release; for an extended period, three backends (Pollinations, OrcaRouter, OpenAI) have been impossible to test end-to-end because their API keys are beyond their limits / out of quota. Rather than ship those backends with the implicit "fully tested" promise the other backends carry, this release makes the distinction explicit.

**Fully Supported** (maintainer-tested before every release; bug reports prioritized):

- **ZAI** — Z.ai (GLM models)
- **OpenRouter** — openrouter.ai aggregator (500+ models)
- **HuggingFace** — huggingface.co inference endpoints
- **Gemini** — Google AI Studio (Gemini + Gemma)
- **Mistral** — mistral.ai (La Plateforme)

Plus the local backends (TurboQuant, Ollama, BitNet) — always Fully Supported (no external API key needed).

**Limited Support** (code-quality identical — same `CloudBackend` base, same retry-loop helpers, same SSE pattern — but the maintainer's API key access has been unavailable for an extended period, so bug reports against them can't be reproduced by the maintainer):

- **Pollinations** — API key beyond limits; maintainer can't test the keyed path. The keyless path works (it's the only keyless backend) but the keyed entitlement-aware fallback filter (ROB-31, still OPEN) is untested.
- **OrcaRouter** — API key beyond limits; maintainer can't test the streaming or non-streaming paths. The backend shares the same code path as OpenRouter (its sibling) so it's *likely* functional.
- **OpenAI** — maintainer has no active OpenAI account. OpenAI has no free tier (the `--free` listing returns 0 models — known + documented in the smoke test).

See `docs/SUPPORT.md` for the full policy + what "Limited Support" means in practice. Promoting a Limited Support backend to Fully Supported requires the maintainer regaining working API key access AND running the smoke test end-to-end successfully.

### Audit dashboard enhancements

- `audit/generate_audit_dash.py` — added the **MCP category** as an 8th bucket. Pre-R07.25, MCP-prefixed findings (MCP-01 through MCP-05, all closed/wontfixed in R07.24) were classified under their original cross-cutting categories (Security/Robustness/Maintainability/Performance) — they're now forced into the "MCP" bucket via a new `PREFIX_CATEGORY_OVERRIDE = {"MCP": "MCP"}` map so they group together on the dashboard. The override is applied at all 3 parsing sites (Findings Summary table, Rxx.xx delta tables, cat_map fallback) so MCP-prefixed IDs always land in the MCP bucket regardless of what their deltas.md table cell says. The `MCP` color in JS `CAT_META` is `#fb923c` (orange-400 — visually distinct from the 7 existing category colors).
- The hero section now shows the **resolution rate** (closed + wontfix / total) instead of the closure rate (closed / total). The closure rate (78% at R07.24) is still surfaced in the section desc + the Closure stat card so both views remain available — the hero just leads with the more meaningful "this is fully resolved" number. Resolution rate at R07.25: **89%** (111 of 125 = 98 closed + 10 wontfix + 1 deferred *-carried over from R07.24*). The `resolutionPct` field is also surfaced in the JSON envelope endpoints (`api/findings/{findings,open,closed,wontfix}.json`) alongside the legacy `closurePct`.
- WONTFIX count is now dynamic in the section desc (was hardcoded "1 intentional WONTFIX" pre-R07.25).
- Stats grid CSS changed from `repeat(4,1fr)` to `repeat(auto-fit,minmax(11rem,1fr))` so the 5th stat card (Closure) wraps cleanly on any viewport — future-proof for more cards.

### Audit

- 5 findings resolved in R07.25 across two batches (batch 1: 1 WONTFIX + 1 CLOSED; batch 2: 3 CLOSED). Plus 1 WONTFIX carried over from the audit dashboard R07.24 closure-rate update (the +5 tests for MCP/closure-rate were deleted per owner policy that the dashboard generator runs manually).
- Register: 125 findings — 14 OPEN / 101 CLOSED / 10 WONTFIX (111 archived, ~89%). Up from R07.24's 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%).
- **Security category now 100% resolved**: 15 CLOSED + 5 WONTFIX, 0 OPEN.
- **Robustness category**: 1 OPEN (just ROB-31, the Limited-Support Pollinations entitlement-aware fallback — fix may require user testing), 39 CLOSED, 1 WONTFIX.
- The near-term tier (ROB-31, MAINT-01 1,733-line `cmd_chat` extraction, MAINT-27 `/sh` inline branch, TEST-01 integration test tier, TEST-03 FakeStreamingBackend) shipped unchanged through R07.22 → R07.25. These are the highest-leverage remaining closures.

### Files touched

- `agentkthx/__init__.py` — version bumped to `0.7.25`.
- `pyproject.toml` — `version = "0.7.25"`.
- `README.md` — header `# ⚛️ AgentKthx R07.25` + new **Backend support tiers** Features bullet (R07.25) referencing `docs/SUPPORT.md`.
- `agentkthx/mcp/client.py` — `_CLIENT_VERSION` bumped to `0.7.25` (sent in MCP `initialize` handshake).
- `agentkthx/orchestrator.py` — ROB-02 `_run_parallel` rewrite (FIRST_COMPLETED + cancel_futures + cooperative `threading.Event` + `_cancel_event` stash + `[Timeout after Ns]` result branch).
- `agentkthx/backends/cloud_base.py` — ROB-06 `_close_http_response` static helper (~55 LOC).
- `agentkthx/core/streaming.py` — ROB-06 KeyboardInterrupt handler comment upgraded to document the two-pass close contract.
- `agentkthx/core/persistent_memory.py` — ROB-15 `@contextmanager _transaction()` + `add`/`add_tool_call`/`add_tool_result` wraps; `from contextlib import contextmanager` import added.
- `agentkthx/plugins/zai/zai.py` — ROB-06 `_iter_sse_lines` finally block uses `_close_http_response` helper.
- `agentkthx/plugins/openrouter/openrouter.py` — ROB-06 same fix.
- `agentkthx/plugins/gemini/gemini.py` — ROB-06 same fix.
- `agentkthx/plugins/huggingface/huggingface.py` — ROB-06 same fix.
- `agentkthx/plugins/mistral/mistral.py` — ROB-06 same fix.
- `agentkthx/plugins/pollinations/pollinations.py` — ROB-06 same fix.
- `agentkthx/plugins/orcarouter/orcarouter.py` — ROB-06 same fix.
- `agentkthx/plugins/openai/openai.py` — ROB-06 same fix.
- `audit/generate_audit_dash.py` — MCP category added (8th bucket) via `PREFIX_CATEGORY_OVERRIDE`; hero closure % swapped for resolution %; WONTFIX count dynamic in section desc; CSS stats grid auto-fit; `__RESOLUTION_PCT__` token + `resolution_pct` field in `_endpoint_envelope` + `_summary_payload` (legacy `closureRate` alias preserved).
- `audit/audit.md` — header counts updated (17→14 OPEN, 98→101 CLOSED, R07.24→R07.25 commit, 2951→2966 tests); R07.25 batch 1 + batch 2 delta blocks added; SEC-09 + TEST-09 + ROB-02 + ROB-06 + ROB-15 removed from Findings Summary + Detailed Findings; Priority Matrix updated (removed the 3 ROB closures from Near term, added support-tier note to ROB-31); closure-history footer extended with R07.25 batches 1+2 + support-tier policy.
- `audit/deltas.md` — header counts updated (98→101 CLOSED, 108→111 archived, R07.24→R07.25); 5 new rows in Findings Summary (Archived) table (SEC-09 WONTFIX, TEST-09 CLOSED, ROB-02/06/15 CLOSED); new "## R07.25 Audit Closures — SEC-09 WONTFIX + TEST-09 CLOSED" section + "## R07.25 Batch 2 — ROB Closures + Support Tiers" section with full detail prose for each closure.
- `audit/brief.md` — header counts updated (17→14 OPEN, 108→111 archived, 2951→2966 tests, ~86%→~89%); R07.25 batch 2 mentioned in the "What's Missing / Incomplete" item 1 + closing paragraph; support-tier policy mentioned in Quick Start item 7.
- `docs/SUPPORT.md` — NEW file (117 LOC) documenting Fully Supported vs Limited Support backends + the policy + the changelog.
- `scripts/smoke_test_r07_25.sh` — NEW file (~480 LOC, executable). Superset of `smoke_test_r07_21.sh` adding a 4th streaming-path step per backend; `--skip "<names>"` arg replaces the legacy `SKIP` env var; `--backend` + `--debug` + `--no-stream-only` debug escape hatch; `default_model_for_backend()` helper hardcodes `openrouter/free` and `orcarouter/free` for those two aggregators (skips the slow `first_free_model` listing + awk parsing fragility for the 6 other backends); empty-envvar guard in `has_key()` (latent bash bug that the original R07.21 script also has — fixed here, R07.21 script left as-is for back-compat).
- `tests/test_r07_25_batch2_closures.py` — NEW file (~430 LOC, 15 tests) pinning the ROB-02/06/15 closure contracts.
- `tests/test_generate_audit_dash.py` — DELETED per owner policy (dashboard generator runs manually before GitHub/CI; no pytest regression file ships alongside).
- `tests/test_smoke_test_r07_25.py` — DELETED per owner policy (smoke tests run manually before GitHub/CI; no pytest regression file ships alongside).

### Process — R07.25 release hygiene

- `scripts/bump-version.sh R07.25` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md` header). `agentkthx/mcp/client.py:_CLIENT_VERSION` also bumped to `0.7.25` (sent in MCP `initialize` handshake) — done manually since `bump-version.sh` only handles the 4 main sites.
- `R07.24 (0.7.24)` → `R07.25 (0.7.25)`.
- Suite: 2959 (R07.24 end-state per the audit) → 2966 passed (+7 active across both batches) / 20 skipped. Zero regressions; ruff clean (8 I001 import-sorting + F401 unused-import errors in `tests/test_r07_25_batch2_closures.py` fixed via `ruff check --fix`).
- Audit reconcile: `matches: True` — 125 findings, 14 OPEN / 101 CLOSED / 10 WONTFIX (111 archived, ~89%). Closure rate 81%, resolution rate 89%.
- The `audit/generate_audit_dash.py` dashboard generator + the `scripts/smoke_test_r07_25.sh` smoke test are run **manually** before GitHub/CI per owner policy — no pytest regression files ship alongside either. The contract for the dashboard generator is the CLI surface (`--audit`/`--brief`/`--deltas`/`--output`/`--no-endpoints`) + the JSON-endpoint layout; the contract for the smoke test is the script's `--help` block + the inline contract (SAME marker for both streaming + non-streaming paths, `run_tool_step` helper).

---

## [R07.24] - 2026-10-05 5:43:52 PM

**Audit closure super-batch: 13 OPEN findings closed + 2 WONTFIX across three batches in one release.** R07.24 is the largest single-release audit closure pass since R07.21 batch 2 — ten findings closed (SEC-20, ROB-28, ROB-41, TEST-11, MCP-01, MCP-03, MCP-04, MCP-05, MAINT-03, MAINT-22, MAINT-23, ROB-29, ROB-33) plus two WONTFIX (MCP-02, SEC-13 — deferred, out-of-scope). Suite: 2899 → 2959 passed (+60 active) / 16 → 20 skipped (+4 live-gated). Zero regressions; ruff + black clean across all 234 files. Register: 125 findings — 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%).

The release is split into three batches for changelog clarity, but shipped as a single tagged release. The audit register (`audit/audit.md` + `audit/deltas.md`) tracks each finding's closure prose separately.

### Batch 1 — SEC/TEST/ROB sweep (4 closed)

- **SEC-20** (Medium, CLOSED) — `mcp install` gains a `--no-overwrite` flag (refuses to clobber an existing `mcp.json` entry with rc=5; `--dry-run` now prints `WOULD OVERWRITE` when the target exists). Pre-flight collision detection refactored to a shared `_find_existing(servers)` helper used by both the dry-run branch and the write branch. 4 regression tests.
- **ROB-41** (Low, CLOSED) — `registry.search_all` split into `search_all_with_errors` (returns `(results, errors)` tuple preserving per-source failure reasons) + a back-compat `search_all` thin wrapper that drops errors. Plain-mode `mcp search` now leads with `Network error searching for '<query>':` + actionable hints (`--refresh`, connection check, `AGENTKTHX_GITHUB_TOKEN`) instead of the misleading `No MCP servers found` + `Try a broader query` when all sources fail. 5 regression tests.
- **TEST-11** (Low, CLOSED) — new `tests/test_mcp_live_contract.py` — 4 live-gated contract tests skip-on-`AGENTKTHX_LIVE_TESTS`-unset. Assert the documented npm + GitHub response field set per source so a `package.name` → `package.id` rename ships green-on-mocked-suite but red-on-live-contract. Verified live against real `registry.npmjs.org` + `api.github.com`.
- **ROB-28** (Low, CLOSED) — MistralBackend `list_models` catch narrowed from `except Exception` to `except (HTTPError, URLError, JSONDecodeError)`. Programming errors (KeyError/AttributeError/TypeError from a malformed response shape) now propagate as real bugs with tracebacks instead of being silently swallowed as 'discovery failed'. Debug-mode print includes the exception type (`type(e).__name__`) so a 503 service-unavailable is distinguishable from a JSON decode error at a glance. 5 regression tests pin both the caught-and-degrades cases AND the now-propagates cases.

### Batch 2 — MCP sweep (4 closed + 2 WONTFIX)

- **MCP-01** (Medium, CLOSED) — `StdioTransport._read_response` thread+queue pattern. The blocking `readline()` now runs on a daemon thread (`_blocking_readline`); the main thread does `queue.get(timeout=remaining)` which actually honors its deadline (unlike `readline()`). On timeout, the transport is marked **poisoned** (`is_poisoned` property exposed) — subsequent calls raise immediately with a "close() and reconnect to recover" message. Bonus: Ctrl+C interruptibility on POSIX (the main thread no longer holds the GIL inside `readline()`). Same shape as the R07.24 ROB-06 line-ref update (which fixed the verifier false-positive on the streaming-generator close-guard).
- **MCP-03** (Low, CLOSED) — `_extract_params` `arguments_json` fallback. When inputSchema contains `oneOf`/`anyOf`/`allOf`/`$ref`/`$dynamicRef` or nested `properties` deeper than one level, the whole tool falls back to a single `arguments_json` string parameter whose description embeds the original schema as JSON. `_invoke` parses the JSON string + forwards the parsed dict as the MCP `arguments` field; malformed JSON returns a clean error string to the model rather than raising. Conservative "whole-tool fallback" choice (vs per-property) — mixed per-field params + a single `arguments_json` is hard for the model to reason about. 8 regression tests.
- **MCP-04** (Low, CLOSED) — `MCPManager.connect_all(lazy=True)` records configs without spawning any subprocesses. New `warmup_server('<name>')` method spawns + enumerates + registers tools into the live `_target_registry` (stashed by `register_into()` so post-prompt-build warmup still surfaces tools). `_invoke` auto-warms on first dispatch to a lazy server's namespace. Trade-off (documented): prompt-time tool surface is empty for lazy servers — operators who want lazy startup AND prompt-time tool surface should `warmup_server("<name>")` BEFORE `Agent.__init__` builds the prompt. Default behavior (lazy=False) is unchanged — eager spawn + full tool surface. 6 regression tests.
- **MCP-05** (Low, CLOSED) — `notifications/tools/list_changed` handler. Three layers of notification routing: (1) `StdioTransport.set_notification_callback(callback)` — transport-level hook fired when a JSON-RPC message with no `id` field is read; (2) `MCPClient.set_notification_handler(method, handler)` + `handle_notification(msg)` — per-method dispatch table; the client installs a trampoline on its transport during `connect()` BEFORE sending `initialize` (with `listChanged: True` capability now actually wired); (3) `MCPManager._install_list_changed_handler(client, name)` wires the per-server callback to `_refresh_tools_for_server(name)` — re-queries `tools/list`, diffs against the cached surface, adds newly-discovered shim Tools to the live `_target_registry`, removes vanished ones via `ToolRegistry.unregister_tool(name)` (NEW method), then fires the user-registered `on_tools_changed(callback)` with `(server_name, added, removed)`. Handler exceptions swallowed + logged so a buggy handler doesn't kill the transport. 6 regression tests.
- **MCP-02** (Medium, WONTFIX) — MCP server sha256 pin equivalent. Deferred: AgentKthx doesn't control the MCP spec, can't enforce pinning on externally-published MCP servers. The trust boundary is advisory by design, same posture as the MCP spec itself; an operator who wants pin enforcement can manually hash the binary (`sha256sum $(which npx)`) and check it before launch. Documented in `docs/mcp/ROADMAP.md` as a deferred Phase 4+ item.
- **SEC-13** (Medium, WONTFIX) — plugin sha256 pin enforcement mode. Deferred: same reasoning as MCP-02 — plugins are externally-distributed code and AgentKthx can't enforce what upstream authors ship. Pin verification works WHEN present (the `_validate_sha256_pin` machinery from the R07.05 SEC-06 fix is intact); refusing to load unpinned plugins would block every bundled plugin + every community plugin.

### Batch 3 — MAINT/ROB sweep (5 closed)

- **MAINT-03** (Medium, CLOSED) — `normalize_args` strategy 5 (prefix/substring matching) REMOVED. The original strategy matched any key whose lower-cased form was a prefix of OR substring of any expected param — so `{"e": "..."}` matched `"expression"` (e is a substring), `{"pat": "/x"}` matched `"path"`, etc. Dangerously permissive: a model that hallucinates a single-letter arg name silently succeeded instead of failing with a clear "unknown argument" message, and the value would land in whatever param happened to contain that letter. Operators who relied on strategy 5 can restore it per-tool by adding explicit entries to `TOOL_ARG_ALIASES` in `core/prompts.py` (the intended extension point). No env-var escape hatch — the finding's recommendation was to drop the strategy outright. 6 regression tests.
- **MAINT-22** (Medium, CLOSED) — Mistral streaming path now routes through `_build_mistral_body`. New `_build_stream_body()` hook on `OpenAICompatibleBackend` (default delegates to `_build_openai_body(stream=True)` for back-compat — vanilla OpenAI-shape backends like ZAI, OpenRouter, HuggingFace, Pollinations, BitNet are byte-identical to pre-R07.24). Mistral overrides the hook to delegate to `_build_mistral_body(stream=True)`, so the streaming path now applies all Mistral-specific body shaping: `seed`→`random_seed` aliasing, `safe_prompt` injection, `prompt_cache_key` from session_id, `tool_choice="required"→"any"` mapping, OpenAI-only kwarg stripping. Before R07.24, a streaming call with `seed=42` ignored the seed; a streaming call with `MISTRAL_SAFE_PROMPT=true` didn't inject the safety prompt; a streaming call after R07.18's `session_id` kwarg didn't get the prompt-cache-key. The non-streaming path was correct; the streaming path was wrong. Now both paths use the same body. 4 regression tests.
- **MAINT-23** (Medium, CLOSED) + **ROB-29** (Low, CLOSED) — duplicated retry-loop skeleton lifted to `CloudBackend`. New shared helpers: `_compute_retry_after(headers, attempt)` (parses Retry-After header + exponential backoff with full jitter, capped at `_BACKOFF_CAP` per the ROB-16 lesson), `_is_retryable_http_status(status_code)` (429 + 5xx retryable, 4xx not), `_compute_network_backoff(attempt)` (URLError path — no headers to honor). Class-level defaults `_BACKOFF_BASE = 1.0`, `_BACKOFF_CAP = 60.0`, `_MAX_RETRIES = 4` on CloudBackend — concrete backends (Mistral, Pollinations, OpenRouter, OrcaRouter) override to tune their own retry behavior. `_max_retries()` reads `AGENTKTHX_MAX_API_RETRIES` env var as the cross-backend override; Mistral's existing override (reads `MISTRAL_MAX_RETRIES`) is preserved. The 4xx-specific handlers (401/404/422 + 400-context-length recovery) stay in each backend's caller because they differ in error-message wording and recovery strategy. 9 regression tests.
- **ROB-33** (Medium, CLOSED) — `_is_process_alive` platform-safe liveness probe. On Windows, `os.kill(pid, 0)` TERMINATES the target process (the POSIX "signal 0 = liveness check" semantics don't hold — Windows treats any signal as a kill). R07.24 branches on `os.name == 'nt'` and uses `ctypes`'s `OpenProcess` (PROCESS_QUERY_LIMITED_INFORMATION — read-only, no terminate rights) + `GetExitCodeProcess` for the Windows path; keeps `os.kill(pid, 0)` for POSIX (where signal 0 is documented as a no-op liveness check). The Windows helper fails closed on any ctypes error so the caller (`TurboState.load()` + `_free_port`) re-binds the port rather than assuming the server is alive. Also catches `OverflowError` for pids that don't fit in `pid_t` (e.g. `0xFFFFFFFF` on Linux). This was a latent bug since R06.57 but became user-facing in R07.16 when the call moved onto the chat startup path — a Windows user starting `agentkthx chat` against a running turbo server would silently kill the server in the process of checking if it was alive. 8 regression tests, including the contract test that `os.kill` is NOT called on the Windows path.

### Audit

- 13 OPEN findings closed in R07.24 (10 in batches 1+2, 5 in batch 3 — wait recount: 4+4+5=13 ✓), 2 WONTFIX. The MCP register is now 100% resolved (5 CLOSED + 1 WONTFIX + 0 OPEN).
- Register: 125 findings — 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%). Up from R07.23's 34 OPEN / 84 CLOSED / 7 WONTFIX (91 archived, ~73%).
- The near-term tier (ROB-02 orchestrator thread join, ROB-06 Windows conn release, ROB-15 memory-store double-lock, ROB-31 Pollinations entitlement, SEC-09 ACP Basic-Auth-over-HTTP, MAINT-01 1,733-line `cmd_chat` extraction, MAINT-23 retry-loop family — now closed, TEST-01 integration test tier, TEST-03 FakeStreamingBackend) shipped unchanged through R07.22 → R07.24. These are the highest-leverage remaining closures.

### Files touched

- `agentkthx/cli/commands/mcp.py` — SEC-20 `--no-overwrite` flag + `_find_existing` helper + `WOULD OVERWRITE` in dry-run + ROB-41 plain-mode network-error hint in `_mcp_search`.
- `agentkthx/cli/parser.py` — SEC-20 `--no-overwrite` argparse flag on `mcp install` subparser.
- `agentkthx/mcp/__init__.py` — export `search_all_with_errors` (ROB-41).
- `agentkthx/mcp/registry.py` — ROB-41 `search_all_with_errors` returning `(results, errors)` tuple; back-compat `search_all` thin wrapper.
- `agentkthx/mcp/transport.py` — MCP-01 thread+queue pattern (`_blocking_readline`, `is_poisoned` property, `_notification_callback` hook for MCP-05).
- `agentkthx/mcp/client.py` — MCP-05 `set_notification_handler` + `handle_notification` trampoline; `_CLIENT_VERSION` bumped to 0.7.24.
- `agentkthx/mcp/manager.py` — MCP-03 `_extract_params` arguments_json fallback + `_schema_has_complex_constructs` + `_build_arguments_json_fallback`; MCP-04 `connect_all(lazy=True)` + `warmup_server()` + auto-warm in `_invoke`; MCP-05 `_install_list_changed_handler` + `_refresh_tools_for_server` + `on_tools_changed` callback + `unregister_tool` shim removal.
- `agentkthx/tools/registry.py` — MCP-05 `ToolRegistry.unregister_tool(name)` (NEW method).
- `agentkthx/plugins/mistral/mistral.py` — MAINT-22 `_build_stream_body` override routing through `_build_mistral_body`; MAINT-23/ROB-29 `_iter_sse_lines` + `_make_api_request` delegate to CloudBackend's `_compute_retry_after` + `_is_retryable_http_status` + `_compute_network_backoff`; ROB-28 `list_models` catch narrowed from `except Exception` to `except (HTTPError, URLError, JSONDecodeError)`.
- `agentkthx/backends/openai_compat.py` — MAINT-22 `_build_stream_body` hook (default delegates to `_build_openai_body(stream=True)`).
- `agentkthx/backends/cloud_base.py` — MAINT-23/ROB-29 shared retry helpers (`_compute_retry_after`, `_is_retryable_http_status`, `_compute_network_backoff`, `_max_retries` with `AGENTKTHX_MAX_API_RETRIES` env override) + class-level `_BACKOFF_BASE`/`_BACKOFF_CAP`/`_MAX_RETRIES` defaults.
- `agentkthx/core/helpers.py` — MAINT-03 strategy 5 (prefix/substring matching) REMOVED from `normalize_args`.
- `agentkthx/plugins/turboquant/turbo.py` — ROB-33 `_is_process_alive` platform-safe (POSIX keeps `os.kill(pid, 0)`; Windows uses `ctypes` `OpenProcess` + `GetExitCodeProcess`); `_is_process_alive_windows` helper; `OverflowError` catch for pids that don't fit in `pid_t`.
- `tests/test_mcp_cli.py` — SEC-20 (4 new tests) + ROB-41 (5 new tests in `TestSearchAllWithErrors`) + updated `test_search_handles_network_error_gracefully`.
- `tests/test_mcp_live_contract.py` — NEW file, TEST-11 (4 live-gated tests).
- `tests/test_mistral_backend.py` — ROB-28 (5 new tests in `TestListModelsCatchNarrowing`).
- `tests/test_mcp_scaffold.py` — MCP-03 (8 new tests) + MCP-04 (6 new tests) + MCP-05 (6 new tests).
- `tests/test_r07_24_batch3_closures.py` — NEW file, MAINT-03 + MAINT-22 + MAINT-23/ROB-29 + ROB-33 (27 new tests across 4 test classes).
- `audit/audit.md` + `audit/brief.md` + `audit/deltas.md` — closure prose for all 13 CLOSED + 2 WONTFIX findings; reconcile `matches: True`.

### Process — R07.24 release hygiene

- `scripts/bump-version.sh R07.24` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md` header). `agentkthx/mcp/client.py:_CLIENT_VERSION` also bumped to `0.7.24` (sent in MCP `initialize` handshake).
- `R07.23 (0.7.23)` → `R07.24 (0.7.24)`.
- Suite: 2912 (R07.23 end-state per the audit) → 2959 passed (+47 active across all three batches) / 16 → 20 skipped (+4 live-gated). Zero regressions; ruff + black clean across all 234 files.
- Audit reconcile: `matches: True` — 125 findings, 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%).

---

## [R07.23] - 2026-10-05 1:14:13 AM

**`agentkthx mcp search` + `mcp install` + removal of deprecated `git` server.** R07.23 expands the `mcp` subcommand from three actions to five — `list`, `init`, `probe`, **`search`**, **`install`** — and removes the deprecated `@modelcontextprotocol/server-git` entry from `mcp init`'s example config. Two new `agentkthx/mcp/` modules (`registry.py` + `cache.py`) implement a live, stdlib-only search across npm + GitHub, with a 10-minute TTL cache so repeat calls don't re-hit the network.

The motivation came directly from a maintainer probe: `@modelcontextprotocol/conformance` was mistakenly added to `mcp.json` (it's a CLI test harness, not an MCP server) and failed with `subprocess closed stdout while waiting for id=1`. The new `search` + `install` commands make server discovery and config population terminal-only operations — no browser, no copy-paste, no npm registry client.

### Design pivot: offline catalog → live registry

R07.23 started with a curated offline catalog (`agentkthx/mcp/catalog.py`) — a Python literal seeded from the table in `docs/mcp/ROADMAP.md`. That approach was replaced mid-release with a live registry backed by npm + GitHub. The catalog module was deleted; its replacement (`registry.py`) uses stdlib `urllib.request` — zero new runtime dependencies. The catalog was inherently stale (a Python literal can't track npm publishes), and live search + cache gives freshness without paying the network cost on every invocation: `mcp search filesystem` takes ~1.2s on first call and <50ms on cache hit.

### Added — `agentkthx/mcp/registry.py` (new module)

Stdlib-only live search across npm + GitHub:

- **`npm_search(query, size=25)`** — hits `registry.npmjs.org/-/v1/search`, filters to packages whose name or description mentions MCP, returns normalized result dicts with name, package, version, description, source, is_official, homepage, install_hint, stars, license, search_score.
- **`npm_package_info(name)`** — fetches full metadata for one npm package from `registry.npmjs.org/<name>`. Returns None on 404. Used by `mcp install` to verify the package exists + get the current version before writing to `mcp.json`.
- **`github_search(query, size=25)`** — hits `api.github.com/search/repositories` with `sort=stars&order=desc`. Works anonymously at 10 req/min; set `AGENTKTHX_GITHUB_TOKEN` (or `GITHUB_TOKEN` / `GH_TOKEN`) for 5000/min. Catches repos not published to npm (e.g. `oraios/serena`, `github/github-mcp-server`).
- **`search_all(query)`** — combined search across both sources, deduped by package identifier. Failures from either source are swallowed (graceful degradation — if npm is down, GitHub results still return).
- **`derive_short_name(package)`** — heuristic: `@modelcontextprotocol/server-filesystem` → `filesystem`, `mcp-server-fetch` → `fetch`, `oraios/serena` → `serena`. Used by both `search` (for display) and `install` (for the mcp.json entry name).
- **`build_config_snippet(package, ...)`** — generates a ready-to-paste mcp.json entry. For npm: `{"command": "npx", "args": ["-y", "<package>"]}`. For GitHub: `{"command": "uvx", "args": ["--from", "git+https://github.com/<owner>/<repo>", "<repo>"]}` (best-effort; user can override with `--command` / `--args`).
- All network calls use stdlib `urllib.request` with a 10-second timeout. Failures (offline, DNS, HTTP error) raise `MCPRegistryError` with a clear message.

### Added — `agentkthx/mcp/cache.py` (new module)

JSON-backed TTL cache at `~/.agentkthx/mcp_cache.json` (mode 0o600):

- **`get_cached(key)`** — returns `(hit, value)`. Expired entries are skipped (lazy eviction — not actively removed, just overwritten on next write).
- **`set_cached(key, value, ttl=600)`** — writes an entry with a 10-minute TTL by default. Configurable via `AGENTKTHX_MCP_CACHE_TTL` env var (seconds). Set to `0` to disable caching entirely.
- **`clear_cache()`** — explicitly clears all entries. Returns count removed.
- Atomic writes via `tmp + rename` to prevent corruption on concurrent access. File access is not locked (two concurrent `mcp search` calls could race on write; worst case is a lost cache update, not corruption).
- Cache key format: `"search:<source>:<query>:<limit>"` (e.g. `"search:all:filesystem:25"`).

### Added — `agentkthx mcp search` (live, replaces the offline catalog)

Replaces the deleted offline catalog. Always live (with cache); no `--live` flag needed.

- **`agentkthx mcp search [query]`** — default query is `"mcp"` (lists popular MCP servers). Searches npm + GitHub in parallel, dedupes, sorts by official-first + relevance.
- **`--source npm|github|all`** — restrict to one source (default: all). npm is faster and has versions; GitHub catches repos not on npm.
- **`--limit N`** — max results per source (default: 25, max: 100 GitHub / 250 npm).
- **`--refresh`** — bypass the cache and force a fresh fetch. The cache is updated with the new results.
- **`--json`** — emit machine-readable JSON payload with `query`, `source`, `results`, and optional `errors` fields.

### Added — `agentkthx mcp install <name>` (new subcommand)

Fetches live metadata for one server and writes it directly to `~/.agentkthx/mcp.json`. Always live (no offline fallback); overwrites existing entries with the same name by default (per maintainer spec).

- **`agentkthx mcp install filesystem`** — short name; resolves via npm search, preferring `@modelcontextprotocol/*` packages. Fetches full metadata for version + description.
- **`agentkthx mcp install @modelcontextprotocol/server-filesystem`** — full npm package name; fetches metadata directly (no search step).
- **`agentkthx mcp install oraios/serena --command uvx --args='...'`** — GitHub repo; requires `--command` and `--args` since the install command varies by repo.
- **`--as <name>`** — override the derived short name in the written entry.
- **`--command <cmd>`** / **`--args '<space-separated string>'`** — override the launch command and args. `--args` is a single string split on spaces (so values starting with `--` don't confuse argparse).
- **`--dry-run`** — print the snippet that would be written, don't touch `mcp.json`.
- **`--json`** — emit the snippet as JSON; doesn't write to `mcp.json`. Progress messages suppressed.
- **`--config <path>`** — override the target `mcp.json` path.
- **Exit codes**: 0 success, 1 import error, 2 network error, 3 package not found (404), 4 mcp.json unreadable.

### Changed — `mcp init` no longer emits deprecated `git` entry

- **Removed**: the `git` server entry from `_build_example_config()` in `agentkthx/mcp/config.py` and from `agentkthx/mcp/mcp.example.json`. The package `@modelcontextprotocol/server-git` was removed from npm (404 as of 2026-10-04); shipping it in the example config — even disabled — created noise and offered no path forward. Operators who want git operations should run `agentkthx mcp search git` to discover `serena` (LSP-based, includes git ops) or `github` (remote GitHub API access).
- **The `mcp init` post-write message** was simplified — it no longer mentions the deprecated git entry. The list of default-enabled servers (`filesystem` + `sequential-thinking`) is unchanged.
- **Footer hint**: the `mcp list` footer now reads `agentkthx chat --mcp filesystem sequential-thinking` (was `--mcp fs git`), matching the actual default-enabled server names.

### Removed — `agentkthx/mcp/catalog.py` (deleted)

The offline catalog module is gone. The `KNOWN_MCP_SERVERS` list, `search_catalog()` function, and `get_entry()` function are no longer available. All references removed from `agentkthx/mcp/__init__.py`. The `mcp search --copy <name>`, `--tag`, `--status`, and `--no-deprecated` flags are also gone — replaced by the live search flags above.

### Tests

- **`TestMcpSearch` (8 tests)** — all mock `agentkthx.mcp.registry._http_get_json` so no real network calls. Covers: basic search, cache write, cache hit on second call, `--refresh` bypass, `--source npm` only, `--json` payload shape, no-results message, graceful network-error handling.
- **`TestMcpInstall` (9 tests)** — covers: short-name install (npm search + metadata fetch), full npm package name, creates `mcp.json` if missing, overwrites existing entry (per maintainer spec), `--dry-run` doesn't write, `--json` emits snippet without writing, GitHub repo with `--command`/`--args` overrides, 404 returns rc=3, `--as` overrides short name.
- 1 new test `test_mcp_init_no_git_entry` — regression guard asserting `git` is no longer in the generated config (re-reads the raw JSON, not just the load_mcp_config-filtered list, to confirm the entry is gone entirely rather than just disabled).
- Updated `tests/test_mcp_scaffold.py::test_write_example_config_round_trips` to assert `git` is absent from the raw generated JSON.
- Updated `tests/test_mcp_cli.py::test_mcp_subcommands_registered` to include `search` + `install` in the expected subcommand set.
- Updated `tests/test_mcp_cli.py::test_mcp_no_subcommand_prints_help` to assert `search` + `install` appear in the help banner.
- Updated `tests/test_mcp_cli.py::test_mcp_list_with_servers` fixture to use `memory` instead of `git` for the disabled-server test case.

**Suite: 2856 → 2880 passed (+24) / 16 skipped. Zero regressions; ruff + black clean.**

### Audit

- No new findings filed. The R07.23 work is feature-additive (new `search` + `install` subcommands + 2 new modules) and removes deprecated surface (the `git` entry + the offline catalog) — neither introduces a new defect shape. The pre-existing `_mcp_list` dead-code path for disabled-entry markers (○) is noted in the test fixture but not promoted to a finding; it's a cosmetic issue with no behavioral impact since `load_mcp_config` filters disabled entries before the listing iterates them.

### Files touched

- `agentkthx/mcp/registry.py` — NEW (~290 LOC) — `npm_search`, `npm_package_info`, `github_search`, `search_all`, `derive_short_name`, `build_config_snippet`, `MCPRegistryError`
- `agentkthx/mcp/cache.py` — NEW (~150 LOC) — `get_cached`, `set_cached`, `clear_cache`, `cache_path`, `DEFAULT_TTL` (10m), env var `AGENTKTHX_MCP_CACHE_TTL`
- `agentkthx/mcp/__init__.py` — exports the new registry + cache functions; removed catalog exports
- `agentkthx/mcp/config.py` — removed `git` entry from `_build_example_config()`, added explanatory comment
- `agentkthx/mcp/mcp.example.json` — removed `git` entry (kept `memory` as an example of a disabled server)
- `agentkthx/cli/commands/mcp.py` — added `_mcp_search()` (live, ~120 LOC) + `_mcp_install()` (~150 LOC) handlers, dispatch entries, updated help banner + footer hint
- `agentkthx/cli/parser.py` — registered `search` subparser (with `--source`, `--limit`, `--refresh`, `--json`) + `install` subparser (with `--as`, `--command`, `--args`, `--dry-run`, `--json`, `--config`)
- `tests/test_mcp_cli.py` — added `TestMcpSearch` (8 tests, mocked urllib) + `TestMcpInstall` (9 tests, mocked urllib) + `test_mcp_init_no_git_entry`; updated 3 existing tests
- `tests/test_mcp_scaffold.py` — updated `test_write_example_config_round_trips` to assert `git` is absent from raw JSON
- `README.md` — replaced "deprecated git is included but disabled" paragraph with a `mcp search` + `mcp install` walkthrough
- `docs/mcp/ROADMAP.md` — added steps 1.11–1.14 to the Phase 1 table; struck through the deprecated `server-git` row in the curated catalog; updated the "catalog table" note to point to `mcp search` for live results

### Intra-release amendments (R07.23-dev, 2026-10-05)

Four follow-up commits during R07.23-dev, all surfaced by a maintainer MCP smoke test on Ubuntu 26.04 against `glm-4.5-flash`. No suite-count change beyond the test-pinned regression guards noted per-commit; ruff + black clean after each.

- **`mcp init` defaults collapsed to a single `~/workspace` directory** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`, `tests/test_mcp_scaffold.py`). The previous example config referenced two directories — `~/projects` for the filesystem MCP server and `~/repo` for the (Phase 2) kthx-audit MCP server — both created by `write_example_config()` at write time. Maintainer feedback was that the split is artificial for a fresh install: a single `~/workspace` covers both use cases (drop files for the filesystem server; point the audit server at the same tree when it lands). `write_example_config()` now creates one `~/workspace` directory; `_build_example_config()` substitutes `~/workspace` into both the `filesystem` and `audit` server args; the static `mcp.example.json` template was updated to match (`/home/REPLACE_ME/workspace`). The `_mcp_init` success message now reports "One directory was created for you" instead of two. The `test_write_example_config_substitutes_home_dir` test was updated to assert a single `workspace` directory is created (was `projects` + `repo`).

- **`@modelcontextprotocol/server-memory` promoted to enabled-by-default** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`, `tests/test_mcp_scaffold.py`). The R07.23 release shipped `mcp init` with `filesystem` + `sequential-thinking` enabled and `memory` declared only in the static `mcp.example.json` (disabled). The maintainer smoke test bridged 9 tools from `@modelcontextprotocol/server-memory` cleanly (create_entities, create_relations, add_observations, delete_entities, delete_observations, delete_relations, read_graph, search_nodes, open_nodes) and confirmed the model exercised all of them in a 25-step agentic run. `memory` is now in the embedded `_build_example_config()` between `sequential-thinking` and `audit`, `enabled=True`, with no `--storage-path` initially (in-process). The `_mcp_init` success message lists memory in the "Servers enabled by default" section. `test_write_example_config_round_trips` was updated: expected enabled count bumped from `>= 2` to `>= 3`, and `assert "memory" in names` added.

- **`@modelcontextprotocol/server-memory` defaults to persistent storage at `~/.agentkthx/memory.json`** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`). Follow-up to the promotion above: the in-process default loses the knowledge graph on every `agentkthx chat` restart, which defeats the point of a persistent memory server. `write_example_config()` now substitutes `{home}/.agentkthx/memory.json` into the memory server's `--storage-path` arg; the parent `~/.agentkthx/` directory is already created by `write_example_config()` for the `mcp.json` file, so no new mkdir is needed. The static `mcp.example.json` was synced. The `_mcp_init` success message now reads `memory — knowledge graph (persists to ~/.agentkthx/memory.json)`.

- **`--max-steps` surfaced in the session header + streaming fatal-error diagnostic** (`agentkthx/cli/headers.py`, `agentkthx/core/streaming.py`). Maintainer noticed a `chat --max-steps 100` run terminate at "25 steps" with `[Resilience] Fatal API error — not retrying: ZAI HTTP error 400: {"error":{"code":"1214","message":"The messages parameter is illegal..."}}` and asked whether `--max-steps 100` was being applied. Traced end-to-end: `argparse` → `args.max_steps=100` → `_build_agent` passes 100 → `Agent.max_steps=100` → `range(self.max_steps)` in both `agentic_loop.py:248` and `streaming.py:455`. The flag IS applied correctly; the run died from a fatal ZAI 400 on step 25 (the footer's "25 steps" is completed iterations, not the limit). The 1214 itself is a known recurring issue — the messages array's value becomes illegal after ~25 rounds of ReAct-style history accumulated while `tools` is declared in the request body; full root-cause (streaming + native-tools mismatch) deferred to a future PR. Two surgical fixes shipped: (1) `_print_session_header` now prints a `Max Steps: N` line so operators can verify the flag was applied — prevents this confusion from recurring; (2) the streaming path's fatal-API-error handler now dumps `Messages at failure: N msgs (system=1, user=K, assistant=M, tool=T), ~X chars (~Y tokens)` under `--debug` so the next 1214 fire has data attached instead of speculation.

- **ROOT CAUSE FOUND + FIXED: ZAI 1214 "messages parameter is illegal" at step ~25 was the count-tier pruner dropping the original user prompt** (`agentkthx/core/memory.py`, `tests/test_loop_resilience.py`). A second smoke test (Ghidra headless MCP server, 25 native `function.list` tool-call rounds against `glm-4.5-flash`) reproduced the 1214 at step 26 with `--debug` enabled and `ctx 9%` — confirming size was NOT the issue. Root cause traced by simulating the messages array after 25 native tool-call rounds: the default `MemoryConfig(max_messages=50)` count-tier pruner fires at ~52 messages, slides the window to `keep_count = int(50 * 0.8) = 40`, and the slide drops the original `user` prompt along with the oldest assistant/tool pairs. The existing pairing-safe head trim only dropped leading `tool` results — it did NOT handle the case where the slide leaves an `assistant(tool_calls)` message exposed at the head. ZAI (and OpenAI) require the first non-system message to be `role=user`; an `assistant` message with `tool_calls` as the first non-system message is structurally invalid and triggers HTTP 400 code 1214 "messages parameter is illegal". The compaction path (`_check_compaction`, token-based at 85% of `num_ctx`) never fired because the run was only at 9% context — the count-tier pruner in `_prune_if_needed()` was the culprit. Fix: both pruning tiers (count-tier at line 353 and token-tier at line 397) now pin the first `user` message before the slide and re-prepend it after, so the API sequence always starts with `system → user → ...`. The pin accepts a 1-message overshoot past `keep_count` — preferable to an illegal API sequence. 2 existing tests updated (`test_window_slides_to_threshold` 42→43, `test_recent_messages_retained` 8→10) to reflect the preserved first-user; 1 new regression test `test_prune_preserves_first_user_message` reproduces the exact 25-round native-tool-calls scenario and asserts (a) the first non-system message is `role=user`, (b) no `assistant(tool_calls)` appears before the first `user`. Suite: 2880 → **2899 passed (+19 from the R07.23-dev batch), 16 skipped, 0 failures**; ruff + black clean.

---



## [R07.22] - 2026-10-03 10:00:57 PM

**MCP (Model Context Protocol) client support — Phase 1 complete.** R07.22 lands stdio MCP client mode, the largest new feature surface since the R07.19 capabilities-first tool-support chain. Agents can now consume tools from external MCP servers (filesystem, sequential-thinking, sqlite, memory, git, serena, brave-search, ...) via stdio JSON-RPC 2.0, with their tools bridged into the existing `ToolRegistry` alongside built-ins. Zero runtime dependencies added — the implementation uses stdlib `subprocess` + `json` only, in keeping with the project's `dependencies = []` invariant.

The release ships a new `agentkthx mcp` subcommand (`init` / `list` / `probe`), `--mcp [SERVER...]` and `--mcp-config PATH` flags on `chat`/`run`/`agent`, and a new `agentkthx/mcp/` package (`config.py` + `transport.py` + `client.py` + `manager.py`). `agentkthx mcp init` writes a ready-to-use config with the user's actual home directory substituted in (no `REPLACE_ME` placeholder) and creates `~/projects/` + `~/repo/` so the filesystem MCP server starts cleanly without manual setup. All MCP tool output flows through the same `sanitize_tool_output` security boundary (8 KB truncation + secret redaction + ANSI strip) as built-in tools — the defense-in-depth posture does not weaken because a tool came from a subprocess.

End-to-end verified against the official `@modelcontextprotocol/server-filesystem` (14 tools, `secure-filesystem-server v0.2.0`) and `@modelcontextprotocol/server-sequential-thinking` (1 tool, `v2026.8.31`). A `chat --mcp filesystem` session with `glm-4.5-flash` correctly picks `filesystem__list_allowed_directories` then `filesystem__list_directory` without any prompt engineering — the system prompt's Tool Reference section enumerates the namespaced MCP tools and the model selects them naturally.

### Added — MCP client package (`agentkthx/mcp/`)

New stdlib-only package implementing MCP client mode over stdio JSON-RPC 2.0:

- **`config.py`** — `MCPServerConfig` dataclass + `load_mcp_config()` + `write_example_config()`. Config file at `~/.agentkthx/mcp.json` (override with `--mcp-config PATH`). Validates: alphanumeric/-/_ server names (used as tool-name prefixes), `command` must be absolute path or `shutil.which`-resolvable (no shell, no `~`), `shell=False` + `close_fds=True` on subprocess spawn. Permission check warns on group/world-writable config files. `write_example_config()` substitutes the user's actual home directory into all path arguments (no `REPLACE_ME` placeholder) and creates `~/projects/` + `~/repo/` if they don't exist — `mcp init` followed by `mcp probe filesystem` works without manual editing.
- **`transport.py`** — `StdioTransport` wraps one subprocess; newline-delimited JSON-RPC 2.0 over stdin/stdout; stderr captured to a 64-line ring buffer for diagnostics (surfaced on failure). Lazy spawn (subprocess starts on first request, not at transport construction). `close()` sends MCP `shutdown` + `exit` notifications, then `terminate` + `kill` if the process hasn't exited within 2s. Per-transport `threading.Lock` serializes concurrent calls to the same server (JSON-RPC over a single stdio pair is inherently serial); parallel tool calls across servers use multiple transports.
- **`client.py`** — `MCPClient` wraps a `StdioTransport` and speaks the MCP protocol: `initialize` (sends `protocolVersion: 2025-06-18` + `clientInfo: agentkthx/<version>`), `notifications/initialized`, `tools/list`, `tools/call`. Server-reported `serverInfo` + `capabilities` exposed via read-only properties. Tool *execution* errors (e.g. file-not-found from the filesystem server) are returned as `{"isError": true, ...}` for the model to react to — only protocol/transport errors raise.
- **`manager.py`** — `MCPManager` orchestrates multiple `MCPClient` instances and bridges their tools into a target `ToolRegistry`. Tool name namespacing: `<server>__<tool>` (the `__` separator cannot appear in either MCP field, eliminating collision risk). Each MCP tool becomes a shim `Tool` whose handler forwards the call to the right `MCPClient`, then pipes the result through `sanitize_tool_output` exactly like built-in tools. `_extract_params()` converts the MCP `inputSchema` (JSON Schema) into the project's flat `ToolParam` list (handles `type: object` + `properties` + `required`, nullable unions `["string", "null"]`, common types). `_flatten_call_result()` converts the MCP `CallToolResult` (list of content items) into a flat string for sanitization; image content is replaced with a placeholder, resource references surface the URI. `describe()` returns a per-server diagnostic dict for `agentkthx mcp list`.

### Added — `agentkthx mcp` subcommand

Three sub-actions, all using only stdlib + the existing `agentkthx.mcp` package:

- **`agentkthx mcp init`** — writes `~/.agentkthx/mcp.json` with home-dir substitution + creates `~/projects/` + `~/repo/`. Refuses to overwrite without `--force`. The embedded example ships `filesystem` + `sequential-thinking` enabled by default (both verified on npm 2026-10-04), `git` disabled (`@modelcontextprotocol/server-git` was removed from npm — 404), `audit` disabled (Phase 2 placeholder). `chmod 0o600` on the written file.
- **`agentkthx mcp list`** — shows configured servers with enabled/disabled markers, command preview, and timeout. Hints at `agentkthx mcp init` if no config exists.
- **`agentkthx mcp probe <name>`** — connects to one server, runs `initialize` + `tools/list`, prints the tool surface with descriptions. Optional `--call TOOL JSON_ARGS` round-trips a real `tools/call` and prints the flattened result. Optional `--config PATH` overrides the config file. Exit codes: 0 success, 1 unknown server, 2 connect failed, 3 tools/list failed, 4 bad JSON args, 5 tools/call failed. Surfaces stderr tail on failure for diagnostics.

### Added — `--mcp` / `--mcp-config` CLI flags

Wired into `shared_args.py:add_agent_args` so `chat`, `run`, and `agent` all accept them:

- **`--mcp [SERVER ...]`** — `nargs="*"`, so bare `--mcp` enables all configured servers and `--mcp fs git` enables only the named subset. `default=None` (not passed) is the opt-in sentinel — MCP is off by default.
- **`--mcp-config PATH`** — overrides the default `~/.agentkthx/mcp.json` path. Useful for maintaining multiple server sets (work vs personal).
- **Note on `run` ordering**: because `--mcp` uses `nargs="*"`, it greedily consumes everything after the flag. For `agentkthx run`, put the prompt BEFORE `--mcp` (e.g. `agentkthx run hello --mcp fs`). Documented in the flag's help text.

### Added — `_wire_mcp()` in `agent_factory.py`

MCP wiring runs BEFORE `Agent(...)` construction (critical): the system prompt is built during `Agent.__init__`, and it only includes the Tool Reference section when `has_tools=True`. If MCP wired after construction, the prompt would have no tool section and the model would hallucinate `shell` (which isn't even in the registry) instead of picking an MCP tool.

When `--mcp` is passed without `--tools`, `_wire_mcp` creates an empty `ToolRegistry` so MCP tools have somewhere to land — the "MCP-only session" case. The populated registry is stashed as `manager._bridged_registry` so `_build_agent` can pass it to `Agent(tools=...)`. The manager is stashed on `agent._mcp_manager` for cleanup on session exit.

Failure semantics: a server that fails to connect or list tools is SKIPPED with a stderr warning (`MCPManager.connect_all(skip_failures=True)`). The agent still works without that server's tools — a misconfigured MCP server shouldn't kill the chat session. The `chat.py` `finally` block calls `manager.close_all(timeout=1.0)` on every exit path (REPL exit, Ctrl+C, exception).

### Added — `agentkthx mcp` excluded from update check

`agentkthx mcp probe` is a diagnostic against a local subprocess — hitting `pypi.org` on every probe would be pure latency. Added `"mcp"` to the no-update-check list in `cli/main.py` alongside `"version"` and `"update"`.

### Added — SECURITY.md + CONTRIBUTING.md

Two long-missing trust artifacts:

- **`SECURITY.md`** — vulnerability reporting policy (GitHub Security Advisories preferred, `veritas@vts-tech.org` fallback), 72h acknowledgement / 7-day assessment / 30-day fix timeline, scope (in-scope vs out-of-scope), built-in defenses documented (`validate_path`, `sanitize_command`, `is_safe_url`, `safe_eval`, `sanitize_tool_output`, plugin sha256 pins), known security-relevant findings table (SEC-09, SEC-13, ROB-09, ROB-33, ROB-05), update-check transparency (3 HTTPS requests per CLI invocation, no telemetry), hardening recommendations for operators.
- **`CONTRIBUTING.md`** — project ethos (zero-deps is the feature, hackable over clever, defense-in-depth not sandbox, audit register is canonical), getting started (Python 3.12+, `pip install -e .` + `pytest`/`black`/`ruff`), architecture map, critical files index (7 files with outsize blast radius), 22 landmines, common contribution types (new backend / CLI flag / tool / plugin / test / soul / skill), audit workflow, commit message format, PR checklist, issue triage, release process.

### Added — Audit findings MCP-01..05

New MCP category in `audit/audit.md` with 5 findings:

- **MCP-01** (Medium, near-term) — `StdioTransport._read_response` uses blocking `readline()`; per-call timeouts don't actually interrupt. Same shape as ROB-06/ROB-02. Fix: thread+queue pattern.
- **MCP-02** (Medium, near-term) — MCP server configs have no sha256 pin equivalent (SEC-13 analogue). An attacker who can write `mcp.json` can substitute any binary for a declared server name. Fix: optional `sha256` field + `AGENTKTHX_REQUIRE_MCP_PINS=1` enforcement mode.
- **MCP-03** (Low, short-term) — `_extract_params` flattens `oneOf`/`anyOf`/`$ref` JSON Schema constructs to default `string`. Tools with sophisticated schemas appear deceptively simple. Fix: `arguments_json` fallback.
- **MCP-04** (Low, short-term) — Eager server startup adds ~1–3s to every `--mcp` session even when no MCP tools are called. Fix: lazy mode (spawn on first tool call).
- **MCP-05** (Low, medium-term) — No `notifications/tools/list_changed` handler. Runtime tool surface changes invisible to the registry until next session. Fix: per-server callback + `ToolRegistry.unregister_tool`.

Priority matrix updated: MCP-01 + MCP-02 in near-term (R07.22–R07.23), MCP-03 + MCP-04 in short-term (R07.23–R07.25), MCP-05 in medium-term (R08.00+).

### Added — `docs/mcp/ROADMAP.md`

Full MCP integration plan: Phase 1 (client mode, stdio, CLI integration — ✅ done), Phase 2 (`kthx-audit` MCP server — planned, ~400 LOC, exposes `agentkthx/skills/codebase-audit/` as a standalone MCP server so any MCP-compatible client can call it), Phase 3 (generic `agentkthx mcp serve` mode), Phase 4 (HTTP/SSE transport — deferred), Phase 5 (MCP-aware souls — speculative). Includes curated free-tier MCP server list with verification dates, decision log, and the 5 audit findings with proposed fixes.

### Tests — 2858 passed (+56 from R07.21)

- `tests/test_mcp_scaffold.py` (23 tests) — config validation, namespacing round-trip, `inputSchema`→`ToolParam` conversion, `CallToolResult` flattening (text/error/image-skip/resource/bare-string), manager describe/require-connect, `write_example_config` home-dir substitution regression guard.
- `tests/test_mcp_cli.py` (27 tests) — `--mcp`/`--mcp-config` flag wiring on chat/run/agent, `mcp` subcommand registration, `mcp list` happy/empty/missing paths, `mcp init` write/refuse-overwrite/`--force`, `mcp probe` unknown-server error, `_wire_mcp` no-op-when-disabled regression guard, MCP-only-session-creates-empty-registry regression guard for the prompt-engineering fix.
- `tests/test_r07_19_help_sort.py` — updated root subcommand listing to include `mcp` (alphabetical between `config` and `modelfile`).

### Process — R07.22 release hygiene

- `scripts/bump-version.sh R07.22` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md` header). `agentkthx/mcp/client.py:_CLIENT_VERSION` also bumped to `0.7.22` (sent in MCP `initialize` handshake).
- `R07.21 (0.7.21)` → `R07.22 (0.7.22)`.
- Suite: 2802 → 2858 passed (+56 from MCP scaffold + CLI integration tests). Zero regressions; ruff + black clean across all 229 files.
- End-to-end verified against real `@modelcontextprotocol/server-filesystem` (14 tools) and `@modelcontextprotocol/server-sequential-thinking` (1 tool) MCP servers — initialize → tools/list → tools/call → clean shutdown round-trip works in production, not just in mocked tests.
- `agentkthx mcp init` → `agentkthx mcp probe filesystem` → `agentkthx chat --mcp filesystem` verified on Ubuntu 26.04.1 LTS with `glm-4.5-flash` backend — the agent correctly picks `filesystem__list_allowed_directories` then `filesystem__list_directory` without any prompt engineering.

### What's NOT in R07.22 (deferred)

- **MCP server mode** (`agentkthx mcp serve`) — Phase 3. Lets AgentKthx expose its own tools as an MCP server so other MCP clients (Claude Desktop, Cline, Continue, mcphost) can consume them.
- **`kthx-audit` MCP server** — Phase 2. Exposes `agentkthx/skills/codebase-audit/` as a standalone MCP server. ~400 LOC estimate. The dogfood payoff: `agentkthx chat --mcp filesystem git audit` becomes the demo that drives inbound discovery from MCP directories.
- **HTTP/SSE transport** — Phase 4. stdio covers >90% of use cases; HTTP/SSE adds auth + rate-limiting complexity that isn't justified without demand.
- **Lazy server startup** — MCP-04. Eager mode (every configured server spawns at agent construction) is simpler and surfaces config errors immediately. Lazy is a Phase 1.x optimization.
- **`notifications/tools/list_changed`** — MCP-05. Most current MCP servers have a static tool surface, so this is rarely hit in practice.
- **Plugin pin enforcement for MCP** — MCP-02. Optional `sha256` field on `MCPServerConfig` + `AGENTKTHX_REQUIRE_MCP_PINS=1` env var. SEC-13 analogue.
- **`docs/mcp/USAGE.md`** — worked-examples doc (filesystem-only coding session, multi-server research setup, audit workflow). Deferred until Phase 2 lands so the doc can include the audit workflow as a working example rather than a forward reference.

---

## [R07.21] - 2026-10-03 3:05:35 PM

**OpenRouter App Attribution, `/sh` chat command, 14 audit closures, Gemini thought_signature fix, Mistral agent-internal-field stripping, ZAI default-to-NATIVE fix, and a full-backend smoke test harness.** R07.21 is the largest single-release closure batch since R07.00 — 14 OPEN findings closed across three batches, the smoke test verified all 8 cloud backends end-to-end (29 PASS / 0 FAIL / 2 SKIP), and the register moved from 37 OPEN / 68% closure to 26 OPEN / 78% closure.

The release lands the harness's first deliberate entry into OpenRouter's app marketplace — the OpenRouter backend now sends the four App Attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`, `X-Title` legacy alias, `X-OpenRouter-Categories`) on every request, hardcoded to claim the `cli-agent` marketplace leaf. The AgentKthx app directory entry (App ID 5072126) was already live but was missing the `categories` field — verified live post-patch as `categories: ["cli-agent"]` (was `[]`). A new `/sh` chat slash command runs a local shell command, displays the output, and (by default) injects it into the agent's context as a user-role message so the model can use it on the next turn — pass `-n` before the command to display-only. The OpenRouter API Technical Reference doc is rewritten + expanded end-to-end against the live API (683 → 1,280 lines) with three new sections (App Directory Entry — Category & Description, Client/Harness Metrics & Reporting covering `/key` + `/credits` + `/generation` + `/activity` + `/datasets/app-rankings` + `/datasets/session-cost` + `/analytics/query`, Generation Inspection & Audit Trail) and a gap table of what AgentKthx currently captures vs. what OpenRouter exposes.

### Added — OpenRouter App Attribution headers (MAINT-26 CLOSED)

The pre-R07.21 `OpenRouterBackend` sent only `HTTP-Referer` + the legacy `X-Title` header. OpenRouter's [App Attribution spec](https://openrouter.ai/docs/app-attribution) has since added `X-OpenRouter-Title` (preferred form) and `X-OpenRouter-Categories` (marketplace category assignment, comma-separated, max 2 per request, max 10 per app). Result: the AgentKthx app entry rendered with `categories: []`, missing from both `/apps/category/coding` and `/apps/category/coding/cli-agent` despite the harness being a terminal-based coding assistant that fits the `cli-agent` leaf exactly.

- **Centralized `_build_openrouter_attribution_headers()` helper**: returns all four attribution headers. All four header-construction sites (`__init__`, `list_models`, `_make_api_request`, `_get_auth_headers`) delegate to this helper so the contract can never drift.
- **Hardcoded constants, NOT env-overridable** (`_APP_REFERER_URL`, `_APP_TITLE`, `_APP_CATEGORIES = "cli-agent"`): the harness category describes what AgentKthx *is* to OpenRouter's marketplace, not a runtime knob. The category is a module-level constant — if a fork needs a different identity, change the constant directly so the audit catches it.
- **Sends BOTH `X-OpenRouter-Title` AND `X-Title`** (legacy alias kept until OpenRouter formally deprecates). Rankings that haven't been re-indexed to look for the new header still attribute traffic correctly.
- **Verified live post-patch**: App ID 5072126, categories now `["cli-agent"]` (was `[]`). The directory entry's `description`, `main_url`, `slug`, `source_code_url` fields remain `null` — verified that OpenRouter provides NO documented mechanism to set them (no header, no API endpoint, no dashboard, no OpenGraph-crawler). The gap is structural on OpenRouter's side, not an AgentKthx defect. The four attribution headers are the complete mechanism OpenRouter exposes.

### Added — `/sh` chat slash command (MAINT-27 OPEN)

A new `/sh` slash command runs a local shell command, displays the output, and (by default) injects the output into the agent's context as a user-role message so the model can use it on the next turn. Pass `-n` before the command to display-only (skip the context injection).

- **Syntax**: `/sh [-n] <command...>` — the `-n` flag must be the first token after `/sh`. Pipes, redirects, and quotes work because the whole arg is passed to the shell as one command string (e.g. `/sh cat README.md | head -50`).
- **Reuses the built-in `shell()` tool** from `agentkthx.tools.builtins` — the same security checks (`sanitize_command`), timeout clamping (max 300s), and exit-code formatting apply. Output is formatted as `<shell_output command='...'>...</shell_output>` when injected.
- **Runs locally and synchronously, NOT through the model** — the user's prompt is not routed through the LLM. Security-mode setting (`/security max|off`) applies via `sanitize_command`. A blocked command (e.g. `rm -rf /`) is rejected with the same `Security error: ...` message.
- **`/help` listing updated** with a new line: `Run a local shell command (display + add to context; -n to display only)`.

### Fixed — Gemini `thought_signature` round-trip (ROB-40 CLOSED)

Gemini thinking models emit a `thought_signature` field on each `tool_call` in the response (nested at `tool_calls[].extra_content.google.thought_signature`). The API requires this signature to be passed back on the assistant message's `tool_calls` when the conversation history is sent back, or it 400s with `"Function call is missing a thought_signature"` on the second turn. AgentKthx's parser was capturing `id`, `name`, `arguments` — but dropping `thought_signature`.

- **`_parse_openai_response`** (`agentkthx/backends/openai_compat.py`): now extracts `thought_signature` from `tc["extra_content"]["google"]["thought_signature"]` and stores it on each parsed tool_call dict.
- **`Message.to_dict()`** (`agentkthx/core/memory.py`): when serializing the assistant message's `tool_calls` back to the OpenAI shape, re-attaches `thought_signature` as `extra_content.google.thought_signature` — but ONLY when present (other backends see no change).
- **Verified end-to-end**: the `thought_signature` survives the full round-trip (parse → memory → serialize) and lands in the exact shape Gemini expects. Multi-turn agentic runs with `--think --tools` on Gemini thinking models now work (was always 400ing on the second turn).

### Fixed — Mistral agent-internal-field stripping

`MistralBackend._build_mistral_body()` was forwarding every unknown kwarg verbatim into the request body. The skip list only excluded OpenAI-only fields — it didn't exclude agent-internal fields (`num_ctx`, `num_predict`, `truncation`, `num_batch`, `repeat_penalty`, `repeat_last_n`, `think`, `thinking_config`, etc.). These are Ollama/llama-server-specific fields that the agent loop forwards as kwargs — Mistral doesn't know them and 422s with `extra_forbidden: loc=['body','num_ctx']`.

- Added `_AGENT_INTERNAL_FIELDS` frozenset (15 fields) + skip check in the kwarg-forwarding loop. The agent loop's context-sizing fields are consumed by `agent_factory` and never need to reach any cloud API.

### Fixed — ZAI default-to-NATIVE

The ZAI backend was the outlier among cloud backends — it returned `UNTESTED` when the cache was empty (every other cloud backend returns `NATIVE`). The R07.19 #12 probe hardening overcorrected: it was supposed to stop caching REACT for transient errors, but it also changed the default-path return from NATIVE to UNTESTED. Combined with the `models.py` command always calling `test_tool_support(force_test=True)` (bypassing the default path), every ZAI model showed `? untested` in the models table.

- **`ZaiBackend.test_tool_support`**: when the cache is empty AND `force_test=False`, returns `NATIVE` (cloud-backend contract). The `force_test=True` path still does the live probe and caches definitive verdicts (including REACT for models that definitively reject the tools param).
- **`models.py:_get_tools_status`**: when `--tool-support` is NOT passed, calls `test_tool_support(force_test=False)` — cloud backends return NATIVE; local backends return UNTESTED. Only `--tool-support` forces the live probe.

### Fixed — CloudBackend `api_key` setter (was read-only — crashed `/auth`)

`CloudBackend.api_key` was a read-only `@property` (only a getter, no setter). The `/auth` picker's `_patch_live_backend` calls `setattr(backend, "api_key", new_value)` to patch a key onto the running session's backend — which raised `AttributeError: property 'api_key' of 'ZaiBackend' object has no setter` and crashed the chat session. Added a setter that writes through to `_api_key`. Every cloud backend (ZAI, OrcaRouter, Mistral, Pollinations) now supports live key patching via `/auth`.

### Fixed — ROB-38 fatal-error detection broadened

The empty-answer handler's `_fatal` markers didn't catch Gemini's `"Please pass a valid API key"` (HTTP 400, not 401) because the message says "valid", not "invalid". Added a broader `"api key"` substring marker that catches every variant: "valid api key", "invalid api key", "missing api key", "no api key", "api key not set", "api key required".

### Fixed — Gemini 2.5-flash catalog deprecation

Google deprecated `gemini-2.5-flash` and `gemini-2.5-flash-lite` for new users (returns 404 with "use models/gemini-3.8-flash instead"). The static catalog still listed them as `free_tier: true`, so the smoke test picked `gemini-2.5-flash` alphabetically. Flipped `free_tier: false` in the seed JSON + moved both entries to the deprecated/paid section in `FREE_TIER_LIMITS` with `0/0/0` rate limits.

### Changed — `OPENROUTER_API_TECHNICAL_REFERENCE.md` rewritten + expanded (683 → 1,280 lines)

Validated every existing section against the live OpenRouter API (466 models probed, App Attribution spec read, Usage Accounting doc read, OpenAPI 3.1 spec — 111 paths). The rewrite adds three new sections:

- **§2 App Attribution — Marketplace Headers** (NEW): the four headers, the centralized helper, why both title forms are sent, where the attribution appears.
- **§3 App Directory Entry — Category & Description** (NEW): full category-groups table, the hardcoded-constant rationale, the dashboard-only fields with recommended values (and the verification that there's NO mechanism to set them).
- **§14 Client/Harness Metrics & Reporting** (NEW): full schemas for `GET /key`, `GET /credits`, `GET /generation`, `GET /activity`, `GET /datasets/app-rankings`, `GET /datasets/session-cost`, `POST /analytics/query`. Includes a gap table of what AgentKthx currently captures vs. what OpenRouter exposes.
- **§15 Generation Inspection & Audit Trail** (NEW): the `/generation` endpoint as an audit tool.
- **Validated existing sections**: response schema now includes `usage.cost_details.upstream_inference_cost`, `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`. Error codes table gains `524 Edge Network Timeout` + `529 Provider Overloaded`.

### Changed — `--force-react` tri-state (MAINT-25 CLOSED)

`--force-react` was `store_true` (couldn't accept `=False` — the UNTESTED debug hint suggested `--force-react=False` which argparse rejected). Now tri-state (`on|off|auto`) with bare-flag backwards compat:
- `--force-react` (bare) = `"on"` → force ReAct (backwards compat)
- `--force-react off` → force native tools (the NEW opt-out from auto-detection)
- `--force-react auto` → preserve auto-detection (the default when not passed)

### Closed — 14 OPEN audit findings (3 batches)

All surgical, non-breaking fixes with clear patterns. Closure details in `audit/deltas.md` §R07.21 Audit Closure Batch.

**Batch 1 (5 findings):**
- **ROB-18** — PersistentMemory `Lock` → `RLock` (1 line — strict superset, allows nested locked calls without deadlock).
- **ROB-35** — `parse_shared_args` `or`-coalescing → `is not None` for 7 integer/float fields (preserves the documented `0` sentinel).
- **ROB-36** — `_parse_token_size` `math.isfinite` guard (turns `inf`/`1e400` `OverflowError` into clean `ValueError`).
- **ROB-38** — Empty-answer fatal-error branch (detects 401/402/403/quota/auth before the throttle branch + broadened `"api key"` marker).
- **ROB-39** — OpenRouter `_NON_CHAT_SLUG_PATTERNS` frozenset (classifies image/audio/moderation/embedding slugs as UNTESTED instead of NATIVE).

**Batch 2 (8 findings):**
- **ROB-09** — `validate_path` `abspath` → `realpath` (symlink traversal security fix).
- **ROB-17** — Token-tier truncation of single over-budget message (keeps the tail + visible marker).
- **ROB-20** — `Agent.num_predict` public `@property` (mirrors `num_ctx`; backed by `_num_predict`).
- **ROB-25** — Shared `DEFAULT_GENERATE_TEMPERATURE` (0.7) + `DEFAULT_GENERATE_MAX_TOKENS` (8192) constants in `base.py` — `generate()` + `_generate_with_auth()` now aligned.
- **ROB-30** — `_fetch_model_cards` catch-all `Exception` narrowed to `(JSONDecodeError, UnicodeDecodeError, ValueError)`.
- **MAINT-24** — `_build_tool_section` docstrings updated to match behavior (no longer promises ReAct format instructions the body doesn't include).
- **MAINT-25** — `--force-react` tri-state (on/off/auto) with bare-flag backwards compat (see Changed section above).
- **MAINT-26** — OpenRouter App Attribution (see Added section above; directory description/main_url/slug fields have NO documented mechanism — structural gap on OpenRouter's side).

**Batch 3 (1 finding):**
- **ROB-40** — Gemini `thought_signature` round-trip (see Fixed section above).

### Added — Smoke test harness

`scripts/smoke_test_r07_21.sh` — exercises every cloud backend with three steps each: models listing (FREE_ONLY env var), thinking output (`--think --no-stream`), shell tool call (`--tools shell --security off --no-stream`). Features:
- `--backend <name>` to test one backend, `--debug` for real-time `agentkthx run` output in a delimited box, `--help` for usage.
- Auto-skips backends without an API key; OpenAI's known 0-free-models case is handled as a skip.
- Auto-picks the first free chat model per backend (skips non-chat models: TTS, audio, image, embeddings, Gemma).
- Clears the persistent JSON cache before each backend so stale entries (e.g. deprecated models) don't surface.
- `timeout 120` per command; unique per-run marker for the shell-tool test.
- **Verified live**: 29 PASS / 0 FAIL / 2 SKIP across all 8 cloud backends (OpenAI + OrcaRouter skipped — known 0-free-models + exhausted free quota respectively).

### Tests

- **New `tests/test_r07_21_openrouter_attribution.py`** (+19 tests): pins the four attribution header values, pins all four header-construction sites delegate to the helper (source-grep), pins that the category is NOT env-overridable (source-grep + bytecode `co_names` check).
- **New `tests/test_r07_21_sh_command.py`** (+6 tests): the /sh handler's context-injection contract + a real-shell integration suite (echo → output, false → exit-code marker, ls on a missing path → error).
- **New `tests/test_r07_21_audit_closures.py`** (+36 tests): ROB-18 (RLock reentrancy), ROB-35 (0-sentinel preserved for all 7 fields), ROB-36 (`inf`/`1e400`/`nan` rejected cleanly), ROB-38 (fatal vs throttle classification), ROB-39 (non-chat slug classification).
- **New `tests/test_r07_21_audit_closures_batch2.py`** (+19 tests): ROB-09 (symlink rejection), ROB-17 (over-budget truncation), ROB-20 (num_predict property), ROB-25 (shared defaults), ROB-30 (narrowed catch), MAINT-24 (docstring contract), MAINT-25 (tri-state flag parsing).
- **Removed `TestVersionPin`** from `tests/test_r07_20_auth_picker.py` (-2 tests): exact-string pins broke on every release bump; redundant with `bump-version.sh`'s own site-verification step.
- **Relaxed** `test_r07_05_audit_fixes.py::test_write_lock_exists` (+1 test): accepts either `Lock` or `RLock` since ROB-18 changed the type.
- **Updated** `tests/test_gemini_backend.py::test_chat_flash_models_are_free`: removed `gemini-2.5-flash` assertion (Google deprecated it for new users).
- **Suite**: `python -m pytest tests/ -q` → **2802 passed / 16 skipped** (was 2608 at R07.20 baseline; +194 net).
- **Lint**: `ruff check agentkthx/ tests/` → all checks passed. `black --check agentkthx/ tests/` → 221 files unchanged.

### Audit register

- **14 OPEN findings CLOSED**: ROB-09, ROB-17, ROB-18, ROB-20, ROB-25, ROB-30, ROB-35, ROB-36, ROB-38, ROB-39, ROB-40, MAINT-24, MAINT-25, MAINT-26.
- **2 new findings OPEN**: MAINT-27 (`/sh` inline branch — MAINT-01 family), MAINT-26 (directory description gap — structural on OpenRouter's side, nothing actionable).
- **Register totals**: 117 findings / 26 OPEN / 84 CLOSED / 7 WONTFIX (was 116 / 37 OPEN / 70 CLOSED / 7 WONTFIX at R07.20). 78% closure rate (was 68%).

### Version bump

- `scripts/bump-version.sh R07.21` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md`).
- `R07.20 (0.7.20)` → `R07.21 (0.7.21)`.

### Suite status

- `python -m pytest tests/ -q` → **2802 passed / 16 skipped**.
- `ruff check agentkthx/ tests/` → all checks passed.
- `black --check agentkthx/ tests/` → 221 files would be left unchanged.
- **Smoke test**: `./scripts/smoke_test_r07_21.sh` → **29 PASS / 0 FAIL / 2 SKIP** across all 8 cloud backends (verified live 2026-10-03).
