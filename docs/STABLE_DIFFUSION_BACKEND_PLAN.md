# Stable Diffusion Backend Plan — sd.cpp via the Ollama Pattern

> **Planning document** — image OUTPUT on free local inference, following the **Ollama
> pattern**: AgentKthx never manages the server process; it registers an
> `OpenAICompatibleBackend`-derived, `is_cloud=False` backend that talks HTTP to an
> externally started server.
> **Rollout**: first exercised in the CPU-only `AgentKthx.ipynb` Colab notebook
> (which already carries the Ollama / BitNet / TurboQuant cells). The notebook compiles
> + serves `sd-server`; AgentKthx then talks to it with `--backend stable-diffusion`
> (shorthand alias: `--backend sd`).
> **Predecessor**: `docs/IMAGE_SUPPORT_PLAN.md` (image INPUT — qwen3-vl on Ollama; P3
> there anticipated this doc). **Supersedes** that doc's P3 image-output routes list.
> **Related**: `docs/ARCH.md`, `backends/ollama.py` (the pattern), `backends/cloud_base.py`
> (the shared transport), `plugins/_loader.py` (alias registration).

---

## 1. Goal & Non-Goals

**Goal**: `agentkthx run "a lovely cat" --backend sd` → PNG on disk, zero API keys,
zero GPU required (CPU-only Colab is the reference environment).

**Non-Goals**:

- AgentKthx does NOT compile, download, install, start, stop, or update sd.cpp —
  the notebook (now) and the user's VM (later) own the process lifecycle, exactly as
  with `ollama serve`.
- No image INPUT here (that is `docs/IMAGE_SUPPORT_PLAN.md` P0–P2).
- No sd.cpp WebUI proxying (`/sdapi/v1/*`) or the native async API (`/sdcpp/v1/*`)
  beyond what P0 needs — OpenAI-compatible surface only.

## 2. Verified Server Facts (sd.cpp master, 2026-10-10)

Checked against `examples/server/README.md`, `examples/server/api.md`, `README.md`,
and `docs/build.md` in leejet/stable-diffusion.cpp — these are PROTOCOL FACTS, same
standard the DDG reference holds:

| Fact | Value |
|---|---|
| Server binary | `sd-server` (CLI sibling is `sd-cli`), both land in `build/bin/` |
| Default bind | `http://127.0.0.1:1234/` (embedded web UI + all APIs) |
| Custom bind | `--listen-ip <ip> --listen-port <port>` |
| Logging | `--log-level debug|verbose|info|warn|error` (default `info`) |
| Model loading | classic single checkpoint `-m/--model <file>` (SD1.x/SDXL/turbo safetensors or GGUF); modular `--diffusion-model` + `--vae` + `--llm`/`--clip`/`--t5xxl` for modern pipelines (Z-Image etc.) |
| Server-side defaults | e.g. `--cfg-scale`, and friends are settable at LAUNCH (become per-request defaults) |
| **OpenAI-compatible surface** | `POST /v1/images/generations`, `POST /v1/images/edits`, `GET /v1/models` — **live-verified 2026-10-10 (Colab CPU, sd_turbo)**: `GET /v1/models` → `{"data":[{"id":"sd-cpp-local","object":"model","owned_by":"local"}]}` — ONE fixed pseudo-model id, **hardcoded in routes_openai.cpp** (source-verified at 228c707), never reflects the loaded weights; `list_models()` must never require a model-name match |
| **Loaded-model discovery (source-verified at 228c707)** | `GET /sdcpp/v1/capabilities` → `model: {name, stem, path}` = the REAL weights (resolver: `-m/--model` path, else `--diffusion-model` path, else all empty strings when started without either); also exposes `defaults` (steps/cfg/scheduler/sample_method), `limits` (64–4096 px, batch ≤ 8), `samplers`, `schedulers`, `output_formats`, `supported_modes`. A1111-compat alternates: `GET /sdapi/v1/sd-models` (title/model_name/filename — hash & sha256 are **hardcoded dummies** `8888888888…`) and `GET /sdapi/v1/options` (`sd_model_checkpoint` = stem). `list_models()` prefers capabilities → falls back to the `/v1/models` pseudo id |
| generations request fields | `prompt` (required), `n`, `size` (`WIDTHxHEIGHT`), `output_format` (`png`/`jpeg`/`webp`), `output_compression` (0..100). NOTE: **no `model` field** — the server serves its loaded pool |
| generations response | `{created, output_format, data: [{b64_json}]}` — base64 image bytes |
| Native extension | `sdcpp API` fields ride inside `prompt` via `sd_cpp_extra_args` (exact wire encoding pinned in P0 from api.md §OpenAI API) |
| Other APIs | `/sdapi/v1/*` (WebUI-style: txt2img, img2img, samplers, schedulers, sd-models, options, progress — hash fields dummy), `/sdcpp/v1/*` (capabilities, async jobs + cancel, upscale, img_gen, vid_gen) |
| `<lora:...>` prompt tags | intentionally unsupported on all three API families; LoRA via structured fields only |
| Verified model URLs | SD1.5: `huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors` (4.27 GB, HTTP 206 verified); sd-turbo: `huggingface.co/stabilityai/sd-turbo/resolve/main/sd_turbo.safetensors` (underscore filename — verified via HF API listing) |
| Release binary (fast path) | tag `master-948-228c707`, asset `sd-master-228c707-bin-Linux-Ubuntu-24.04-x86_64.zip` (25 MB): `sd-server` + `sd-cli` + bundled ggml per-microarch dispatch `.so`s (sse42…zen4) + webp encoders; `RUNPATH=$ORIGIN` (binaries find their bundled libs in their own dir — keep files together, no env vars); **requires GLIBC ≥ 2.38** (Ubuntu 24.04 build). Verified 2026-10-10 by download + execution (`--help` runs, commit matches tag) |

