"""R07.12 intra-release — quick-wins closure batch regression tests.

Six audit findings closed without a version bump (the "quick, non-breaking"
batch). This file pins every closure in the house per-release style:

- PERF-05  ToolParser.parse dedupes cross-strategy echoes (a call present
           in two shapes executed once, not once per parsing strategy)
- ROB-12   cmd_chat / cmd_agent clear ``agent._on_step_callback`` in their
           ``finally`` blocks — the stale closure must not outlive the
           command frame on a reused Agent (functional cmd_agent lifecycle
           pin lives in test_agent_mode_footer.py)
- ROB-19   Agent.register_tool reads ``self.debug`` directly (the old
           ``getattr(self, "debug", False)`` masked init-order bugs)
- MAINT-18 apply_model_switch's return dict is consumed by cmd_chat
           (verification-only closure — the source scan IS the evidence)
- MAINT-21 _parse_mistral_response keys the error envelope on the
           documented ``object == "error"`` marker; the old
           ``(A or (B and C))`` precedence misfiled any response carrying
           a top-level ``message`` field and no ``choices``

(TEST-02 is pinned in place — tests/test_security.py's ``test_percent2e``
was rewritten from a no-op into a deterministic assertion.)
"""

import unittest
from pathlib import Path

import pytest

from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.tool_parse import ToolParser

_CMDS_DIR = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands"
_AGENT_SRC = (Path(__file__).resolve().parent.parent / "agentkthx" / "agent.py").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# PERF-05 — cross-strategy parse dedupe
# ---------------------------------------------------------------------------


class TestPerf05ParseDedupe:
    JSON_CODEBLOCK = "```json\n" '{"name": "shell", "arguments": {"command": "ls"}}\n' "```\n"

    def test_same_call_in_two_shapes_executes_once(self):
        """A ```json codeblock AND a ReAct block describing the SAME call:
        the old parser emitted it twice (once per strategy) and the loop
        would execute it twice. Dedupe collapses the echo."""
        text = self.JSON_CODEBLOCK + 'Action: shell\nAction Input: {"command": "ls"}'
        calls = ToolParser(["shell"]).parse(text)
        assert len(calls) == 1, f"expected 1 deduped call, got {len(calls)}"
        assert calls[0].name == "shell"
        assert calls[0].arguments == {"command": "ls"}

    def test_json_wrapped_react_is_single_call(self):
        """The dolphin/qwen2.5-coder JSON-wrapped ReAct shape must stay a
        single call (the native parser unwraps it; the ReAct scanner must
        not double-produce it)."""
        text = '{"Action": "shell", "Action Input": {"command": "ls"}}'
        calls = ToolParser(["shell"]).parse(text)
        assert len(calls) == 1
        assert calls[0].name == "shell"

    def test_distinct_calls_survive_in_order(self):
        """Dedupe is by (name, canonical args) — genuinely DIFFERENT calls
        from the same message all survive, in first-seen order."""
        text = (
            self.JSON_CODEBLOCK + "Action: calculator\n" + 'Action Input: {"expression": "2 + 2"}'
        )
        calls = ToolParser(["shell", "calculator"]).parse(text)
        assert [(c.name, c.arguments) for c in calls] == [
            ("shell", {"command": "ls"}),
            ("calculator", {"expression": "2 + 2"}),
        ]

    def test_single_format_behavior_unchanged(self):
        """Behavior guard: single-format texts parse identically to the
        pre-dedupe parser (the overwhelming real-world case)."""
        react_only = ToolParser(["shell"]).parse(
            "Action: shell\nAction Input: {'command': 'ls -la'}"
        )
        assert len(react_only) == 1
        assert react_only[0].name == "shell"
        json_only = ToolParser(["shell"]).parse('{"name": "shell", "arguments": {"command": "ls"}}')
        assert len(json_only) == 1
        assert json_only[0].arguments == {"command": "ls"}


# ---------------------------------------------------------------------------
# ROB-12 — step-callback lifetime (source scan; functional pin in
# test_agent_mode_footer.py drives the real cmd_agent loop)
# ---------------------------------------------------------------------------


