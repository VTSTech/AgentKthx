"""
Tests for StableDiffusionBackend — manifest v0.2 compliance, the Ollama-
pattern contract (is_cloud=False, backend_type, 'sd' alias registration),
/v1/models → house catalog mapping, the /v1/images/generations wire body
(NO model field), message flattening (system suppressed DDG-style,
user/assistant/tool multi-turn), the size-capped b64 → PNG artifact
pipeline, connection-refused remediation, the single-delta stream
wrapper, constructor URL resolution, and the R07.32 --max-steps →
sample_steps remap (sd_cpp_extra_args override + session-header relabel).

Plan: docs/STABLE_DIFFUSION_BACKEND_PLAN.md (§6 test plan, tests 1–8).
Live server facts pinned 2026-10-10 on Colab CPU (sd_turbo).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the AgentKthx package importable when running from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentkthx.core.types import BackendType, ToolSupportLevel
from agentkthx.plugins.stablediffusion import register, unregister
from agentkthx.plugins.stablediffusion.backend import StableDiffusionBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(base_url: str = "http://127.0.0.1:1234") -> StableDiffusionBackend:
    """Construct a backend pointed at a fake local server."""
    return StableDiffusionBackend(base_url=base_url)


def _ok_response(payload: dict) -> MagicMock:
    """Build a mock urlopen response returning JSON."""
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.status = 200  # is_running() health checks read .status
    resp.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
    return resp


def _err_response(status: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    """Build an HTTPError like urlopen raises for non-2xx."""
    return urllib.error.HTTPError(
        url="http://127.0.0.1:1234/v1/images/generations",
        code=status,
        msg="error",
        hdrs={},
        fp=io.BytesIO(body),
    )


def _png_b64() -> str:
    """Minimal bytes that start with the PNG magic number, as b64."""
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")


def _generations_payload() -> dict:
    """Live-shaped /v1/images/generations response (plan §2)."""
    return {
        "created": 1791566859,
        "output_format": "png",
        "data": [{"b64_json": _png_b64()}],
    }


def _models_payload() -> dict:
    """Live-shaped /v1/models response (pinned on Colab, 2026-10-10)."""
    return {"data": [{"id": "sd-cpp-local", "object": "model", "owned_by": "local"}]}


@pytest.fixture(autouse=True)
def _isolated_artifacts(tmp_path, monkeypatch):
    """Point the artifacts dir at a per-test directory."""
    monkeypatch.setenv("AGENTKTHX_ARTIFACTS_DIR", str(tmp_path / "generated"))
    monkeypatch.delenv("SD_BASE_URL", raising=False)
    yield


# ---------------------------------------------------------------------------
# Manifest compliance (v0.2 form)
# ---------------------------------------------------------------------------


class TestManifestCompliance(unittest.TestCase):
    """The plugin.json must parse as a v0.2 manifest."""

    def setUp(self):
        self.plugin_dir = (
            Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "stablediffusion"
        )
        self.manifest = json.loads((self.plugin_dir / "plugin.json").read_text(encoding="utf-8"))

    def test_manifest_top_level_fields(self):
        self.assertEqual(self.manifest["name"], "stablediffusion")
        self.assertEqual(self.manifest["license"], "MIT")
        self.assertIn("version", self.manifest)
        self.assertIn("description", self.manifest)

    def test_manifest_declares_backend_and_alias(self):
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        self.assertEqual(ext["type"], "backend")
        backends = ext["provides"]["backends"]
        self.assertEqual(backends, {"stable-diffusion": "backend.StableDiffusionBackend"})
        # The 'sd' alias must be declared for the lazy-load chicken-and-egg
        # path (find_plugin_for_backend checks cli_flags BEFORE register()).
        self.assertIn("stable-diffusion", ext["provides"]["cli_flags"]["--backend"])
        self.assertIn("sd", ext["provides"]["cli_flags"]["--backend"])


# ---------------------------------------------------------------------------
# Ollama-pattern contract
# ---------------------------------------------------------------------------


class TestBackendContract(unittest.TestCase):
    """The class-level facts that make this an Ollama-pattern backend."""

    def test_is_cloud_is_false(self):
        self.assertFalse(StableDiffusionBackend.is_cloud)

    def test_backend_type(self):
        backend = _make_backend()
        self.assertIs(backend.backend_type, BackendType.STABLE_DIFFUSION)
        self.assertEqual(backend.backend_type.value, "stable-diffusion")

    def test_provider_label(self):
        self.assertEqual(_make_backend()._provider_label, "SD")

    def test_default_timeout_covers_cpu_generation(self):
        # 120 s would abort mid-diffusion (plan §D5: up to ~8 min on CPU).
        self.assertGreaterEqual(StableDiffusionBackend.GENERATION_TIMEOUT, 600)
        self.assertGreaterEqual(_make_backend().config.timeout, 600)

    def test_url_resolution_priority(self):
        self.assertEqual(_make_backend(base_url="http://x:1/").base_url, "http://x:1")
        self.assertEqual(
            StableDiffusionBackend(host="10.0.0.5", port=9999).base_url,
            "http://10.0.0.5:9999",
        )
        self.assertTrue(_make_backend().base_url.startswith("http://"))


# ---------------------------------------------------------------------------
# §6 test 1 — list_models() maps /v1/models → catalog
# ---------------------------------------------------------------------------


class TestListModels(unittest.TestCase):
    def test_models_endpoint_maps_to_house_catalog(self):
        backend = _make_backend()
        with patch("urllib.request.urlopen", return_value=_ok_response(_models_payload())):
            models = backend.list_models()

        self.assertEqual(len(models), 1)
        entry = models[0]
        self.assertEqual(entry["name"], "sd-cpp-local")
        self.assertEqual(entry["details"]["family"], "stable-diffusion")
        self.assertEqual(entry["details"]["owned_by"], "local")

    def test_models_endpoint_failure_returns_empty(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            self.assertEqual(backend.list_models(), [])


class TestCapabilitiesDiscovery(unittest.TestCase):
    """§2 wire facts — /sdcpp/v1/capabilities exposes the REAL loaded weights.

    Source-verified at the release tag master-948-228c707 (commit 228c707):
    routes_sdcpp.cpp builds result["model"] = {name, stem, path} from
    resolve_display_model_path() — -m/--model first, else
    --diffusion-model, else all-empty strings. /v1/models stays a
    hardcoded "sd-cpp-local" pseudo id (routes_openai.cpp).
    """

    def _caps_payload(self) -> dict:
        """Shape of /sdcpp/v1/capabilities (routes_sdcpp.cpp, 228c707)."""
        return {
            "model": {
                "name": "sd_turbo.safetensors",
                "stem": "sd_turbo",
                "path": "/content/sd_models/sd_turbo.safetensors",
            },
            "current_mode": "img_gen",
            "supported_modes": ["img_gen"],
            "limits": {"max_batch_count": 8, "max_width": 4096},
        }

    @staticmethod
    def _routing_urlopen(responses: dict, capabilities_first: bool = True):
        """Route mock urlopen by request URL.

        `responses` maps full_url → response/exception. Any URL not in
        the map that ends with /capabilities raises HTTPError 404; any
        other unrouted URL maps through _models_payload() behavior only
        when explicitly provided — otherwise URLError.
        """

        def handler(req, timeout=None):
            url = req.full_url
            if url in responses:
                entry = responses[url]
                if isinstance(entry, Exception):
                    raise entry
                return entry
            if url.endswith("/sdcpp/v1/capabilities"):
                raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b"{}"))
            if url.endswith("/v1/models"):
                return _ok_response(_models_payload())
            raise urllib.error.URLError(f"unrouted url in test: {url}")

        return handler

    def test_list_models_prefers_capabilities_stem(self):
        backend = _make_backend()
        handler = self._routing_urlopen(
            {"http://127.0.0.1:1234/sdcpp/v1/capabilities": _ok_response(self._caps_payload())}
        )
        with patch("urllib.request.urlopen", side_effect=handler):
            models = backend.list_models()

        self.assertEqual(len(models), 1)
        entry = models[0]
        self.assertEqual(entry["name"], "sd_turbo")
        self.assertEqual(entry["details"]["filename"], "sd_turbo.safetensors")
        self.assertEqual(entry["details"]["path"], "/content/sd_models/sd_turbo.safetensors")
        self.assertEqual(entry["details"]["family"], "stable-diffusion")
        self.assertEqual(entry["details"]["owned_by"], "local")

    def test_list_models_falls_back_when_capabilities_model_block_empty(self):
        # Server started WITHOUT -m / --diffusion-model: all fields "".
        empty_caps = {"model": {"name": "", "stem": "", "path": ""}}
        backend = _make_backend()
        handler = self._routing_urlopen(
            {"http://127.0.0.1:1234/sdcpp/v1/capabilities": _ok_response(empty_caps)}
        )
        with patch("urllib.request.urlopen", side_effect=handler):
            models = backend.list_models()
        self.assertEqual(models[0]["name"], "sd-cpp-local")

    def test_list_models_falls_back_when_capabilities_404(self):
        # Older server build without the capabilities endpoint.
        backend = _make_backend()
        with patch("urllib.request.urlopen", side_effect=self._routing_urlopen({})):
            models = backend.list_models()
        self.assertEqual(models[0]["name"], "sd-cpp-local")

    def test_list_models_returns_empty_when_both_surfaces_fail(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            self.assertEqual(backend.list_models(), [])

    def test_get_model_info_merges_house_details_block(self):
        backend = _make_backend()
        handler = self._routing_urlopen(
            {"http://127.0.0.1:1234/sdcpp/v1/capabilities": _ok_response(self._caps_payload())}
        )
        with patch("urllib.request.urlopen", side_effect=handler):
            info = backend.get_model_info("anything-advisory")

        self.assertIsNotNone(info)
        self.assertEqual(info["details"]["family"], "stable-diffusion")
        # Real stem wins over the advisory name.
        self.assertEqual(info["details"]["name"], "sd_turbo")
        self.assertEqual(info["details"]["filename"], "sd_turbo.safetensors")
        # Raw capabilities keys survive the merge.
        self.assertEqual(info["current_mode"], "img_gen")

    def test_get_model_info_none_when_unreachable(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            self.assertIsNone(backend.get_model_info("sd_turbo"))

    def test_get_model_info_caches_capabilities_probe(self):
        backend = _make_backend()
        handler = self._routing_urlopen(
            {"http://127.0.0.1:1234/sdcpp/v1/capabilities": _ok_response(self._caps_payload())}
        )
        with patch("urllib.request.urlopen", side_effect=handler) as mock_urlopen:
            backend.get_model_info("a")
            backend.get_model_info("b")
        # One probe total — the second call hits the instance cache.
        self.assertEqual(mock_urlopen.call_count, 1)


class TestIsRunning(unittest.TestCase):
    """BaseBackend.is_running() probes /api/version — Ollama-only.

    sd-server 404s that (no /health either), so the models CLI reported
    "Sd is not running" against a live server (observed on Colab,
    2026-10-10). The override probes capabilities → /v1/models.
    """

    def test_capabilities_reachable_means_running(self):
        backend = _make_backend()
        caps = TestCapabilitiesDiscovery()._caps_payload()
        handler = TestCapabilitiesDiscovery._routing_urlopen(
            {"http://127.0.0.1:1234/sdcpp/v1/capabilities": _ok_response(caps)}
        )
        with patch("urllib.request.urlopen", side_effect=handler):
            self.assertTrue(backend.is_running())

    def test_capabilities_404_falls_back_to_v1_models(self):
        # Older build: capabilities missing, but /v1/models answers.
        backend = _make_backend()
        handler = TestCapabilitiesDiscovery._routing_urlopen({})
        with patch("urllib.request.urlopen", side_effect=handler):
            self.assertTrue(backend.is_running())

    def test_unreachable_server_reports_not_running(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            self.assertFalse(backend.is_running())


class TestModelContextPlaceholders(unittest.TestCase):
    """An image backend has NO token context — None → "?" in the models table.

    Live-observed crash on Colab 2026-10-10: agentkthx models --backend sd
    hit AttributeError at OpenAICompatibleBackend.get_model_runtime_context
    → get_model_max_context (SD is the first OpenAICompatibleBackend
    subclass to reach that table without cloud_base's implementation).
    The plugin now implements both methods; cli/footer.fmt_token_size
    renders None as "?".
    """

    def test_get_model_max_context_returns_none(self):
        backend = _make_backend()
        self.assertIsNone(backend.get_model_max_context("sd_turbo"))
        self.assertIsNone(backend.get_model_max_context("sd_turbo", family="stable-diffusion"))

    def test_get_model_runtime_context_returns_none(self):
        backend = _make_backend()
        self.assertIsNone(backend.get_model_runtime_context("sd_turbo"))

    def test_none_renders_as_question_mark(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(None), "?")


# ---------------------------------------------------------------------------
# §6 tests 2+3 — generate(): wire body, flattening, artifact pipeline
# ---------------------------------------------------------------------------


class TestGenerate(unittest.TestCase):
    def _capture_generate(self, backend, messages, **kwargs):
        captured = {}

        def capturing_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _ok_response(_generations_payload())

        with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
            result = backend.generate("whatever", messages, **kwargs)
        return captured, result

    def test_wire_body_has_no_model_field(self):
        backend = _make_backend()
        messages = [
            {"role": "system", "content": "You are AgentKthx."},
            {"role": "user", "content": "a lovely cat"},
        ]
        captured, _ = self._capture_generate(backend, messages)

        self.assertEqual(captured["url"], "http://127.0.0.1:1234/v1/images/generations")
        self.assertEqual(captured["method"], "POST")
        # The sd.cpp wire has NO model field — --model is advisory only.
        # Exact-equality also pins system-prompt suppression: the harness
        # prompt is in the input above but must not appear anywhere in
        # the body (no model field, no system field, not in the prompt).
        self.assertEqual(
            captured["body"],
            {
                "prompt": "a lovely cat",
                "n": 1,
                "size": "512x512",
                "output_format": "png",
            },
        )

    def test_multi_turn_history_flattens_in_order(self):
        backend = _make_backend()
        messages = [
            {"role": "user", "content": "a red house"},
            {"role": "assistant", "content": "[image saved: ./generated/sd_old.png]"},
            {"role": "user", "content": "now make it blue"},
        ]
        captured, _ = self._capture_generate(backend, messages)
        self.assertEqual(
            captured["body"]["prompt"],
            "a red house\n[image saved: ./generated/sd_old.png]\nnow make it blue",
        )

    def test_empty_messages_raises(self):
        backend = _make_backend()
        with pytest.raises(ValueError, match="no prompt content"):
            backend.generate("sd-cpp-local", [{"role": "user", "content": "   "}])

    def test_width_height_kwargs_become_size(self):
        backend = _make_backend()
        captured, _ = self._capture_generate(
            backend,
            [{"role": "user", "content": "a cat"}],
            width=768,
            height=512,
        )
        self.assertEqual(captured["body"]["size"], "768x512")

    def test_b64_decoded_to_png_artifact(
        self,
    ):
        backend = _make_backend()
        _, result = self._capture_generate(backend, [{"role": "user", "content": "a lovely cat"}])

        # House content marker + images extension field (plan §D2).
        self.assertIn("[image saved:", result["content"])
        self.assertEqual(len(result["images"]), 1)
        path = Path(result["images"][0]["path"])
        self.assertTrue(path.exists())
        self.assertTrue(path.name.startswith("sd_"))
        self.assertTrue(path.name.endswith(".png"))
        # Decoded bytes are the PNG we sent (magic number intact).
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        # usage: the API reports none — house {"estimated": True} convention.
        self.assertEqual(result["usage"], {"estimated": True})
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["finish_reason"], "stop")

    def test_advisory_model_is_never_sent(self):
        # Even with a bogus model name the wire body stays model-free.
        backend = _make_backend()
        captured, _ = self._capture_generate(
            backend,
            [{"role": "user", "content": "a cat"}],
        )
        # generate() received model="whatever" (test helper) — not in body.
        self.assertNotIn("model", captured["body"])


# ---------------------------------------------------------------------------
# R07.32 — system-prompt suppression (DDG R07.30 precedent): the harness
# prompt never reaches the image wire
# ---------------------------------------------------------------------------


class TestSystemPromptSuppression(unittest.TestCase):
    """The system prompt is DROPPED from the flattened image prompt.

    Mirrors the DDG contract (_strip_system_messages, R07.30): the
    harness prompt — ReAct scaffolding, personality, tool schemas — is
    chat plumbing, not image content. User/assistant/tool content still
    flattens in full.
    """

    def test_system_dropped_from_wire_prompt(self):
        backend = _make_backend()
        body = _capture_body(
            backend,
            [
                {"role": "system", "content": "You are AgentKthx, a ReAct agent."},
                {"role": "user", "content": "a lovely cat"},
            ],
        )
        self.assertEqual(body["prompt"], "a lovely cat")
        self.assertNotIn("AgentKthx", body["prompt"])

    def test_multiple_system_messages_all_dropped(self):
        backend = _make_backend()
        body = _capture_body(
            backend,
            [
                {"role": "system", "content": "Rule A."},
                {"role": "system", "content": "Rule B."},
                {"role": "user", "content": "a red house"},
            ],
        )
        self.assertEqual(body["prompt"], "a red house")

    def test_system_only_conversation_raises(self):
        """System-only → nothing sendable → generate() refuses
        (ValueError), matching DDG's empty-convo refusal."""
        backend = _make_backend()
        with pytest.raises(ValueError, match="no prompt content"):
            backend.generate("sd-cpp-local", [{"role": "system", "content": "Be terse."}])

    def test_tool_output_still_flattens(self):
        """Suppression is system-SCOPED — tool results still ride the
        prompt (the ReAct loop depends on it, as on DDG)."""
        backend = _make_backend()
        body = _capture_body(
            backend,
            [
                {"role": "system", "content": "You are AgentKthx."},
                {"role": "user", "content": "draw the result"},
                {"role": "tool", "content": "weather: sunny, 22C"},
            ],
        )
        self.assertEqual(body["prompt"], "draw the result\nweather: sunny, 22C")

    def test_flatten_skips_system_directly(self):
        """Unit-level pin on _flatten_messages (DDG-style direct test)."""
        prompt = StableDiffusionBackend._flatten_messages(
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ]
        )
        self.assertEqual(prompt, "Hi\nHello")


