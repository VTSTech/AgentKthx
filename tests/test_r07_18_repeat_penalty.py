"""
R07.18 regression: llama-server kwargs forwarding + repeat_penalty / repeat_last_n.

Two related fixes in R07.18:

1. **llama-server /completion kwargs forwarding (bug fix)** — before R07.18,
   `_generate_completion` and `_stream_completion` built a hardcoded 4-field
   body (prompt, n_predict, temperature, stop) and silently dropped every
   other sampling param the user set via /param or --top-p. This was a
   parity bug vs Ollama's generic kwargs-to-options loop. R07.18 adds the
   same generic forwarding loop to both llama-server completion methods.

2. **BitNet repeat_penalty override (feature)** — before R07.18, BitNet's
   `repeat_penalty=1.3` was hardcoded and user-supplied values (via
   /param repeat_penalty 1.4) were silently dropped. R07.18 makes it a
   default that the caller can override via kwargs.

3. **repeat_penalty + repeat_last_n exposed** — new Agent constructor
   params, CLI flags (--repeat-penalty, --repeat-last-n), /param entries,
   SharedConfig fields, and env-var fallbacks. Same pattern as num_batch
   (R07.17).

These tests verify:
- Agent constructor accepts repeat_penalty / repeat_last_n.
- _generate() forwards them to backend_kwargs.
- Streaming paths forward them too.
- CLI flags accepted on all three arg parsers.
- SharedConfig carries them from CLI / env.
- _build_agent wires them + stashes _explicit pin flags.
- apply_model_switch preserves them (per-request, not model-derived).
- /param PARAM_MATRIX includes them for ollama + llama_server + bitnet.
- OllamaBackend.generate places them in body["options"].
- LlamaServerBackend._generate_completion forwards them at top level.
- LlamaServerBackend._stream_completion forwards them at top level.
- BitNet repeat_penalty default (1.3) applies when no kwarg supplied.
- BitNet repeat_penalty kwarg overrides the 1.3 default.
- Cloud backends do NOT leak them into the OpenAI body.
- /param reset clears the _explicit pin flags.

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
# 1. Agent.__init__ accepts repeat_penalty / repeat_last_n
# ─────────────────────────────────────────────────────────────────────


def test_agent_init_accepts_repeat_penalty_none():
    from agentkthx.agent import Agent

    agent = Agent(model="qwen2.5:0.5b", system_prompt="test", repeat_penalty=None)
    assert agent._repeat_penalty is None
    assert agent._repeat_last_n is None


def test_agent_init_accepts_repeat_penalty_float():
    from agentkthx.agent import Agent

    agent = Agent(
        model="qwen2.5:0.5b",
        system_prompt="test",
        repeat_penalty=1.4,
        repeat_last_n=128,
    )
    assert agent._repeat_penalty == 1.4
    assert agent._repeat_last_n == 128


def test_agent_init_repeat_penalty_in_kwargs_error_message():
    from agentkthx.agent import Agent

    with pytest.raises(TypeError) as excinfo:
        Agent(model="x", system_prompt="x", bogus_kwarg=123)
    msg = str(excinfo.value)
    assert "repeat_penalty" in msg
    assert "repeat_last_n" in msg


def test_agent_setup_mixin_signature_has_repeat_penalty():
    import inspect

    from agentkthx.core.agent_setup import AgentSetupMixin

    params = inspect.signature(AgentSetupMixin.__init__).parameters
    assert "repeat_penalty" in params
    assert "repeat_last_n" in params
    assert params["repeat_penalty"].default is None
    assert params["repeat_last_n"].default is None


# ─────────────────────────────────────────────────────────────────────
# 2. _generate() forwards repeat_penalty / repeat_last_n
# ─────────────────────────────────────────────────────────────────────


def _make_minimal_agent():
    from agentkthx.agent import Agent
    from agentkthx.core.openresponses import ToolChoiceType

    a = Agent.__new__(Agent)
    a.model = "test-model"
    a.backend = MagicMock()
    a.backend.api_mode = None
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
    a._repeat_penalty = None
    a._repeat_last_n = None
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


class TestGenerateForwardsRepeatPenalty(unittest.TestCase):
    def test_none_repeat_penalty_not_forwarded(self):
        a = _make_minimal_agent()
        a._repeat_penalty = None
        a._repeat_last_n = None
        a.backend.generate.return_value = {
            "content": "ok",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        }
        with patch("sys.stdout", new=io.StringIO()):
            a._generate()
        _, kwargs = a.backend.generate.call_args
        self.assertNotIn("repeat_penalty", kwargs)
        self.assertNotIn("repeat_last_n", kwargs)

    def test_explicit_repeat_penalty_forwarded(self):
        a = _make_minimal_agent()
        a._repeat_penalty = 1.4
        a._repeat_last_n = 128
        a.backend.generate.return_value = {
            "content": "ok",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        }
        with patch("sys.stdout", new=io.StringIO()):
            a._generate()
        _, kwargs = a.backend.generate.call_args
        self.assertEqual(kwargs.get("repeat_penalty"), 1.4)
        self.assertEqual(kwargs.get("repeat_last_n"), 128)


# ─────────────────────────────────────────────────────────────────────
# 3. CLI flags accepted
# ─────────────────────────────────────────────────────────────────────


def test_add_agent_args_has_repeat_penalty():
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args(["--repeat-penalty", "1.4", "--repeat-last-n", "128"])
    assert args.repeat_penalty == 1.4
    assert args.repeat_last_n == 128


def test_add_shared_args_has_repeat_penalty():
    from agentkthx.shared_args import add_shared_args

    p = argparse.ArgumentParser()
    add_shared_args(p)
    args = p.parse_args(["--repeat-penalty", "1.3", "--repeat-last-n", "64"])
    assert args.repeat_penalty == 1.3
    assert args.repeat_last_n == 64


def test_test_subcommand_has_repeat_penalty():
    from agentkthx.cli.parser import create_parser

    parser = create_parser()
    args = parser.parse_args(["test", "--repeat-penalty", "1.5", "--repeat-last-n", "256", "01"])
    assert args.repeat_penalty == 1.5
    assert args.repeat_last_n == 256


def test_repeat_penalty_defaults_to_none():
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args([])
    assert args.repeat_penalty is None
    assert args.repeat_last_n is None


# ─────────────────────────────────────────────────────────────────────
# 4. SharedConfig carries from CLI / env
# ─────────────────────────────────────────────────────────────────────


def test_shared_config_repeat_penalty_from_args():
    from agentkthx.shared_args import parse_shared_args

    args = argparse.Namespace(repeat_penalty=1.4, repeat_last_n=128)
    cfg = parse_shared_args(args)
    assert cfg.repeat_penalty == 1.4
    assert cfg.repeat_last_n == 128


def test_shared_config_repeat_penalty_from_env(monkeypatch):
    from agentkthx.shared_args import parse_shared_args

    monkeypatch.setenv("AGENTKTHX_REPEAT_PENALTY", "1.5")
    monkeypatch.setenv("AGENTKTHX_REPEAT_LAST_N", "256")
    args = argparse.Namespace(repeat_penalty=None, repeat_last_n=None)
    cfg = parse_shared_args(args)
    assert cfg.repeat_penalty == 1.5
    assert cfg.repeat_last_n == 256


def test_shared_config_repeat_penalty_defaults_none():
    from agentkthx.shared_args import parse_shared_args

    args = argparse.Namespace(repeat_penalty=None, repeat_last_n=None)
    cfg = parse_shared_args(args)
    assert cfg.repeat_penalty is None
    assert cfg.repeat_last_n is None


# ─────────────────────────────────────────────────────────────────────
# 5. _build_agent wiring + _explicit pin flags
# ─────────────────────────────────────────────────────────────────────


def test_build_agent_passes_repeat_penalty_to_agent(monkeypatch):
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
        num_batch=None,
        repeat_penalty=1.4,
        repeat_last_n=128,
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
    assert captured.get("repeat_penalty") == 1.4
    assert captured.get("repeat_last_n") == 128


def test_build_agent_stashes_repeat_penalty_explicit(monkeypatch):
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
        repeat_penalty=None,
        repeat_last_n=None,
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

    # Explicit → _explicit True
    args1 = argparse.Namespace(**{**base_args, "repeat_penalty": 1.4, "repeat_last_n": 128})
    a1 = agent_factory._build_agent(args1, _Cfg())
    assert a1._repeat_penalty_explicit is True
    assert a1._repeat_last_n_explicit is True

    # None → _explicit False
    args2 = argparse.Namespace(**base_args)
    a2 = agent_factory._build_agent(args2, _Cfg())
    assert a2._repeat_penalty_explicit is False
    assert a2._repeat_last_n_explicit is False


# ─────────────────────────────────────────────────────────────────────
# 6. apply_model_switch preserves repeat_penalty / repeat_last_n
# ─────────────────────────────────────────────────────────────────────


class TestApplyModelSwitchPreservesRepeatPenalty(unittest.TestCase):
    def test_repeat_penalty_survives_model_switch(self):
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
                self._num_batch = 512
                self._repeat_penalty = 1.4  # user-pinned
                self._repeat_last_n = 128  # user-pinned
                self.model_config = "old"
                self.model_family = "old"
                self.backend = _LocalBackend()

        agent = _StubAgent()
        changes = apply_model_switch(agent, "llama3.2:3b")
        self.assertEqual(agent._repeat_penalty, 1.4)
        self.assertEqual(agent._repeat_last_n, 128)
        self.assertNotIn("repeat_penalty", changes)
        self.assertNotIn("repeat_last_n", changes)


# ─────────────────────────────────────────────────────────────────────
# 7. /param PARAM_MATRIX includes repeat_penalty / repeat_last_n
# ─────────────────────────────────────────────────────────────────────


def test_param_matrix_has_repeat_penalty():
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
    text = src.read_text(encoding="utf-8")
    assert '"repeat_penalty"' in text, "PARAM_MATRIX lost its repeat_penalty entry"
    assert '"repeat_last_n"' in text, "PARAM_MATRIX lost its repeat_last_n entry"
    # Verify backends list includes ollama + llama_server + bitnet
    idx = text.index('"repeat_penalty"')
    block = text[idx : idx + 1500]
    assert '"ollama"' in block
    assert '"llama_server"' in block
    assert '"bitnet"' in block
    assert "_repeat_penalty" in block


def test_param_reset_clears_repeat_penalty_explicit_pin():
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
    text = src.read_text(encoding="utf-8")
    assert (
        "_repeat_penalty_explicit = False" in text
    ), "Lost the /param reset pin-clear for _repeat_penalty_explicit"
    assert (
        "_repeat_last_n_explicit = False" in text
    ), "Lost the /param reset pin-clear for _repeat_last_n_explicit"
    assert (
        "_repeat_penalty_explicit = True" in text
    ), "Lost the /param set pin-set for _repeat_penalty_explicit"
    assert (
        "_repeat_last_n_explicit = True" in text
    ), "Lost the /param set pin-set for _repeat_last_n_explicit"


# ─────────────────────────────────────────────────────────────────────
# 8. OllamaBackend.generate places repeat_penalty in body["options"]
# ─────────────────────────────────────────────────────────────────────


class TestOllamaBackendForwardsRepeatPenalty(unittest.TestCase):
    def _make_backend(self):
        from agentkthx.backends.ollama import OllamaBackend
        from agentkthx.core.types import ApiMode

        b = OllamaBackend.__new__(OllamaBackend)
        b._base_url = "http://localhost:11434"
        b._api_mode = ApiMode.OPENRE
        b.config = MagicMock()
        b.config.timeout = 30
        return b

    def test_repeat_penalty_lands_in_options(self):
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
                repeat_penalty=1.4,
                repeat_last_n=128,
            )

        self.assertEqual(captured_body["options"].get("repeat_penalty"), 1.4)
        self.assertEqual(captured_body["options"].get("repeat_last_n"), 128)

    def test_no_repeat_penalty_means_no_options_key(self):
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
            )

        self.assertNotIn("repeat_penalty", captured_body["options"])
        self.assertNotIn("repeat_last_n", captured_body["options"])


# ─────────────────────────────────────────────────────────────────────
# 9. LlamaServerBackend._generate_completion forwards kwargs (R07.18 bug fix)
# ─────────────────────────────────────────────────────────────────────


class TestLlamaServerKwargsForwarding(unittest.TestCase):
    """R07.18: /completion endpoint forwards kwargs (was silently dropped before)."""

    def _make_backend(self, bitnet_mode=False):
        from agentkthx.backends.llama_server import LlamaServerBackend

        b = LlamaServerBackend.__new__(LlamaServerBackend)
        b._base_url = "http://localhost:8080"
        b._bitnet_mode = bitnet_mode
        b.config = MagicMock()
        b.config.timeout = 30
        return b

    def _fake_urlopen(self, captured_body):
        class _FakeResp:
            def read(self):
                return json.dumps(
                    {
                        "content": "ok",
                        "tokens_evaluated": 10,
                        "tokens_predicted": 5,
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake(req, timeout=None):
            captured_body.update(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        return _fake

    def test_top_p_forwarded_to_completion_body(self):
        """R07.18: top_p is no longer silently dropped in /completion mode."""
        b = self._make_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="test",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                top_p=0.9,
            )
        self.assertEqual(captured.get("top_p"), 0.9, "top_p was silently dropped (R07.18 bug)")

    def test_top_k_forwarded_to_completion_body(self):
        b = self._make_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="test",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                top_k=40,
            )
        self.assertEqual(captured.get("top_k"), 40)

    def test_seed_forwarded_to_completion_body(self):
        b = self._make_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="test",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                seed=12345,
            )
        self.assertEqual(captured.get("seed"), 12345)

    def test_repeat_penalty_kwarg_forwarded_to_completion_body(self):
        b = self._make_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="test",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                repeat_penalty=1.5,
            )
        self.assertEqual(captured.get("repeat_penalty"), 1.5)

    def test_agent_internal_kwargs_not_forwarded(self):
        """Agent-internal kwargs (think, num_ctx, etc.) must NOT leak to /completion."""
        b = self._make_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="test",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                think=True,
                reasoning_effort="high",
                num_ctx=8192,
                num_predict=256,
                num_batch=512,
                tool_choice="auto",
                response_format={"type": "json_object"},
                truncation="auto",
            )
        # None of these should appear in the /completion body
        self.assertNotIn("think", captured)
        self.assertNotIn("reasoning_effort", captured)
        self.assertNotIn("num_ctx", captured)
        self.assertNotIn("num_predict", captured)
        self.assertNotIn("num_batch", captured)
        self.assertNotIn("tool_choice", captured)
        self.assertNotIn("response_format", captured)
        self.assertNotIn("truncation", captured)


class TestLlamaServerStreamKwargsForwarding(unittest.TestCase):
    """R07.18: /completion streaming also forwards kwargs (was dropped before)."""

    def _make_backend(self, bitnet_mode=False):
        from agentkthx.backends.llama_server import LlamaServerBackend

        b = LlamaServerBackend.__new__(LlamaServerBackend)
        b._base_url = "http://localhost:8080"
        b._bitnet_mode = bitnet_mode
        b.config = MagicMock()
        b.config.timeout = 30
        return b

    def test_top_p_forwarded_in_stream(self):
        captured = {}

        class _FakeResp:
            def __iter__(self):
                # Yield a single "done" chunk so the generator exits
                yield json.dumps({"content": "", "stop": True}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake(req, timeout=None):
            captured.update(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        b = self._make_backend()
        with patch("urllib.request.urlopen", side_effect=_fake):
            # Consume the generator so the request fires
            list(
                b._stream_completion(
                    model="test",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=None,
                    temperature=0.7,
                    max_tokens=64,
                    top_p=0.85,
                )
            )
        self.assertEqual(captured.get("top_p"), 0.85, "top_p dropped in stream mode (R07.18 bug)")


# ─────────────────────────────────────────────────────────────────────
# 10. BitNet repeat_penalty default + override
# ─────────────────────────────────────────────────────────────────────


class TestBitNetRepeatPenaltyDefault(unittest.TestCase):
    """R07.18: BitNet's repeat_penalty=1.3 is a default, not a hardcode.

    Before R07.18, the value was hardcoded and user-supplied values were
    silently dropped. Now: 1.3 applies when no kwarg supplied; an explicit
    kwarg overrides it.
    """

    def _make_bitnet_backend(self):
        from agentkthx.backends.llama_server import LlamaServerBackend

        b = LlamaServerBackend.__new__(LlamaServerBackend)
        b._base_url = "http://localhost:8080"
        b._bitnet_mode = True  # BitNet mode
        b.config = MagicMock()
        b.config.timeout = 30
        return b

    def _fake_urlopen(self, captured_body):
        class _FakeResp:
            def read(self):
                return json.dumps(
                    {"content": "ok", "tokens_evaluated": 10, "tokens_predicted": 5}
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake(req, timeout=None):
            captured_body.update(json.loads(req.data.decode("utf-8")))
            return _FakeResp()

        return _fake

    def test_bitnet_default_repeat_penalty_applied(self):
        """No kwarg → BitNet default of 1.3 applies."""
        b = self._make_bitnet_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="bitnet",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
            )
        self.assertEqual(captured.get("repeat_penalty"), 1.3)

    def test_bitnet_explicit_repeat_penalty_overrides_default(self):
        """Explicit kwarg → overrides the 1.3 default (R07.18 fix)."""
        b = self._make_bitnet_backend()
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="bitnet",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
                repeat_penalty=1.5,  # explicit override
            )
        self.assertEqual(
            captured.get("repeat_penalty"), 1.5, "BitNet default 1.3 should be overridden by kwarg"
        )

    def test_non_bitnet_no_default_repeat_penalty(self):
        """Non-BitNet llama-server: no default repeat_penalty applied."""
        from agentkthx.backends.llama_server import LlamaServerBackend

        b = LlamaServerBackend.__new__(LlamaServerBackend)
        b._base_url = "http://localhost:8080"
        b._bitnet_mode = False
        b.config = MagicMock()
        b.config.timeout = 30
        captured = {}
        with patch("urllib.request.urlopen", side_effect=self._fake_urlopen(captured)):
            b._generate_completion(
                model="llama3.2",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=64,
            )
        self.assertNotIn("repeat_penalty", captured)


# ─────────────────────────────────────────────────────────────────────
# 11. Cloud backends do NOT leak repeat_penalty into OpenAI body
# ─────────────────────────────────────────────────────────────────────


def test_openai_compat_body_does_not_leak_repeat_penalty():
    from agentkthx.backends.ollama import OllamaBackend

    b = OllamaBackend.__new__(OllamaBackend)
    body = b._build_openai_body(
        model="gpt-4",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=0.7,
        max_tokens=64,
        stream=False,
        repeat_penalty=1.4,  # should be silently dropped
        repeat_last_n=128,  # should be silently dropped
    )
    assert (
        "repeat_penalty" not in body
    ), f"repeat_penalty leaked into OpenAI body: {body!r} — cloud backends would 400."
    assert "repeat_last_n" not in body


# ─────────────────────────────────────────────────────────────────────
# 12. Streaming _prepare_stream_params forwards repeat_penalty
# ─────────────────────────────────────────────────────────────────────


def test_prepare_stream_params_includes_repeat_penalty():
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
    a._num_batch = None
    a._repeat_penalty = 1.4
    a._repeat_last_n = 128
    a.num_ctx = None
    a.model_family = None
    a._response_format = None
    a.tool_choice = MagicMock()
    a.tool_choice.type = ToolChoiceType.AUTO
    a.tool_choice.to_dict = MagicMock(return_value={})
    a.truncation = "auto"
    a.debug = False
    a._runtime_kwargs = {}

    params = a._prepare_stream_params([])
    assert params["backend_kwargs"].get("repeat_penalty") == 1.4
    assert params["backend_kwargs"].get("repeat_last_n") == 128


def test_prepare_stream_params_omits_none_repeat_penalty():
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
    a._num_batch = None
    a._repeat_penalty = None
    a._repeat_last_n = None
    a.num_ctx = None
    a.model_family = None
    a._response_format = None
    a.tool_choice = MagicMock()
    a.tool_choice.type = ToolChoiceType.AUTO
    a.tool_choice.to_dict = MagicMock(return_value={})
    a.truncation = "auto"
    a.debug = False
    a._runtime_kwargs = {}

    params = a._prepare_stream_params([])
    assert "repeat_penalty" not in params["backend_kwargs"]
    assert "repeat_last_n" not in params["backend_kwargs"]


if __name__ == "__main__":
    unittest.main()
