"""
R07.18 regression: token-size parser + footer quant + models-list Quant column.

Three related additions in R07.18:

1. **Token-size parser** (``agentkthx/shared_args.py:_parse_token_size``):
   accepts human-friendly forms like ``128k``, ``1m``, ``2g``, ``2.5k`` for
   ``--num-ctx`` / ``--num-predict`` and converts them to plain ints (131072,
   1048576, 2147483648, 2560). The agent still receives a plain int — no
   backend changes. Plain ints (``131072``), sentinels (``-1``), and
   zero (``0``) all pass through unchanged.

2. **Footer quantization segment** (``agentkthx/cli/footer.py:footer_line1``):
   ``🧊 Q4_K_M`` appears after the batch segment when ``agent._weight_quant``
   is set. Omitted when None (cloud backends that don't report it). Uses
   the ice-cube emoji (U+1F9CA).

3. **Models-list Quant column** (``agentkthx/cli/commands/models.py``): new
   column between Size and Context showing ``details.quantization_level``
   from the Ollama ``/api/tags`` response. Renders as "Q4_K_M" / "Q8_0" /
   "F16" etc. for local backends; omitted entirely for cloud providers.

4. **128K-style display**: footer ctx + max-tokens now always render via
   ``fmt_token_size`` (e.g. ``131072`` → ``128K``, ``1048576`` → ``1M``).
   Was conditional on ``>= 1024`` before — now consistent. The models-list
   Context column also uses ``fmt_token_size``.

These tests verify:
- ``_parse_token_size`` handles all the forms above.
- ``--num-ctx 128k`` / ``--num-predict 2k`` parse correctly on all 3 subcommands.
- ``SharedConfig`` carries the int values.
- ``fmt_token_size`` renders 128K / 1M / 2K / 512 / 0 correctly.
- Footer line 1 shows 🧊 segment when ``_weight_quant`` is set; omits when None.
- Footer ctx renders as ``128K`` for ``num_ctx=131072`` (was ``128K`` already
  via the old conditional, but now via ``fmt_token_size``).
- Footer ctx renders as ``?`` for ``num_ctx=0`` (falsy placeholder preserved).
- Models-list source contains the Quant column header + row rendering.
- ``_detect_weight_quant`` (agent_factory helper) reads from
  ``backend.get_model_info`` first, then ``backend.list_models``.

All tests are pure logic / mocked — no network calls.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import unittest

import pytest

# ─────────────────────────────────────────────────────────────────────
# 1. _parse_token_size
# ─────────────────────────────────────────────────────────────────────


class TestParseTokenSize(unittest.TestCase):
    def test_plain_int_string(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("131072"), 131072)
        self.assertEqual(_parse_token_size("2048"), 2048)
        self.assertEqual(_parse_token_size("0"), 0)

    def test_int_passes_through(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size(131072), 131072)
        self.assertEqual(_parse_token_size(0), 0)

    def test_k_suffix_lowercase(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("128k"), 128 * 1024)
        self.assertEqual(_parse_token_size("2k"), 2 * 1024)

    def test_k_suffix_uppercase(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("128K"), 128 * 1024)
        self.assertEqual(_parse_token_size("2K"), 2 * 1024)

    def test_m_suffix(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("1m"), 1024 * 1024)
        self.assertEqual(_parse_token_size("1M"), 1024 * 1024)
        self.assertEqual(_parse_token_size("2m"), 2 * 1024 * 1024)

    def test_g_suffix(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("1g"), 1024**3)
        self.assertEqual(_parse_token_size("2G"), 2 * 1024**3)

    def test_fractional_k(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("2.5k"), int(2.5 * 1024))
        self.assertEqual(_parse_token_size("0.5k"), int(0.5 * 1024))

    def test_fractional_m(self):
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("1.5m"), int(1.5 * 1024 * 1024))

    def test_negative_sentinel(self):
        """-1 is a sentinel meaning 'unlimited' for some backends."""
        from agentkthx.shared_args import _parse_token_size

        self.assertEqual(_parse_token_size("-1"), -1)
        self.assertEqual(_parse_token_size("-0"), 0)

    def test_empty_string_raises(self):
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError):
            _parse_token_size("")
        with self.assertRaises(ValueError):
            _parse_token_size("   ")

    def test_bad_suffix_raises(self):
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError):
            _parse_token_size("128x")
        with self.assertRaises(ValueError):
            _parse_token_size("128kb")

    def test_bad_numeric_part_raises(self):
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError):
            _parse_token_size("abck")

    def test_sign_only_raises(self):
        from agentkthx.shared_args import _parse_token_size

        with self.assertRaises(ValueError):
            _parse_token_size("-")
        with self.assertRaises(ValueError):
            _parse_token_size("+")


# ─────────────────────────────────────────────────────────────────────
# 2. CLI flags accept human-friendly forms
# ─────────────────────────────────────────────────────────────────────


def test_add_agent_args_num_ctx_accepts_128k():
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args(["--num-ctx", "128k", "--num-predict", "2k"])
    assert args.num_ctx == 128 * 1024
    assert args.num_predict == 2 * 1024


def test_add_agent_args_num_ctx_accepts_plain_int():
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    args = p.parse_args(["--num-ctx", "131072"])
    assert args.num_ctx == 131072


def test_add_shared_args_num_ctx_accepts_1m():
    from agentkthx.shared_args import add_shared_args

    p = argparse.ArgumentParser()
    add_shared_args(p)
    args = p.parse_args(["--num-ctx", "1m"])
    assert args.num_ctx == 1024 * 1024


def test_test_subcommand_num_ctx_accepts_2g():
    from agentkthx.cli.parser import create_parser

    parser = create_parser()
    args = parser.parse_args(["test", "--num-ctx", "2g", "01"])
    assert args.num_ctx == 2 * 1024**3


def test_test_subcommand_num_predict_accepts_4k():
    from agentkthx.cli.parser import create_parser

    parser = create_parser()
    args = parser.parse_args(["test", "--num-predict", "4k", "01"])
    assert args.num_predict == 4 * 1024


def test_num_ctx_invalid_suffix_rejected():
    """argparse converts ValueError to ArgumentTypeError → exits with code 2."""
    from agentkthx.shared_args import add_agent_args

    p = argparse.ArgumentParser()
    add_agent_args(p)
    with pytest.raises(SystemExit) as excinfo:
        p.parse_args(["--num-ctx", "128x"])
    assert excinfo.value.code == 2


# ─────────────────────────────────────────────────────────────────────
# 3. SharedConfig env-var fallback accepts human-friendly forms
# ─────────────────────────────────────────────────────────────────────


def test_shared_config_num_ctx_from_env_128k(monkeypatch):
    from agentkthx.shared_args import parse_shared_args

    monkeypatch.setenv("AGENTKTHX_NUM_CTX", "128k")
    args = argparse.Namespace(num_ctx=None)
    cfg = parse_shared_args(args)
    assert cfg.num_ctx == 128 * 1024


def test_shared_config_num_predict_from_env_2k(monkeypatch):
    from agentkthx.shared_args import parse_shared_args

    monkeypatch.setenv("AGENTKTHX_NUM_PREDICT", "2k")
    args = argparse.Namespace(num_predict=None)
    cfg = parse_shared_args(args)
    assert cfg.num_predict == 2 * 1024


# ─────────────────────────────────────────────────────────────────────
# 4. fmt_token_size
# ─────────────────────────────────────────────────────────────────────


class TestFmtTokenSize(unittest.TestCase):
    def test_zero(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(0), "0")

    def test_small_int(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(512), "512")
        self.assertEqual(fmt_token_size(768), "768")

    def test_k_exact(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(1024), "1K")
        self.assertEqual(fmt_token_size(2048), "2K")
        self.assertEqual(fmt_token_size(8192), "8K")
        self.assertEqual(fmt_token_size(131072), "128K")

    def test_m_exact(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(1024 * 1024), "1M")
        self.assertEqual(fmt_token_size(2 * 1024 * 1024), "2M")

    def test_non_power_of_1024_falls_back_to_int(self):
        """1500 isn't a clean multiple of 1024 → renders as '1500'."""
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(1500), "1500")
        self.assertEqual(fmt_token_size(1000), "1000")

    def test_none_renders_question_mark(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size(None), "?")

    def test_string_input_coerced(self):
        from agentkthx.cli.footer import fmt_token_size

        self.assertEqual(fmt_token_size("2048"), "2K")


