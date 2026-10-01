"""
R07.17 regression: ``num_batch`` parameter support.

Adds the ``num_batch`` parameter to ``Agent`` (constructor + agent attribute),
forwarding it to backends that support it as a per-request option. The primary
target is Ollama's ``options.num_batch`` (prompt-processing batch size —
lower values reduce peak RAM during prompt eval at the cost of more
iterations). Other backends (llama-server/TurboQuant, cloud providers)
silently ignore the value:

- **Ollama native /api/chat**: forwarded via the generic kwargs-to-options
  loop in ``OllamaBackend.generate`` (``body["options"]["num_batch"]``).
- **Ollama OpenAI-compat /v1/chat/completions**: NOT forwarded (Ollama's
  OpenAI-compat endpoint doesn't accept per-request num_batch). Use
  ``--api openre`` to use num_batch with Ollama.
- **llama-server / TurboQuant / BitNet**: server-start only — use
  ``turbo start --batch-size N`` (the ``-b`` flag).
- **Cloud backends (ZAI, OpenRouter, Gemini, etc.)**: silently dropped
  (``_build_openai_body`` only forwards a specific allowlist of optional
  fields — ``num_batch`` is not in it, so no API leak).

These tests verify:
1. ``Agent.__init__`` accepts ``num_batch`` and stores it on ``self._num_batch``.
2. ``Agent.__init__`` rejects unknown kwargs (ARCH-05 fail-fast still works).
3. ``self._num_batch`` is forwarded in ``backend_kwargs`` by both
   ``_generate()`` (non-streaming) and the streaming paths
   (``_generate_stream_chunks`` + ``_prepare_stream_params``).
4. ``--num-batch`` CLI flag is accepted by ``add_agent_args``,
   ``add_shared_args``, and the ``test`` subcommand parser.
5. ``SharedConfig`` carries ``num_batch`` from CLI args / ``AGENTKTHX_NUM_BATCH`` env.
6. ``_build_agent`` wires ``args.num_batch`` to the Agent and stashes
   ``agent._num_batch_explicit``.
7. ``apply_model_switch`` does NOT touch ``num_batch`` (per-request, not
   model-derived — a /model switch preserves the user-pinned value).
8. ``/param`` PARAM_MATRIX includes ``num_batch`` for the Ollama backend.
9. Ollama backend's generate() places ``num_batch`` in ``body["options"]``
   when called with the kwarg (integration check against the real
   OllamaBackend method, mocked urlopen).
10. Cloud backends (OpenAICompatibleBackend._build_openai_body) do NOT
    leak ``num_batch`` into the request body.

All tests are pure logic / mocked — no network calls.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _set_dummy_api_keys(monkeypatch):
    """Avoid ValueError when ZAI/OpenRouter constructors check for keys."""
    monkeypatch.setenv("ZAI_API_KEY", "sk-test-dummy-key-for-test-1234")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy-key-1234")


# ─────────────────────────────────────────────────────────────────────
# 1. Agent.__init__ accepts num_batch
# ─────────────────────────────────────────────────────────────────────


def test_agent_init_accepts_num_batch_none():
    """Default: num_batch=None means use the backend's own default."""
    from agentkthx.agent import Agent

    agent = Agent(model="qwen2.5:0.5b", system_prompt="test", num_batch=None)
    assert agent._num_batch is None


def test_agent_init_accepts_num_batch_int():
    """An explicit int is stored verbatim on self._num_batch."""
    from agentkthx.agent import Agent

    agent = Agent(model="qwen2.5:0.5b", system_prompt="test", num_batch=256)
    assert agent._num_batch == 256


def test_agent_init_num_batch_in_kwargs_error_message():
    """ARCH-05 fail-fast: the kwargs error message lists num_batch as valid."""
    from agentkthx.agent import Agent

    with pytest.raises(TypeError) as excinfo:
        Agent(model="x", system_prompt="x", bogus_kwarg=123)
    assert "num_batch" in str(excinfo.value)


