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
    # R07.17: optional fields read by getattr — default to not-set so the
    # footer's "only show when set" guard works correctly.
    if "_num_batch" in kw:
        a._num_batch = kw["_num_batch"]
    if "_run_start_time" in kw:
        a._run_start_time = kw["_run_start_time"]
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
        a = _agent(
            _num_predict=None,
            _temperature=None,
            model_config=_ModelConfig(max_tokens=8192, temperature=0.55),
        )
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


# ─────────────────────────────────────────────────────────────────────
# R07.17: batch size + TPS + temp formatting
# ─────────────────────────────────────────────────────────────────────


class TestFooterBatchSize(unittest.TestCase):
    """Line 1 shows 🔧 N when _num_batch is set; omits the segment when None."""

    def test_batch_shown_when_set(self):
        a = _agent(_num_batch=64)
        line = footer_line1(a)
        self.assertIn("\U0001f527", line)  # wrench emoji
        self.assertIn("64", line)

    def test_batch_omitted_when_none(self):
        a = _agent()  # _num_batch not set → getattr returns None
        line = footer_line1(a)
        self.assertNotIn("\U0001f527", line)

    def test_batch_shown_after_temp(self):
        """The batch segment appears after the temp segment."""
        a = _agent(_temperature=0.1, _num_batch=256)
        line = footer_line1(a)
        temp_idx = line.index("0.1")
        batch_idx = line.index("256")
        self.assertGreater(batch_idx, temp_idx, "batch must appear after temp")


class TestFooterTPS(unittest.TestCase):
    """Line 2 shows ⚡ N.N tok/s when _run_start_time is set and output > 0."""

    def test_tps_shown_during_run(self):
        """When _run_start_time is set and _running_tokens_out > 0, TPS appears."""
        import time as _time

        a = _agent(_run_start_time=_time.time() - 10.0)  # 10s ago
        a._running_tokens_in = 100
        a._running_tokens_out = 200
        line = footer_line2(a)
        self.assertIn("\u26a1", line)  # lightning bolt
        self.assertIn("tok/s", line)

    def test_tps_omitted_when_no_run_start(self):
        """When _run_start_time is not set, TPS is omitted."""
        a = _agent()
        a._running_tokens_in = 100
        a._running_tokens_out = 200
        line = footer_line2(a)
        self.assertNotIn("tok/s", line)

    def test_tps_omitted_when_no_output_tokens(self):
        """When _running_tokens_out is 0, TPS is omitted (avoid div-by-zero)."""
        import time as _time

        a = _agent(_run_start_time=_time.time() - 5.0)
        a._running_tokens_in = 100
        a._running_tokens_out = 0
        line = footer_line2(a)
        self.assertNotIn("tok/s", line)

    def test_tps_value_is_reasonable(self):
        """200 tokens in 10s → ~20.0 tok/s."""
        import time as _time

        a = _agent(_run_start_time=_time.time() - 10.0)
        a._running_tokens_in = 100
        a._running_tokens_out = 200
        line = footer_line2(a)
        # The TPS value should be around 20.0 (allow some slack for timing)
        self.assertIn("20.", line)


class TestFooterTempFormatting(unittest.TestCase):
    """Temperature is formatted cleanly with :g (no float-precision noise)."""

    def test_temp_0_1(self):
        a = _agent(_temperature=0.1)
        line = footer_line1(a)
        self.assertIn("0.1", line)

    def test_temp_0_7(self):
        a = _agent(_temperature=0.7)
        line = footer_line1(a)
        self.assertIn("0.7", line)

    def test_temp_float_noise_cleaned(self):
        """0.1 + 0.2 = 0.30000000000000004 → should render as '0.3'."""
        a = _agent(_temperature=0.1 + 0.2)
        line = footer_line1(a)
        self.assertIn("0.3", line)
        self.assertNotIn("0000000", line)  # no float noise

    def test_temp_icon_has_no_vs16(self):
        """R07.17: the thermometer emoji must NOT carry VS16 (causes spacing gap).

        U+1F321 THERMOMETER defaults to emoji presentation, so the VS16
        (\ufe0f) is redundant. Some terminals render the VS16 as an extra
        blank column, causing a 6-space gap between the icon and the value.
        """
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "footer.py"
        text = src.read_text(encoding="utf-8")
        # The _e_temp line must use \U0001f321 WITHOUT \ufe0f
        for line in text.split("\n"):
            if "_e_temp =" in line and "U0001f321" in line:
                self.assertNotIn(
                    "\\ufe0f",
                    line,
                    "_e_temp must not carry VS16 — it causes a spacing gap on some terminals",
                )
                break
        else:
            self.fail("Could not find _e_temp definition in footer.py")


class TestFooterDeduplicated(unittest.TestCase):
    """Pin the dedup itself: no footer logic may regress into the commands."""

    def test_command_modules_no_longer_define_fmt_tok_or_footer_bodies(self):
        cmds_dir = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands"
        for name in ("agent.py", "chat.py"):
            src = (cmds_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(
                "def _fmt_tok", src, f"{name} regressed: carries its own _fmt_tok copy"
            )
            self.assertNotIn(
                'f"{{n/1000:.1f}}k"', src, f"{name} regressed: carries inline token formatting"
            )
            self.assertIn(
                "from ..footer import footer_line1, footer_line2",
                src,
                f"{name} must render via the shared cli.footer builder",
            )


if __name__ == "__main__":
    unittest.main()
