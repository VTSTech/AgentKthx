"""
AgentKthx — ROB-37 regression tests: dynamic Name column width in ``cmd_models``

R07.18 set the models-table Name column to a fixed ``NAME_W = 48`` (local) /
``50`` (cloud) while keeping the no-truncation policy (the user must be able
to copy the full name into ``-m``). ``pad_colored`` pads short names but does
not truncate long ones, so any model name longer than the fixed width pushed
the Size/Quant/Context columns right for that row only — the table visibly
broke. Real-world trigger (user report, R07.19 pre-release):

    krith/meta-llama-3.2-1b-instruct-uncensored:IQ4_XS   (50 chars, 2 over)

R07.19 makes NAME_W dynamic: it is measured from the longest model name in
the already-loaded (and free-filtered) ``models`` list, floored at the
R07.18 defaults. Header, separator and every data row share the measured
width because the measurement happens before any of them renders.

These tests drive the REAL ``cmd_models`` over stub backends (no network)
and pin:

1. Alignment — with mixed short/long names, every row's Size/Quant/Context
   columns start at the same character offset (the user-report bug).
2. Widening — the separator/header width grows to fit the longest name.
3. Floor — short listings keep the R07.18 widths byte-for-byte (48 local /
   50 cloud), so the common case renders identically.
4. Cloud layout — same dynamic behavior for the cloud branch.
5. No truncation — the longest name appears in full in the output.
"""

import argparse
import contextlib
import io
import re

import pytest

from agentkthx.backends.ollama import OllamaBackend
from agentkthx.cli.commands import models as models_mod

# The user-reported name that broke the old fixed 48-char column.
LONG_NAME = "krith/meta-llama-3.2-1b-instruct-uncensored:IQ4_XS"

# A cloud name genuinely longer than the 50-char cloud floor, so the cloud
# widening test proves measurement (the krith name is exactly 50 = floor).
CLOUD_LONG = "org/super-long-openrouter-model-name-exceeding-fifty-chars:free"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def _visible(line: str) -> str:
    """Strip ANSI SGR sequences so character-offset math is honest."""
    return _ANSI_RE.sub("", line)


def _entry(name: str, size_gb: float = 0.7, quant: str = "Q4_K_M", family: str = "llama") -> dict:
    """One models-list entry shaped like the Ollama /api/tags payload."""
    return {
        "name": name,
        "size": int(size_gb * 1024**3),
        "details": {"family": family, "quantization_level": quant},
    }


class StubOllamaBackend(OllamaBackend):
    """OllamaBackend stand-in — no network, no super().__init__ side effects.

    Subclassing (rather than a bare duck type) is required because
    ``cmd_models`` branches on ``isinstance(backend, OllamaBackend)`` to
    pick the local row-rendering path.
    """

    def __init__(self, entries):
        self._entries = entries
        self._base_url = "http://stub:11434"  # base_url is a read-only property
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return list(self._entries)

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768


class StubCloudBackend:
    """Cloud stand-in (is_cloud=True, NOT an OllamaBackend instance) so
    ``cmd_models`` takes the cloud row-rendering branch."""

    is_cloud = True

    def __init__(self, entries):
        self._entries = entries
        self.base_url = "https://stub.cloud/v1"
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return list(self._entries)

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768


