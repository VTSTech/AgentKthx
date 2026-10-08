# Backend Support Tiers

AgentKthx ships 10 cloud backends plus TurboQuant (local llama.cpp) and
Ollama (local). As of R07.27, the cloud backends are split into two
support tiers based on **owner testing coverage** — not on code
quality, completeness, or feature surface.

## Why two tiers?

The maintainer (VTSTech) personally tests every backend before a
release. For an extended period, three backends (Pollinations,
OrcaRouter, OpenAI) have been impossible to test end-to-end because
their API keys are beyond their limits / out of quota / have
practical usage issues that the maintainer can't reproduce or resolve
in the foreseeable future. Rather than ship those backends with the
implicit "fully tested" promise the other backends carry, this file
makes the distinction explicit so users know what to expect.

**This is not a code-quality judgment.** All 10 cloud backends share
the same `CloudBackend` base class (R07.05 MAINT-02), the same
retry-loop helpers (R07.24 MAINT-23), the same SSE streaming pattern,
the same tool-support detection, and the same JSON-endpoint layout.
A Limited Support backend can still work perfectly — it just hasn't
been *verified* to work by the maintainer in the recent releases.

## Fully Supported

These backends are tested by the maintainer in multiple workflows
before every release. Bug reports against them are prioritized.

| Backend | Provider | Env Var | Free Tier |
|---------|----------|---------|-----------|
| **ZAI** | Z.ai (GLM models) | `ZAI_API_KEY` | Yes (`ZAI_FREE_ONLY=1`) |
| **OpenRouter** | openrouter.ai aggregator (500+ models) | `OPENROUTER_API_KEY` | Yes (`OPENROUTER_FREE_ONLY=1`) |
| **HuggingFace** | huggingface.co (inference endpoints) | `HF_TOKEN` | Yes (`HF_FREE_ONLY=1`) |
| **Gemini** | Google AI Studio (Gemini + Gemma) | `GEMINI_API_KEY` | Yes (`GEMINI_FREE_ONLY=1`) |
| **Mistral** | mistral.ai (La Plateforme) | `MISTRAL_API_KEY` | Yes (`MISTRAL_FREE_ONLY=1`) |
| **Cloudflare** | Cloudflare Workers AI (20+ open models) | `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` | Yes (`CLOUDFLARE_FREE_ONLY=1`) — 10,000 neurons/day, UTC reset |

Plus the local backends:
- **TurboQuant** — llama.cpp's `llama-server` binary (primary local backend)
- **Ollama** — local Ollama daemon
- **BitNet** — Microsoft BitNet (runs through TurboQuant with `_bitnet_mode=True`)

## Limited Support

These backends ship with the same code-quality guarantees as the
Fully Supported ones (same `CloudBackend` base, same regression
tests, same audit findings closure path) but the maintainer has been
**unable to test them end-to-end** for an extended period due to API
key quota issues beyond their control. They are *expected* to work
but have not been recently verified.

| Backend | Provider | Env Var | Status | Why Limited |
|---------|----------|---------|--------|--------------|
| **Pollinations** | pollinations.ai (keyless + keyed) | `POLLINATIONS_API_KEY` | Limited | API key beyond limits; maintainer can't test the keyed path. The keyless path works (it's the only keyless backend) but the keyed entitlement-aware fallback filter (ROB-31, still OPEN) is untested. |
| **OrcaRouter** | orcarouter.com (zero-markup gateway) | `ORCAROUTER_API_KEY` | Limited | API key beyond limits; maintainer can't test the streaming or non-streaming paths. The backend shares the same code path as OpenRouter (its sibling) so it's *likely* functional. |
| **OpenAI** | openai.com (GPT models) | `OPENAI_API_KEY` | Limited | Maintainer has no active OpenAI account. OpenAI has no free tier (the `--free` listing returns 0 models — known + documented in the smoke test). Bug reports against the OpenAI backend are accepted but the maintainer can't reproduce them without an active key. |

### What "Limited Support" means in practice

- **Bug reports**: accepted, but the maintainer can't reproduce them
  without an active key. Users who report bugs and can test a fix
  will get fast turnaround; users who report bugs without a
  reproducer may wait until the maintainer has key access again.