## 3. Pattern Alignment (what "the Ollama pattern" means here)

| Ollama precedent | sd.cpp equivalent |
|---|---|
| `OllamaBackend(OpenAICompatibleBackend)`, `backends/ollama.py:22` | `StableDiffusionBackend(OpenAICompatibleBackend)` in a new plugin `agentkthx/plugins/stablediffusion/` |
| `is_cloud = False` override (MAINT-05, `ollama.py:44`) | same — skips streaming-by-default, cloud column layout, billing-style errors |
| `OLLAMA_BASE_URL` in `config.py` | `SD_BASE_URL` in `config.py`, default `http://127.0.0.1:1234` |
| `BackendType.OLLAMA` enum entry | `BackendType.STABLE_DIFFUSION = "stable-diffusion"` in `core/types.py` |
| Plugin-era registration | plugin `register()`: `register_backend("stable-diffusion", StableDiffusionBackend)` **plus** `register_backend("sd", StableDiffusionBackend, alias_of="stable-diffusion")` — alias support already exists (`plugins/_loader.py:1404`, `_backend_aliases:711`) |
| Server process owned externally (`ollama serve` in the notebook) | `sd-server` started by the notebook cell / user shell |
| Live discovery via `/api/tags` | live discovery via `GET /v1/models` |
| Divergent wire translation in ONE override (`_convert_messages_to_ollama_format`) | divergent wire translation in ONE override: `generate()` maps the images-generation surface instead of chat completions |

The transport inheritance is the point: HTTP via the shared JEV/urllib machinery,
retry/error-recovery parity, debug labeling (`SD` label like `ZAI`/`OLLAMA`) all come
free from `OpenAICompatibleBackend`/`cloud_base.py`.

## 4. Design

### D1 — Backend class + registration

- `StableDiffusionBackend(OpenAICompatibleBackend)` with `is_cloud = False`,
  `backend_type = BackendType.STABLE_DIFFUSION`.
- `__init__(base_url=None, host=None, port=None)` mirrors Ollama's constructor shape;
  default `SD_BASE_URL`.
- Plugin `plugins/stablediffusion/` with `plugin.toml`-style manifest per
  `PLUGIN_SPEC_v0.2.md`, registering backend + alias as above.

### D2 — Endpoint mapping

- `list_models()` → `GET /v1/models`; the response's model list (server pool)
  becomes the catalog. `--model` is **advisory only** on this backend (the wire has no
  model field — §2); when the user passes a model that isn't in the pool, warn and
  proceed (server uses its loaded model), matching "no `model` field" reality.