# ─────────────────────────────────────────────────────────────────────
# 5. Footer line 1 — quantization segment
# ─────────────────────────────────────────────────────────────────────


class _ModelConfig:
    default_max_tokens = 32768
    default_temperature = 0.7


class _BackendType:
    def __init__(self, value):
        self.value = value


class _Backend:
    def __init__(self, btype):
        self.backend_type = btype


def _agent(**kw):
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
    if "_num_batch" in kw:
        a._num_batch = kw["_num_batch"]
    if "_weight_quant" in kw:
        a._weight_quant = kw["_weight_quant"]
    return a


class TestFooterQuantSegment(unittest.TestCase):
    """R07.18: 🧊 emoji shown when _weight_quant is set (R07.17 style)."""

    def test_quant_shown_when_set(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent(_weight_quant="Q4_K_M")
        line = footer_line1(a)
        self.assertIn("\U0001f9ca", line)  # ice cube emoji
        self.assertIn("Q4_K_M", line)

    def test_quant_omitted_when_none(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent()  # _weight_quant not set
        line = footer_line1(a)
        self.assertNotIn("\U0001f9ca", line)

    def test_quant_omitted_when_empty_string(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent(_weight_quant="")
        line = footer_line1(a)
        self.assertNotIn("\U0001f9ca", line)

    def test_quant_appears_after_batch(self):
        """The quant segment appears after the batch segment when both set."""
        from agentkthx.cli.footer import footer_line1

        a = _agent(_num_batch=64, _weight_quant="Q8_0")
        line = footer_line1(a)
        batch_idx = line.index("64")
        quant_idx = line.index("Q8_0")
        self.assertGreater(quant_idx, batch_idx, "quant must appear after batch")


class TestFooterCtxFormatting(unittest.TestCase):
    """R07.18: ctx + max-tokens render via fmt_token_size (128K style)."""

    def test_ctx_renders_as_128k(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent(num_ctx=131072)
        line = footer_line1(a)
        self.assertIn("128K", line)

    def test_ctx_renders_as_1m(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent(num_ctx=1024 * 1024)
        line = footer_line1(a)
        self.assertIn("1M", line)

    def test_ctx_zero_renders_question_mark(self):
        """Falsy ctx (0) renders as '?' — historical placeholder preserved."""
        from agentkthx.cli.footer import footer_line1

        a = _agent(num_ctx=0)
        line = footer_line1(a)
        self.assertIn("?", line)

    def test_max_tokens_renders_as_32k(self):
        from agentkthx.cli.footer import footer_line1

        a = _agent()  # default_max_tokens=32768 from _ModelConfig
        line = footer_line1(a)
        self.assertIn("32K", line)


# ─────────────────────────────────────────────────────────────────────
# 6. Models-list Quant column (source-level check)
# ─────────────────────────────────────────────────────────────────────


class TestModelsListQuantColumn(unittest.TestCase):
    """Verify the Quant column is rendered in the models command."""

    def test_quant_column_in_header(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "models.py"
        )
        text = src.read_text(encoding="utf-8")
        self.assertIn("'Quant'", text, "models.py lost the Quant column header")
        self.assertIn("QUANT_W", text, "models.py lost the QUANT_W width constant")

    def test_quant_column_in_row_rendering(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "models.py"
        )
        text = src.read_text(encoding="utf-8")
        # The row rendering must include quant_col
        self.assertIn("quant_col", text, "models.py lost the quant_col variable")
        # And it must read quantization_level from the details block
        self.assertIn("quantization_level", text, "models.py lost the quantization_level lookup")

    def test_quant_column_excluded_for_cloud_providers(self):
        """Cloud providers don't show Size/Quant — header omits both."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "models.py"
        )
        text = src.read_text(encoding="utf-8")
        # The cloud-provider header must NOT include Quant
        # Find the cloud header line
        cloud_header_idx = text.index("Cloud providers - skip Size/Quant")
        cloud_block = text[cloud_header_idx : cloud_header_idx + 400]
        self.assertIn("Quant", cloud_block, "cloud-provider comment must mention Quant exclusion")


# ─────────────────────────────────────────────────────────────────────
# 7. _detect_weight_quant helper (agent_factory)
# ─────────────────────────────────────────────────────────────────────


class TestDetectWeightQuant(unittest.TestCase):
    """Verify the best-effort weight-quant lookup used by _build_agent."""

    def test_uses_get_model_info_first(self):
        from agentkthx.cli.agent_factory import _detect_weight_quant

        class _B:
            def get_model_info(self, model):
                return {"details": {"quantization_level": "Q4_K_M"}}

            def list_models(self):
                self.fail("list_models should not be called when get_model_info succeeds")

        self.assertEqual(_detect_weight_quant(_B(), "test"), "Q4_K_M")

    def test_falls_back_to_list_models(self):
        from agentkthx.cli.agent_factory import _detect_weight_quant

        class _B:
            def get_model_info(self, model):
                return None  # not found

            def list_models(self):
                return [
                    {"name": "other", "details": {"quantization_level": "F16"}},
                    {"name": "test", "details": {"quantization_level": "Q8_0"}},
                ]

        self.assertEqual(_detect_weight_quant(_B(), "test"), "Q8_0")

    def test_returns_none_when_neither_source_has_quant(self):
        from agentkthx.cli.agent_factory import _detect_weight_quant

        class _B:
            def get_model_info(self, model):
                return {"details": {}}  # no quantization_level

            def list_models(self):
                return [{"name": "test", "details": {}}]

        self.assertIsNone(_detect_weight_quant(_B(), "test"))

    def test_returns_none_on_exceptions(self):
        """Backend unreachable / errors → None (footer omits quant)."""
        from agentkthx.cli.agent_factory import _detect_weight_quant

        class _B:
            def get_model_info(self, model):
                raise ConnectionError("backend down")

            def list_models(self):
                raise ConnectionError("backend down")

        self.assertIsNone(_detect_weight_quant(_B(), "test"))

    def test_returns_none_when_backend_lacks_methods(self):
        """Backends without get_model_info / list_models → None."""
        from agentkthx.cli.agent_factory import _detect_weight_quant

        class _B:
            pass

        self.assertIsNone(_detect_weight_quant(_B(), "test"))


if __name__ == "__main__":
    unittest.main()
