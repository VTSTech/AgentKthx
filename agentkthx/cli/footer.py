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
"""

from __future__ import annotations

from .. import __version__
from ..colors import cyan, dim, green, red, yellow

__all__ = ["fmt_tok", "footer_line1", "footer_line2", "footer_text"]


def fmt_tok(n) -> str:
    """Format a token/char count compactly: 950 -> '950', 12000 -> '12.0k'."""
    n = int(str(n).strip())
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def footer_line1(agent) -> str:
    """Build the first footer line: version, model, prompt, context, tokens."""
    ctx = agent.num_ctx
    ctx_str = f"{ctx // 1024}K" if ctx and ctx >= 1024 else str(ctx) if ctx else "?"
    max_t = (
        agent._num_predict
        if agent._num_predict is not None
        else agent.model_config.default_max_tokens
    )
    max_t_str = f"{max_t // 1024}K" if max_t >= 1024 else str(max_t)
    temp = (
        agent._temperature
        if agent._temperature is not None
        else agent.model_config.default_temperature
    )
    _sys_prompt = getattr(agent, "_custom_system_prompt", "") or ""
    _prompt_chr = len(_sys_prompt)
    _prompt_tok = _prompt_chr // 4
    prompt_str = f"{fmt_tok(_prompt_chr)} chr {fmt_tok(_prompt_tok)} tok"
    _e_brand = "\u269b\ufe0f"
    _e_model = "\U0001f9e0"
    _e_ctx = "\U0001f4e6"
    _e_resp = "\U0001f4ac"
    _e_temp = "\U0001f321\ufe0f"
    _e_prmpt = "\U0001f4dd"
    parts = [
        f"{dim(_e_brand)} {cyan(__version__)}",
        f"{dim(_e_model)} {cyan(agent.model)}",
        f"{dim(_e_prmpt)} {yellow(prompt_str)}",
        f"{dim(_e_ctx)} {yellow(ctx_str)}",
        f"{dim(_e_resp)} {yellow(max_t_str)}",
        f"{dim(_e_temp)} {yellow(str(temp))}",
    ]
    return " ".join(parts)


def footer_line2(agent, session_tokens_in: int = 0, session_tokens_out: int = 0) -> str:
    """Build the second footer line: backend, token usage, context %, debug flag.

    `session_tokens_in/out` are the caller's per-session accumulated counters;
    they are only used when the agent does not expose live running totals
    (`_running_tokens_in/out`, updated during the streaming loop — the
    session counters alone only advance after `agent.run()` returns).
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
    parts = [
        f"{dim(_e_be)} {green(bname)}",
        f"{dim(_e_tok)} {yellow(tok_str)}",
        f"{dim('ctx')} {_ctx_pct_str}",
    ]
    if agent.debug:
        parts.append(f"{red(_e_dbg + ' debug')}")
    return " ".join(parts)


def footer_text(agent, session_tokens_in: int = 0, session_tokens_out: int = 0) -> str:
    """Both footer lines joined with a newline (legacy single-string shape)."""
    return f"{footer_line1(agent)}\n{footer_line2(agent, session_tokens_in, session_tokens_out)}"