def test_agent_init_rejects_unknown_kwargs_still():
    """ARCH-05: adding num_batch did not reopen kwargs swallowing."""
    from agentkthx.agent import Agent

    with pytest.raises(TypeError):
        Agent(model="x", system_prompt="x", num_batch=64, bogus_kwarg=True)


def test_agent_setup_mixin_signature_has_num_batch():
    """The constructor signature exposes num_batch as a named parameter."""
    import inspect

    from agentkthx.core.agent_setup import AgentSetupMixin

    params = inspect.signature(AgentSetupMixin.__init__).parameters
    assert "num_batch" in params, "AgentSetupMixin.__init__ lost num_batch parameter"
    assert params["num_batch"].default is None, "num_batch default should be None"


# ─────────────────────────────────────────────────────────────────────
# 2. _generate() (non-streaming) forwards num_batch
# ─────────────────────────────────────────────────────────────────────


def _make_minimal_agent():
    """Construct an Agent bypassing __init__ — we only test _generate forwarding."""
    from agentkthx.agent import Agent
    from agentkthx.core.openresponses import ToolChoiceType

    a = Agent.__new__(Agent)
    a.model = "test-model"
    a.backend = MagicMock()
    a.backend.api_mode = None  # _is_comp_mode → False
    a.memory = MagicMock()
    a.memory.get_messages.return_value = [{"role": "user", "content": "hi"}]
    a.tools = MagicMock()
    a.tools.all.return_value = []
    a.tools.names.return_value = []
    a.tools.__len__ = lambda self: 0
    a.model_config = MagicMock()
    a.model_config.stop_tokens = []
    a.model_config.default_temperature = 0.7
    a.model_config.default_max_tokens = 128
    a.model_config.default_top_p = 0.9
    a._think = None
    a._reasoning_effort = None
    a._temperature = None
    a._top_p = None
    a._num_predict = None
    a._num_batch = None
    a._repeat_penalty = None  # R07.18
    a._repeat_last_n = None  # R07.18
    a.num_ctx = None
    a.model_family = None
    a._response_format = None
    a.tool_choice = MagicMock()
    a.tool_choice.type = ToolChoiceType.AUTO
    a.tool_choice.to_dict = MagicMock(return_value={})
    a.truncation = "auto"
    a.debug = False
    a._parser = MagicMock()
    a._parser.parse.return_value = []
    a._parser.is_final_answer.return_value = True
    return a


class TestGenerateForwardsNumBatch(unittest.TestCase):
    """_generate() puts self._num_batch into backend_kwargs."""

    def test_none_num_batch_not_forwarded(self):
        a = _make_minimal_agent()
        a._num_batch = None
        a.backend.generate.return_value = {
            "content": "ok",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        }
        with patch("sys.stdout", new=io.StringIO()):
            a._generate()
        # backend.generate called with **backend_kwargs — num_batch absent
        # because the agent skips None values.
        _, kwargs = a.backend.generate.call_args
        self.assertNotIn("num_batch", kwargs)

    def test_explicit_num_batch_forwarded(self):
        a = _make_minimal_agent()
        a._num_batch = 256
        a.backend.generate.return_value = {
            "content": "ok",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        }
        with patch("sys.stdout", new=io.StringIO()):
            a._generate()
        _, kwargs = a.backend.generate.call_args
        self.assertEqual(kwargs.get("num_batch"), 256)


# ─────────────────────────────────────────────────────────────────────
# 3. Streaming paths forward num_batch
# ─────────────────────────────────────────────────────────────────────


