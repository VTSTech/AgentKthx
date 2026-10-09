"""
⚛️ AgentKthx — Stable Diffusion Backend (sd.cpp)

Image-generation backend for an externally-managed sd.cpp server
(leejet/stable-diffusion.cpp). Follows the Ollama pattern exactly:

- The server process is NOT managed by AgentKthx. It is started outside
  (notebook serve cell / user shell: ``sd-server -m <model> --listen-port 1234``)
  and this backend only interacts with the resulting HTTP endpoints.
- Extends ``OpenAICompatibleBackend`` so the shared transport / debug
  conventions are inherited from one place (ARCH-01 parity with Ollama).
- ``is_cloud = False`` (R06.57 MAINT-05 precedent): local server — no rate
  limits, no billing, no cloud column layout, buffered output by default.

Wire facts (verified against sd.cpp source at the release tag
master-948-228c707 / commit 228c707 + live Colab server, 2026-10-10):

- ``GET /v1/models`` → ``{"data": [{"id": "sd-cpp-local", ...}]}``.
  ONE fixed pseudo-model id — hardcoded in routes_openai.cpp, NEVER
  reflects the loaded weights.
- ``GET /sdcpp/v1/capabilities`` → the REAL loaded weights:
  ``{"model": {"name": "sd_turbo.safetensors", "stem": "sd_turbo",
  "path": "/content/sd_models/sd_turbo.safetensors"}, ...}`` plus defaults
  (steps/cfg/scheduler), limits (64–4096 px, batch ≤ 8), samplers,
  schedulers, output_formats. All fields are empty strings when the
  server was started without ``-m`` / ``--diffusion-model``.
  A1111-compat alternates: ``GET /sdapi/v1/sd-models`` and
  ``GET /sdapi/v1/options`` (``sd_model_checkpoint``) — their hash and
  sha256 fields are hardcoded dummy values ("8888888888…"), so
  capabilities is the only worthwhile discovery surface.
- ``POST /v1/images/generations`` → ``{"prompt", "n", "size", "output_format"}``.
  NO ``model`` field on the wire — the server serves its loaded pool, so
  ``--model`` here is advisory only (warned, never sent).
- Per-request sample steps (R07.32): the OpenAI route has NO ``steps``
  field, but the server regex-extracts a ``<sd_cpp_extra_args>{...}
  </sd_cpp_extra_args>`` block from the prompt (routes_openai.cpp:11) and
  applies it via the native schema — ``{"sample_params":
  {"sample_steps": N}}``. The block MUST stay on ONE line (the extraction
  regex's ``.`` does not match newlines). Server clamps to 1–100 steps
  (strict resolve, common.cpp:2482). ``/sdapi/v1/options`` is GET-only,
  so the server DEFAULT (``--steps``) only changes on restart — but any
  request can override it. The A1111 surface (``/sdapi/v1/txt2img``)
  also takes ``steps`` directly as a body field.
- Response: ``{"created", "output_format", "data": [{"b64_json"}]}``.

See docs/STABLE_DIFFUSION_BACKEND_PLAN.md for the full design (D1–D5).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from agentkthx.backends.base import BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.config import SD_BASE_URL
from agentkthx.core.types import ApiMode, BackendType, ToolSupportLevel


class StableDiffusionBackend(OpenAICompatibleBackend):
    """
    Backend for a local sd.cpp image-generation server.

    Every chat turn becomes ONE image: the house messages are flattened
    into a single prompt (system prompt suppressed — DDG precedent),
    POSTed to ``/v1/images/generations``, and the returned base64 PNG is
    decoded (size-capped) into the artifacts dir
    (``AGENTKTHX_ARTIFACTS_DIR``, default ``./generated``). The response
    carries ``content = "[image saved: <path>]"`` plus an
    ``images: [{"path": ...}]`` extension field (extra_content precedent,
    core/memory.py) so callers can retrieve the artifact path.
    """

    #: R06.57 (MAINT-05) precedent — local server, overrides
    #: OpenAICompatibleBackend's True.
    is_cloud: bool = False

    #: Debug/monitoring label (like ZAI / OLLAMA).
    _provider_label: str = "SD"

    #: CPU generation on sd-server takes MINUTES (plan §D5: sd-turbo 1–4
    #: steps ≈ 1–2 min on Colab's 2 vCPU; SD1.5 at 20 steps ≈ 4–8 min).
    #: The 120 s BackendConfig default would abort mid-diffusion — 15 min
    #: covers the worst CPU case. Override via get_backend(timeout=...).
    GENERATION_TIMEOUT: int = 900

    #: Security (plan §5): reject data[] entries whose decoded size exceeds
    #: this cap BEFORE writing to disk.
    MAX_B64_DECODED_BYTES: int = 32 * 1024 * 1024

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENAI,
        sample_steps: int | None = None,
    ):
        # Resolve base URL — priority: base_url > host/port > config default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        else:
            resolved_url = SD_BASE_URL.rstrip("/")

        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())

        # CPU image generation needs the long timeout; only apply the
        # backend default when the caller didn't supply a config.
        if config is None:
            config = BackendConfig(timeout=self.GENERATION_TIMEOUT)

        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # R07.32: --max-steps (remapped by agent_factory for this backend
        # ONLY) lands here as the diffusion sample steps. None = no
        # override — the server's own --steps default applies untouched.
        self.sample_steps = sample_steps

    @property
    def backend_type(self) -> BackendType:
        return BackendType.STABLE_DIFFUSION

    @property
    def base_url(self) -> str:
        return self._base_url

    _sample_steps: int | None = None

    @property
    def sample_steps(self) -> int | None:
        """Per-request diffusion sample steps override (None = server default).

        Set by agent_factory from ``--max-steps`` when the backend is sd
        (every other backend keeps the reasoning-loop semantics). The
        value rides the wire as an ``sd_cpp_extra_args`` block appended
        to the prompt — see ``generate()`` for the exact encoding.
        """
        return self._sample_steps

    @sample_steps.setter
    def sample_steps(self, value: int | None) -> None:
        # Validate 1..100 — the server's strict resolve clamps to the same
        # range (common.cpp:2482 at 228c707). Failing fast keeps the
        # operator's number honest instead of silently generating at a
        # clamped value they never asked for.
        if value is None:
            self._sample_steps = None
            return
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValueError(
                f"sample_steps must be an integer in 1..100 (sd-server clamp range), got {value!r}"
            )
        self._sample_steps = value

    def sample_steps_display(self) -> str:
        """The sample-steps value worth showing an operator, as a string.

        Precedence: the explicit ``--max-steps`` override, then the
        server's reported default from the CACHED capabilities payload
        (``defaults.sample_params.sample_steps`` — the factory warms the
        cache during model discovery), then ``?`` (house unknown
        convention). NEVER performs network IO — this is a display
        helper called from session-header render paths; a cold cache
        simply yields "?".
        """
        if self._sample_steps is not None:
            return str(self._sample_steps)
        caps = getattr(self, "_capabilities_cache", None)
        if caps:
            try:
                return str(caps["defaults"]["sample_params"]["sample_steps"])
            except (KeyError, TypeError):
                return "?"
        return "?"

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def list_models(self) -> list[dict]:
        """List the server's loaded model.

        Precedence:

        1. ``GET /sdcpp/v1/capabilities`` — reports the REAL loaded
           weights (``model.stem``, e.g. "sd_turbo"). Used as the
           catalog ``name`` so ``--model sd_turbo`` and the CLI banner
           reflect the actual checkpoint.
        2. ``GET /v1/models`` fallback — ONE fixed pseudo-model id
           ("sd-cpp-local"), hardcoded server-side. Covers servers
           started without ``-m`` (empty capabilities model block) and
           builds older than the capabilities endpoint.

        Neither the pseudo id nor the real stem is ever used to gate
        generation — ``generate()`` transmits no model field. Transient
        failures return [] (house convention, like OllamaBackend).
        """
        # Preferred discovery surface — real loaded weights.
        caps = self._fetch_capabilities()
        if caps is not None:
            model_block = caps.get("model") or {}
            stem = (model_block.get("stem") or model_block.get("name") or "").strip()
            if stem:
                return [
                    {
                        "name": stem,
                        "size": 0,
                        "details": {
                            "family": "stable-diffusion",
                            "backend": "stable-diffusion",
                            "filename": model_block.get("name"),
                            "path": model_block.get("path"),
                            "supported_modes": caps.get("supported_modes"),
                            "owned_by": "local",
                        },
                    }
                ]

        # Fallback — OpenAI-compat pseudo id (never reflects the weights).
        url = f"{self.base_url}/v1/models"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            return []

        entries = []
        for item in result.get("data", []) or []:
            model_id = item.get("id", "sd-cpp-local")
            entries.append(
                {
                    "name": model_id,
                    "size": 0,
                    "details": {
                        "family": "stable-diffusion",
                        "backend": "stable-diffusion",
                        "object": item.get("object", "model"),
                        "owned_by": item.get("owned_by", "local"),
                    },
                }
            )
        return entries

    def get_model_info(self, model: str) -> dict | None:
        """Model metadata via GET /sdcpp/v1/capabilities (cached).

        ``model`` is advisory here too — capabilities describes whatever
        weights the server loaded, regardless of the name passed. The
        raw capabilities payload is returned with a house-shaped
        ``details`` block merged in (Ollama get_model_info precedent),
        so consumers like _detect_weight_quant find ``details.family``.
        Returns None when the server is unreachable (house convention).
        """
        if not hasattr(self, "_capabilities_cache"):
            self._capabilities_cache: dict | None = None
            self._capabilities_fetched = False
        if not self._capabilities_fetched:
            self._capabilities_cache = self._fetch_capabilities()
            self._capabilities_fetched = True
        caps = self._capabilities_cache
        if caps is None:
            return None

        info = dict(caps)
        model_block = caps.get("model") or {}
        info["details"] = {
            "family": "stable-diffusion",
            "backend": "stable-diffusion",
            "name": model_block.get("stem") or model,
            "filename": model_block.get("name"),
            "path": model_block.get("path"),
        }
        return info

    def is_running(self) -> bool:
        """Health check via the server's own discovery surfaces.

        sd-server serves neither ``/api/version`` (the BaseBackend probe —
        Ollama-only, 404s here) nor ``/health`` (llama-server). Probe
        capabilities first (richest, always present on 228c707+), then
        fall back to ``/v1/models`` (exists on every build). A 200 from
        either means the server is up — an empty capabilities model
        block still counts as running (the server answers, it just
        started without ``-m``).
        """
        if self._fetch_capabilities() is not None:
            return True
        try:
            req = urllib.request.Request(f"{self.base_url}/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False

    def get_model_max_context(self, model: str, family: str | None = None) -> int | None:
        """An image backend has NO token context — report None ("?" cell).

        Capabilities exposes PIXEL limits (min/max width/height 64–4096),
        which are not token context and must not masquerade as one in
        the ``agentkthx models`` Context column. Returning None renders
        "?" (the cli/footer.fmt_token_size contract). Without this
        override the models table crashed with AttributeError — SD is
        the first OpenAICompatibleBackend subclass to reach that table
        without cloud_base's get_model_max_context (live-observed on
        Colab, 2026-10-10).
        """
        return None

    def get_model_runtime_context(self, model: str) -> int | None:
        """No runtime context either — see get_model_max_context()."""
        return None

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> dict:
        """Generate ONE image from the conversation.

        Args:
            model: Advisory only — the wire has NO model field (the server
                serves its loaded pool). Never transmitted.
            messages: House messages; non-empty user/assistant/tool
                contents are flattened (in order) into a single image
                prompt. The system prompt is SUPPRESSED — never
                transmitted (DDG precedent, see ``_flatten_messages``).
            tools / temperature / max_tokens: Chat-style parameters with no
                meaning on the image surface — silently dropped (a debug
                notice lists what was dropped, mirroring the DDG sampling
                parity note).
            **kwargs: ``width`` / ``height`` (default 512x512) are carried
                on the wire via ``size``. The ``sd_cpp_extra_args`` encoding
                is now PINNED (api.md §sd_cpp_extra_args, source-verified
                at 228c707 — plan §8 / P0 test 8 resolved): the backend
                itself uses it for the ``--max-steps`` → sample_steps
                remap. A caller-supplied block in the prompt wins — the
                backend never double-appends.

        Returns:
            House response dict with ``content`` ("[image saved: <path>]"),
            ``images: [{"path": ...}]``, and the ``{"estimated": True}``
            usage convention (the API reports none — DDG precedent).
        """
        # Advisory-model notice (never sent — no model field on the wire).
        if model and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [{self._provider_label}] --model '{model}' is advisory on this "
                "backend (no model field on the wire); the server serves its "
                "loaded pool"
            )

        # Chat-style kwargs have no meaning here — drop with a debug notice.
        dropped = [
            k
            for k in ("tools", "temperature", "max_tokens", "think", "stop", "top_p")
            if (k in kwargs or (k == "tools" and tools))
        ]
        # tools/temperature/max_tokens arrive as named params; normalize.
        named_present = [
            k
            for k, v in (("tools", tools), ("temperature", temperature), ("max_tokens", max_tokens))
            if v not in (None, 0.7, 2048)
        ]
        dropped = sorted(set(dropped) | set(named_present))
        if dropped and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [{self._provider_label}] dropped chat-only parameters (no image "
                f"surface equivalent): {', '.join(dropped)}"
            )

        # System-prompt suppression (DDG R07.30 precedent) — the harness
        # prompt never rides the image wire. Debug notice only when a
        # system message was actually present and dropped.
        has_system = any((m.get("role") or "").lower() == "system" for m in messages or [])
        if has_system and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [{self._provider_label}] system prompt suppressed (image surface has "
                "no system role — DDG precedent); user/assistant/tool content only"
            )

        prompt = self._flatten_messages(messages)
        if not prompt.strip():
            raise ValueError("no prompt content in messages — nothing to generate an image from")

        # R07.32: an explicit --max-steps (factory-remapped to
        # sample_steps) overrides the server's --steps default per
        # request. The OpenAI image route has no steps field, so the
        # value rides the server's sd_cpp_extra_args extension: a JSON
        # block embedded in the prompt, regex-extracted and stripped
        # server-side (routes_openai.cpp:11 at 228c707). The block MUST
        # stay on ONE line — the extraction regex's `.` does not match
        # newlines, so a pretty-printed block would be left in the prompt
        # verbatim. A caller-supplied marker wins: never double-append.
        if self.sample_steps is not None:
            if "<sd_cpp_extra_args>" in prompt:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [{self._provider_label}] prompt already carries an sd_cpp_extra_args "
                        "block — leaving it to the caller (sample_steps override not appended)"
                    )
            else:
                prompt = (
                    f"{prompt} <sd_cpp_extra_args>"
                    f'{{"sample_params":{{"sample_steps":{self.sample_steps}}}}}'
                    "</sd_cpp_extra_args>"
                )
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [{self._provider_label}] sample_steps={self.sample_steps} "
                        "(--max-steps remap, sd_cpp_extra_args override)"
                    )

        width = kwargs.pop("width", 512)
        height = kwargs.pop("height", 512)
        body = {
            "prompt": prompt,
            "n": 1,
            "size": f"{width}x{height}",
            "output_format": "png",
        }

        url = f"{self.base_url}/v1/images/generations"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [{self._provider_label}] POST {url} size={body['size']}")

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise RuntimeError(
                f"[{self._provider_label}] image generation failed "
                f"(HTTP {e.code}): {error_body[:300]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"[{self._provider_label}] connection error — is sd-server running? "
                "(notebook serve cell, or: sd-server -m <model> --listen-port 1234)"
            ) from e
        except TimeoutError as e:
            raise RuntimeError(
                f"[{self._provider_label}] request timed out after "
                f"{self.config.timeout}s — CPU generation can take minutes; "
                "use fewer steps or a longer timeout"
            ) from e

        data = result.get("data") or []
        if not data or not data[0].get("b64_json"):
            raise RuntimeError(
                f"[{self._provider_label}] response contained no image data "
                f"(keys: {sorted(result.keys())})"
            )

        png_bytes = self._decode_image(data[0]["b64_json"])
        path = self._write_artifact(png_bytes)

        return {
            "content": f"[image saved: {path}]",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"estimated": True},
            "reasoning_content": "",
            "images": [{"path": str(path)}],
            "raw": result,
        }

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Buffered streaming: run generate(), yield the final content once.

        No fake token streaming — sd-server has no incremental image
        surface, and house ``is_cloud=False`` behavior already prefers
        buffered output for local backends.
        """
        result = self.generate(
            model, messages, tools=tools, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        yield result.get("content", "")

    # ------------------------------------------------------------------
    # Capability surfaces
    # ------------------------------------------------------------------

    def test_tool_support(
        self, model: str, family: str | None = None, force_test: bool = False
    ) -> ToolSupportLevel:
        """The image API has no native tool-calling surface.

        R07.19 (follow-up #10): NONE is legacy-only — the produced verdict
        for "no native tools" is REACT (the ReAct fallback), so that is
        what this backend reports. Note for operators: with ReAct, tool
        RESULT messages still fold into the image prompt (the loop
        depends on it); the scaffolding itself lives in the system
        prompt, which is suppressed — never transmitted (DDG precedent).
        """
        return ToolSupportLevel.REACT

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_capabilities(self) -> dict | None:
        """GET /sdcpp/v1/capabilities with a short probe timeout.

        Returns the parsed JSON, or None on ANY failure (unreachable,
        non-2xx, malformed body) — callers fall back to the /v1/models
        pseudo id. The 10 s probe matches list_models()'s transport
        budget and keeps CLI startup snappy when the server is down.
        """
        url = f"{self.base_url}/sdcpp/v1/capabilities"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
        ):
            return None

    @staticmethod
    def _flatten_messages(messages: list[dict]) -> str:
        """Flatten house messages into a single image prompt.

        The system prompt is DROPPED, never flattened (DDG precedent,
        R07.30): there is no system role on an image surface, and the
        harness prompt — ReAct scaffolding, personality, tool schemas —
        is chat plumbing, not image content. Forwarding it would have
        the diffusion model render scaffolding tokens into the picture
        or dilute the user's actual request. Tool outputs and the full
        user/assistant history still flatten (the ReAct loop depends on
        tool results riding the prompt, as on DDG).

        Part-list contents (IMAGE_SUPPORT_PLAN D1 shape) are tolerated by
        extracting their text parts, mirroring DDG's _coerce_content.
        Returns "" when nothing sendable remains (system-only
        conversation) — generate() refuses that with ``ValueError``.
        """
        parts: list[str] = []
        for msg in messages or []:
            role = (msg.get("role") or "").lower()
            if role == "system":
                # Suppressed, never forwarded (see docstring).
                continue
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        return "\n".join(parts)

    def _decode_image(self, b64_json: str) -> bytes:
        """Size-capped base64 decode (plan §5).

        Decoded size is ~len(b64) * 3/4 — checked BEFORE decoding so a
        pathological response can't allocate hundreds of MB of string
        before we reject it.
        """
        if len(b64_json) * 3 // 4 > self.MAX_B64_DECODED_BYTES:
            raise RuntimeError(
                f"[{self._provider_label}] image exceeds the "
                f"{self.MAX_B64_DECODED_BYTES // (1024 * 1024)} MB decoded-size "
                "cap; refusing to write"
            )
        try:
            return base64.b64decode(b64_json)
        except Exception as e:
            raise RuntimeError(f"[{self._provider_label}] invalid base64 image data: {e}") from e

    def _write_artifact(self, png_bytes: bytes) -> Path:
        """Write the PNG to the artifacts dir with a generated filename.

        The backend NEVER opens paths from the model (untrusted-input
        discipline, plan §5) — the filename is fully generated here.
        """
        artifacts_dir = Path(os.environ.get("AGENTKTHX_ARTIFACTS_DIR") or "./generated")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        counter = getattr(self, "_artifact_counter", 0) + 1
        self._artifact_counter = counter
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = artifacts_dir / f"sd_{stamp}_{counter:03d}.png"
        path.write_bytes(png_bytes)
        return path