- `generate(model, messages, …)` → concatenate/flatten the house messages to a single
  prompt (system content included, subject to §6), then
  `POST /v1/images/generations {prompt, n:1, size, output_format:"png"}`.
  Generation params: `steps` / `cfg_scale` / `seed` / `width` / `height` accepted as
  backend kwargs; whatever the OpenAI field set can't carry goes through
  `sd_cpp_extra_args` (encoding pinned in P0). Sample-params parity note: like DDG's
  temperature notice, unsupported chat-style kwargs are silently dropped with a
  debug-mode notice.
- Response handling: decode `data[0].b64_json` → write PNG to the artifacts dir
  (`AGENTKTHX_ARTIFACTS_DIR`, default `./generated/`, created on demand; filename
  `sd_<utc YYYYmmdd-HHMMSS>_<seed? or counter>.png`) → return the house shape with
  `content = "[image saved: <path>]"` (+ `images: [{"path": …}]` extension field
  following the `extra_content` precedent, `core/memory.py:65`). `usage` stays the
  house `{"estimated": True}` convention (the API reports none).
- `generate_stream()` → thin wrapper: run `generate()`, yield the final content as a
  single delta. No fake token streaming; house `is_cloud=False` behavior already
  prefers buffered for local backends.

### D3 — CLI / notebook UX

- `agentkthx run "a lovely cat" --backend sd` — one-shot, the primary surface.
- `agentkthx chat --backend sd` — legal but unusual: every turn produces an image;
  the REPL prints the saved path (+ inline render is IMAGE_SUPPORT_PLAN P2 territory).
- `cmd_models --backend sd` lists the server pool with the local-column layout
  (free via `is_cloud=False`, same as Ollama per MAINT-05).
- Health surfacing: connection-refused gets the same remediation-message treatment
  the DDG backend gives missing Node — "is sd-server running? (notebook cell /
  `sd-server -m <model> --listen-port 1234`)".

### D4 — Notebooks (the rollout vehicle)

New cells appended to `AgentKthx.ipynb` beside the Ollama/BitNet/TurboQuant
cells, matching house cell conventions (`Popen`+nohup+log, `pkill` first, `0.0.0.0`
bind for tunnels, Drive backups, `%cd /content`):

1. **Binary cell (primary; ~25 MB download, no compile)** — fetch the pinned
   release zip (`master-948-228c707`, see §2 fast-path row), unzip straight into
   the canonical path `stable-diffusion.cpp/build/bin/` so Model/Serve cells are
   path-identical to a source build; self-check `./sd-server --help`; on failure
   (host glibc < 2.38 — the asset is an Ubuntu 24.04 build) print a pointer to
   the compile fallback.
2. **Compile cell (fallback; ~10–20 min)** — clone `--depth 1 --recursive`
   (ggml is a git submodule; a plain `--depth 1` clone leaves `ggml/` empty and
   CMake configure fails with "does not contain a CMakeLists.txt file"),
   CPU-only CMake (no CUDA toolkit needed), build the `sd-server` target only,
   verify `build/bin/sd-server`, copy to Drive.
3. **Model cell** — download `sd_turbo.safetensors` (default; 1–4 steps ⇒ the only
   sane latency on Colab CPU) or `v1-5-pruned-emaonly.safetensors` (lighter RAM,
   more steps); Drive backup/restore pair like the Ollama model cells.
4. **Serve cell** — `pkill sd-server`; `subprocess.Popen` with
   `--listen-ip 0.0.0.0 --listen-port 1234 --threads <nproc>`; log to
   `sd_server.log`; poll `GET /v1/models` until it answers; print the pool.

Full cell bodies live in the Appendix below (paste-ready).

### D5 — CPU environment expectations (Colab free tier, ~2 vCPU / ~12.7 GB RAM)

| Model | File size | Steps | Expect. per 512×512 image |
|---|---|---|---|
| sd-turbo (default) | 5.14 GB | 1–4 | **~1–2 min** incl. load |
| SD1.5 emaonly | 4.27 GB | 20 | ~4–8 min |

RAM headroom is the constraint: one model loaded at a time; `--offload-to-cpu` helps
larger pipelines. Compile time on 2 vCPU: ~10–20 min (build `sd-server` only).

## 5. Security Notes