- **Smoke test coverage**: the smoke test (`scripts/smoke_test.sh`)
  exercises all 9 cloud backends, but Limited Support backends are
  expected to skip with "no API key" on the maintainer's machine. A
  user with a working key can run `./scripts/smoke_test.sh
  --backend pollinations` to verify their own setup.
- **Audit findings**: findings filed against Limited Support backends
  (e.g. ROB-31 Pollinations entitlement mismatch, FEAT-08
  Pollinations free-TIER mode) are not blocked by the Limited Support
  status — they're still OPEN and will be closed when a fix is
  implementable without live testing.
- **Code changes**: Limited Support backends receive the same code
  changes as Fully Supported ones (e.g. R07.25 ROB-06 deterministic
  HTTP close was applied to all 8 cloud backends, including the 3
  Limited Support ones). The `getattr(self, "_close_http_response",
  None)` guard pattern means the helper degrades gracefully even if
  the backend's MRO doesn't include `CloudBackend`.

### Promoting a backend to Fully Supported

A Limited Support backend is promoted to Fully Supported when the
maintainer regains working API key access AND runs the smoke test
end-to-end successfully. There's no automated promotion path —
it's a manual status change in this file + the README features
bullet.

### Demoting a backend to Limited Support

A Fully Supported backend is demoted to Limited Support when the
maintainer's API key becomes unusable for an extended period AND
they don't expect to regain access in the foreseeable future. The
demotion is announced in the release notes + this file.

## Local backends (always Fully Supported)

The local backends (TurboQuant, Ollama, BitNet) are always Fully
Supported because the maintainer can test them locally without any
external API key. They're not listed in the cloud-backend tables above.

## Plugin backends (out of scope)

The `agentkthx/plugins/test-plugin/` fixture and any user-installed
external plugins (under `~/.agentkthx/plugins/`) are out of scope for
this support-tier policy. Their support status is the responsibility
of their respective maintainers.

## Changelog

- **R07.27** (2026-10-08): Cloudflare Workers AI added as a new cloud
  backend and **promoted straight to Fully Supported** after the
  maintainer's end-to-end smoke test passed 5/5 (`./scripts/smoke_test.sh
  --backend cloudflare`): models listing, non-streaming inference,
  non-streaming shell tool, streaming shell tool, all on a free-tier
  account using `@cf/meta/llama-3.3-70b-instruct-fp8-fast`. The smoke
  test also verified the batch-3 paid-only cache fix: the log line
  `[Cloudflare] CLOUDFLARE_FREE_ONLY=true: filtered out 2 cached
  paid-only model(s) from the catalog` confirmed the `cf-paid:<model>`
  cache (populated by earlier 403/5035 hits on `@cf/zai-org/glm-5.3-flash`
  + `@cf/zai-org/glm-5.2`) auto-filtered them out before the model picker
  ran — so the smoke test only saw models the free plan can actually
  access. Scaffolds the full plugin (`agentkthx/plugins/cloudflare/`,
  ~750 LOC + 115 unit tests) modeled on the NVIDIA NIM pattern but with
  the unique Cloudflare twists: account-ID baked into the URL path,
  no `/v1/models` on the OpenAI-compat path (uses native
  `/ai/models/search` with a Cloudflare-shaped `{result, success}`
  envelope — NOT OpenAI's `{data}`; the model ID lives in `name` not
  `id`, the category lives in `task.name == "Text Generation"` not a
  lowercase `type` field), daily-neuron-quota 429 classification, and
  403/5035 paid-plan-only detection + cache. The 9 prior cloud backends
  unchanged.
- **R07.25** (2026-10-06): Support tier policy introduced. Pollinations,
  OrcaRouter, and OpenAI demoted to Limited Support. ZAI, OpenRouter,
  HuggingFace, Gemini, Mistral confirmed as Fully Supported. Local
  backends (TurboQuant, Ollama, BitNet) confirmed as Fully Supported
  (always have been — no external API dependency). Documented in
  `docs/SUPPORT.md` + referenced in `README.md` Features section.
