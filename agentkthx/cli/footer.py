"""Shared 2-line session footer builder for `agentkthx chat` / `agentkthx agent`.

R07.12 intra-release deduplication (CodeFlow duplicate-block report): the two
command modules had byte-identical copies of the footer builder — including
FOUR identical nested `_fmt_tok` definitions — ever since R06.58 ported the
chat footer to agent mode. The logic now lives here once; `cmd_chat` and
`cmd_agent` render through it with thin closures that keep their own
per-session token counters as call-time arguments.

Deliberately NOT shared: the terminal scroll-region management (DECSTBM
setup/teardown, cursor save/restore, resize handling). That is presentation
plumbing owned by each command loop; only footer TEXT construction is shared.

Pure formatting — stdlib only, no state, no I/O.

R07.17 additions:
- Line 1: optional batch-size segment (🔧 N) shown when ``agent._num_batch``
  is set. Placed after temp.
- Line 2: optional TPS segment (⚡ N.N tok/s) showing PER-RESPONSE tokens/sec
  for the most recent completed generation call (not run-average — run-average
  skews low because it includes tool execution + memory update gaps between
  generation calls). Computed from ``agent._gen_start_time`` /
  ``_gen_end_time`` / ``_gen_tokens_out`` (set in ``_generate_with_retry``).
- Fixed temp icon spacing: removed redundant VS16 from thermometer emoji
  (U+1F321 defaults to emoji presentation; the VS16 was rendering as extra
  blank columns on some terminals, causing the large gap between 🌡️ and
  the value).
- Temp value now formatted with ``:g`` to avoid float-precision noise
  (e.g. ``0.30000000000000004`` → ``0.3``).
"""

from __future__ import annotations

from .. import __version__
from ..colors import cyan, dim, green, red, yellow

__all__ = ["fmt_tok", "fmt_token_size", "footer_line1", "footer_line2", "footer_text"]


def fmt_tok(n) -> str:
    """Format a token/char count compactly: 950 -> '950', 12000 -> '12.0k'.

    Used for prompt-size + cumulative token counters (chr/tok), where
    fractional-k display is desirable (1.1k chr, 282 tok).
    """
    n = int(str(n).strip())
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def fmt_token_size(n) -> str:
    """Format a context-window / max-tokens size as '128K', '1M', '2K', '512'.

    R07.19: used for the ctx + max-tokens segments in the footer line 1 and
    for the Context column in ``agentkthx models``. Distinct from
    ``fmt_tok`` (which is for prompt-size + cumulative counters — those want
    fractional-k like '1.1k'); this one wants clean power-of-1024 forms:

      - 0       → '0'
      - 512     → '512'
      - 1024    → '1K'
      - 2048    → '2K'
      - 8192    → '8K'
      - 131072  → '128K'
      - 1048576 → '1M'

    Always integer K/M (no '2.5K') — matches how Ollama/llama-server report
    context sizes in their docs and how users specify them via --num-ctx 128k.
    """
    if n is None:
        return "?"
    n = int(str(n).strip())
    if n >= 1024 * 1024 and n % (1024 * 1024) == 0:
        return f"{n // (1024 * 1024)}M"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}K"
    return str(n)


def _fmt_temp(temp) -> str:
    """Format temperature cleanly: 0.1 -> '0.1', 0.30000000000000004 -> '0.3'.

    Uses ``:g`` to strip trailing zeros and avoid float-precision noise.
    Falls back to ``str()`` for non-float types (e.g. None handled by caller).
    """
    if isinstance(temp, float):
        return f"{temp:g}"
    return str(temp)


def footer_line1(agent) -> str:
    """Build the first footer line: version, model, prompt, context, tokens, temp, batch, quant.

    R07.17: added optional batch-size segment (🔧 N) after temp, shown only
    when ``agent._num_batch`` is not None. Also fixed temp icon spacing by
    removing the redundant VS16 from the thermometer emoji.
    R07.19: ctx + max-tokens now always render in 128K style via
    ``fmt_token_size`` (was conditional on >=1024). Added optional
    quantization segment (🧊 Q4_K_M) after batch, shown when
    ``agent._weight_quant`` is set.
    """
    ctx = agent.num_ctx
    # R07.19: falsy ctx (0/None) renders as '?' to match the historical
    # "missing context" placeholder. fmt_token_size is a pure formatter
    # (0 → '0'); the falsy check lives here so the helper stays reusable.
    ctx_str = fmt_token_size(ctx) if ctx else "?"
    max_t = (
        agent._num_predict
        if agent._num_predict is not None
        else agent.model_config.default_max_tokens
    )
    max_t_str = fmt_token_size(max_t)
    temp = (
        agent._temperature
        if agent._temperature is not None
        else agent.model_config.default_temperature
    )
    _sys_prompt = getattr(agent, "_custom_system_prompt", "") or ""
    _prompt_chr = len(_sys_prompt)
    _prompt_tok = _prompt_chr // 4
    prompt_str = f"{fmt_tok(_prompt_chr)} chr {fmt_tok(_prompt_tok)} tok"
    # R07.17: removed \ufe0f (VS16) from thermometer — U+1F321 defaults to
    # emoji presentation, so VS16 is redundant. Some terminals render the
    # VS16 as an extra blank column, causing a 6-space gap between the icon
    # and the value. Other emojis below (🧠 📝 📦 💬) are in the Emoji
    # category and don't need VS16 either.
    _e_brand = "\u269b\ufe0f"  # atom — needs VS16 (defaults to text)
    _e_model = "\U0001f9e0"  # brain
    _e_ctx = "\U0001f4e6"  # package
    _e_resp = "\U0001f4ac"  # speech bubble
    _e_temp = "\U0001f321"  # thermometer — NO VS16 (defaults to emoji)
    _e_prmpt = "\U0001f4dd"  # memo
    _e_batch = "\U0001f527"  # wrench — for batch size (R07.17)
    _e_quant = "\U0001f9ca"  # ice cube — for weight quant (R07.19)
    parts = [
        f"{dim(_e_brand)} {cyan(__version__)}",
        f"{dim(_e_model)} {cyan(agent.model)}",
        f"{dim(_e_prmpt)} {yellow(prompt_str)}",
        f"{dim(_e_ctx)} {yellow(ctx_str)}",
        f"{dim(_e_resp)} {yellow(max_t_str)}",
        f"{dim(_e_temp)} {yellow(_fmt_temp(temp))}",
    ]
    # R07.17: batch size — only shown when explicitly set
    _num_batch = getattr(agent, "_num_batch", None)
    if _num_batch is not None:
        parts.append(f"{dim(_e_batch)} {yellow(str(_num_batch))}")
    # R07.19: weight quantization — only shown when detected (Ollama
    # /api/show details.quantization_level, or GGUF header for local
    # backends). None for cloud backends that don't report it.
    _weight_quant = getattr(agent, "_weight_quant", None)
    if _weight_quant:
        parts.append(f"{dim(_e_quant)} {yellow(str(_weight_quant))}")
    return " ".join(parts)