# ---------------------------------------------------------------------------
# §6 test 4 — oversized b64 → clean error, no file written
# ---------------------------------------------------------------------------


class TestSizeCap(unittest.TestCase):
    def test_oversized_b64_rejected_before_write(self, tmp_path=None):
        import os

        backend = _make_backend()
        # Shrink the cap instead of building a 44 MB string.
        backend.MAX_B64_DECODED_BYTES = 8
        payload = {"created": 1, "data": [{"b64_json": _png_b64()}]}
        artifacts = Path(os.environ["AGENTKTHX_ARTIFACTS_DIR"])

        with patch("urllib.request.urlopen", return_value=_ok_response(payload)):
            with pytest.raises(RuntimeError, match="size cap"):
                backend.generate("sd-cpp-local", [{"role": "user", "content": "x"}])

        self.assertFalse(artifacts.exists() and any(artifacts.iterdir()))


# ---------------------------------------------------------------------------
# §6 test 5 — connection-refused → remediation RuntimeError
# ---------------------------------------------------------------------------


class TestErrorPaths(unittest.TestCase):
    def test_connection_refused_gets_remediation_message(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with pytest.raises(RuntimeError, match="is sd-server running"):
                backend.generate("sd-cpp-local", [{"role": "user", "content": "x"}])

    def test_http_error_surfaces_status_and_body(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            side_effect=_err_response(500, b'{"error": "out of memory"}'),
        ):
            with pytest.raises(RuntimeError, match="HTTP 500.*out of memory"):
                backend.generate("sd-cpp-local", [{"role": "user", "content": "x"}])

    def test_empty_data_raises_clean_error(self):
        backend = _make_backend()
        with patch(
            "urllib.request.urlopen",
            return_value=_ok_response({"created": 1, "data": []}),
        ):
            with pytest.raises(RuntimeError, match="no image data"):
                backend.generate("sd-cpp-local", [{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# §6 test 7 — generate_stream(): exactly one delta = generate().content
# ---------------------------------------------------------------------------


class TestStream(unittest.TestCase):
    def test_stream_yields_single_delta_matching_generate(self):
        backend = _make_backend()

        def capturing_urlopen(req, timeout=None):
            return _ok_response(_generations_payload())

        with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
            buffered = backend.generate("sd-cpp-local", [{"role": "user", "content": "a cat"}])
        # Same backend instance — reset the artifact counter so both runs
        # produce identical content strings (filename counter restarts).
        backend._artifact_counter = 0
        with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
            deltas = list(
                backend.generate_stream("sd-cpp-local", [{"role": "user", "content": "a cat"}])
            )

        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0], buffered["content"])


# ---------------------------------------------------------------------------
# §6 test 6 — alias: "sd" resolves the same class as "stable-diffusion"
# ---------------------------------------------------------------------------


class TestAliasRegistration(unittest.TestCase):
    def test_sd_alias_resolves_same_class(self):
        from agentkthx.plugins._loader import get_plugin_manager

        pm = get_plugin_manager()
        register(pm)
        try:
            canonical = pm.get_backend_class("stable-diffusion")
            alias = pm.get_backend_class("sd")
            self.assertIs(canonical, StableDiffusionBackend)
            self.assertIs(alias, StableDiffusionBackend)
            self.assertIn("sd", pm.list_backend_names())
        finally:
            unregister(pm)
        self.assertIsNone(pm.get_backend_class("stable-diffusion"))
        self.assertIsNone(pm.get_backend_class("sd"))

    def test_tool_support_reports_react_fallback(self):
        # R07.19: NONE is legacy-only; no native tools → REACT verdict.
        backend = _make_backend()
        self.assertIs(backend.test_tool_support("sd-cpp-local"), ToolSupportLevel.REACT)


# ---------------------------------------------------------------------------
# R07.32: --max-steps → sample_steps remap (sd backend only)
# ---------------------------------------------------------------------------


def _capture_body(backend, messages, **kwargs) -> dict:
    """Run generate() against a capturing urlopen; return the wire body."""
    captured = {}

    def capturing_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_response(_generations_payload())

    with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
        backend.generate("sd_turbo", messages, **kwargs)
    return captured["body"]


class TestSampleStepsOverride(unittest.TestCase):
    """--max-steps (factory-remapped) drives the sd_cpp_extra_args block."""

    def test_default_is_none_and_setter_validates(self):
        backend = _make_backend()
        self.assertIsNone(backend.sample_steps)
        for bad in (0, -1, 101, 1000, "4", 4.0, True):
            with self.assertRaises(ValueError):
                backend.sample_steps = bad
        for good in (1, 4, 25, 100):
            backend.sample_steps = good
            self.assertEqual(backend.sample_steps, good)
        backend.sample_steps = None
        self.assertIsNone(backend.sample_steps)

    def test_constructor_kwarg_and_rejection(self):
        backend = StableDiffusionBackend(base_url="http://127.0.0.1:1234", sample_steps=4)
        self.assertEqual(backend.sample_steps, 4)
        with self.assertRaises(ValueError):
            StableDiffusionBackend(base_url="http://127.0.0.1:1234", sample_steps=0)

    def test_generate_appends_single_line_marker(self):
        backend = _make_backend()
        backend.sample_steps = 4
        body = _capture_body(backend, [{"role": "user", "content": "a lovely cat"}])
        # Exact block from api.md §sd_cpp_extra_args (pinned at 228c707):
        # single-line JSON, server regex `.` cannot span newlines.
        self.assertEqual(
            body["prompt"],
            'a lovely cat <sd_cpp_extra_args>{"sample_params":{"sample_steps":4}}</sd_cpp_extra_args>',
        )
        self.assertNotIn("\n", body["prompt"])
        self.assertNotIn("model", body)  # still advisory-model-free
        self.assertEqual(body["n"], 1)

    def test_generate_without_override_leaves_prompt_bare(self):
        backend = _make_backend()  # sample_steps None → server --steps default
        body = _capture_body(backend, [{"role": "user", "content": "a lovely cat"}])
        self.assertEqual(body["prompt"], "a lovely cat")
        self.assertNotIn("<sd_cpp_extra_args>", body["prompt"])

    def test_generate_caller_marker_wins_no_double_append(self):
        backend = _make_backend()
        backend.sample_steps = 4
        caller_block = (
            '<sd_cpp_extra_args>{"sample_params":{"sample_steps":28}}' "</sd_cpp_extra_args>"
        )
        body = _capture_body(backend, [{"role": "user", "content": f"a lovely cat {caller_block}"}])
        self.assertEqual(body["prompt"], f"a lovely cat {caller_block}")
        self.assertEqual(body["prompt"].count("<sd_cpp_extra_args>"), 1)

    def test_display_precedence_explicit_then_caps_then_unknown(self):
        backend = _make_backend()
        self.assertEqual(backend.sample_steps_display(), "?")  # cold cache, no override
        backend._capabilities_cache = {"defaults": {"sample_params": {"sample_steps": 25}}}
        backend._capabilities_fetched = True
        self.assertEqual(backend.sample_steps_display(), "25")  # warm cache → server default
        backend.sample_steps = 4
        self.assertEqual(backend.sample_steps_display(), "4")  # explicit override wins

    def test_display_tolerates_malformed_caps(self):
        backend = _make_backend()
        backend._capabilities_cache = {"defaults": {}}
        backend._capabilities_fetched = True
        self.assertEqual(backend.sample_steps_display(), "?")


class TestSessionHeaderSampleSteps(unittest.TestCase):
    """The session header relabels the steps line for the sd backend."""

    def _render(self, backend) -> str:
        from agentkthx.cli import headers as headers_mod

        class _FakeAgent:
            def __init__(self):
                self.model = "sd_turbo"
                self.backend = backend
                self.soul = None
                self.num_ctx = None
                self.max_steps = 25
                self._response_format = None
                self._is_persistent = False

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            headers_mod._print_session_header(
                _FakeAgent(),
                argparse.Namespace(backend="sd"),
                argparse.Namespace(backend="sd"),
                "Chat",
            )
        return buf.getvalue()

    def test_sd_backend_shows_sample_steps(self):
        backend = _make_backend()
        backend.sample_steps = 4
        out = self._render(backend)
        self.assertIn("Sample Steps: 4", out)
        self.assertNotIn("Max Steps:", out)

    def test_sd_backend_falls_back_to_server_default(self):
        backend = _make_backend()
        backend._capabilities_cache = {"defaults": {"sample_params": {"sample_steps": 25}}}
        backend._capabilities_fetched = True
        out = self._render(backend)
        self.assertIn("Sample Steps: 25", out)

    def test_other_backends_keep_max_steps_label(self):
        class _PlainBackend:
            backend_type = None
            base_url = "http://localhost:11434"

        out = self._render(_PlainBackend())
        self.assertIn("Max Steps: 25", out)
        self.assertNotIn("Sample Steps:", out)


class TestFactorySampleStepsMapping:
    """agent_factory remaps --max-steps to sample_steps for sd ONLY."""

    def _args(self, max_steps) -> argparse.Namespace:
        # Key set mirrors the test_num_batch factory harness; backend/model
        # switched to the sd discovery path.
        return argparse.Namespace(
            backend="sd",
            model=None,
            api_mode="openre",
            debug=False,
            tools="",
            soul=None,
            soul_level=2,
            num_ctx=None,
            num_predict=None,
            num_batch=None,
            temperature=None,
            top_p=None,
            timeout=None,
            force_react=False,
            max_steps=max_steps,
            response_format="text",
            truncation="auto",
            compaction="auto",
            thinking_level="auto",
            show_reasoning=False,
            skills=None,
            session=None,
            no_retry=False,
            max_tool_retries=None,
            confirm_dangerous=False,
            acp=False,
            acp_url=None,
            security="max",
        )

    def _install_fakes(self, monkeypatch, backend_kwargs: dict, agent_kwargs: dict) -> None:
        from agentkthx.cli import agent_factory

        class _FakeAgent:
            def __init__(self, **kwargs):
                agent_kwargs.update(kwargs)

        class _FakeSDBackend:
            is_cloud = False
            backend_type = BackendType.STABLE_DIFFUSION
            base_url = "http://127.0.0.1:1234"

            def __init__(self, **kwargs):
                # Mimic the real constructor contract: sample_steps is
                # validated 1..100 by the StableDiffusionBackend property
                # setter — the fake must reject out-of-range values too so
                # the factory's error path stays honest under test.
                steps = kwargs.get("sample_steps")
                if steps is not None and (
                    isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 100
                ):
                    raise ValueError(
                        "sample_steps must be an integer in 1..100 "
                        f"(sd-server clamp range), got {steps!r}"
                    )
                backend_kwargs.update(kwargs)

            def list_models(self):
                return [{"name": "sd_turbo", "size": 0, "details": {"family": "stable-diffusion"}}]

            def get_model_info(self, model):
                return {"details": {}}

            def test_tool_support(self, *a, **k):
                return ToolSupportLevel.REACT

        monkeypatch.setattr(agent_factory, "Agent", _FakeAgent)
        monkeypatch.setattr(agent_factory, "get_backend", lambda name, **kw: _FakeSDBackend(**kw))

    def _config(self):
        class _Cfg:
            backend = "sd"
            default_model = "qwen2.5:0.5b"
            num_ctx = 8192
            max_tool_retries = 2

        return _Cfg()

    def test_explicit_max_steps_becomes_sample_steps(self, monkeypatch):
        from agentkthx.cli import agent_factory

        backend_kwargs: dict = {}
        agent_kwargs: dict = {}
        self._install_fakes(monkeypatch, backend_kwargs, agent_kwargs)

        agent_factory._build_agent(self._args(max_steps=4), self._config())
        assert backend_kwargs.get("sample_steps") == 4
        # The Agent() loop ceiling still receives --max-steps unchanged —
        # other backends' semantics are untouched by construction.
        assert agent_kwargs.get("max_steps") == 4

    def test_omitted_max_steps_passes_no_sample_steps(self, monkeypatch):
        from agentkthx.cli import agent_factory

        backend_kwargs: dict = {}
        agent_kwargs: dict = {}
        self._install_fakes(monkeypatch, backend_kwargs, agent_kwargs)

        agent_factory._build_agent(self._args(max_steps=None), self._config())
        assert "sample_steps" not in backend_kwargs  # server default applies
        # The factory passes args.max_steps verbatim (None here); the REAL
        # Agent constructor's defensive fix (agent_setup) resolves None → 25
        # downstream — pre-existing behavior, unchanged for every backend.
        assert agent_kwargs.get("max_steps") is None

    def test_validation_error_propagates_for_out_of_range(self, monkeypatch):
        from agentkthx.cli import agent_factory

        backend_kwargs: dict = {}
        agent_kwargs: dict = {}
        self._install_fakes(monkeypatch, backend_kwargs, agent_kwargs)

        with pytest.raises(ValueError, match="1..100"):
            agent_factory._build_agent(self._args(max_steps=150), self._config())