def _run_models(monkeypatch, backend, entries) -> str:
    """Drive the real cmd_models over a stub backend, return captured stdout."""
    monkeypatch.setattr(
        "agentkthx.cli.commands.models.get_config",
        lambda: argparse.Namespace(backend="stub"),
    )
    monkeypatch.setattr(
        "agentkthx.cli.commands.models.get_backend", lambda name, api_mode=None: backend
    )
    monkeypatch.setattr("agentkthx.cli._init_acp", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_tool_support",
        lambda model, api_mode="openre": None,
    )
    args = argparse.Namespace(
        backend="stub", tool_support=False, api_mode=None, no_cache=False
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = models_mod.cmd_models(args)
    assert rc == 0
    return buf.getvalue()


def _table_lines(output: str):
    """Split captured output into (header, separator, data rows) for the
    models table. Layout: dash, header, dash, rows..., dash — and separator
    lines are wrapped in dim(), so detect dashes on VISIBLE text."""
    lines = output.splitlines()
    dash_idx = [i for i, ln in enumerate(lines) if ln and set(_visible(ln)) == {"-"}]
    assert len(dash_idx) >= 3, f"expected 3 table separators in output:\n{output}"
    first, second, third = dash_idx[0], dash_idx[1], dash_idx[2]
    header = _visible(lines[first + 1])
    separator = lines[first]
    rows = [_visible(ln) for ln in lines[second + 1 : third]]
    return header, separator, rows


class TestLocalTableDynamicWidth:
    """Local (Ollama) backend — the layout the user report hit."""

    def test_long_name_row_aligns_with_short_rows(self, monkeypatch):
        """THE bug: a 52-char name must not push its row's numeric columns
        right relative to the short-name rows."""
        entries = [
            _entry("gemma3:270m", size_gb=0.27, quant="Q8_0", family="gemma3"),
            _entry(LONG_NAME, size_gb=0.7, quant="IQ1_M", family="llama"),
            _entry("qwen2.5:0.5b", size_gb=0.37, quant="Q4_K_M", family="qwen2"),
            _entry("granite4:350m", size_gb=0.66, quant="BF16", family="granite"),
        ]
        out = _run_models(monkeypatch, StubOllamaBackend(entries), entries)
        header, separator, rows = _table_lines(out)
        name_w = len(LONG_NAME)

        gb_indexes = {row.find(" GB") for row in rows if " GB" in row}
        assert len(rows) == 4
        assert gb_indexes == {name_w + 9}, (
            f"' GB' column must sit at offset {name_w + 9} on EVERY row, "
            f"got {gb_indexes} — the long-name row is misaligned\n{out}"
        )
        # Header cells share the same grid: the right-aligned 'Size' header
        # sits inside the same 9-char slot whose tail is ' GB' on data rows.
        assert header[name_w + 8 : name_w + 12] == "Size"
        assert len(separator) == len(header)

    def test_name_column_widens_to_longest_name(self, monkeypatch):
        """Separator/header grow to 76 + longest_name (the local-layout
        formula 2+W+1+9+1+8+1+12+2+12+2+12+2+12), proving the column is
        measured, not fixed."""
        entries = [_entry(LONG_NAME), _entry("gemma3:270m")]
        out = _run_models(monkeypatch, StubOllamaBackend(entries), entries)
        _, separator, _ = _table_lines(out)
        expected = 76 + len(LONG_NAME)  # 128 for the 52-char name
        assert len(separator) == expected, (
            f"separator must widen to {expected} (76 + {len(LONG_NAME)}), "
            f"got {len(separator)}"
        )

    def test_quant_and_context_columns_align(self, monkeypatch):
        """Pin the Quant and Context offsets too — alignment is a property
        of the whole grid, not just the Size column."""
        entries = [
            _entry("qwen2.5-coder:0.5b-instruct-q4_k_m", quant="Q4_K_M"),
            _entry(LONG_NAME, quant="IQ1_M"),
        ]
        out = _run_models(monkeypatch, StubOllamaBackend(entries), entries)
        _, _, rows = _table_lines(out)
        name_w = len(LONG_NAME)
        for row in rows:
            # Quant: left-aligned 8-wide column starting right after Size.
            assert row[name_w + 13 : name_w + 21].strip() in {"Q4_K_M", "IQ1_M"}
            # Context: right-aligned 12-wide column ending at name_w+34.
            assert row[name_w + 31 : name_w + 34] == "32K"

    def test_short_listings_keep_r0718_floor(self, monkeypatch):
        """The floor keeps short listings byte-identical to R07.18:
        48-char Name column → separator = 76 + 48 = 124."""
        entries = [
            _entry("gemma3:270m"),
            _entry("qwen2.5:0.5b"),
            _entry("granite4:350m"),
        ]
        out = _run_models(monkeypatch, StubOllamaBackend(entries), entries)
        header, separator, rows = _table_lines(out)
        assert len(separator) == 124
        assert len(separator) == len(header)
        assert all(row.find(" GB") == 48 + 9 for row in rows if " GB" in row)

    def test_full_name_not_truncated(self, monkeypatch):
        """The no-truncation contract (R07.18) survives the width change:
        the user must still be able to copy the full name into `-m`."""
        entries = [_entry(LONG_NAME)]
        out = _run_models(monkeypatch, StubOllamaBackend(entries), entries)
        assert LONG_NAME in _visible(out)


class TestCloudTableDynamicWidth:
    """Cloud branch — same dynamic measurement, different layout."""

    def test_cloud_table_widens_past_50_floor(self, monkeypatch):
        """Cloud floor is 50; a longer name still widens the table.
        Cloud separator formula: 2+W+1+12+2+12+2+12 = 43 + NAME_W."""
        assert len(CLOUD_LONG) > 50
        entries = [
            {"name": CLOUD_LONG, "size": 0, "details": {"family": "llama"}},
            {"name": "glm-4.5-flash", "size": 0, "details": {"family": "glm"}},
        ]
        out = _run_models(monkeypatch, StubCloudBackend(entries), entries)
        header, separator, rows = _table_lines(out)
        name_w = len(CLOUD_LONG)
        assert len(separator) == 43 + name_w
        assert len(separator) == len(header)
        # Context column (right-aligned, 12) ends at name_w+15 on every row.
        for row in rows:
            assert row[name_w + 12 : name_w + 15] == "32K"

    def test_cloud_short_list_keeps_floor(self, monkeypatch):
        """Short cloud listings stay at the R07.18 width: 43 + 50 = 93."""
        entries = [
            {"name": "glm-4.5-flash", "size": 0, "details": {"family": "glm"}},
            {"name": "glm-4.7-flash", "size": 0, "details": {"family": "glm"}},
        ]
        out = _run_models(monkeypatch, StubCloudBackend(entries), entries)
        _, separator, _ = _table_lines(out)
        assert len(separator) == 93


class TestRegressionPremise:
    """Pins the premise the fix rests on, so a future width tweak cannot
    silently re-break the user's case."""

    def test_reported_name_exceeds_r0719_fixed_width(self):
        """The reported name really is longer than the old fixed 48 — if a
        future edit shortens this string or raises the floor above it,
        this test stops representing the regression and should be updated."""
        assert len(LONG_NAME) == 50
        assert len(LONG_NAME) > 48