def footer_line2(agent, session_tokens_in: int = 0, session_tokens_out: int = 0) -> str:
    """Build the second footer line: backend, token usage, TPS, context %, debug flag.

    `session_tokens_in/out` are the caller's per-session accumulated counters;
    they are only used when the agent does not expose live running totals
    (`_running_tokens_in/out`, updated during the streaming loop — the
    session counters alone only advance after `agent.run()` returns).

    R07.17: TPS segment (⚡ N.N tok/s) shows PER-RESPONSE tokens/sec for the
    most recent completed generation call. Computed from:
      ``agent._gen_tokens_out / (_gen_end_time - _gen_start_time)``
    where ``_gen_start_time`` / ``_gen_end_time`` / ``_gen_tokens_out`` are
    set in ``_generate_with_retry`` (the single chokepoint for both streaming
    and non-streaming paths). This avoids the run-average skew where tool
    execution time, memory updates, and other non-generation gaps drag the
    average down. Omitted before the first generation completes.
    """
    backend = getattr(agent.backend, "backend_type", None)
    bname = (
        backend.value if backend and hasattr(backend, "value") else str(backend) if backend else "?"
    )
    _tok_in = getattr(agent, "_running_tokens_in", 0) or session_tokens_in
    _tok_out = getattr(agent, "_running_tokens_out", 0) or session_tokens_out
    tok_str = f"\u2191{fmt_tok(_tok_in)} \u2193{fmt_tok(_tok_out)}"
    # Session context usage percentage: (in + out) / num_ctx
    _total_session = _tok_in + _tok_out
    _ctx = agent.num_ctx or 8192
    _ctx_pct = min(100, int((_total_session / _ctx) * 100)) if _ctx > 0 else 0
    # Color the percentage based on usage level
    if _ctx_pct >= 85:
        _ctx_pct_str = red(f"{_ctx_pct}%")
    elif _ctx_pct >= 60:
        _ctx_pct_str = yellow(f"{_ctx_pct}%")
    else:
        _ctx_pct_str = green(f"{_ctx_pct}%")
    _e_be = "\U0001f50c"
    _e_tok = "\U0001f4c8"
    _e_dbg = "\U0001f41b"
    _e_tps = "\u26a1"  # R07.17: lightning bolt for TPS
    parts = [
        f"{dim(_e_be)} {green(bname)}",
        f"{dim(_e_tok)} {yellow(tok_str)}",
    ]
    # R07.17: per-RESPONSE TPS (not run-average). Shows tokens/sec for the
    # most recent completed generation call only — avoids skew from tool
    # execution time, memory updates, and inter-step gaps.
    # Use explicit getattr-with-default to avoid MagicMock auto-attr creation
    # in test stubs (a MagicMock returns a new MagicMock for any attr access,
    # which would fail the `> 0` comparison below with TypeError).
    _gen_start = getattr(agent, "_gen_start_time", 0.0)
    _gen_end = getattr(agent, "_gen_end_time", 0.0)
    _gen_tokens = getattr(agent, "_gen_tokens_out", 0)
    # Coerce to numeric in case a test stub left a non-int (MagicMock)
    if not isinstance(_gen_start, (int, float)):
        _gen_start = 0.0
    if not isinstance(_gen_end, (int, float)):
        _gen_end = 0.0
    if not isinstance(_gen_tokens, int):
        _gen_tokens = 0
    if _gen_start and _gen_end and _gen_tokens > 0:
        _elapsed = _gen_end - _gen_start
        if _elapsed > 0.001:  # avoid div-by-zero on instant returns
            _tps = _gen_tokens / _elapsed
            parts.append(f"{dim(_e_tps)} {yellow(f'{_tps:.1f}')} {dim('tok/s')}")
    parts.append(f"{dim('ctx')} {_ctx_pct_str}")
    if agent.debug:
        parts.append(f"{red(_e_dbg + ' debug')}")
    return " ".join(parts)


def footer_text(agent, session_tokens_in: int = 0, session_tokens_out: int = 0) -> str:
    """Both footer lines joined with a newline (legacy single-string shape)."""
    return f"{footer_line1(agent)}\n{footer_line2(agent, session_tokens_in, session_tokens_out)}"
