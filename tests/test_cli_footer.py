"""R07.12 intra-release — cli/footer.py dedup regression tests.

Pins the shared session-footer builder extracted from cmd_chat/cmd_agent
(CodeFlow duplicate-block report: 4 byte-identical nested _fmt_tok defs +
2 copy-forked footer builders). Also pins the dedup itself: the command
modules must NOT regress to carrying their own footer logic.
"""

import unittest
from pathlib import Path

from agentkthx.cli.footer import fmt_tok, footer_line1, footer_line2, footer_text


class _ModelConfig:
    def __init__(self, max_tokens=32768, temperature=0.7):
        self.default_max_tokens = max_tokens
        self.default_temperature = temperature


class _BackendType:
    def __init__(self, value):
        self.value = value


class _Backend:
    def __init__(self, btype):
        self.backend_type = btype


def _agent(**kw):
    """Minimal agent stub carrying every attribute the footer reads."""

    class _A:
        pass

    a = _A()
    a.num_ctx = kw.get("num_ctx", 204800)
    a.model = kw.get("model", "glm-4.7-flash")
    a._num_predict = kw.get("_num_predict", None)
    a._temperature = kw.get("_temperature", None)
    a.model_config = kw.get("model_config", _ModelConfig())
    a._custom_system_prompt = kw.get("_custom_system_prompt", "")
    a.backend = kw.get("backend", _Backend(_BackendType("ollama")))
    a.debug = kw.get("debug", False)
    return a


class TestFmtTok(unittest.TestCase):
    def test_below_1000_stays_plain(self):
        self.assertEqual(fmt_tok(950), "950")

    def test_thousands_become_k(self):
        self.assertEqual(fmt_tok(1000), "1.0k")
        self.assertEqual(fmt_tok(12345), "12.3k")
        self.assertEqual(fmt_tok(204800), "204.8k")

    def test_string_input_tolerated(self):
        # Historical contract: the nested original accepted str/int alike.
        self.assertEqual(fmt_tok(" 1000 "), "1.0k")
        self.assertEqual(fmt_tok("0"), "0")

    def test_zero_and_negative(self):
        self.assertEqual(fmt_tok(0), "0")
        self.assertEqual(fmt_tok(-5), "-5")


class TestFooterLine1(unittest.TestCase):
    def test_contains_version_model_ctx_and_prompt_parts(self):
        a = _agent(model="test-model-x", _custom_system_prompt="x" * 4096)
        line = footer_line1(a)
        self.assertIn("test-model-x", line)
        self.assertIn("200K", line)  # num_ctx 204800 -> 200K
        self.assertIn("32K", line)  # default_max_tokens 32768 -> 32K
        self.assertIn("4.1k chr 1.0k tok", line)  # chr + //4 tok estimate, k-formatted

    def test_derived_defaults_used_when_pinned_unset(self):
        a = _agent(_num_predict=None, _temperature=None,
                   model_config=_ModelConfig(max_tokens=8192, temperature=0.55))
        line = footer_line1(a)
        self.assertIn("8K", line)
        self.assertIn("0.55", line)

    def test_pinned_values_win_over_defaults(self):
        a = _agent(_num_predict=4096, _temperature=0.1)
        line = footer_line1(a)
        self.assertIn("4K", line)
        self.assertIn("0.1", line)

    def test_missing_context_renders_placeholder(self):
        a = _agent(num_ctx=0)
        self.assertIn("?", footer_line1(a))


class TestFooterLine2(unittest.TestCase):
    def test_backend_name_and_token_glyphs(self):
        a = _agent()
        a._running_tokens_in = 12000
        a._running_tokens_out = 3000
        line = footer_line2(a)
        self.assertIn("ollama", line)
        self.assertIn("\u219112.0k", line)
        self.assertIn("\u21933.0k", line)
        self.assertIn("ctx", line)

    def test_running_totals_preferred_over_session_counters(self):
        a = _agent()
        a._running_tokens_in = 12000
        a._running_tokens_out = 3000
        line = footer_line2(a, session_tokens_in=999999, session_tokens_out=999999)
        self.assertIn("\u219112.0k", line)
        self.assertNotIn("999999", line)

    def test_session_counters_used_when_no_running_totals(self):
        a = _agent()
        self.assertFalse(hasattr(a, "_running_tokens_in"))
        line = footer_line2(a, session_tokens_in=2000, session_tokens_out=1000)
        self.assertIn("\u21912.0k", line)
        self.assertIn("\u21931.0k", line)

    def test_ctx_percentage_bands_and_clamp(self):
        # 64000/204800 -> ~31% (low band, plain green) — just assert digits.
        a = _agent()
        a._running_tokens_in = 64000
        a._running_tokens_out = 0
        self.assertIn("31%", footer_line2(a))
        # 204800/204800 = 100% (top band) and clamp holds above num_ctx.
        a._running_tokens_in = 400000
        self.assertIn("100%", footer_line2(a))

    def test_num_ctx_zero_falls_back_to_8192(self):
        a = _agent(num_ctx=0)
        a._running_tokens_in = 8192
        a._running_tokens_out = 0
        # 8192/8192 -> 100% rather than a ZeroDivisionError
        self.assertIn("100%", footer_line2(a))

    def test_debug_flag_appended(self):
        a = _agent(debug=True)
        self.assertIn("debug", footer_line2(a))
        a2 = _agent(debug=False)
        self.assertNotIn("debug", footer_line2(a2))

    def test_plain_string_backend_type(self):
        a = _agent(backend=_Backend("ollama"))  # no .value attr
        self.assertIn("ollama", footer_line2(a))


class TestFooterText(unittest.TestCase):
    def test_two_lines_joined(self):
        a = _agent()
        text = footer_text(a, 1000, 500)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(text, f"{footer_line1(a)}\n{footer_line2(a, 1000, 500)}")


class TestFooterDeduplicated(unittest.TestCase):
    """Pin the dedup itself: no footer logic may regress into the commands."""

    def test_command_modules_no_longer_define_fmt_tok_or_footer_bodies(self):
        cmds_dir = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands"
        for name in ("agent.py", "chat.py"):
            src = (cmds_dir / name).read_text(encoding="utf-8")
            self.assertNotIn("def _fmt_tok", src,
                             f"{name} regressed: carries its own _fmt_tok copy")
            self.assertNotIn("f\"{{n/1000:.1f}}k\"", src,
                             f"{name} regressed: carries inline token formatting")
            self.assertIn("from ..footer import footer_line1, footer_line2", src,
                          f"{name} must render via the shared cli.footer builder")


if __name__ == "__main__":
    unittest.main()