class TestStreamingForwardsNumBatch(unittest.TestCase):
    """_generate_stream_chunks and _prepare_stream_params forward num_batch."""

    def test_generate_stream_chunks_forwards_num_batch(self):
        from agentkthx.agent import Agent
        from agentkthx.core.openresponses import ToolChoiceType

        a = Agent.__new__(Agent)
        a.model = "test"
        a.backend = MagicMock()
        a.backend.api_mode = None
        a.memory = MagicMock()
        a.memory.get_messages.return_value = [{"role": "user", "content": "hi"}]
        a.tools = MagicMock()
        a.tools.all.return_value = []
        a.tools.__len__ = lambda self: 0
        a.model_config = MagicMock()
        a.model_config.stop_tokens = []
        a.model_config.default_temperature = 0.7
        a.model_config.default_max_tokens = 64
        a.model_config.default_top_p = 0.9
        a._think = None
        a._reasoning_effort = None
        a._temperature = None
        a._top_p = None
        a._num_predict = None
        a.num_ctx = None
        a.model_family = None
        a._response_format = None
        a.tool_choice = MagicMock()
        a.tool_choice.type = ToolChoiceType.AUTO
        a.tool_choice.to_dict = MagicMock(return_value={})
        a.truncation = "auto"
        a.debug = False
        a._num_batch = 512
        a._repeat_penalty = None  # R07.18
        a._repeat_last_n = None  # R07.18

        # Use native generate_stream path (text-only)
        def _gen(**kwargs):
            yield "chunk"

        a.backend.generate_stream = MagicMock(side_effect=_gen)

        with patch("sys.stdout", new=io.StringIO()):
            # _generate_stream_chunks takes a prompt arg (unused — memory has it)
            list(a._generate_stream_chunks("dummy prompt"))

        _, kwargs = a.backend.generate_stream.call_args
        self.assertEqual(kwargs.get("num_batch"), 512)

    def test_prepare_stream_params_includes_num_batch(self):
        from agentkthx.agent import Agent
        from agentkthx.core.openresponses import ToolChoiceType

        a = Agent.__new__(Agent)
        a.model = "test"
        a.backend = MagicMock()
        a.backend.api_mode = None
        a.memory = MagicMock()
        a.tools = MagicMock()
        a.tools.all.return_value = []
        a.tools.__len__ = lambda self: 0
        a.model_config = MagicMock()
        a.model_config.stop_tokens = []
        a.model_config.default_temperature = 0.7
        a.model_config.default_max_tokens = 64
        a.model_config.default_top_p = 0.9
        a._think = None
        a._reasoning_effort = None
        a._temperature = None
        a._top_p = None
        a._num_predict = None
        a.num_ctx = None
        a.model_family = None
        a._response_format = None
        a.tool_choice = MagicMock()
        a.tool_choice.type = ToolChoiceType.AUTO
        a.tool_choice.to_dict = MagicMock(return_value={})
        a.truncation = "auto"
        a.debug = False
        a._num_batch = 128
        a._repeat_penalty = None  # R07.18
        a._repeat_last_n = None  # R07.18
        a._runtime_kwargs = {}

        params = a._prepare_stream_params([])
        self.assertEqual(params["backend_kwargs"].get("num_batch"), 128)

    def test_prepare_stream_params_omits_none_num_batch(self):
        from agentkthx.agent import Agent
        from agentkthx.core.openresponses import ToolChoiceType

        a = Agent.__new__(Agent)
        a.model = "test"
        a.backend = MagicMock()
        a.backend.api_mode = None
        a.memory = MagicMock()
        a.tools = MagicMock()
        a.tools.all.return_value = []
        a.tools.__len__ = lambda self: 0
        a.model_config = MagicMock()
        a.model_config.stop_tokens = []
        a.model_config.default_temperature = 0.7
        a.model_config.default_max_tokens = 64
        a.model_config.default_top_p = 0.9
        a._think = None
        a._reasoning_effort = None
        a._temperature = None
        a._top_p = None
        a._num_predict = None
        a.num_ctx = None
        a.model_family = None
        a._response_format = None
        a.tool_choice = MagicMock()
        a.tool_choice.type = ToolChoiceType.AUTO
        a.tool_choice.to_dict = MagicMock(return_value={})
        a.truncation = "auto"
        a.debug = False
        a._num_batch = None  # explicit None
        a._repeat_penalty = None  # R07.18
        a._repeat_last_n = None  # R07.18
        a._runtime_kwargs = {}

        params = a._prepare_stream_params([])
        self.assertNotIn("num_batch", params["backend_kwargs"])


