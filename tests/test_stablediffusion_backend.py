"""
Tests for StableDiffusionBackend — manifest v0.2 compliance, the Ollama-
pattern contract (is_cloud=False, backend_type, 'sd' alias registration),
/v1/models → house catalog mapping, the /v1/images/generations wire body
(NO model field), message flattening (system+user, multi-turn), the
size-capped b64 → PNG artifact pipeline, connection-refused remediation,
the single-delta stream wrapper, and constructor URL resolution.

Plan: docs/STABLE_DIFFUSION_BACKEND_PLAN.md (§6 test plan, tests 1–8).
Live server facts pinned 2026-10-10 on Colab CPU (sd_turbo).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import base64
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
        self.assertEqual(
            captured["body"],
            {
                "prompt": "You are AgentKthx.\na lovely cat",
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
