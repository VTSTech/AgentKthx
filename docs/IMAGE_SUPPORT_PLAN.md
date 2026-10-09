# Image Support Plan — Ollama First

> **Planning document** — image INPUT (vision) on local inference, then image OUTPUT.
> **Context (2026-10-10)**: OpenAI / Pollinations / OrcaRouter API keys are exhausted and will
> not be renewed. AgentKthx was originally written for local inference before cloud backends
> existed — this plan returns to that root. First target: **Ollama + `qwen3-vl:2b`** as the
> live test model.
> **Answer to the triggering question up front**: Qwen3-VL is image **INPUT** only (a
> vision-language model — it looks at images, it does not paint them). See §2.
> **Related**: `docs/ARCH.md`, `audit/audit.md`, `docs/R07.00-MODULARIZATION-PLAN.md` (format precedent)

---

## Table of Contents

1. [Goal & Non-Goals](#1-goal--non-goals)
2. [Model Facts — Qwen3-VL and Ollama Image Generation](#2-model-facts)
3. [Current-State Audit (what the tree already has)](#3-current-state-audit)
4. [Design](#4-design)
5. [CLI / UX](#5-cli--ux)
6. [Security Notes](#6-security-notes)
7. [Test Plan (qwen3-vl:2b as live fixture)](#7-test-plan)
8. [Phasing & Acceptance Criteria](#8-phasing--acceptance-criteria)
9. [Risks & Open Questions](#9-risks--open-questions)
10. [Out of Scope](#10-out-of-scope)

---

## 1. Goal & Non-Goals

**Goal**: attach images to a conversation and have a local vision model reason about them,
end to end, with zero API keys — starting with the Ollama backend and `qwen3-vl:2b`, and
folding cloud backends in later behind the same house-format change.

**Non-Goals (this document)**:

- TTS / ASR / audio pipeline (separate plan; Pollinations TTS is gone with the key anyway).
- Making DDG multimodal — it is text-only and prompt-less by design (R07.30).
- Training / fine-tuning; model management beyond what `list_models()` already does.

## 2. Model Facts

### 2.1 Qwen3-VL — image INPUT only

Qwen3-VL is Qwen's vision-language model family (2B / 4B / 8B / 32B / 235B dense+MoE
variants). Verified against the current Ollama registry page (`ollama.com/library/qwen3-vl:2b`),
the Qwen3-VL GitHub repo, and the Unsloth run/fine-tune guide:

| Capability | Supported? | Notes |
|---|---|---|
| Image understanding | **YES** | caption, VQA, OCR, charts, documents; high-res input (≥1024×1024 tiles) |
| Video understanding | **YES** | the family accepts video input; 2B in Ollama is the practical entry point |
| Visual grounding | **YES** | outputs bounding-box coordinates as TEXT — this is still text output |
| GUI / computer-use | **YES** | recognizes UI elements, buttons — "visual agent" framing on the model card |
| Native tool calling | **YES** | the model card ships a tool-calling template; matters for the ReAct interplay (§7) |
| Context | 256K | extendable to 1M upstream; Ollama default num_ctx is much lower — check `/api/show` |
| **Image generation** | **NO** | it is a VLM, not a diffusion model. Text/bbox out, pixels in — never pixels out |

**Consequence**: "image output" is a DIFFERENT problem with a different model class
(diffusion). Do not couple the two — image input ships first and stands alone.

### 2.2 Ollama image generation — exists, but not for us yet

Ollama added image generation on **2026-01-20** (blog: "Image generation (experimental)"):

- **macOS only** at release; Windows/Linux "coming soon" as of the 2026-06 blog state.
- Models: Z-Image, FLUX.2 — served separately from the LLM chat path (not `/api/chat`).

AgentKthx runs on Ubuntu VMs, so the Ollama image-gen path is **not actionable today**.
When Linux support lands it becomes the cleanest local image-OUT route (same server we
already talk to); until then the realistic local options are a sidecar diffusion service
(ComfyUI / A1111 / stable-diffusion.cpp) wrapped as a TOOL (the agent calls `image_gen`,
gets a file path back) — evaluated in Phase P3, not before.

## 3. Current-State Audit

What the tree already has (recon 2026-10-10, all file:line refs verified):

| Asset | Location | Relevance |
|---|---|---|
| Ollama backend (local-first) | `backends/ollama.py` (1,557 lines) | `OllamaBackend(OpenAICompatibleBackend)`, `is_cloud = False` (MAINT-05), dual API modes: native `/api/chat` (OPENRE) + OpenAI-compat `/v1/chat/completions` |
| Message format choke point | `backends/ollama.py:94` `_convert_messages_to_ollama_format()` | THE single place native-mode bodies are built — image injection point for OPENRE mode |
| Live capability rail | `backends/ollama.py:1079` `_capabilities_map()` + `:1122` `model_capabilities()` | `/api/tags` reports a per-model `capabilities` array — Ollama emits `"vision"` for VLMs exactly like it emits `"tools"` (R07.19 precedent). Vision detection is a field read, not a probe |
| Per-model metadata | `backends/ollama.py:919` `get_model_info()` (`/api/show`) | context length, family — needed for image token budgeting |
| OpenAI-compat transport | `backends/ollama.py:406` / `:674` / `backends/cloud_base.py:74` | compat mode accepts standard `content` part-lists with `image_url` data-URLs — near-free for compat path |
| Part-list tolerance precedent | `plugins/duckduckgo/duckduckgo.py` `_coerce_content()` | a backend already flattens OpenAI-style part-lists to text — the house layer may carry parts without breaking every backend |
| Non-str payload precedent | `core/memory.py:65-107` (`extra_content` / thought_signature) | `Message` already serializes provider-adjacent extra payloads — extension pattern exists |
| **THE blocker** | `core/memory.py:46` `Message.content: str` | token accounting (`len(content)//4`), compaction, tool-loop transcript rendering, footer, and several backends all assume string content |

Also relevant: the model seed (`data/model_seed.json`) carries only
context/temperature/max_tokens/pricing — no modality flags. Ollama needs NO seed entry:
its catalog is live-discovered via `/api/tags` and its capabilities are live-read, so
Ollama vision support is purely code, no seed surgery.

## 4. Design

### D1 — Widen `Message.content` to `str | list[Part]`

Introduce a minimal part model in `core/memory.py` (or `core/types.py`):

```
TextPart(text: str)
ImagePart(source: str, media_type: str)   # source = file path, http(s) URL, or data: URL
```

- `Message.content` accepts `str` (unchanged) or `list[TextPart|ImagePart]`.
- Serialization: `to_dict()` emits OpenAI-style part dicts
  (`{"type": "text", "text": ...}` / `{"type": "image_url", "image_url": {"url": ...}}`)
  so every OpenAI-compatible consumer keeps working; `from_dict()` reverses it.
- **Gate everything on a single helper**: `as_text(content)` — the one function every
  string-context consumer (token estimate, compaction, ReAct transcript, footer,
  `/system` display, log lines) calls. Image parts render as `[image: <path> <WxH?>]`
  placeholders in text contexts. This keeps the blast radius of D1 to "call sites that
  must switch to `as_text()`", which grep can enumerate mechanically.
- Token accounting: images count as a conservative flat estimate (start: 1,024 tok per
  image, constant `IMAGE_TOKEN_ESTIMATE` in `core/memory.py`), replaced by
  provider-reported usage where available (Ollama reports `prompt_eval_count`, which
  already lands in `usage`). Compaction: **image parts are dropped first**, text survives.

### D2 — Ollama wire adapters (both API modes)

- **Native `/api/chat` (OPENRE)** — extend `_convert_messages_to_ollama_format()`:
  flatten part-list content to plain text, collect `ImagePart`s into the message's
  `images: ["<base64>"]` array (Ollama native format wants raw base64 WITHOUT the
  data-URL prefix, one entry per image, riding the SAME message dict). File-path and
  http(s) sources are read and base64-encoded here (stdlib `base64`/`urllib`), with a
  size guard (reject > 8 MB before encode with a clear message; Ollama re-encodes the
  vision pass every turn, so this cost recurs — see §9).
- **OpenAI-compat `/v1/chat/completions`** — pass part-lists through as-is
  (data-URL `image_url` parts). Ollama's compat layer accepts them; verify against
  `qwen3-vl:2b` live in P0.

### D3 — Capability detection ("vision" rides the R07.19 rail)

- `model_capabilities()` already returns the `/api/tags` array; add
  `supports_vision(model) -> bool | None` beside `test_tool_support()` —
  `"vision" in caps`. `None` (legacy server) → permissive-with-warning, same posture
  the tool path takes with the sampled probe.
- `cmd_models` gains a 👁 column/badge; `chat` banner shows `Vision: yes/no`.
- **Guard rail**: if images are attached and the active model is NOT vision-capable,
  fail fast with a clear error *before* the HTTP call (non-vision models either 400 or
  silently ignore images — both are bad failure modes).

### D4 — Image lifecycle & memory policy

- Images enter as `ImagePart` on the user message; base64 lives in the part (path kept
  alongside for display/re-encode).
- Sessions persistence (`sessions.py`): parts round-trip through JSON — verify size
  implications (a 5 MB image → ~6.7 MB base64 in every saved session; consider storing
  the path + re-reading on restore as a follow-up, not P0).
- DDG (`_coerce_content`) already flattens parts → text; no change needed there.

## 5. CLI / UX

- `agentkthx chat --image ~/shot.png --image https://...` — attach at session start
  (delivered with the first user turn).
- `/image <path-or-url>` — attach mid-session; the next send carries all pending images.
- `/images` — list attached/pending images.
- `agentkthx run "what's in this screenshot?" --image shot.png` — one-shot.
- Footer: existing `📝 chr` counter keeps counting TEXT only (images are budgeted
  separately via `IMAGE_TOKEN_ESTIMATE` in the ctx% math).
- Optional (P2): sixel/kitty inline render when `$TERM` supports it, else print the
  saved path. Never block on render failures.

## 6. Security Notes

Images are **untrusted input**, same class as tool output (the R07.30 `<tool_output>`
framing):

- OCR-extracted text inside an image is DATA, not instruction. When an image's text
  flows into the ReAct transcript, it must land inside the untrusted-data wrapping the
  same way tool output does — not as a bare user/assistant instruction turn.
- File reads for base64 encoding go through the same path-allowlist thinking as the
  `shell` tool's restrictions: `--image` accepts a path the user typed, but a MODEL
  (via ReAct) must never gain the ability to request arbitrary file reads through an
  `image` tool argument in later phases.
- URL sources: reuse the SSRF guard already built for webfetch
  (`tools/builtins.py:382` `_SSRFSafeRedirectHandler`) rather than raw urllib fetch.

## 7. Test Plan

Live fixture: `ollama pull qwen3-vl:2b` (~1.8 GB Q4; runs in ~3 GB VRAM / CPU-fallback
OK for smoke tests). Fixture images land in `tests/fixtures/images/` (tiny synthetic
PNGs generated in-test via zlib+struct — no binary blobs in git, house is zero-dep):

1. **Smoke**: single-image VQA ("what color is the square?") — native mode + compat mode.
2. **OCR**: text-in-image extraction round-trips through `as_text()`.
3. **Multi-image**: two images on one turn; assert `images` array order preserved.
4. **Capability gating**: `supports_vision()` reads `"vision"` from a canned `/api/tags`
   fixture; image + non-vision model → pre-flight error (no HTTP).
5. **Accounting**: ctx math includes `IMAGE_TOKEN_ESTIMATE`; compaction drops image
   parts first and text last.
6. **Serialization**: `Message` part-list round-trip (`to_dict`/`from_dict`), session
   save/restore with an attached image.
7. **DDG coexistence**: part-list message hitting the DDG backend flattens cleanly
   (extends the existing `_coerce_content` tests).
8. **Streaming**: image + `generate_stream()` — deltas unaffected by image parts.
9. **Tools × vision** (live, P1 exit): qwen3-vl declares tool support — verify ReAct
   still works with an image in context (this is the integration case the cloud
   providers 400'd on; local should NOT).

House sizing estimate: ~1,200–1,500 new test lines (R07.30 added 1,409 for one protocol
rewrite; this is a comparable surface).

## 8. Phasing & Acceptance Criteria

| Phase | Scope | Acceptance |
|---|---|---|
| **P0** (this plan's core) | D1 widening + D2 ollama adapters + D3 capability gate + `/image` command + `--image` flag; qwen3-vl:2b smoke suite green | `agentkthx chat --backend ollama --model qwen3-vl:2b --image tests/fixtures/...` answers VQA correctly; full suite green; ruff/black clean |
| **P1** | Cloud backends ride the same format (zai / openai-compat family part-list pass-through + per-provider quirks table); `modalities` field in seed catalogs for static-catalog backends | vision-in works on ≥1 cloud backend behind the same house format |
| **P2** | Terminal image render (sixel/kitty detect, path fallback); session size mitigation (path-ref storage) | `/image` preview renders on a sixel-capable terminal without crashing on xterm |
| **P3** | Image OUTPUT: ollama image-gen when Linux support lands, else sidecar diffusion wrapped as `image_gen` TOOL (artifact dir + path return; agent never renders inline) | `image_gen` tool produces a PNG file and returns its path through the normal tool-result path |
| **P4** | Mic/ASR and TTS — separate planning doc | — |

## 9. Risks & Open Questions

- **Per-turn re-encode cost**: Ollama re-runs the vision encoder on every turn the
  images are still in context — multi-turn chats with large images get slow and hot.
  Mitigation candidate (not P0): `/image --once` semantics (attach, consume, drop).
- **Memory bloat**: base64 in `Message.content` inflates transcripts, sessions, and
  `len(content)//4` accounting until D1's estimate lands. Decide: keep base64 inline
  (simple, fat) vs path-ref + lazy encode (thin, more states). P0 goes inline + 8 MB cap.
- **ReAct transcript rendering**: tool_parse renders history as text for the ReAct
  prompt — must route through `as_text()` or images leak into prompts as base64 walls.
- **openresponses.py / MCP**: if either validates content shape, it needs the part-list
  branch too (recon says openresponses touches content; verify during P0).
- **Legacy Ollama servers** (no `capabilities` field): permissive-with-warning posture,
  mirroring the tool-support fallback.
- **Ollama Linux image-gen timing** is outside our control — P3 has two candidate routes
  for exactly this reason.

## 10. Out of Scope

- Audio in/out (TTS, ASR, mic) — separate plan, separate model class.
- DDG multimodal — text-only by protocol, and prompt-less by R07.30 design.
- Video input — qwen3-vl supports it upstream, but P0–P2 scope is still images; video
  frames can be delivered as multi-image in the meantime.