# ─────────────────────────────────────────────────────────────────────
# 4. CLI flag --num-batch accepted by all three arg parsers
# ─────────────────────────────────────────────────────────────────────


def test_add_agent_args_has_num_batch():
    """chat/run/agent subcommands accept --num-batch."""
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args(["--num-batch", "256"])
    assert args.num_batch == 256


def test_add_shared_args_has_num_batch():
    """Example-script shared-args surface accepts --num-batch."""
    from agentkthx.shared_args import add_shared_args

    p = argparse.ArgumentParser()
    add_shared_args(p)
    args = p.parse_args(["--num-batch", "128"])
    assert args.num_batch == 128


def test_test_subcommand_has_num_batch():
    """The `agentkthx test` subcommand accepts --num-batch."""
    from agentkthx.cli.parser import create_parser

    parser = create_parser()
    args = parser.parse_args(["test", "--num-batch", "64"])
    assert args.num_batch == 64


def test_num_batch_defaults_to_none():
    """No flag → None (backend default)."""
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args([])
    assert args.num_batch is None


# ─────────────────────────────────────────────────────────────────────
# 5. SharedConfig carries num_batch from CLI / env
# ─────────────────────────────────────────────────────────────────────


def test_shared_config_num_batch_from_args():
    from agentkthx.shared_args import parse_shared_args

    args = argparse.Namespace(num_batch=512)
    cfg = parse_shared_args(args)
    assert cfg.num_batch == 512


def test_shared_config_num_batch_from_env(monkeypatch):
    from agentkthx.shared_args import parse_shared_args

    monkeypatch.setenv("AGENTKTHX_NUM_BATCH", "1024")
    args = argparse.Namespace(num_batch=None)
    cfg = parse_shared_args(args)
    assert cfg.num_batch == 1024


def test_shared_config_num_batch_defaults_none():
    from agentkthx.shared_args import parse_shared_args

    args = argparse.Namespace(num_batch=None)
    cfg = parse_shared_args(args)
    assert cfg.num_batch is None


# ─────────────────────────────────────────────────────────────────────
# 6. _build_agent wires args.num_batch + stashes _num_batch_explicit
# ─────────────────────────────────────────────────────────────────────