class TestRob12CallbackLifetime:
    def test_both_commands_register_and_clear_the_callback(self):
        for name in ("chat.py", "agent.py"):
            src = (_CMDS_DIR / name).read_text(encoding="utf-8")
            assert "agent._on_step_callback = lambda step, tin, tout: _update_footer()" in src, (
                f"{name} no longer registers the footer-refresh callback — "
                "the persistent footer would freeze during streaming"
            )
            assert "agent._on_step_callback = None" in src, (
                f"{name} regressed: _on_step_callback is never cleared on exit — "
                "the stale closure outlives the command frame (ROB-12)"
            )


# ---------------------------------------------------------------------------
# ROB-19 — register_tool reads self.debug directly
# ---------------------------------------------------------------------------


def _calc_tool() -> Tool:
    return Tool(
        name="calc2",
        description="A second calculator",
        params=[ToolParam(name="expression", type="string")],
    )


class TestRob19DebugAttribute:
    def test_register_tool_reads_live_debug_flag(self):
        from agentkthx.agent import Agent

        agent = Agent(
            model="qwen2.5:0.5b",
            tools=["calculator"],
            system_prompt="You are a test agent.",
            debug=False,
        )
        assert agent._parser.debug is False
        # Flip the flag post-construction, then register a tool — the
        # rebuilt parser must follow the LIVE attribute.
        agent.debug = True
        agent.register_tool(_calc_tool())
        assert (
            agent._parser.debug is True
        ), "register_tool did not propagate self.debug into the rebuilt parser"

    def test_source_scan_no_getattr_debug_in_register_tool(self):
        # Line scan, comment lines excluded: the ROB-19 fix comment quotes
        # the historical call — only live CODE may not contain it.
        code_lines = [ln for ln in _AGENT_SRC.splitlines() if not ln.strip().startswith("#")]
        assert 'getattr(self, "debug", False)' not in "\n".join(code_lines), (
            'Agent.register_tool regressed to getattr(self, "debug", ...) — '
            "the defensive default masks init-order bugs (ROB-19)"
        )


# ---------------------------------------------------------------------------
# MAINT-21 — Mistral error-envelope classification
# ---------------------------------------------------------------------------


class TestMaint21MistralErrorEnvelope:
    def test_notice_shape_without_choices_is_not_an_api_error(self):
        """A gateway notice/annotation body (top-level ``message``, no
        ``choices``) must NOT surface the provider prose as a Mistral API
        error — it falls through to the honest "no choices" branch."""
        from agentkthx.plugins.mistral.mistral import MistralBackend

        with pytest.raises(RuntimeError, match="no choices"):
            MistralBackend._parse_mistral_response(
                {
                    "object": "chat.completion",
                    "message": "rate limit window resets soon",
                    "data": {"remaining_requests": 3},
                }
            )

    def test_success_shape_with_top_level_message_parses(self):
        """The audit's prescribed regression shape: a legitimate completion
        that also carries a top-level ``message`` field must parse."""
        from agentkthx.plugins.mistral.mistral import MistralBackend

        out = MistralBackend._parse_mistral_response(
            {
                "object": "chat.completion",
                "message": "served by gateway eu-west",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Hello!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
        )
        assert out["content"] == "Hello!"
        assert out["finish_reason"] == "stop"

    def test_documented_error_envelope_still_raises(self):
        """Guard: the documented {"object": "error", ...} envelope keeps
        raising with the provider message."""
        from agentkthx.plugins.mistral.mistral import MistralBackend

        with pytest.raises(RuntimeError, match="Provider rate limited"):
            MistralBackend._parse_mistral_response(
                {
                    "object": "error",
                    "message": "Provider rate limited",
                    "type": "rate_limit_error",
                }
            )


# ---------------------------------------------------------------------------
# MAINT-18 — apply_model_switch return dict is consumed (verification-only
# closure: the source scan IS the evidence the caller uses it)
# ---------------------------------------------------------------------------


class TestMaint18VerifiedConsumer:
    def test_cmd_chat_consumes_apply_model_switch_return(self):
        src = (_CMDS_DIR / "chat.py").read_text(encoding="utf-8")
        assert (
            "changes = _cli.apply_model_switch(agent, new_model)" in src
        ), "cmd_chat no longer captures apply_model_switch's return dict"
        assert (
            'changes.get("model"' in src
        ), "cmd_chat no longer consumes the switch-delta dict for printing"


if __name__ == "__main__":
    unittest.main()