- The prompt sent to `/v1/images/generations` is user-authored text; when this later
  becomes an agent-callable TOOL (IMAGE_SUPPORT_PLAN P3), the prompt argument is
  model-generated — same untrusted-input discipline as any tool arg (no path/file
  exfiltration channels: the backend writes only to the artifacts dir with a
  generated filename; it never opens paths from the model).
- b64 decode is size-capped (reject `data[]` entries > 32 MB decoded before writing).
- Localhost binding by default; the notebook tunnel cell exposes it deliberately and
  the user should keep the tunnel one-shot (cloudflared quick tunnels) unless auth is
  added — sd-server has no built-in API-key auth (§2), so treat any tunnel as public.

## 6. Test Plan

Mocked-HTTP fixtures in the house style (`tests/test_stablediffusion_backend.py`,
modeled on the DDG suite's recorder pattern but with OpenAI-shaped bodies):

1. `list_models()` maps `/v1/models` → catalog.
2. `generate()` posts `{prompt, n:1, size:"512x512", output_format:"png"}`; message
   flattening covers system+user and multi-turn history.
3. b64 decode → PNG written to artifacts dir; house shape asserted (`content`,
   `images[0].path` exists on disk).
4. Oversized b64 → clean error, no file written.
5. Connection-refused → remediation RuntimeError text.
6. Alias: `get_backend("sd")` resolves the same class as `"stable-diffusion"`
   (loader `_backend_aliases` contract test).
7. `generate_stream()` yields exactly one delta equal to `generate().content`.
8. `sd_cpp_extra_args` encoding test (pinned against api.md in P0).

Live test: run the notebook's serve cell, then `agentkthx run "a lovely cat" --backend sd`
against `http://127.0.0.1:1234` — expect a PNG in `./generated/`. This is the
colab-verified acceptance for P0.

House sizing estimate: ~600–900 new test lines (smaller surface than DDG: no
challenge ladder, no SSE grammar).

## 7. Phasing & Acceptance Criteria

| Phase | Scope | Acceptance |
|---|---|---|
| **P0** | Notebook cells (Appendix) + backend plugin D1–D3 + tests 1–8; SD_BASE_URL/AGENTKTHX_ARTIFACTS_DIR config; enum entry | Colab CPU: compile → serve → `agentkthx run "a lovely cat" --backend sd` produces a PNG; full suite green; ruff/black clean |
| **P1** | Polish: `/v1/images/edits` (img2img) mapping, `GET /sdapi/v1/progress` surfacing into the chat footer during generation, model-pool switching via server restart detection | img2img round-trips; progress shown during long CPU generations |
| **P2** | `image_gen` TOOL wrapper on the same server (IMAGE_SUPPORT_PLAN P3 convergence); inline render handoff (sixel/kitty) | agent on any chat backend can call `image_gen` and cite the returned path |

## 8. Risks & Open Questions

- **No `model` field on the wire** — server pool selection semantics need a P0
  pin-down (api.md's `/v1/models` response shape + multi-model pool behavior with
  LRU eviction observed in third-party docs). **Partially resolved live
  2026-10-10**: single loaded model reports fixed id `sd-cpp-local`; the
  request-side no-`model`-field fact stands confirmed by the working generation.
- **CPU latency** — minutes per image on free Colab; manage user expectations in the
  notebook markdown (sd-turbo default exists precisely for this).
- **`sd_cpp_extra_args` encoding** — api.md says fields ride *inside* `prompt`; the
  exact syntax must be pinned from api.md §OpenAI API before P0 merges (test 8).
- **Artifact dir growth** — PNGs accumulate; P1 may add a retention knob
  (`AGENTKTHX_ARTIFACTS_KEEP`, default keep-all).
- **Frontend build requirement** — the server README mentions an embedded web UI built
  with Node/pnpm when building from source; P0 must confirm whether the default CMake
  build produces a working `sd-server` without the frontend toolchain (expected yes —
  the UI is optional — verify in the notebook cell). The release-binary fast path
  (D4 cell 1) sidesteps the build-side question entirely; whether the embedded UI
  ships inside the release binary is unverified (irrelevant for the API-only P0 path).

## Appendix — Notebook Cells (paste-ready, CPU-only Colab)

See the companion cells delivered with this plan (Binary / Compile / Model / Serve),
written to match the existing Ollama/BitNet/TurboQuant cell conventions in
`AgentKthx.ipynb`.
They are the authoritative copy for P0.