def test_build_agent_passes_num_batch_to_agent(monkeypatch):
    """_build_agent forwards args.num_batch to the Agent constructor."""
    from agentkthx.cli import agent_factory

    captured = {}

    class _FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeBackend:
        is_cloud = False
        backend_type = None
        base_url = "http://localhost:11434"

        def test_tool_support(self, *a, **k):
            from agentkthx.core.types import ToolSupportLevel

            return ToolSupportLevel.UNTESTED

    monkeypatch.setattr(agent_factory, "Agent", _FakeAgent)
    monkeypatch.setattr(agent_factory, "get_backend", lambda *a, **k: _FakeBackend())

    # Minimal args namespace with num_batch set
    args = argparse.Namespace(
        backend="ollama",
        model="qwen2.5:0.5b",
        api_mode="openre",
        debug=False,
        tools="",
        soul=None,
        soul_level=2,
        num_ctx=None,
        num_predict=None,
        num_batch=256,
        temperature=None,
        top_p=None,
        timeout=None,
        force_react=False,
        max_steps=None,
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

    class _Cfg:
        backend = "ollama"
        default_model = "qwen2.5:0.5b"
        num_ctx = 8192
        max_tool_retries = 2

    agent_factory._build_agent(args, _Cfg())
    assert captured.get("num_batch") == 256


def test_build_agent_stashes_num_batch_explicit(monkeypatch):
    """_build_agent stashes agent._num_batch_explicit = (args.num_batch is not None)."""
    from agentkthx.cli import agent_factory

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeBackend:
        is_cloud = False
        backend_type = None
        base_url = "http://localhost:11434"

        def test_tool_support(self, *a, **k):
            from agentkthx.core.types import ToolSupportLevel

            return ToolSupportLevel.UNTESTED

    monkeypatch.setattr(agent_factory, "Agent", _FakeAgent)
    monkeypatch.setattr(agent_factory, "get_backend", lambda *a, **k: _FakeBackend())

    base_args = dict(
        backend="ollama",
        model="qwen2.5:0.5b",
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
        max_steps=None,
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

    class _Cfg:
        backend = "ollama"
        default_model = "qwen2.5:0.5b"
        num_ctx = 8192
        max_tool_retries = 2

    # Explicit num_batch → _num_batch_explicit True
    args1 = argparse.Namespace(**{**base_args, "num_batch": 256})
    a1 = agent_factory._build_agent(args1, _Cfg())
    assert a1._num_batch_explicit is True

    # No num_batch → _num_batch_explicit False
    args2 = argparse.Namespace(**base_args)
    a2 = agent_factory._build_agent(args2, _Cfg())
    assert a2._num_batch_explicit is False


# ─────────────────────────────────────────────────────────────────────
# 7. apply_model_switch does NOT touch num_batch
# ─────────────────────────────────────────────────────────────────────


class TestApplyModelSwitchPreservesNumBatch(unittest.TestCase):
    """num_batch is per-request, not model-derived — /model switch leaves it alone."""

    def test_num_batch_survives_model_switch(self):
        from agentkthx.cli.agent_factory import apply_model_switch

        class _LocalBackend:
            is_cloud = False
            base_url = "http://localhost:11434"
            _context_safe_max_tokens = None

        class _StubAgent:
            def __init__(self):
                self.model = "qwen2.5:0.5b"
                self.num_ctx = 8192
                self._num_predict = 256
                self._num_batch = 512  # user-pinned batch size
                self.model_config = "old"
                self.model_family = "old"
                self.backend = _LocalBackend()

        agent = _StubAgent()
        changes = apply_model_switch(agent, "llama3.2:3b")
        # num_batch untouched
        self.assertEqual(agent._num_batch, 512)
        # num_batch not reported in changes (no re-derivation)
        self.assertNotIn("num_batch", changes)

    def test_none_num_batch_survives_model_switch(self):
        from agentkthx.cli.agent_factory import apply_model_switch

        class _LocalBackend:
            is_cloud = False
            base_url = "http://localhost:11434"
            _context_safe_max_tokens = None

        class _StubAgent:
            def __init__(self):
                self.model = "qwen2.5:0.5b"
                self.num_ctx = 8192
                self._num_predict = 256
                self._num_batch = None  # backend default
                self.model_config = "old"
                self.model_family = "old"
                self.backend = _LocalBackend()

        agent = _StubAgent()
        apply_model_switch(agent, "llama3.2:3b")
        self.assertIsNone(agent._num_batch)


# ─────────────────────────────────────────────────────────────────────
# 8. /param PARAM_MATRIX includes num_batch for ollama
# ─────────────────────────────────────────────────────────────────────


def test_param_matrix_has_num_batch_for_ollama():
    """The /param slash command lists num_batch as settable for the ollama backend.

    We extract the PARAM_MATRIX by reading the chat.py source (it's a local
    inside cmd_chat — not exported). A regression here means the entry was
    removed or its backend list changed.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
    text = src.read_text(encoding="utf-8")
    assert '"num_batch"' in text, "PARAM_MATRIX lost its num_batch entry"
    # The entry must list "ollama" as a supported backend.
    # Find the num_batch block and check it contains "ollama" — the block is
    # ~900 chars (description + backends + agent_attr), so grab a generous slice.
    idx = text.index('"num_batch"')
    block = text[idx : idx + 1500]
    assert '"ollama"' in block, "num_batch PARAM_MATRIX entry lost 'ollama' backend"
    # And the agent_attr must be _num_batch
    assert "_num_batch" in block, "num_batch PARAM_MATRIX entry lost agent_attr='_num_batch'"


# ─────────────────────────────────────────────────────────────────────
# 9. OllamaBackend.generate places num_batch in body["options"]
# ─────────────────────────────────────────────────────────────────────


class TestOllamaBackendForwardsNumBatch(unittest.TestCase):
    """Ollama native /api/chat path puts num_batch in body['options']['num_batch'].

    Uses a mocked urlopen so no network call is made — we just verify the
    request body shape.
    """

    def _make_backend(self):
        from agentkthx.backends.ollama import OllamaBackend

        b = OllamaBackend.__new__(OllamaBackend)
        b._base_url = "http://localhost:11434"
        b._api_mode = None  # bypass ApiMode.OPENAI dispatch
        # Set the api_mode property's underlying attr
        from agentkthx.core.types import ApiMode

        b._api_mode = ApiMode.OPENRE
        b.config = MagicMock()
        b.config.timeout = 30
        return b

    def test_num_batch_lands_in_options(self):

        b = self._make_backend()

        captured_body = {}

        class _FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "message": {"content": "ok", "tool_calls": []},
                        "prompt_eval_count": 1,
                        "eval_count": 1,
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured_body.update(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            b.generate(
                model="qwen2.5:0.5b",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                num_batch=256,
            )

        self.assertIn("options", captured_body)
        self.assertEqual(captured_body["options"].get("num_batch"), 256)

    def test_no_num_batch_means_no_options_key(self):

        b = self._make_backend()

        captured_body = {}

        class _FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "message": {"content": "ok", "tool_calls": []},
                        "prompt_eval_count": 1,
                        "eval_count": 1,
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake_urlopen(req, timeout=None):
            captured_body.update(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            b.generate(
                model="qwen2.5:0.5b",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                # no num_batch kwarg
            )

        self.assertIn("options", captured_body)
        self.assertNotIn("num_batch", captured_body["options"])


# ─────────────────────────────────────────────────────────────────────
# 10. Cloud backends do NOT leak num_batch into the OpenAI body
# ─────────────────────────────────────────────────────────────────────


def test_openai_compat_body_does_not_leak_num_batch():
    """_build_openai_body only forwards a specific allowlist — num_batch is not in it.

    Uses OllamaBackend (a concrete subclass of OpenAICompatibleBackend) so we
    can call the inherited ``_build_openai_body`` method without instantiating
    an abstract class.
    """
    from agentkthx.backends.ollama import OllamaBackend

    b = OllamaBackend.__new__(OllamaBackend)
    body = b._build_openai_body(
        model="gpt-4",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=0.7,
        max_tokens=64,
        stream=False,
        num_batch=256,  # should be silently dropped
    )
    assert "num_batch" not in body, (
        f"num_batch leaked into OpenAI request body: {body!r} — cloud backends "
        f"would reject this with a 400."
    )


def test_openai_compat_body_does_not_leak_num_batch_with_other_kwargs():
    """Even with other valid kwargs present, num_batch stays out."""
    from agentkthx.backends.ollama import OllamaBackend

    b = OllamaBackend.__new__(OllamaBackend)
    body = b._build_openai_body(
        model="gpt-4",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=0.7,
        max_tokens=64,
        stream=False,
        top_k=40,  # valid — should be forwarded
        seed=12345,  # valid — should be forwarded
        num_batch=256,  # invalid — must NOT be forwarded
    )
    assert body.get("top_k") == 40
    assert body.get("seed") == 12345
    assert "num_batch" not in body


# ─────────────────────────────────────────────────────────────────────
# 11. /param reset clears _num_batch_explicit (parity with num_ctx/num_predict)
# ─────────────────────────────────────────────────────────────────────


def test_param_reset_clears_num_batch_explicit_pin():
    """The /param reset code path clears the _num_batch_explicit pin flag.

    Verified by reading the chat.py source — the reset logic is inline in
    cmd_chat and not exported. A regression here means the elif branch
    for _num_batch was removed.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
    text = src.read_text(encoding="utf-8")
    assert "_num_batch_explicit = False" in text, (
        "Lost the /param reset pin-clear for _num_batch_explicit — /param reset "
        "num_batch would leave the pin flag set, breaking parity with num_ctx/"
        "num_predict."
    )
    assert "_num_batch_explicit = True" in text, (
        "Lost the /param set pin-set for _num_batch_explicit — /param num_batch "
        "<value> would not pin the value."
    )


if __name__ == "__main__":
    unittest.main()
