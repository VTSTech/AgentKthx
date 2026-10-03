"""
Shared CLI arguments for AgentKthx example scripts.

All test/example scripts can use these standard arguments:
  --force-react       Force ReAct text-based tool calling
  --use-mf-sys        Use Modelfile system prompt
  --model MODEL       Model override
  --debug             Enable debug output
  --acp               Enable ACP integration
  --acp-url URL       ACP server URL
  --num-ctx TOKENS    Context window size
  --num-predict TOKENS Max tokens to generate
  --num-batch N       Prompt-processing batch size (Ollama)
  --repeat-penalty P  Repetition penalty (llama.cpp native)
  --repeat-last-n N   Repetition window (llama.cpp native)
  --fast              Fast mode preset (ctx=2048, predict=256)

Usage in example scripts:
    import argparse
    from agentkthx.shared_args import add_shared_args, parse_shared_args

    parser = argparse.ArgumentParser(description="My test script")
    add_shared_args(parser)
    args = parser.parse_args()
    config = parse_shared_args(args)

Then use config.force_react, config.num_ctx, etc.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SharedConfig:
    """Parsed shared configuration from CLI args or env vars."""

    force_react: bool = False
    use_modelfile_system: bool = False
    model: Optional[str] = None
    debug: bool = False
    acp: bool = False
    acp_url: Optional[str] = None
    num_ctx: Optional[int] = None
    num_predict: Optional[int] = None
    num_batch: Optional[int] = None
    repeat_penalty: Optional[float] = None
    repeat_last_n: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    fast: bool = False
    extra_args: dict = field(default_factory=dict)

    def __post_init__(self):
        """Apply --fast preset if specified."""
        if self.fast:
            if self.num_ctx is None:
                self.num_ctx = 2048
            if self.num_predict is None:
                self.num_predict = 256


def add_shared_args(parser: argparse.ArgumentParser) -> None:
    """Add shared arguments to an argument parser.

    Args:
        parser: The ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--force-react",
        action="store_true",
        help="Force ReAct text-based tool calling for all models",
    )
    parser.add_argument(
        "--use-mf-sys",
        action="store_true",
        dest="use_modelfile_system",
        help="Use the system prompt from the model's Modelfile",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        metavar="MODEL",
        help="Model to use (overrides AGENTKTHX_MODEL env var)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )
    parser.add_argument(
        "--acp",
        action="store_true",
        help="Enable ACP (Agent Control Panel) integration",
    )
    parser.add_argument(
        "--acp-url",
        default=None,
        metavar="URL",
        help="ACP server URL (default: from AGENTKTHX_ACP_URL env var)",
    )
    parser.add_argument(
        "--num-ctx",
        type=_parse_token_size,
        default=None,
        dest="num_ctx",
        metavar="TOKENS",
        help="Context window size in tokens. Accepts plain ints (131072) or "
        "human-friendly forms like 128k, 1m, 2g. R07.18+.",
    )
    parser.add_argument(
        "--num-predict",
        type=_parse_token_size,
        default=None,
        dest="num_predict",
        metavar="TOKENS",
        help="Maximum tokens to generate. Accepts plain ints (2048) or "
        "human-friendly forms like 2k, 4k. R07.18+.",
    )
    parser.add_argument(
        "--num-batch",
        type=int,
        default=None,
        dest="num_batch",
        metavar="N",
        help="Prompt-processing batch size (Ollama per-request option; "
        "llama-server/TurboQuant use 'turbo start --batch-size N' at server "
        "start). Lower values reduce peak memory during prompt eval.",
    )
    parser.add_argument(
        "--repeat-penalty",
        type=float,
        default=None,
        dest="repeat_penalty",
        metavar="PENALTY",
        help="Repetition penalty (llama.cpp native, >1.0 discourages repetition). "
        "Forwarded to Ollama + llama-server/TurboQuant/BitNet; cloud backends "
        "silently drop it. BitNet default is 1.3 for small models prone to looping.",
    )
    parser.add_argument(
        "--repeat-last-n",
        type=int,
        default=None,
        dest="repeat_last_n",
        metavar="N",
        help="Number of recent tokens to consider for repetition penalty "
        "(llama.cpp native, in tokens). 0 = full context, -1 = model default. "
        "Forwarded to Ollama + llama-server/TurboQuant/BitNet.",
    )
    parser.add_argument(
        "--temp",
        "--temperature",
        type=float,
        default=None,
        dest="temperature",
        metavar="TEMP",
        help="Sampling temperature 0.0-2.0 (default: model-specific)",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        dest="top_p",
        metavar="P",
        help="Nucleus sampling probability 0.0-1.0 (default: model-specific)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: num_ctx=2048, num_predict=256",
    )


def _backend_choices_for_help() -> list[str]:
    """Return the merged ``--backend`` choices for help text.

    R07.15 amendment (user-reported during VM smoke testing): the help
    string was hardcoded and had drifted — plugin backends mistral,
    orcarouter and pollinations (plus every alias) were accepted at
    runtime but invisible in ``chat -h`` / ``agent -h`` / ``run -h``.
    The CLI's main() loads all plugins BEFORE create_parser(), so
    get_backend_choices() sees the full native + plugin registry at
    help-render time. Falls back to the core native names when called
    outside the CLI (standalone example scripts) where plugins have
    not been loaded yet.
    """
    try:
        from .backends import get_backend_choices

        names = list(get_backend_choices())
    except Exception:
        names = []
    if not names:
        names = [
            "ollama",
            "bitnet",
            "llama-server",
            "zai",
            "openrouter",
            "huggingface",
            "gemini",
            "openai",
            "mistral",
            "orcarouter",
            "pollinations",
        ]
    return names


def add_agent_args(
    parser: argparse.ArgumentParser,
    tools_default: str = "",
    include_confirm: bool = True,
) -> None:
    """Add the full set of arguments shared by run/chat/agent CLI commands.

    This replaces the ~18 args that were individually duplicated across
    the three subcommands in cli.py.  Only command-specific args (like
    the positional *prompt* for ``run`` or ``--stream``) need to be
    added afterwards.

    Args:
        parser:  Sub-parser to add arguments to.
        tools_default:  Default value for the ``--tools`` flag
                         (e.g. "calculator" for run, "" for chat).
        include_confirm:  Whether to include the ``--confirm`` flag.
    """
    parser.add_argument("-m", "--model", default=None, help="Model to use")
    parser.add_argument("--tools", default=tools_default, help="Comma-separated tool list")
    parser.add_argument(
        "--backend",
        default=None,
        help="Backend to use (" + ", ".join(_backend_choices_for_help()) + ")",
    )
    parser.add_argument(
        "--api",
        choices=["openre", "openai", "jev"],
        default="openai",
        dest="api_mode",
        help="API mode: 'openre' (OpenResponses), 'openai' (Chat-Completions), "
        "or 'jev' (System-One decision shape via any LLM)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "--security",
        default="max",
        choices=["max", "off"],
        help="Security mode: 'max' (default, all checks enabled) or 'off' (disable all checks — use with caution)",
    )
    parser.add_argument(
        "--force-react",
        action="store_true",
        help="Force ReAct mode for tool calling",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        dest="max_steps",
        help="Maximum reasoning steps (default: 25)",
    )
    parser.add_argument(
        "--soul",
        default=None,
        help="Path to Soul Spec package (disabled by default)",
    )
    parser.add_argument(
        "--soul-level",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Soul progressive disclosure level (1=quick, 2=full, 3=deep)",
    )
    parser.add_argument(
        "--num-ctx",
        type=_parse_token_size,
        default=None,
        dest="num_ctx",
        help="Context window size in tokens. Accepts plain ints (131072) or "
        "human-friendly forms like 128k, 1m, 2g. R07.18+.",
    )
    parser.add_argument(
        "--num-predict",
        type=_parse_token_size,
        default=None,
        dest="num_predict",
        help="Maximum tokens to generate. Accepts plain ints (2048) or "
        "human-friendly forms like 2k, 4k. R07.18+.",
    )
    parser.add_argument(
        "--num-batch",
        type=int,
        default=None,
        dest="num_batch",
        help="Prompt-processing batch size (Ollama per-request option; "
        "llama-server/TurboQuant use 'turbo start --batch-size N' at server "
        "start; cloud backends ignore it). Default: backend default.",
    )
    parser.add_argument(
        "--repeat-penalty",
        type=float,
        default=None,
        dest="repeat_penalty",
        help="Repetition penalty (llama.cpp native, >1.0 discourages repetition). "
        "Forwarded to Ollama + llama-server/TurboQuant/BitNet; cloud backends "
        "silently drop it. BitNet default is 1.3.",
    )
    parser.add_argument(
        "--repeat-last-n",
        type=int,
        default=None,
        dest="repeat_last_n",
        help="Tokens to consider for repetition penalty (llama.cpp native). "
        "0 = full context, -1 = model default. Ollama + llama-server only.",
    )
    parser.add_argument(
        "--temp",
        "--temperature",
        type=float,
        default=None,
        dest="temperature",
        help="Sampling temperature 0.0-2.0 (default: model-specific)",
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        dest="top_p",
        help="Nucleus sampling probability 0.0-1.0 (default: model-specific)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Request timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--acp", action="store_true", help="Enable ACP logging to Agent Control Panel"
    )
    parser.add_argument("--acp-url", default=None, help="ACP server URL (default: from config)")
    parser.add_argument(
        "--response-format",
        choices=["text", "json"],
        default="text",
        dest="response_format",
        help="Response format: 'text' (default) or 'json' (structured output)",
    )
    parser.add_argument(
        "--truncation",
        choices=["auto", "disabled"],
        default="auto",
        help="Truncation behavior for context overflow (default: auto)",
    )
    parser.add_argument(
        "--compaction",
        default="auto",
        help="Memory compaction threshold as %% of context window (default: auto=85%%). "
        "When token usage exceeds this %% of num_ctx, older messages are compacted "
        "(tool results truncated, content summarized) instead of dropped. "
        "Use 'off' or '0' to disable, or a number like '90' for 90%%.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        default=None,
        dest="stream",
        help="Force streaming output. Cloud providers (zai/openrouter) stream by default; "
        "use --no-stream to disable streaming for cloud providers.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_false",
        default=None,
        dest="stream",
        help="Disable streaming output (useful for cloud providers when streaming causes issues).",
    )
    parser.add_argument(
        "--thinking",
        choices=["off", "auto", "low", "medium", "high"],
        default="auto",
        dest="thinking_level",
        help="Thinking / reasoning effort: 'off' (disable, fastest — recommended "
        "for JEV decisions), 'auto' (default, let model decide), "
        "'low'/'medium'/'high' (forwarded as reasoning_effort for "
        "thinking-capable models like GLM-5/o-series)",
    )
    parser.add_argument(
        "--think",
        action="store_true",
        default=False,
        dest="show_reasoning",
        help="Display reasoning_content (chain-of-thought) in CLI output when the "
        "model emits it. Off by default. Use --think to inspect what the "
        "model was 'thinking' before its final answer.",
    )
    parser.add_argument(
        "--skills",
        default=None,
        help="Comma-separated skill names to load (e.g., acp,skill-creator)",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Session ID for persistent memory (resume previous conversation)",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        dest="no_retry",
        help="Disable retry-with-error-feedback on tool failures",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        dest="max_tool_retries",
        help="Maximum retries per tool call failure (default: 2)",
    )
    if include_confirm:
        parser.add_argument(
            "--confirm",
            action="store_true",
            dest="confirm_dangerous",
            help="Require confirmation before executing dangerous tools (shell, write_file, edit_file)",
        )


def parse_shared_args(args) -> SharedConfig:
    """Parse shared args into a SharedConfig, falling back to env vars.

    ROB-35 (R07.21 CLOSED): the ``or``-coalescing used here dropped the
    documented ``0`` sentinel — ``--repeat-last-n 0`` ("0 = full context"
    per its own help text) reached ``SharedConfig`` as ``None`` (or the
    env-var value), because ``0 or _env_int(...)`` short-circuits to the
    env fallback when the arg is ``0``. The integer fields now use
    explicit ``is not None`` checks so ``0`` is preserved. The boolean
    fields (``force_react``, ``debug``, ``acp``, ``fast``,
    ``use_modelfile_system``) keep the ``or`` pattern because ``False``
    falling through to the env var is the correct behavior for them. The
    string fields (``model``, ``acp_url``) keep ``or`` because empty
    string is not a meaningful value.

    Args:
        args: Parsed argparse Namespace object.

    Returns:
        SharedConfig with values from args or env vars.
    """
    # ROB-35 (R07.21 CLOSED): explicit ``is not None`` for integer/float
    # fields so the documented ``0`` sentinel survives the coalescing
    # (was ``or _env_int(...)`` which short-circuits to env on ``0``).
    _num_ctx = getattr(args, "num_ctx", None)
    if _num_ctx is None:
        _num_ctx = _env_int("AGENTKTHX_NUM_CTX")
    _num_predict = getattr(args, "num_predict", None)
    if _num_predict is None:
        _num_predict = _env_int("AGENTKTHX_NUM_PREDICT")
    _num_batch = getattr(args, "num_batch", None)
    if _num_batch is None:
        _num_batch = _env_int("AGENTKTHX_NUM_BATCH")
    _repeat_penalty = getattr(args, "repeat_penalty", None)
    if _repeat_penalty is None:
        _repeat_penalty = _env_float("AGENTKTHX_REPEAT_PENALTY")
    _repeat_last_n = getattr(args, "repeat_last_n", None)
    if _repeat_last_n is None:
        _repeat_last_n = _env_int("AGENTKTHX_REPEAT_LAST_N")
    _temperature = getattr(args, "temperature", None)
    if _temperature is None:
        _temperature = _env_float("AGENTKTHX_TEMPERATURE")
    _top_p = getattr(args, "top_p", None)
    if _top_p is None:
        _top_p = _env_float("AGENTKTHX_TOP_P")

    return SharedConfig(
        force_react=getattr(args, "force_react", False)
        or os.environ.get("AGENTKTHX_FORCE_REACT", "0") == "1",
        use_modelfile_system=getattr(args, "use_modelfile_system", False)
        or os.environ.get("AGENTKTHX_USE_MF_SYS", "0") == "1",
        model=getattr(args, "model", None) or os.environ.get("AGENTKTHX_MODEL"),
        debug=getattr(args, "debug", False) or os.environ.get("AGENTKTHX_DEBUG", "0") == "1",
        acp=getattr(args, "acp", False) or os.environ.get("AGENTKTHX_ACP", "0") == "1",
        acp_url=getattr(args, "acp_url", None) or os.environ.get("AGENTKTHX_ACP_URL"),
        num_ctx=_num_ctx,
        num_predict=_num_predict,
        num_batch=_num_batch,
        repeat_penalty=_repeat_penalty,
        repeat_last_n=_repeat_last_n,
        temperature=_temperature,
        top_p=_top_p,
        fast=getattr(args, "fast", False) or os.environ.get("AGENTKTHX_FAST", "0") == "1",
    )


def _env_int(name: str) -> Optional[int]:
    """Read an integer from an environment variable.

    R07.18: also accepts token-size suffixes via ``_parse_token_size`` —
    ``AGENTKTHX_NUM_CTX=128k`` and ``AGENTKTHX_NUM_CTX=131072`` both work.
    """
    val = os.environ.get(name)
    if val:
        try:
            return _parse_token_size(val)
        except (ValueError, TypeError):
            pass
    return None


def _parse_token_size(s) -> int:
    """Parse a token size string with optional k/m/g suffix into an int.

    R07.18: accepts human-friendly forms for ``--num-ctx`` / ``--num-predict``:
      - ``131072``         → 131072
      - ``128k`` / ``128K`` → 131072  (1024 * 128)
      - ``1m`` / ``1M``     → 1048576 (1024 * 1024)
      - ``2.5k``           → 2560    (1024 * 2.5, fractional k allowed)
      - ``1.5m``           → 1572864 (1024*1024 * 1.5)
      - ``0``              → 0
      - ``-1``             → -1 (sentinel: "unlimited" for some backends)

    Used as ``type=_parse_token_size`` on the argparse ``--num-ctx`` /
    ``--num-predict`` flags so users can write ``--num-ctx 128k`` instead of
    ``--num-ctx 131072``. The agent still receives a plain int — no backend
    changes needed.

    Args:
        s: string or int (ints pass through unchanged)

    Returns:
        int token count

    Raises:
        argparse.ArgumentTypeError: on malformed input (raised as ValueError
        by argparse, which formats it with the flag name).
    """
    if s is None:
        raise ValueError("token size cannot be None")
    if isinstance(s, int):
        return s
    if isinstance(s, float):
        return int(s)
    s = str(s).strip()
    if not s:
        raise ValueError("token size cannot be empty")
    # Allow leading sign for sentinels like -1 (llama-server: "unlimited")
    sign = 1
    if s[0] in "+-":
        if s[0] == "-":
            sign = -1
        s = s[1:]
        if not s:
            raise ValueError("token size: sign with no digits")
    # No suffix → plain int
    if s.isdigit():
        return sign * int(s)
    # Suffix form: <number>[kKmMgG]
    last = s[-1]
    if last.lower() in "kmg":
        num_part = s[:-1]
        try:
            num = float(num_part)
        except ValueError:
            raise ValueError(
                f"token size: bad numeric part {num_part!r} in {s!r} "
                f"(expected forms like '128k', '2.5m', '1g')"
            )
        # ROB-36 (R07.21 CLOSED): reject inf/nan before the int() cast.
        # ``float("inf")`` and ``float("1e400")`` succeed without raising,
        # but ``int(inf * 1024)`` raises ``OverflowError`` — which argparse
        # does NOT convert to a clean usage error (it only catches
        # ValueError/TypeError), so the user saw a raw traceback. The
        # ``math.isfinite`` guard turns both into a clean ``ValueError``
        # that argparse formats with the flag name. Same treatment for
        # ``nan`` (``float("nan")`` succeeds, ``int(nan)`` raises
        # ``ValueError``, but the error message is opaque — fail with a
        # clear diagnostic instead).
        if not math.isfinite(num):
            raise ValueError(
                f"token size: numeric part {num_part!r} in {s!r} is not finite "
                f"(inf/nan are not valid token sizes)"
            )
        mult = {"k": 1024, "m": 1024**2, "g": 1024**3}[last.lower()]
        return sign * int(num * mult)
    raise ValueError(
        f"token size: unrecognized suffix {last!r} in {s!r} "
        f"(valid suffixes: k, m, g — e.g. '128k', '1m', '2g')"
    )


def _env_float(name: str) -> Optional[float]:
    """Read a float from an environment variable."""
    val = os.environ.get(name)
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return None


__all__ = [
    "SharedConfig",
    "add_shared_args",
    "add_agent_args",
    "parse_shared_args",
    "_parse_token_size",
]
