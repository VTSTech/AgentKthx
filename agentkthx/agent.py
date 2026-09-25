"""
⚛️ AgentKthx — Agent
Main agent class implementing the OpenResponses Agentic Loop specification.

OpenResponses Compliance (https://www.openresponses.org/specification):
- Items: Atomic units of context (message, function_call, function_call_output)
- State Machines: Items and Response have lifecycle states
- tool_choice: Control tool invocation (auto, required, none, specific, allowed_tools)
- allowed_tools: Restrict which tools can be invoked
- Agentic Loop: Model samples → tool call → execute → observation → repeat

Tool Calling Strategy:
- Uses ReAct prompting (Action/Action Input format) for all models
- No distinction between "native" and "react" modes
- Model must explicitly format tool calls, no fallbacks/synthesis
- Tool execution is developer-hosted (outside the model provider)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, Generator, Optional

from .core.models import AgentRun, StepResult, Tool, ToolParam, ToolCall
from .core.types import StepResultType, ApiMode, BackendType
from .core.memory import Memory, MemoryConfig
from .core.tool_parse import ToolParser
from .core.error_recovery import (
    ErrorRecoveryTracker,
    build_enhanced_observation,
    build_retry_context,
    is_error_result,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_TOTAL_FAILURES,
    DEFAULT_MAX_TOOL_RETRIES,
    DEFAULT_RETRY_ON_ERROR,
)
from .core.api_resilience import (
    is_transient_api_error,
    backoff_delay,
    max_api_retries_from_env,
    describe_wait,
    describe_terminal,
)
from .core.openresponses import (
    Response, ResponseStatus, ItemStatus,
    ToolChoice, ToolChoiceType,
    MessageItem, FunctionCallItem, FunctionCallOutputItem, ReasoningItem,
    OutputText, InputText,
    RequestConfig, Error,
    EventType, ResponseEvent, OutputItemEvent,
    create_message_item, create_function_call_item, create_function_call_output,
    create_function_call_output_item,
    stream_response_events,
)
from .tools import ToolRegistry, make_builtin_registry
from .backends import BaseBackend, get_default_backend
from .config import get_config


class Agent:
    """
    AgentKthx Agent - OpenResponses Agentic Loop Implementation.
    
    This class implements the core agentic loop as defined by OpenResponses:
    
        1. Model samples from input
        2. If tool call: execute tool, return observation, continue
        3. If no tool call: return final output items
    
    OpenResponses Features:
        - tool_choice: Control tool invocation behavior
          - "auto" (default): Model may call tools or respond directly
          - "required": Model MUST call at least one tool
          - "none": Model MUST NOT call any tools
          - {"type": "function", "name": "tool"}: Force specific tool
          - {"type": "allowed_tools", "tools": [...]}: Restrict to tool list
        - allowed_tools: Hard constraint on which tools can be invoked
        - Response state machine: queued → in_progress → completed/failed/incomplete
        - Items: Atomic units of context with lifecycle states
    
    Tool Calling:
        All models use ReAct prompting (Action/Action Input format).
        The model must explicitly format tool calls - no fallback synthesis.
        
        Format:
            Action: tool_name
            Action Input: {"arg": "value"}
    
    Example:
        # Basic usage
        agent = Agent(model="qwen2.5:0.5b", tools=["calculator"])
        result = agent.run("What is 15 * 8?")
        print(result.final_answer)
        
        # Force tool usage
        agent = Agent(model="llama3", tools=["calculator"], tool_choice="required")
        
        # Restrict tools
        agent = Agent(
            model="llama3", 
            tools=["calculator", "shell"],
            allowed_tools=["calculator"]  # shell is blocked
        )
        
        # Force specific tool
        agent = Agent(model="llama3", tools=["calculator"], tool_choice=ToolChoice.specific("calculator"))
    """

    def __init__(
        self,
        model: str,
        tools: ToolRegistry | list[str] | list[Tool] | None = None,
        backend: BaseBackend | str | None = None,
        max_steps: int = 25,
        memory_config: MemoryConfig | None = None,
        debug: bool = False,
        system_prompt: str | None = None,
        soul: str = "nova-helper",
        soul_level: int = 3,
        num_ctx: int | None = None,
        # Generation parameters
        temperature: float | None = None,
        top_p: float | None = None,
        num_predict: int | None = None,
        # OpenResponses parameters
        tool_choice: str | ToolChoice = "auto",  # Default per OpenResponses spec
        allowed_tools: list[str] | None = None,
        # Skills injection
        skills_prompt: str | None = None,
        # Retry-with-error-feedback
        retry_on_error: bool = DEFAULT_RETRY_ON_ERROR,
        max_tool_retries: int = DEFAULT_MAX_TOOL_RETRIES,
        # R06.54: transient API error tolerance
        max_api_retries: int | None = None,
        # Truncation behavior
        truncation: str = "auto",
        # Thinking controls (R05.8)
        thinking_level: str | None = "auto",
        think: bool | None = None,
        reasoning_effort: str | None = None,
        show_reasoning: bool = False,
        **kwargs,
    ):
        """
        Initialize an Agent.

        Args:
            model: Model name (e.g., "qwen2.5:0.5b")
            tools: ToolRegistry, list of tool names, or list of Tool objects
            backend: Backend instance or name ("ollama", "bitnet")
            max_steps: Maximum reasoning steps
            memory_config: Memory configuration
            debug: Enable debug output
            system_prompt: Custom system prompt (overrides soul)
            soul: Path to Soul Spec package (default: "nova-helper")
            soul_level: Progressive disclosure level for soul (1-3)
            num_ctx: Context window size in tokens (default: 8192)
            temperature: Sampling temperature (default: model-specific)
            top_p: Nucleus sampling probability (default: model-specific)
            num_predict: Maximum tokens to generate (default: model-specific)
            tool_choice: Control tool invocation ("auto", "required", "none", or specific tool name)
            allowed_tools: List of tools the model is allowed to invoke (subset of tools)
            skills_prompt: Optional skill instructions to append to the system prompt
            retry_on_error: Whether to retry failed tool calls with error feedback (default: True)
            max_tool_retries: Maximum retries per tool call failure (default: 2)
            max_api_retries: Consecutive transient API failures tolerated per step
                before the run terminates (default: AGENTKTHX_MAX_API_RETRIES env or 5).
                R06.54: rate limits / empty responses / connection blips retry with
                exponential back-off instead of killing the run.
            **kwargs: Additional configuration (persistent, session_id, memory_db, confirm_dangerous, response_format)
        """
        # Ensure max_steps is never None (defensive fix)
        if max_steps is None:
            max_steps = 25  # Default value
        
        self.model = model
        self.max_steps = max_steps
        self.debug = debug

        # R06.54: transient API error tolerance (rate limits, empty
        # responses, connection blips). 0 disables retrying entirely.
        self.max_api_retries = (
            max_api_retries if max_api_retries is not None
            else max_api_retries_from_env()
        )

        # Generate a unique session ID for this agent instance.
        # Used for per-session todo isolation and logging.
        import uuid as _uuid
        self.session_id = _uuid.uuid4().hex[:12]
        # Get num_ctx from: explicit param > config/env > default 8192
        if num_ctx is not None:
            self.num_ctx = num_ctx
        else:
            config = get_config()
            self.num_ctx = config.num_ctx if config.num_ctx else 8192

        # Generation parameters (use model defaults if not specified)
        self._temperature = temperature
        self._top_p = top_p
        self._num_predict = num_predict

        # ── Thinking controls (R05.8) ────────────────────────────────────
        # --thinking off|auto|low|medium|high → (think, reasoning_effort)
        # When the user passes an explicit `think=` or `reasoning_effort=`,
        # those override what `thinking_level` would have resolved to.
        # This lets programmatic callers bypass the CLI parsing layer.
        from .core.types import parse_thinking_arg
        resolved_think, resolved_effort = parse_thinking_arg(thinking_level)
        self._thinking_level = thinking_level or "auto"
        # Explicit kwargs override the parsed level
        self._think = think if think is not None else resolved_think
        self._reasoning_effort = reasoning_effort if reasoning_effort is not None else resolved_effort
        # --think flag: display reasoning_content in CLI when the model emits it
        self._show_reasoning = bool(show_reasoning)
        if self.debug:
            print(
                f"[Agent] Thinking: level={self._thinking_level} → "
                f"think={self._think}, reasoning_effort={self._reasoning_effort}, "
                f"show_reasoning={self._show_reasoning}"
            )
        # ──────────────────────────────────────────────────────────────────

        # Retry-with-error-feedback
        self._retry_on_error = retry_on_error
        self._max_tool_retries = max_tool_retries

        # Structured output / JSON mode.
        # When set, the backend will be instructed to return JSON.
        # Accepts a dict (e.g. {"type": "json_object"}) or the
        # convenience string "json" which is expanded automatically.
        raw_rf = kwargs.pop("response_format", None)
        if raw_rf is not None:
            if isinstance(raw_rf, str):
                self._response_format = {"type": "json_object"}
            elif isinstance(raw_rf, dict):
                self._response_format = raw_rf
            else:
                self._response_format = None
        else:
            self._response_format = None

        # Dangerous tool confirmation callback.
        # When set, any tool with dangerous=True must be approved by
        # this callback before execution. The callback receives
        # (tool_name, args) and returns True (allow) or False (deny).
        # If not set, dangerous tools execute without confirmation.
        self._confirm_dangerous = kwargs.pop("confirm_dangerous", None)
        
        # Truncation behavior for context overflow
        self.truncation = truncation
        if self.debug:
            print(f"[Agent] Truncation mode: {self.truncation}")

        # ROB-06: Memory compaction threshold.
        # When estimated token usage exceeds this fraction of num_ctx,
        # older messages are compacted (content truncated, tool results
        # shortened) instead of dropped. Parsed from --compaction CLI arg.
        # "auto" = 0.85 (85%), "off"/"0" = disabled, or a float like 0.90.
        self._compaction_threshold = 0.85  # default: auto = 85%
        if self.debug:
            print(f"[Agent] Compaction threshold: {int(self._compaction_threshold * 100)}%")

        # Running token totals — updated during the agentic loop so the
        # CLI footer can show real-time token usage and context %.
        # Reset at the start of each run() call.
        self._running_tokens_in = 0
        self._running_tokens_out = 0
        # Optional callback invoked after each step completes, used by
        # the CLI to refresh the persistent footer during streaming.
        # Signature: callback(step_num, tokens_in, tokens_out)
        self._on_step_callback = None

        # Initialize backend
        if backend is None:
            self.backend = get_default_backend()
        elif isinstance(backend, str):
            self.backend = get_default_backend(backend)
        else:
            self.backend = backend

        # Initialize tools
        if tools is None:
            self.tools = ToolRegistry()
        elif isinstance(tools, ToolRegistry):
            self.tools = tools
        elif isinstance(tools, list):
            if all(isinstance(t, str) for t in tools):
                # List of tool names
                self.tools = make_builtin_registry().subset(tools)
            elif all(isinstance(t, Tool) for t in tools):
                # List of Tool objects
                self.tools = ToolRegistry(tools)
            else:
                raise ValueError("tools must be a list of strings or Tool objects")
        else:
            raise ValueError("tools must be ToolRegistry, list[str], or list[Tool]")

        # v0.2 plugin tools: merge plugin-registered tools into the registry
        # (spec §Tools). Best-effort; never blocks Agent construction.
        try:
            from .plugins import get_plugin_manager as _get_pm
            _pm = _get_pm(init=False)
            if _pm is not None and _pm.plugin_tools():
                _pm.apply_to_registry(self.tools)
        except Exception:
            pass

        # Wire up per-session todo isolation for this agent instance.
        # Each Agent gets its own todo store keyed by session_id.
        if "todo" in self.tools.names():
            from .tools.builtins import set_todo_session
            set_todo_session(self.session_id)

        # OpenResponses: tool_choice
        if isinstance(tool_choice, ToolChoice):
            self.tool_choice = tool_choice
        else:
            self.tool_choice = ToolChoice(tool_choice)

        # Structured output and tool calling are mutually exclusive:
        # JSON mode forces the model to format ALL output as JSON objects,
        # which breaks the ReAct tool-calling format (the parser misinterprets
        # the JSON response as a tool call). When response_format is set,
        # disable tools and force tool_choice to "none".
        if self._response_format is not None:
            self.tool_choice = ToolChoice("none")
            self.tools = ToolRegistry()
            if debug:
                print(f"[Agent] JSON mode enabled — tools disabled (response_format and tool calling are mutually exclusive)")

        if debug and not self._is_comp_mode:
            print(f"[OpenResponses] tool_choice initialized: type={self.tool_choice.type.value}, name={self.tool_choice.name or 'N/A'}, tools={self.tool_choice.tools or 'N/A'}")

        # OpenResponses: allowed_tools
        # Combine explicit allowed_tools with tool_choice.allowed_tools if present
        effective_allowed = set(allowed_tools) if allowed_tools else None
        
        # If tool_choice is ALLOWED_TOOLS mode, merge with allowed_tools
        if self.tool_choice.type == ToolChoiceType.ALLOWED_TOOLS and self.tool_choice.tools:
            if effective_allowed is None:
                effective_allowed = set(self.tool_choice.tools)
            else:
                effective_allowed = effective_allowed.intersection(set(self.tool_choice.tools))
        
        # If tool_choice is SPECIFIC mode, only that tool is allowed
        if self.tool_choice.type == ToolChoiceType.SPECIFIC and self.tool_choice.name:
            effective_allowed = {self.tool_choice.name}
        
        self._allowed_tools = list(effective_allowed) if effective_allowed else None
        
        # Filter the tools registry to only include allowed tools
        if self._allowed_tools is not None and len(self._allowed_tools) > 0:
            allowed_set = set(self._allowed_tools)
            current_tools = set(self.tools.names())
            filtered = current_tools.intersection(allowed_set)
            if debug and not self._is_comp_mode:
                print(f"[OpenResponses] allowed_tools filter: {current_tools} ∩ {allowed_set} = {filtered}")
            if filtered != current_tools:
                if debug and not self._is_comp_mode:
                    print(f"[OpenResponses] Tools filtered: {current_tools} -> {filtered}")
                self.tools = self.tools.subset(list(filtered))
            else:
                if debug and not self._is_comp_mode:
                    print(f"[OpenResponses] No tools filtered out")

        # Detect BitNet backend early for memory tuning and prompt formatting.
        # BitNet's degraded tokenizer falls back to 'default' pre-tokenizer, which
        # causes reserved token IDs when it encounters markdown tables, code
        # fences, or certain character sequences — crashing the i2_s kernel.
        # IMPORTANT: We check the MODEL family, not just the backend type.
        # A non-BitNet model (e.g. qwen2.5) running on the BitNet backend has
        # a proper tokenizer and must NOT receive BitNet-specific constraints
        # (tight memory, lean prompt, budgeting).
        _backend_is_bitnet = (
            hasattr(self.backend, 'backend_type')
            and self.backend.backend_type == BackendType.BITNET
        )
        if _backend_is_bitnet:
            from .core.model_family_config import detect_family
            _model_family = detect_family(model)
            self._is_bitnet = (_model_family == "bitnet")
        else:
            self._is_bitnet = False

        # Initialize memory
        # BitNet: tighten memory to reduce context confusion.
        # BitNet's degraded tokenizer and tiny context window mean the model
        # degrades quickly as conversation history grows. Limit to 6 recent
        # messages (3 turns) to keep the prompt focused.
        if self._is_bitnet and memory_config is None:
            memory_config = MemoryConfig(max_messages=6, keep_recent=4)

        # Persistent memory: use PersistentMemory if session_id or persistent=True
        _persistent = kwargs.pop("persistent", False)
        _session_id = kwargs.pop("session_id", None)
        _memory_db = kwargs.pop("memory_db", None)

        if _persistent or _session_id:
            from .core.persistent_memory import PersistentMemory
            self.memory = PersistentMemory(
                session_id=_session_id,
                db_path=_memory_db,
                config=memory_config or MemoryConfig(),
            )
            self._is_persistent = True
            if _session_id:
                loaded = self.memory.load()
                if self.debug and loaded > 0:
                    print(f"[Memory] Restored {loaded} messages from session '{_session_id}'")
        else:
            self.memory = Memory(memory_config or MemoryConfig())
            self._is_persistent = False

        # Get model configuration (for temperature, max_tokens defaults)
        from .core.model_family_config import get_model_config
        self.model_config = get_model_config(model)

        # Detect model family (for backend-specific settings like think=False)
        from .core.model_family_config import detect_family
        self.model_family = detect_family(model)

        # Load Soul Spec package (default: nova-helper)
        self.soul = None
        self._soul_level = soul_level

        # Determine if tools are available
        has_tools = self.tools and len(self.tools) > 0 and self.tool_choice.type != ToolChoiceType.NONE
        
        if system_prompt is not None:
            # Custom system prompt provided
            self._custom_system_prompt = system_prompt
        elif soul is not None:
            # Load soul and build system prompt with dynamic tools
            try:
                from .soul import load_soul, build_system_prompt_with_tools
                self.soul = load_soul(soul, level=soul_level)
                
                # Filter tools based on soul.allowed_tools (additional filtering)
                if self.soul.allowed_tools and len(self.soul.allowed_tools) > 0:
                    allowed = set(self.soul.allowed_tools)
                    current_tools = set(self.tools.names())
                    filtered = current_tools.intersection(allowed)
                    if filtered != current_tools:
                        if debug:
                            print(f"[Soul] Filtering tools: {current_tools} -> {filtered}")
                        self.tools = self.tools.subset(list(filtered))
                        has_tools = len(self.tools) > 0
                
                # Build system prompt with dynamic tool injection
                if has_tools:
                    self._custom_system_prompt = build_system_prompt_with_tools(
                        self.soul,
                        self.tools.all(),
                        level=soul_level,
                        tool_choice=self.tool_choice,  # OpenResponses: communicate constraints
                        native_tools=self._is_comp_mode,
                    )
                else:
                    from .soul import build_system_prompt
                    self._custom_system_prompt = build_system_prompt(self.soul, level=soul_level)
                
                if debug:
                    print(f"[Soul] Loaded: {self.soul.display_name} v{self.soul.version}")
            except ImportError:
                if debug:
                    print("[Soul] Soul module not available, using default prompt")
                self._custom_system_prompt = self._build_default_prompt(has_tools)
            except FileNotFoundError as e:
                if debug:
                    print(f"[Soul] Soul package not found: {e}")
                self._custom_system_prompt = self._build_default_prompt(has_tools)
            except Exception as e:
                if debug:
                    print(f"[Soul] Error loading soul: {e}")
                self._custom_system_prompt = self._build_default_prompt(has_tools)
        else:
            self._custom_system_prompt = self._build_default_prompt(has_tools)
            if has_tools:
                # Add tool section to default prompt
                from .soul.loader import _build_tool_section
                tool_section = _build_tool_section(self.tools.all(), native_tools=self._is_comp_mode)
                self._custom_system_prompt = f"{self._custom_system_prompt}\n\n{tool_section}"

        # Append skills prompt if provided
        if skills_prompt:
            self._custom_system_prompt = f"{self._custom_system_prompt}\n{skills_prompt}"
            if debug:
                print(f"[Skills] Appended skills prompt to system prompt ({len(skills_prompt)} chars)")

        # Initialize tool parser
        self._parser = ToolParser(self.tools.names())

        # Add system prompt to memory
        self.memory.add("system", self._custom_system_prompt)

        # Store kwargs
        self._kwargs = kwargs

        # Response history for previous_response_id support
        self._response_history: dict[str, Response] = {}
        
        # Error recovery state tracking
        self._error_tracker = ErrorRecoveryTracker(
            max_consecutive_failures=DEFAULT_MAX_CONSECUTIVE_FAILURES,
            max_total_failures=DEFAULT_MAX_TOTAL_FAILURES,
        )
        
        if self.debug:
            print(f"[Agent] Retry on error: {self._retry_on_error}, max_tool_retries: {self._max_tool_retries}")

    @property
    def _is_comp_mode(self) -> bool:
        """Check if backend is using Chat-Completions (comp) API mode."""
        return hasattr(self.backend, 'api_mode') and self.backend.api_mode == ApiMode.OPENAI

    def _log_openresponses(self, msg: str) -> None:
        """Log OpenResponses debug message only when not in comp mode."""
        if self.debug and not self._is_comp_mode:
            print(msg)

    def _build_default_prompt(self, has_tools: bool) -> str:
        """Build a default system prompt when soul is not available.

        When self._is_bitnet is True, returns a lean prompt that avoids markdown
        tables, code fences, and bold markers — these produce reserved token IDs
        in BitNet's degraded tokenizer, crashing the inference engine.

        When self._is_comp_mode is True (OpenAI Chat-Completions), returns a prompt
        without ReAct format instructions — tools are passed via the API body and
        the model uses native function calling.
        """
        if not has_tools:
            return "You are AI AgentKthx. Answer questions directly and accurately."

        if self._is_bitnet:
            # Ultra-lean ReAct prompt for BitNet's degraded tokenizer.
            # BitNet's tokenizer fallback produces reserved token IDs at certain
            # token positions, crashing the i2_s kernel at ~320 tokens.
            # Keep this under 500 chars to stay safely below the crash threshold.
            return (
                "You are AI AgentKthx with tools.\n"
                "Use the ReAct format shown in the tool section below.\n"
                "After tool result, give Final Answer: <answer>"
            )

        if self._is_comp_mode:
            # OpenAI Chat-Completions mode — tools are in the API body.
            # No ReAct format instructions needed; model uses native function calling.
            return """You are AI AgentKthx with access to tools.

Use the available tools when needed. The tools are provided via the API — call them naturally as function calls.

**CRITICAL RULES:**
1. Only use tools from the available tools list
2. Always use tools for calculations and external operations
3. Never make up information"""

        return """You are AI AgentKthx with access to tools.

When you need to use a tool, follow this EXACT format:

```
Thought: <brief reasoning>
Action: <tool_name>
Action Input: <JSON arguments>
```

After receiving a tool result, provide the Final Answer:

```
Thought: I have the result
Final Answer: <the answer>
```

**CRITICAL RULES:**
1. Only use tools from the available tools list
2. Action Input must be valid JSON
3. Always use tools for calculations and external operations
4. Never make up information"""

    def run(self, prompt: str, stream: bool = False) -> AgentRun:
        """
        Run the agent on a prompt (v0.2: emits plugin lifecycle hooks).

        Emits ``on_run_start`` before the agentic loop, ``on_run_end`` after
        a successful run, and ``on_error`` if the run raises. Hook failures
        never affect the run itself (spec §Hooks).
        """
        pm = None
        try:
            from .plugins import get_plugin_manager as _get_pm
            pm = _get_pm(init=False)
        except Exception:
            pm = None

        session = getattr(self, "session_id", None)
        _backend = getattr(self, "backend", None)
        backend_name = (
            getattr(_backend, "backend_name", None)
            or getattr(_backend, "name", None)
            or (type(_backend).__name__ if _backend is not None else None)
        )

        if pm is not None:
            try:
                pm.emit("on_run_start", {
                    "prompt": prompt,
                    "session": session,
                    "backend": backend_name,
                    "model": getattr(self, "model", None),
                })
            except Exception:
                pass

        try:
            result = self._run_core(prompt, stream)
        except Exception as e:
            if pm is not None:
                try:
                    pm.emit("on_error", {
                        "prompt": prompt,
                        "session": session,
                        "error": str(e),
                        "exception": e,
                    })
                except Exception:
                    pass
            raise

        if pm is not None:
            try:
                pm.emit("on_run_end", {
                    "prompt": prompt,
                    "session": session,
                    "usage": {"total_tokens": getattr(result, "total_tokens", 0)},
                    "duration_ms": getattr(result, "total_ms", 0),
                })
            except Exception:
                pass

        return result

    # ── MAINT-04 Phase 1: shared API-resilience retry loop ──────────────
    # Both _run_core and _run_core_streaming had nearly identical retry
    # loops (~67 lines each, ~38 lines of overlap) with the ONLY structural
    # difference being the streaming path's context-length-compaction
    # handler (ROB-06 / R06.58). Extracting this loop into a single helper
    # eliminates ~80 lines of duplication and — critically — makes the
    # retry/backoff/terminal-error logic live in ONE place so future bug
    # fixes (a new error pattern, a new retry policy) apply to both paths
    # automatically.
    #
    # Phase 2 of MAINT-04 will merge the full agentic loop. Phase 1 is
    # intentionally surgical: same control flow, same side effects, same
    # return shape — just moved.

    def _generate_with_retry(
        self,
        generate_fn: "Callable[[], dict]",
        *,
        step_num: int,
        steps: list,
        response: "Response",
        enable_compaction_recovery: bool = False,
    ) -> tuple["Optional[dict]", bool]:
        """Call ``generate_fn()`` with API-resilience retry.

        Shared between ``_run_core`` (non-streaming) and
        ``_run_core_streaming`` (streaming). The only behavioral difference
        between the two paths is the context-length-400 compaction handler,
        gated by ``enable_compaction_recovery`` — streaming enables it
        because that's the path that runs long enough to hit input-too-large
        conditions during multi-tool agentic runs.

        Returns ``(gen_response, terminated)``. The caller must break its
        outer step loop when ``terminated`` is True.
        """
        gen_response = None
        _api_failure = 0
        _api_wait_total = 0.0
        _terminated = False
        while True:
            try:
                gen_response = generate_fn()
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # ROB-06 / R06.58: Context-length 400 where input alone
                # exceeds the context window. The _iter_sse_lines retry
                # already reduced max_tokens, but if the INPUT is larger
                # than the context, no max_tokens reduction can help.
                # Compact memory (truncate old tool results) and retry.
                # Only enabled on the streaming path — the non-streaming
                # path doesn't run long enough agentic loops to need it,
                # and enabling it there would be a behavioral change.
                if enable_compaction_recovery:
                    err_str = str(e)
                    if "context length" in err_str.lower():
                        compacted = self.memory.compact_messages(keep_count=10)
                        # Always re-snapshot after a compaction attempt so
                        # the footer reflects the post-compaction state.
                        self._snapshot_running_tokens()
                        if compacted > 0:
                            post_tokens = (self._running_tokens_in
                                           + self._running_tokens_out)
                            print(f"  [Context] Input exceeded context "
                                  f"window — compacted {compacted} messages "
                                  f"(~{post_tokens // 1000}K tokens remaining)")
                            _api_failure = 0  # reset retry counter — new state
                            continue  # retry with compacted memory
                        # If compaction freed nothing, the input is already
                        # minimal — fall through to the transient-error
                        # path so we don't infinite-loop on the same 400.

                _api_failure += 1
                _transient = is_transient_api_error(e)
                _exhausted = _transient and _api_failure > self.max_api_retries
                if not _transient or _exhausted:
                    # R06.54: always tell the user WHY the run stopped —
                    # the old code stayed silent here (non-debug), so chat
                    # mode showed a bare "(empty response)".
                    if _exhausted:
                        print(describe_terminal(
                            e, self.max_api_retries, _api_wait_total))
                    elif not _transient:
                        print(f"  [Resilience] Fatal API error — "
                              f"not retrying: {e}")
                    if self.debug:
                        print(f"  ERROR: {e}")
                    steps.append(StepResult(
                        type=StepResultType.ERROR,
                        error=str(e),
                    ))
                    response.mark_failed({"message": str(e), "type": "model_error"})
                    _terminated = True
                    break
                _waited = backoff_delay(_api_failure)
                _api_wait_total += _waited
                print(describe_wait(_api_failure, self.max_api_retries, _waited, e))
                time.sleep(_waited)
        return gen_response, _terminated

    # ── MAINT-04 Phase 2: shared finish_reason handler ──────────────────
    # Both _run_core and _run_core_streaming had near-identical finish_reason
    # blocks (~25 lines each) handling the "length" and "content_filter"
    # cases. Extracting into a single helper eliminates ~25 lines of
    # duplication and ensures both paths produce the same StepResult
    # entries + response status transitions for the same finish_reason.
    #
    # Returns True if the run should break (terminal finish_reason),
    # False if the run should continue (normal "stop" or unknown reason).

    def _handle_finish_reason(
        self,
        gen_response: dict,
        steps: list,
        response: "Response",
    ) -> bool:
        """Handle ``finish_reason`` from the backend response.

        Shared between ``_run_core`` and ``_run_core_streaming``.

        Handles two terminal finish reasons:
        - ``"length"``: token budget exhausted → mark response incomplete,
          append a MAX_STEPS step, return True (break the loop).
        - ``"content_filter"``: provider blocked the response → mark
          response failed, append an ERROR step, return True.

        For any other finish reason (including ``"stop"``), returns False
        so the caller continues processing tool calls / final answer.

        Returns
        -------
        True if the caller should break its step loop (terminal reason);
        False if the caller should continue.
        """
        tokens = gen_response.get("usage", {}).get("total_tokens", 0)
        finish_reason = gen_response.get("_finish_reason", "stop")
        if finish_reason == "length":
            # Token budget exhausted — response is incomplete
            if self.debug:
                print(f"  [OpenResponses] finish_reason='length' — marking incomplete")
            steps.append(StepResult(
                type=StepResultType.MAX_STEPS,
                content="Response truncated: token limit reached",
                tokens_used=tokens,
            ))
            response.mark_incomplete()
            return True
        elif finish_reason == "content_filter":
            # Content was filtered — response failed
            if self.debug:
                print(f"  [OpenResponses] finish_reason='content_filter' — marking failed")
            steps.append(StepResult(
                type=StepResultType.ERROR,
                error="Response blocked by content filter",
                tokens_used=tokens,
            ))
            response.mark_failed({"message": "Content filtered by provider", "type": "content_filter"})
            return True
        return False

    # ── MAINT-04 Phase 3a: shared tool_choice enforcement check ─────────
    # Both _run_core and _run_core_streaming had this 6-line block
    # duplicated 4 times total (twice each — once for the Final Answer
    # case, once for the no-tool-no-final-answer case). Extracting it
    # eliminates ~24 lines of duplication and ensures both paths enforce
    # tool_choice identically.

    def _check_tool_choice_required(self, tool_calls: int) -> tuple[bool, str]:
        """Check whether ``tool_choice`` requires a tool call that didn't happen.

        Shared between ``_run_core`` and ``_run_core_streaming``. Called in
        two places per method: (1) when the model emits a Final Answer
        without having called any tools, (2) when the model responds with
        neither a tool call nor a Final Answer.

        Returns ``(needs_tool, rejection_reason)``. When ``needs_tool`` is
        True, the caller must NOT accept the response — it should inject a
        user message telling the model to use a tool, then ``continue`` the
        agentic loop.
        """
        if self.tool_choice.type == ToolChoiceType.REQUIRED and tool_calls == 0:
            return True, "tool_choice='required' but no tool was called"
        if self.tool_choice.type == ToolChoiceType.SPECIFIC and tool_calls == 0:
            return (True,
                    f"tool_choice requires '{self.tool_choice.name}' "
                    f"but no tool was called")
        return False, ""

    # ── MAINT-04 Phase 3b: shared tool-call parser ──────────────────────
    # Both _run_core and _run_core_streaming had a ~20-line block that
    # parsed tool calls from the model response into the unified
    # ``tool_calls_found`` list — handling both native (OpenAI-format)
    # tool calls and ReAct/JSON/XML parsed calls. Extracting this block
    # eliminates ~40 lines of duplication (20 per method) and ensures
    # both paths produce identical tool_calls_found shape.

    def _parse_tool_calls(
        self,
        content: str,
        native_tool_calls: list,
        response: "Response",
    ) -> list[dict]:
        """Parse tool calls from the model response into a unified list.

        Shared between ``_run_core`` and ``_run_core_streaming``.

        Handles two sources of tool calls:
        1. **Native** (OpenAI-format): ``native_tool_calls`` is a list of
           ``{"name", "arguments", "id"}`` dicts from the backend. These
           are normalized to the unified shape directly.
        2. **ReAct/JSON/XML** (parsed from ``content``): when the backend
           doesn't return native tool calls, the content is parsed by
           ``self._parser``. Parsed calls may include a ``thought`` field
           which is captured as a ``ReasoningItem`` on the response.

        Returns a list of dicts in the unified shape:
        ``{"name", "arguments", "id", "final_answer"}``. The
        ``final_answer`` key is only present for ReAct calls that include
        one (may be None).
        """
        tool_calls_found: list[dict] = []

        # Check for native tool calls from backend
        if native_tool_calls:
            for tc in native_tool_calls:
                tool_calls_found.append({
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", {}),
                    "id": tc.get("id", ""),
                })
            return tool_calls_found

        # Check for tool calls in model output (ReAct, JSON, or XML format)
        if not content:
            return tool_calls_found

        parsed_calls = self._parser.parse(content)
        if self.debug and parsed_calls and not self._is_comp_mode:
            print(f"  [OpenResponses] Tool calls detected: {len(parsed_calls)}")

        for call in parsed_calls:
            if self.debug and not self._is_comp_mode:
                print(f"  [OpenResponses] Parsed: name={call.name}, "
                      f"args={call.arguments}, "
                      f"final_answer={call.final_answer}")

            # OpenResponses: Capture ReasoningItem if thought is present
            if hasattr(call, 'thought') and call.thought:
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] Captured thought for "
                          f"ReasoningItem: {call.thought[:50]}...")
                reasoning_item = ReasoningItem(
                    content=[OutputText(text=call.thought)]
                )
                reasoning_item.status = ItemStatus.COMPLETED
                response.add_output_item(
                    reasoning_item,
                    debug=not self._is_comp_mode and self.debug,
                )

            tool_calls_found.append({
                "name": call.name,
                "arguments": call.arguments,
                "id": "",
                "final_answer": call.final_answer,  # May be None
            })

        return tool_calls_found

    # ── MAINT-04 Phase 3c: shared run finalization ──────────────────────
    # Both _run_core and _run_core_streaming had this ~7-line block
    # duplicated at every successful exit point:
    #     self._response_history[response.id] = response
    #     response.usage["total_tokens"] = total_tokens
    #     total_ms = (time.time() - start_time) * 1000
    #     return AgentRun(final_answer=..., steps=steps, ...)
    # Extracting it eliminates ~70 lines of duplication (7 lines × 10
    # exit points) and ensures every exit path stores the response + sets
    # total_tokens + computes total_ms identically.

    def _finalize_run(
        self,
        final_answer: str,
        steps: list,
        total_tokens: int,
        start_time: float,
        tool_calls: int,
        response: "Response",
        success: bool = True,
        mark_completed: bool = True,
    ) -> AgentRun:
        """Build the final ``AgentRun`` and store the response for
        ``previous_response_id`` support.

        Shared between ``_run_core`` and ``_run_core_streaming``. Called
        at every exit point where the run produced a final answer (or
        terminated with an empty answer).

        Side effects:
        - Stores ``response`` in ``self._response_history`` so callers can
          chain via ``previous_response_id``.
        - Sets ``response.usage["total_tokens"]``.
        - Optionally marks the response as COMPLETED (when
          ``mark_completed=True`` and status is IN_PROGRESS).

        Returns a fully-populated ``AgentRun``.
        """
        if mark_completed and response.status == ResponseStatus.IN_PROGRESS:
            response.mark_completed()
        self._response_history[response.id] = response
        response.usage["total_tokens"] = total_tokens
        total_ms = (time.time() - start_time) * 1000
        return AgentRun(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            total_ms=total_ms,
            tool_calls=tool_calls,
            success=success,
        )

    @staticmethod
    def _extract_last_final_answer(steps: list) -> str:
        """Walk ``steps`` in reverse and return the content of the last
        ``FINAL_ANSWER`` step (or ``""`` if none). Used by both
        ``_run_core`` and ``_run_core_streaming`` at their end-of-loop
        fallthrough path.
        """
        for step in reversed(steps):
            if step.type == StepResultType.FINAL_ANSWER:
                return step.content or ""
        return ""

    # ── MAINT-04 Phase 4a: shared Final Answer enforcement ─────────────
    # The "if _expecting_final_answer and _last_successful_result is not
    # None" block was duplicated 4× (2× per method). Each instance did
    # the same thing: force final_answer = _last_successful_result,
    # create a message item, append a FINAL_ANSWER StepResult, then
    # finalize the run. Extracting eliminates ~56 lines and ensures
    # all 4 exit paths produce identical output items + step records.

    def _enforce_final_answer(
        self,
        _last_successful_result: str,
        tokens: int,
        reasoning_content: str,
        steps: list,
        total_tokens: int,
        start_time: float,
        tool_calls: int,
        response: "Response",
        debug_context: str = "",
    ) -> AgentRun:
        """Force a Final Answer from the last successful tool result.

        Shared between ``_run_core`` and ``_run_core_streaming``. Called
        when the agent was expecting a Final Answer (after a successful
        terminal-tool call) but the model either tried to call tools
        again or responded without the "Final Answer:" format. Instead
        of accepting the model's potentially-wrong answer, we use the
        last successful tool result as the final answer.

        Parameters
        ----------
        debug_context : str
            Optional context string for the debug log — e.g. "Model
            tried to call tools" vs "Model responded without Final
            Answer format". When empty, no debug line is printed
            (matches the streaming path which has no debug print here).

        Returns
        -------
        AgentRun — the caller must ``return`` this immediately.
        """
        if self.debug and not self._is_comp_mode and debug_context:
            print(f"  [OpenResponses] FINAL ANSWER ENFORCEMENT: {debug_context}")
            print(f"  [OpenResponses] Forcing Final Answer from last result: {_last_successful_result}")

        final_answer = _last_successful_result
        msg_item = create_message_item("assistant", final_answer)
        msg_item.status = ItemStatus.COMPLETED
        response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)

        steps.append(StepResult(
            type=StepResultType.FINAL_ANSWER,
            content=final_answer,
            tokens_used=tokens,
            reasoning_content=reasoning_content,
        ))

        return self._finalize_run(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            start_time=start_time,
            tool_calls=tool_calls,
            response=response,
            success=True,
        )

    # ── MAINT-04 Phase 4b: shared blocked-tool-call handler ────────────
    # The "should_block_repeat" guard + blocked-call handler was
    # duplicated 2× (1× per method). Each instance built the blocked
    # message, recorded it to memory (native vs ReAct format), recorded
    # the failure, appended a StepResult, and checked should_terminate.
    # Extracting eliminates ~46 lines and ensures both paths handle
    # repeat-blocked calls identically.

    def _handle_blocked_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        native_tool_calls: list,
        step_num: int,
        tool_calls: int,
        tokens: int,
        steps: list,
        response: "Response",
    ) -> tuple[bool, bool]:
        """Handle a repeat-blocked tool call (R06.52 identical-repeat guard).

        Shared between ``_run_core`` and ``_run_core_streaming``. Called
        BEFORE tool execution when ``_error_tracker.should_block_repeat``
        returns True — i.e., the same call already failed
        ``max_identical_failures`` times.

        Side effects:
        - Builds a "blocked" message via ``_error_tracker.format_repeat_block``
        - Records it to memory (native format via ``add_tool_result``,
          ReAct format via ``add("user", "Observation: ...")``)
        - Records the failure on ``_error_tracker`` (so consecutive counter
          increments — a stubborn model re-issuing the same call can't
          loop forever at max_steps)
        - Appends an ERROR ``StepResult`` to ``steps``
        - If ``_error_tracker.should_terminate()`` is True, marks the
          response failed

        Returns ``(was_blocked, should_terminate)``:
        - ``was_blocked`` is always True (the caller should ``continue``
          the for-loop, skipping tool execution for this call).
        - ``should_terminate`` is True if the error tracker declared the
          run stuck — the caller must ``break`` the for-loop AND set
          ``_terminated = True`` so the outer step loop stops too.
        """
        blocked_msg = self._error_tracker.format_repeat_block(tool_name, tool_args)
        if self.debug:
            print(f"  [ErrorRecovery] Blocking repeated identical call: "
                  f"{tool_name}({tool_args})")

        if native_tool_calls:
            self.memory.add_tool_result(
                tool_call_id=tool_call_id or f"blocked_{step_num}_{tool_calls}",
                name=tool_name,
                content=blocked_msg,
            )
        else:
            self.memory.add("user", f"Observation: {blocked_msg}")

        # A blocked call still counts as a failure for the consecutive
        # counter — otherwise a stubborn model re-issuing the same call
        # would only stop at max_steps.
        self._error_tracker.record_failure(
            tool_name=tool_name,
            error_message=blocked_msg,
            step=step_num,
            arguments=tool_args,
        )
        steps.append(StepResult(
            type=StepResultType.ERROR,
            error=blocked_msg,
            tool_call=ToolCall(name=tool_name, arguments=tool_args),
            tokens_used=tokens,
        ))

        if self._error_tracker.should_terminate():
            response.mark_failed({"message": "Too many tool failures", "type": "error_recovery"})
            return True, True
        return True, False

    # ── MAINT-04 Phase 4c: shared tool_choice rejection ─────────────────
    # The "needs_tool → memory.add(assistant, content) + memory.add(user,
    # 'You must use ...')" block was duplicated 4× (2× per method). Each
    # instance had slightly different user-facing message text — the
    # variation was accidental, not intentional (non-streaming said
    # "Use the Action/Action Input format", streaming didn't). The helper
    # parameterizes both dimensions so the behavior is preserved exactly
    # while the duplication is eliminated.

    def _reject_for_tool_choice(
        self,
        content: str,
        is_final_answer_context: bool = False,
        include_format_hint: bool = True,
    ) -> None:
        """Reject the model's response and tell it to use a tool.

        Shared between ``_run_core`` and ``_run_core_streaming``. Called
        when ``_check_tool_choice_required`` returned ``needs_tool=True``
        — i.e., ``tool_choice`` is REQUIRED or SPECIFIC but the model
        responded without calling any tools.

        Side effects:
        - Adds the model's content to memory as an assistant message
        - Adds a user message telling the model to use a tool

        The caller must ``continue`` the agentic loop after this returns.

        Parameters
        ----------
        is_final_answer_context : bool
            True when the rejection is in response to a Final Answer
            (the message says "before providing a final answer").
            False when the model just responded with plain text.
        include_format_hint : bool
            True to append "Use the Action/Action Input format" (the
            non-streaming path's behavior). False to omit it (the
            streaming path's behavior). Both are preserved for
            backward compatibility — the difference was unintentional
            but this helper keeps it rather than silently changing
            user-facing messages.
        """
        self.memory.add("assistant", content)
        # Build the qualifier
        qualifier = " before providing a final answer" if is_final_answer_context else ""
        format_hint = " Use the Action/Action Input format to call a tool." if include_format_hint else ""

        if self.tool_choice.type == ToolChoiceType.SPECIFIC:
            self.memory.add("user",
                f"You must use the '{self.tool_choice.name}' tool"
                f"{qualifier}.{format_hint}")
        else:
            self.memory.add("user",
                f"You must use at least one tool{qualifier}.{format_hint}")

    def _run_core(self, prompt: str, stream: bool = False) -> AgentRun:
        """
        Run the agent on a prompt.

        This method implements the agentic loop following OpenResponses specification:
        1. Model samples from input
        2. If tool call: execute tool, return observation, continue
        3. If no tool call: return final output items

        IMPORTANT: No fallbacks that bypass the AI model are used.
        All tool calls must come from the model itself.

        Args:
            prompt: User prompt
            stream: When True, delegate to ``_run_core_streaming`` which
                prints model output chunks to stdout as they arrive (PERF-01).
                The returned ``AgentRun`` shape is identical to the non-
                streaming path; only the display behavior differs.

        Returns:
            AgentRun with final answer and execution details
        """
        # PERF-01: streaming path. Delegates to a parallel implementation
        # that uses backend.generate_completions_stream() and prints
        # text/reasoning deltas to stdout as they arrive. Returns the
        # same AgentRun shape as the non-streaming path so callers
        # (CLI, programmatic users, tests) are unaffected.
        if stream:
            return self._run_core_streaming(prompt)

        start_time = time.time()
        steps = []
        total_tokens = 0
        tool_calls = 0
        successful_results = []

        # Create OpenResponses Response object
        response = Response(
            model=self.model,
            status=ResponseStatus.QUEUED,
            tool_choice=self.tool_choice,
            allowed_tools=self._allowed_tools or [],
        )
        
        if self.debug and not self._is_comp_mode:
            print(f"\n[OpenResponses] Response created: id={response.id}")
            print(f"[OpenResponses] Response status: {response.status.value}")
        
        response.mark_in_progress()
        
        if self.debug and not self._is_comp_mode:
            print(f"[OpenResponses] Response status: {response.status.value}")

        # Add user prompt to memory
        self.memory.add("user", prompt)

        # Add input item
        user_item = create_message_item("user", prompt)
        response.input.append(user_item)
        
        if self.debug and not self._is_comp_mode:
            print(f"[OpenResponses] Input item added: id={user_item.id}, type={user_item.type}, role={user_item.role}")

        if self.debug:
            print(f"\n[AgentKthx] Model: {self.model}")
            print(f"[AgentKthx] Backend: {self.backend.base_url}")
            print(f"[AgentKthx] tool_choice: {self.tool_choice.type.value}")
            print(f"[AgentKthx] Tools: {self.tools.names()}")
            print(f"[AgentKthx] Prompt: {prompt}\n")

        # OpenResponses: Agentic Loop
        # The model decides whether to call tools or respond directly.
        # No synthesis or fallback mechanisms are used.
        
        # Track when we're expecting a Final Answer (after successful tool use)
        # Only enforced for terminal tools (calculator, get_time, etc.) to avoid
        # breaking multi-step workflows that need multiple tool calls.
        _expecting_final_answer = False
        _last_successful_result = None
        _last_tool_name = None
        
        # R06.52: true-termination flag. When the error tracker declares the
        # run stuck, the inner tool loop breaks AND the outer step loop must
        # stop too — the old code only broke the inner loop and kept calling
        # the model with dangling tool_calls.
        _terminated = False
        
        # Reset error tracker for new run
        self._error_tracker.reset()
        
        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Step {step_num + 1}]")

            # Generate response from model.
            # R06.54: transient API errors (rate limits, empty responses,
            # connection blips, provider 5xx) no longer kill the run — the
            # same step is retried after an escalating back-off. Only a
            # persistent failure (max_api_retries consecutive) or a permanent
            # error (auth, bad request) terminates the run, and it does so
            # with a clean history (nothing was announced for this step).
            #
            # MAINT-04 Phase 1 (R06.59): the retry loop now lives in
            # ``_generate_with_retry`` so both _run_core and
            # _run_core_streaming share it. Non-streaming path passes
            # enable_compaction_recovery=False — the context-length-400
            # compaction handler is streaming-only because non-streaming
            # runs don't accumulate enough history to trigger it.
            gen_response, _terminated = self._generate_with_retry(
                self._generate,
                step_num=step_num,
                steps=steps,
                response=response,
                enable_compaction_recovery=False,
            )
            if _terminated or gen_response is None:
                break

            content = gen_response.get("content", "")
            native_tool_calls = gen_response.get("tool_calls", [])
            tokens = gen_response.get("usage", {}).get("total_tokens", 0)
            total_tokens += tokens
            # R05.8: Capture reasoning_content (chain-of-thought) if the
            # backend surfaced it. Displayed in the CLI when --think is set.
            reasoning_content = gen_response.get("reasoning_content", "") or ""

            # Handle user cancellation (Ctrl+C during generate)
            if gen_response.get("_cancelled"):
                if self.debug:
                    print(f"  [Cancelled] Generation interrupted by user")
                steps.append(StepResult(
                    type=StepResultType.ERROR,
                    error="Cancelled by user",
                    tokens_used=tokens,
                ))
                response.mark_cancelled(debug=self.debug)
                break

            # OpenResponses: Handle finish_reason from backend.
            # MAINT-04 Phase 2 (R06.59): the length/content_filter handling
            # now lives in ``_handle_finish_reason`` so both _run_core and
            # _run_core_streaming share it. Returns True if terminal.
            if self._handle_finish_reason(gen_response, steps, response):
                break

            if self.debug:
                print(f"  Content: {content[:200] if content else '(empty)'}...")
                print(f"  Native tool calls: {native_tool_calls}")

            # ---- Process tool calls (native or ReAct) ----
            # MAINT-04 Phase 3b (R06.59): parsing now lives in
            # ``_parse_tool_calls`` so both paths produce the same
            # tool_calls_found shape.
            tool_calls_found = self._parse_tool_calls(
                content, native_tool_calls, response,
            )

            # Execute tool calls if found
            if tool_calls_found:
                # OpenResponses Enhancement: Final Answer Enforcement
                # If we asked for Final Answer but model tried to call tools again,
                # intercept and force Final Answer extraction.
                # MAINT-04 Phase 4a (R06.59): now via shared _enforce_final_answer.
                if _expecting_final_answer and _last_successful_result is not None:
                    return self._enforce_final_answer(
                        _last_successful_result=_last_successful_result,
                        tokens=tokens,
                        reasoning_content=reasoning_content,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                        debug_context="Model tried to call tools instead of Final Answer",
                    )

                # Track if any tool call has a final_answer
                pending_final_answer = None
                
                # For native calls, use special memory format
                if native_tool_calls:
                    self.memory.add_tool_call("assistant", content, native_tool_calls)
                else:
                    self.memory.add("assistant", content)

                for tc in tool_calls_found:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    tool_call_id = tc.get("id", "") or ""
                    
                    # Check if this tool call also has a final_answer
                    if tc.get("final_answer"):
                        pending_final_answer = tc["final_answer"]

                    # OpenResponses: Check allowed_tools
                    if self._allowed_tools and tool_name not in self._allowed_tools:
                        error_msg = f"Tool '{tool_name}' not in allowed_tools: {self._allowed_tools}"
                        if self.debug and not self._is_comp_mode:
                            print(f"  [OpenResponses] BLOCKED by allowed_tools: '{tool_name}' not in {self._allowed_tools}")
                        
                        if native_tool_calls:
                            self.memory.add_tool_result(
                                tool_call_id=tool_call_id,
                                name=tool_name,
                                content=f"Error: {error_msg}",
                            )
                        else:
                            self.memory.add("user", f"Observation: Error: {error_msg}")
                        continue

                    # R06.52: identical-repeat guard. If this exact call
                    # (tool + arguments) already failed max_identical_failures
                    # times, block it BEFORE execution and teach the model to
                    # change approach. The result is recorded in memory so the
                    # sequence stays paired.
                    # MAINT-04 Phase 4b (R06.59): now via shared _handle_blocked_tool_call.
                    if self._error_tracker.should_block_repeat(tool_name, tool_args):
                        _blocked, _term = self._handle_blocked_tool_call(
                            tool_name=tool_name,
                            tool_args=tool_args,
                            tool_call_id=tool_call_id,
                            native_tool_calls=native_tool_calls,
                            step_num=step_num,
                            tool_calls=tool_calls,
                            tokens=tokens,
                            steps=steps,
                            response=response,
                        )
                        if _term:
                            _terminated = True
                            break
                        continue

                    # Create FunctionCallItem
                    fc_item = create_function_call_item(tool_name, tool_args, tool_call_id)
                    fc_item.status = ItemStatus.IN_PROGRESS
                    response.add_output_item(fc_item, debug=not self._is_comp_mode and self.debug)
                    
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] FunctionCallItem created: id={fc_item.id}, call_id={fc_item.call_id}")
                        print(f"  [OpenResponses] FunctionCallItem status: {fc_item.status.value}")

                    try:
                        result = self._execute_tool(tool_name, tool_args, prompt)
                    except KeyboardInterrupt:
                        fc_item.status = ItemStatus.FAILED
                        response.mark_cancelled(debug=self.debug)
                        steps.append(StepResult(
                            type=StepResultType.ERROR,
                            error="Cancelled by user during tool execution",
                        ))
                        break

                    tool_calls += 1
                    
                    # Track success/failure for error recovery
                    is_error = is_error_result(str(result))
                    if is_error:
                        self._error_tracker.record_failure(
                            tool_name=tool_name,
                            error_message=str(result),
                            step=step_num,
                            arguments=tool_args
                        )
                        
                        # Check if we should terminate due to too many failures
                        if self._error_tracker.should_terminate():
                            if self.debug:
                                print(f"  [ErrorRecovery] Terminating: {self._error_tracker.consecutive_all} consecutive all-failure steps >= max ({self._error_tracker.max_total_failures})")
                            fc_item.status = ItemStatus.FAILED
                            term_msg = (
                                f"Error: run terminated after "
                                f"{self._error_tracker.consecutive_all} consecutive steps in which "
                                f"every tool call failed. Review the observations above and "
                                f"adjust the approach."
                            )
                            # R06.52: record the blocked result for THIS call so
                            # the history stays paired; sanitize_history() fills
                            # any remaining calls from this same step.
                            if native_tool_calls:
                                self.memory.add_tool_result(
                                    tool_call_id=tool_call_id or f"terminated_{step_num}_{tool_calls}",
                                    name=tool_name,
                                    content=term_msg,
                                )
                            else:
                                self.memory.add("user", f"Observation: {term_msg}")
                            steps.append(StepResult(
                                type=StepResultType.ERROR,
                                error=term_msg,
                                tool_call=ToolCall(name=tool_name, arguments=tool_args),
                                tokens_used=tokens,
                            ))
                            response.mark_failed({"message": "Too many tool failures", "type": "error_recovery"})
                            _terminated = True
                            break
                    else:
                        # Record success to reset consecutive failure counter
                        self._error_tracker.record_success(tool_name)

                    # Update FunctionCallItem status
                    fc_item.status = ItemStatus.COMPLETED
                    
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] FunctionCallItem status: {fc_item.status.value}")

                    # Create FunctionCallOutputItem
                    fco_item = create_function_call_output(fc_item.call_id, str(result))
                    response.add_output_item(fco_item, debug=not self._is_comp_mode and self.debug)
                    
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] FunctionCallOutputItem created: id={fco_item.id}, call_id={fco_item.call_id}")

                    # Add tool result to memory with enhanced guidance
                    if native_tool_calls:
                        self.memory.add_tool_result(
                            tool_call_id=fc_item.call_id,
                            name=tool_name,
                            content=str(result),
                        )
                        # Native tool calls also get retry context on error
                        if is_error and self._retry_on_error:
                            retry_msg = build_retry_context(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                tracker=self._error_tracker,
                                max_tool_retries=self._max_tool_retries,
                            )
                            if retry_msg:
                                if self.debug:
                                    print(f"  [Retry Context] Adding retry hint for native tool call: {tool_name}")
                                self.memory.add("user", retry_msg)
                    else:
                        # Use error recovery module for enhanced observation
                        observation_msg = build_enhanced_observation(
                            tool_name=tool_name,
                            result=str(result),
                            tracker=self._error_tracker,
                            available_tools=self.tools.names(),
                            is_error=is_error,
                            retry_on_error=self._retry_on_error,
                            tool_args=tool_args,
                        )
                        
                        # Update expecting_final_answer flag
                        # Only enforce for terminal tools (simple, direct-answer tools)
                        # to avoid breaking multi-step workflows
                        if is_error:
                            _expecting_final_answer = False
                            _last_tool_name = None
                        else:
                            from .core.error_recovery import _is_simple_result
                            if _is_simple_result(str(result), tool_name):
                                _expecting_final_answer = True
                                _last_successful_result = str(result)
                                _last_tool_name = tool_name
                            else:
                                # Complex/intermediate result — allow more tool calls
                                _expecting_final_answer = False
                                _last_tool_name = None
                        
                        self.memory.add("user", observation_msg)

                    if not is_error:
                        successful_results.append(f"{tool_name}: {result}")

                    steps.append(StepResult(
                        type=StepResultType.TOOL_CALL,
                        content=content,
                        tool_call=ToolCall(name=tool_name, arguments=tool_args),
                        tool_result=result,
                        tokens_used=tokens,
                    ))

                    if self.debug:
                        print(f"  Tool: {tool_name}({tool_args})")
                        print(f"  Result: {str(result)[:200]}...")

                # R06.52: if the run was terminated inside the tool loop,
                # stop the WHOLE run. The old code only broke the inner loop
                # and then called the model again with dangling tool_calls —
                # an illegal API sequence (OpenRouter 400 / ZAI 1214).
                #
                # MAINT-04 Phase 3c (R06.59): finalize via shared helper
                # (mark_completed=False because we're in a failed state).
                if _terminated:
                    return self._finalize_run(
                        final_answer="",
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                        success=False,
                        mark_completed=False,
                    )

                # Check if model provided final_answer along with tool call
                if pending_final_answer:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] Model provided final_answer with tool call")
                        print(f"  [OpenResponses] Using final_answer: {pending_final_answer[:100]}...")

                    # Create output message item
                    msg_item = create_message_item("assistant", pending_final_answer)
                    msg_item.status = ItemStatus.COMPLETED
                    response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)

                    steps.append(StepResult(
                        type=StepResultType.FINAL_ANSWER,
                        content=pending_final_answer,
                        tokens_used=tokens,
                        reasoning_content=reasoning_content,
                    ))

                    # MAINT-04 Phase 3c (R06.59): finalize via shared helper.
                    return self._finalize_run(
                        final_answer=pending_final_answer,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                        success=True,
                    )

                # Continue the agentic loop
                continue

            # ---- Check for Final Answer ----
            # The model explicitly signals completion with "Final Answer:"
            if self._parser.is_final_answer(content):
                # OpenResponses: Check tool_choice enforcement.
                # MAINT-04 Phase 3a (R06.59): the 6-line needs_tool block
                # now lives in ``_check_tool_choice_required``.
                needs_tool, rejection_reason = self._check_tool_choice_required(tool_calls)

                if needs_tool:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                        print(f"  [OpenResponses] Enforcing tool requirement...")
                    # Tell model to use tools.
                    # MAINT-04 Phase 4c (R06.59): now via shared _reject_for_tool_choice.
                    self._reject_for_tool_choice(
                        content,
                        is_final_answer_context=True,
                        include_format_hint=True,
                    )
                    continue

                answer = self._parser.extract_final_answer(content)

                # Reset the expecting_final_answer flag
                _expecting_final_answer = False

                # Create output message item
                msg_item = create_message_item("assistant", answer)
                msg_item.status = ItemStatus.COMPLETED
                response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)
                
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] MessageItem created: id={msg_item.id}, role={msg_item.role}")
                    print(f"  [OpenResponses] MessageItem status: {msg_item.status.value}")

                steps.append(StepResult(
                    type=StepResultType.FINAL_ANSWER,
                    content=answer,
                    tokens_used=tokens,
                    reasoning_content=reasoning_content,
                ))

                if self.debug:
                    print(f"  Final answer: {answer}")

                break

            # ---- No tool call, no final answer ----
            # Model responded directly without explicit final answer format
            # Check tool_choice enforcement before accepting.
            # MAINT-04 Phase 3a (R06.59): uses shared _check_tool_choice_required.
            needs_tool, rejection_reason = self._check_tool_choice_required(tool_calls)

            if needs_tool:
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                    print(f"  [OpenResponses] Enforcing tool requirement...")
                # Tell model to use tools.
                # MAINT-04 Phase 4c (R06.59): now via shared _reject_for_tool_choice.
                self._reject_for_tool_choice(
                    content,
                    is_final_answer_context=False,
                    include_format_hint=True,
                )
                continue

            # OpenResponses Enhancement: Final Answer Enforcement
            # If we were expecting Final Answer but model responded without "Final Answer:" format,
            # use the last successful result instead of accepting the model's potentially wrong answer.
            # MAINT-04 Phase 4a (R06.59): now via shared _enforce_final_answer.
            if _expecting_final_answer and _last_successful_result is not None:
                return self._enforce_final_answer(
                    _last_successful_result=_last_successful_result,
                    tokens=tokens,
                    reasoning_content=reasoning_content,
                    steps=steps,
                    total_tokens=total_tokens,
                    start_time=start_time,
                    tool_calls=tool_calls,
                    response=response,
                    debug_context="Model responded without Final Answer format",
                )

            # Accept model's response as the final answer
            # This is the model's decision (OpenResponses: model decides in 'auto' mode)

            # Create output message item
            if content:
                msg_item = create_message_item("assistant", content)
                msg_item.status = ItemStatus.COMPLETED
                response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)

            if self.debug:
                print(f"  No tool calls detected, accepting as final answer")

            steps.append(StepResult(
                type=StepResultType.FINAL_ANSWER,
                content=content,
                tokens_used=tokens,
                reasoning_content=reasoning_content,
            ))
            self.memory.add("assistant", content)
            break

        else:
            # Max steps reached
            response.mark_incomplete()
            if self.debug and not self._is_comp_mode:
                print(f"\n[OpenResponses] Response status: {response.status.value} (max steps reached)")
            steps.append(StepResult(
                type=StepResultType.MAX_STEPS,
                content="Maximum steps reached without final answer",
            ))

        total_ms = (time.time() - start_time) * 1000

        # Mark response as completed
        if response.status == ResponseStatus.IN_PROGRESS:
            response.mark_completed()

        if self.debug and not self._is_comp_mode:
            print(f"\n[OpenResponses] Response completed: id={response.id}")
            print(f"[OpenResponses] Final status: {response.status.value}")
            print(f"[OpenResponses] Output items: {len(response.output)}")
            print(f"[OpenResponses] Tool calls made: {tool_calls}")

        # Get final answer via shared helper (MAINT-04 Phase 3c).
        final_answer = self._extract_last_final_answer(steps)

        # MAINT-04 Phase 3c (R06.59): finalize via shared helper.
        # mark_completed=False because we already marked it above (to
        # keep the debug print ordering intact).
        return self._finalize_run(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            start_time=start_time,
            tool_calls=tool_calls,
            response=response,
            success=bool(final_answer),
            mark_completed=False,
        )

    def run_stream(self, prompt: str) -> Generator[str, None, None]:
        """
        Run the agent on a prompt with streaming OpenResponses SSE events.

        This method implements the agentic loop with streaming output following
        OpenResponses specification. It yields Server-Sent Events (SSE) that
        describe the response lifecycle and content deltas.

        IMPORTANT: The agentic loop is fully supported during streaming.
        When the model produces a tool call, it is executed and the loop
        continues, streaming the next model response.

        SSE Event Sequence (per OpenResponses spec):
            1. response.queued - Response is queued
            2. response.in_progress - Response started
            3. response.output_item.added - New output item added
            4. response.content_part.added - New content part added
            5. response.output_text.delta - Text deltas (multiple)
            6. response.output_text.done - Text completed
            7. response.content_part.done - Content part completed
            8. response.output_item.done - Output item completed
            9. response.completed - Response finished

        Args:
            prompt: User prompt

        Yields:
            SSE-formatted strings (event: ...\\ndata: ...\\n\\n)

        Example:
            agent = Agent(model="qwen2.5:0.5b")
            for sse_event in agent.run_stream("Hello!"):
                print(sse_event)  # SSE formatted event
        """
        start_time = time.time()

        # Create OpenResponses Response object
        response = Response(
            model=self.model,
            status=ResponseStatus.QUEUED,
            tool_choice=self.tool_choice,
            allowed_tools=self._allowed_tools or [],
        )

        if self.debug:
            print(f"\n[OpenResponses stream] Response created: id={response.id}")

        # Add user prompt to memory
        self.memory.add("user", prompt)

        # Add input item
        user_item = create_message_item("user", prompt)
        response.input.append(user_item)

        if self.debug:
            print(f"\n[AgentKthx stream] Model: {self.model}")
            print(f"[AgentKthx stream] Backend: {self.backend.base_url}")
            print(f"[AgentKthx stream] tool_choice: {self.tool_choice.type.value}")
            print(f"[AgentKthx stream] Tools: {self.tools.names()}")
            print(f"[AgentKthx stream] Prompt: {prompt}\n")

        # OpenResponses: Agentic Loop (streaming variant)
        # Stream model output, check for tool calls, execute them, repeat.
        _expecting_final_answer = False
        _last_successful_result = None
        _last_tool_name = None
        tool_call_count = 0

        # R06.52: mirror of run() — true-termination flag for the tracker.
        _terminated = False

        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Stream Step {step_num + 1}]")

            # Collect the full streamed response
            full_content = ""

            # Stream model response, collecting content for tool-call detection.
            # R06.54: transient API errors (rate limits, empty responses,
            # connection blips) are retried with escalating back-off instead of
            # killing the stream. Permanent errors fail immediately.
            _api_failure = 0
            _api_wait_total = 0.0
            while True:
                try:
                    for chunk in self._generate_stream_chunks(prompt):
                        full_content += chunk
                    break
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    _api_failure += 1
                    _transient = is_transient_api_error(e)
                    _exhausted = _transient and _api_failure > self.max_api_retries
                    if not _transient or _exhausted:
                        # R06.54: surface the terminal outcome to non-debug
                        # users too (mirrors the run() path).
                        if _exhausted:
                            print(describe_terminal(
                                e, self.max_api_retries, _api_wait_total))
                        elif not _transient:
                            print(f"  [Resilience] Fatal API error — "
                                  f"not retrying: {e}")
                        if self.debug:
                            print(f"  [Stream] ERROR: {e}")
                        # Emit failure event
                        response.mark_failed({"message": str(e), "type": "stream_error"})
                        fail_event = ResponseEvent(
                            type=EventType.RESPONSE_FAILED,
                            response=response,
                        )
                        yield fail_event.to_sse()
                        return
                    _waited = backoff_delay(_api_failure)
                    _api_wait_total += _waited
                    print(describe_wait(_api_failure, self.max_api_retries, _waited, e))
                    time.sleep(_waited)

            # Parse for tool calls (ReAct format)
            tool_calls_found = []

            if full_content:
                parsed_calls = self._parser.parse(full_content)
                for call in parsed_calls:
                    if hasattr(call, 'thought') and call.thought:
                        reasoning_item = ReasoningItem(
                            content=[OutputText(text=call.thought)]
                        )
                        reasoning_item.status = ItemStatus.COMPLETED
                        response.add_output_item(reasoning_item)

                    tool_calls_found.append({
                        "name": call.name,
                        "arguments": call.arguments,
                        "id": "",
                        "final_answer": getattr(call, 'final_answer', None),
                    })

            # ---- Execute tool calls if found ----
            if tool_calls_found:
                # Final Answer enforcement (same logic as run())
                if _expecting_final_answer and _last_successful_result is not None:
                    text_chunks_gen = iter([_last_successful_result])
                    for sse_event in stream_response_events(
                        Response(model=self.model, status=ResponseStatus.IN_PROGRESS,
                                tool_choice=self.tool_choice, allowed_tools=self._allowed_tools or []),
                        text_chunks_gen, debug=self.debug,
                    ):
                        yield sse_event
                    return

                pending_final_answer = None
                self.memory.add("assistant", full_content)

                for tc in tool_calls_found:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]

                    if tc.get("final_answer"):
                        pending_final_answer = tc["final_answer"]

                    # Check allowed_tools
                    if self._allowed_tools and tool_name not in self._allowed_tools:
                        error_msg = f"Tool '{tool_name}' not in allowed_tools: {self._allowed_tools}"
                        self.memory.add("user", f"Observation: Error: {error_msg}")
                        continue

                    # R06.52: identical-repeat guard (mirror of run()).
                    if self._error_tracker.should_block_repeat(tool_name, tool_args):
                        blocked_msg = self._error_tracker.format_repeat_block(tool_name, tool_args)
                        if self.debug:
                            print(f"  [ErrorRecovery] Blocking repeated identical call: {tool_name}({tool_args})")
                        self.memory.add("user", f"Observation: {blocked_msg}")
                        self._error_tracker.record_failure(
                            tool_name=tool_name,
                            error_message=blocked_msg,
                            step=step_num,
                            arguments=tool_args,
                        )
                        if self._error_tracker.should_terminate():
                            term_msg = (
                                f"Error: run terminated after "
                                f"{self._error_tracker.consecutive_all} consecutive steps in which "
                                f"every tool call failed. Review the observations above and "
                                f"adjust the approach."
                            )
                            self.memory.add("user", f"Observation: {term_msg}")
                            response.mark_incomplete()
                            incomplete_event = ResponseEvent(
                                type=EventType.RESPONSE_INCOMPLETE,
                                response=response,
                            )
                            yield incomplete_event.to_sse()
                            return
                        continue

                    # Create FunctionCallItem and emit SSE events
                    fc_item = create_function_call_item(tool_name, tool_args)
                    fc_item.status = ItemStatus.IN_PROGRESS
                    response.add_output_item(fc_item)
                    output_index = len(response.output) - 1

                    fc_added = OutputItemEvent(
                        type=EventType.OUTPUT_ITEM_ADDED,
                        item=fc_item,
                        output_index=output_index,
                    )
                    yield fc_added.to_sse()

                    # Execute the tool
                    try:
                        result = self._execute_tool(tool_name, tool_args, prompt)
                    except KeyboardInterrupt:
                        fc_item.status = ItemStatus.FAILED
                        response.mark_cancelled(debug=self.debug)
                        # Yield cancellation event and stop
                        cancel_event = ResponseStateEvent(
                            type=EventType.RESPONSE_FAILED,
                            response=response,
                        )
                        yield cancel_event.to_sse()
                        return
                    tool_call_count += 1

                    fc_item.status = ItemStatus.COMPLETED

                    fc_done = OutputItemEvent(
                        type=EventType.OUTPUT_ITEM_DONE,
                        item=fc_item,
                        output_index=output_index,
                    )
                    yield fc_done.to_sse()

                    # Create function_call_output
                    fco_item = create_function_call_output(fc_item.call_id, str(result))
                    response.add_output_item(fco_item)

                    # Build observation and add to memory
                    is_error = is_error_result(str(result))
                    # R06.52: mirror run() — feed the recovery tracker so the
                    # consecutive-failure termination and repeat blocking work
                    # in streaming mode too.
                    if is_error:
                        self._error_tracker.record_failure(
                            tool_name=tool_name,
                            error_message=str(result),
                            step=step_num,
                            arguments=tool_args,
                        )
                        if self._error_tracker.should_terminate():
                            term_msg = (
                                f"Error: run terminated after "
                                f"{self._error_tracker.consecutive_all} consecutive steps in which "
                                f"every tool call failed. Review the observations above and "
                                f"adjust the approach."
                            )
                            self.memory.add("user", f"Observation: {term_msg}")
                            response.mark_incomplete()
                            incomplete_event = ResponseEvent(
                                type=EventType.RESPONSE_INCOMPLETE,
                                response=response,
                            )
                            yield incomplete_event.to_sse()
                            return
                    else:
                        self._error_tracker.record_success(tool_name)

                    observation_msg = build_enhanced_observation(
                        tool_name=tool_name,
                        result=str(result),
                        tracker=self._error_tracker,
                        available_tools=self.tools.names(),
                        is_error=is_error,
                        retry_on_error=self._retry_on_error,
                        tool_args=tool_args,
                    )

                    if is_error:
                        _expecting_final_answer = False
                        _last_tool_name = None
                    else:
                        from .core.error_recovery import _is_simple_result
                        if _is_simple_result(str(result), tool_name):
                            _expecting_final_answer = True
                            _last_successful_result = str(result)
                            _last_tool_name = tool_name
                        else:
                            _expecting_final_answer = False
                            _last_tool_name = None

                    self.memory.add("user", observation_msg)

                # Check for pending final answer
                if pending_final_answer:
                    text_chunks_gen = iter([pending_final_answer])
                    for sse_event in stream_response_events(
                        Response(model=self.model, status=ResponseStatus.IN_PROGRESS,
                                tool_choice=self.tool_choice, allowed_tools=self._allowed_tools or []),
                        text_chunks_gen, debug=self.debug,
                    ):
                        yield sse_event
                    return

                # Continue the agentic loop (next streaming iteration)
                continue

            # ---- No tool calls — stream final response ----
            # Check for Final Answer format
            if self._parser.is_final_answer(full_content):
                answer = self._parser.extract_final_answer(full_content)
                text_chunks_gen = iter([answer])
            else:
                text_chunks_gen = iter([full_content])

            # Stream the final response with proper OpenResponses events
            final_response = Response(
                model=self.model,
                status=ResponseStatus.IN_PROGRESS,
                tool_choice=self.tool_choice,
                allowed_tools=self._allowed_tools or [],
            )
            # Carry over any items from previous loop iterations
            final_response.output = response.output
            final_response.input = response.input
            final_response.usage = response.usage

            for sse_event in stream_response_events(final_response, text_chunks_gen, debug=self.debug):
                yield sse_event

            # Only one pass needed when there are no tool calls
            return

        else:
            # Max steps reached
            response.mark_incomplete()
            incomplete_event = ResponseEvent(
                type=EventType.RESPONSE_INCOMPLETE,
                response=response,
            )
            yield incomplete_event.to_sse()

    def _generate_stream_chunks(self, prompt: str) -> Generator[str, None, None]:
        """
        Generate streaming text chunks from the backend.

        This is a helper method that wraps the backend's streaming functionality
        and yields raw text chunks for the OpenResponses event generator.

        Args:
            prompt: User prompt (unused, memory already has the prompt)

        Yields:
            Text chunks from the model
        """
        messages = self.memory.get_messages()

        if self.debug:
            print(f"  [DEBUG] Streaming {len(messages)} messages")

        # ── Thinking / reasoning controls (R05.8) ────────────────────────
        # Resolve the final `think` value:
        #   1. If the user explicitly set --thinking (self._think is not None),
        #      honor it.
        #   2. Else, if the model family needs no-think directive (qwen3,
        #      deepseek-r1, etc.), force think=False.
        #   3. Else, leave as None (model decides).
        think = self._think
        if think is None and self.model_family:
            from .core.model_family_config import needs_no_think_directive
            if needs_no_think_directive(self.model_family):
                think = False

        # Build kwargs for backend
        backend_kwargs = {"think": think}
        # Forward reasoning_effort when set (low/medium/high).
        # OpenAI o-series, GLM-5.x, and other compatible models honor this.
        # Backends that don't recognize it will pass it through via **kwargs
        # to the underlying HTTP request body.
        if self._reasoning_effort is not None:
            backend_kwargs["reasoning_effort"] = self._reasoning_effort
        if self.num_ctx is not None:
            backend_kwargs["num_ctx"] = self.num_ctx

        # R06.3: Forward runtime kwargs set via /param slash command.
        # These are params that don't have a dedicated agent attribute
        # (top_k, seed, n, presence_penalty, frequency_penalty).
        # They're stashed on agent._runtime_kwargs by /param in cli.py.
        if hasattr(self, '_runtime_kwargs') and self._runtime_kwargs:
            for k, v in self._runtime_kwargs.items():
                backend_kwargs[k] = v

        # Stop tokens: forward model-family stop sequences to backend.
        stops = self.model_config.stop_tokens if self.model_config else []
        if stops:
            backend_kwargs["stop"] = stops

        # Structured output: forward response_format to backend
        if self._response_format is not None:
            backend_kwargs["response_format"] = self._response_format

        # Check if backend has streaming support
        if hasattr(self.backend, 'generate_stream'):
            # Use native Ollama streaming
            for chunk in self.backend.generate_stream(
                model=self.model,
                messages=messages,
                tools=self.tools.all() if self.tools and len(self.tools) > 0 else None,
                temperature=self.model_config.default_temperature,
                max_tokens=self.model_config.default_max_tokens,
                **backend_kwargs,
            ):
                yield chunk
        elif hasattr(self.backend, 'generate_completions_stream'):
            # Use OpenAI-compatible streaming
            for chunk_dict in self.backend.generate_completions_stream(
                model=self.model,
                messages=messages,
                tools=self.tools.all() if self.tools and len(self.tools) > 0 else None,
                temperature=self.model_config.default_temperature,
                max_tokens=self.model_config.default_max_tokens,
                **backend_kwargs,
            ):
                delta = chunk_dict.get("delta", "")
                if delta:
                    yield delta
        else:
            # Fallback: non-streaming with simulated streaming
            result = self.backend.generate(
                model=self.model,
                messages=messages,
                tools=self.tools.all() if self.tools and len(self.tools) > 0 else None,
                temperature=self.model_config.default_temperature,
                max_tokens=self.model_config.default_max_tokens,
                **backend_kwargs,
            )
            content = result.get("content", "")
            # Yield content in chunks for consistent behavior
            chunk_size = 20
            for i in range(0, len(content), chunk_size):
                yield content[i:i + chunk_size]

    def create_response(
        self,
        input_items: list = None,
        previous_response_id: str | None = None,
    ) -> Response:
        """
        Create a new Response following OpenResponses specification.

        This is the primary API for OpenResponses-compliant usage.

        Args:
            input_items: List of input items (messages, function call outputs)
            previous_response_id: ID of previous response to continue from

        Returns:
            Response object with output items
        """
        response = Response(
            model=self.model,
            status=ResponseStatus.QUEUED,
            tool_choice=self.tool_choice,
            allowed_tools=self._allowed_tools or [],
            previous_response_id=previous_response_id,
        )

        # Load previous response context if specified
        if previous_response_id and previous_response_id in self._response_history:
            prev_response = self._response_history[previous_response_id]
            # The previous input and output become part of context
            response.input = list(prev_response.input)
            response.input.extend(prev_response.output)

        # Add new input items
        if input_items:
            response.input.extend(input_items)

        return response

    def _generate(self) -> dict:
        """Generate a response from the backend."""
        messages = self.memory.get_messages()

        if self.debug:
            print(f"  [DEBUG] Sending {len(messages)} messages")
            for i, msg in enumerate(messages):
                role = msg.get('role', '?')
                content = msg.get('content', '')
                tc = msg.get('tool_calls', [])
                tool_call_id = msg.get('tool_call_id', '')
                # Show just length for system prompts, content for others
                if role == 'system':
                    content_preview = f"<{len(content)} chars>"
                elif role == 'tool':
                    # Show tool message with tool_call_id
                    if self.truncation == "disabled":
                        content_preview = f"{content if content else '(empty)'} (tool_call_id={tool_call_id})"
                    else:
                        content_preview = f"{content[:100] if content else '(empty)'} (tool_call_id={tool_call_id})"
                else:
                    if self.truncation == "disabled":
                        content_preview = content if content else '(empty)'
                    else:
                        content_preview = content[:200] if content else '(empty)'
                print(f"  [MSG {i}] role={role}, content={content_preview!r}{' as tool_calls]' if tc else ']'}")
            print(f"  [DEBUG] Tools: {[t.name for t in self.tools.all()] if self.tools else None}")

        # ── Thinking / reasoning controls (R05.8) ────────────────────────
        # Resolve the final `think` value:
        #   1. If the user explicitly set --thinking (self._think is not None),
        #      honor it.
        #   2. Else, if the model family needs no-think directive (qwen3,
        #      deepseek-r1, etc.), force think=False.
        #   3. Else, leave as None (model decides).
        think = self._think
        if think is None and self.model_family:
            from .core.model_family_config import needs_no_think_directive
            if needs_no_think_directive(self.model_family):
                think = False

        # Build kwargs for backend
        backend_kwargs = {"think": think}
        # Forward reasoning_effort when set (low/medium/high).
        # OpenAI o-series, GLM-5.x, and other compatible models honor this.
        # Backends that don't recognize it will pass it through via **kwargs
        # to the underlying HTTP request body.
        if self._reasoning_effort is not None:
            backend_kwargs["reasoning_effort"] = self._reasoning_effort
        if self.num_ctx is not None:
            backend_kwargs["num_ctx"] = self.num_ctx
        if self._num_predict is not None:
            backend_kwargs["num_predict"] = self._num_predict

        # Stop tokens: forward model-family stop sequences to backend.
        # Critical for llama-server /completion and Ollama OPENRE where the
        # raw completion endpoint has NO chat template and no default stop
        # sequences — the model will generate until n_predict is exhausted
        # without them, producing garbled multi-turn output.
        stops = self.model_config.stop_tokens if self.model_config else []
        if stops:
            backend_kwargs["stop"] = stops

        # OpenResponses: Forward tool_choice to backend API
        # This allows the backend to enforce tool invocation constraints natively
        if self.tool_choice and self.tool_choice.type != ToolChoiceType.AUTO:
            backend_kwargs["tool_choice"] = self.tool_choice.to_dict()

        # Structured output: forward response_format to backend
        if self._response_format is not None:
            backend_kwargs["response_format"] = self._response_format

        # Pass tools for native tool calling (OpenResponses/ChatCompletions compliant)
        # ReAct parsing remains as fallback for models without native support
        tools_for_backend = self.tools.all() if self.tools and len(self.tools) > 0 else None

        # Pass truncation setting to backend
        backend_kwargs["truncation"] = self.truncation

        # Get generation parameters (use overrides or model defaults)
        gen_temperature = self._temperature if self._temperature is not None else self.model_config.default_temperature
        gen_max_tokens = self._num_predict if self._num_predict is not None else self.model_config.default_max_tokens
        gen_top_p = self._top_p if self._top_p is not None else self.model_config.default_top_p

        # R06.57: Cap max_tokens to num_ctx/32 so input + output fits the
        # context window. The model_config default_max_tokens is 8192, which
        # equals the entire runtime context if num_ctx=8192 — leaving zero
        # room for input. Cap to num_ctx//32 (256 for 8K context, 1024 for
        # 32K, etc.). The backend's _get_model_defaults cap only fires when
        # max_tokens is None, but the agent always passes a value — so we
        # need to cap here too.
        # Skip the cap if the user explicitly set --num-predict (gen_max_tokens
        # came from self._num_predict, not the default).
        if self._num_predict is None and self.num_ctx and self.num_ctx > 0:
            capped = self.num_ctx // 32
            if gen_max_tokens > capped:
                gen_max_tokens = capped

        if self.debug:
            params_str = f"temp={gen_temperature}, top_p={gen_top_p}, max_tokens={gen_max_tokens}, num_ctx={self.num_ctx}"
            if think is not None:
                params_str += f", think={think}"
            if stops:
                params_str += f", stops={stops}"
            print(f"  [DEBUG] Model params: {params_str}")

        try:
            response = self.backend.generate(
                model=self.model,
                messages=messages,
                tools=tools_for_backend,  # Native tool calling support
                temperature=gen_temperature,
                max_tokens=gen_max_tokens,
                top_p=gen_top_p,
                **backend_kwargs,
            )
        except KeyboardInterrupt:
            # User cancelled during backend HTTP call
            return {
                "content": "",
                "tool_calls": [],
                "usage": {},
                "_finish_reason": "cancelled",
                "_cancelled": True,
            }

        # OpenResponses / Chat Completions: Handle finish_reason
        # Per spec, finish_reason affects response status:
        #   "stop"      → normal completion (default)
        #   "length"    → incomplete — token budget exhausted
        #   "content_filter" → failed — content was filtered
        finish_reason = response.get("finish_reason", "stop")
        if self.debug:
            print(f"  [DEBUG] finish_reason: {finish_reason}")
        # Store for caller to consume
        response["_finish_reason"] = finish_reason

        if self.debug:
            print(f"  [DEBUG] Response keys: {list(response.keys())}")
            print(f"  [DEBUG] Content: {response.get('content', '')[:100]}...")
            print(f"  [DEBUG] Native tool calls: {response.get('tool_calls', [])}")

        return response

    # ── PERF-01: streaming generation ─────────────────────────────────
    # Streaming path that mirrors _generate() but uses backend streaming
    # and prints content/reasoning deltas to stdout as they arrive.
    # Returns the SAME dict shape as _generate() so _run_core_streaming
    # can reuse all of the non-streaming loop's logic (tool dispatch,
    # error recovery, finish_reason handling, memory tracking).

    def _check_compaction(self) -> int:
        """Check if memory needs compaction and compact if over threshold.

        Estimates the total token count of all messages in memory and
        compares against ``num_ctx * _compaction_threshold``. If over
        threshold, calls ``memory.compact_messages()`` to truncate older
        messages while keeping recent ones intact.

        ROB-06: This is the preventive compaction path — it runs before
        each generate call to avoid context-length 400s on long agentic
        runs. The reactive path (in _iter_sse_lines) still handles the
        case where compaction wasn't enough.

        Returns:
            Number of messages that were compacted (0 if none needed).
        """
        if getattr(self, "_compaction_threshold", 0.85) >= 1.0:
            return 0  # compaction disabled

        # Estimate total tokens: ~4 chars per token (rough heuristic)
        total_chars = 0
        for msg in self.memory:
            content = getattr(msg, 'content', '') or ''
            total_chars += len(content)
            # Also count tool_calls (small but present)
            tc = getattr(msg, 'tool_calls', None)
            if tc:
                total_chars += len(json.dumps(tc, ensure_ascii=False))
        estimated_tokens = total_chars // 4

        # Get context limit
        ctx = self.num_ctx or 8192
        threshold_tokens = int(ctx * getattr(self, "_compaction_threshold", 0.85))

        if estimated_tokens <= threshold_tokens:
            # R06.58 BUGFIX: even when no compaction is needed, the
            # running token totals may be stale (e.g., the fallback
            # estimation path used to accumulate the whole history every
            # step). Re-snapshot from current memory so the footer's ctx%
            # reflects reality, not a stale cumulative total.
            self._snapshot_running_tokens()
            return 0  # under threshold, no compaction needed

        # Over threshold — compact older messages
        # Keep the most recent 10 messages intact
        keep_count = 10
        compacted = self.memory.compact_messages(keep_count=keep_count)

        if compacted > 0:
            # Recount post-compaction size for an accurate log line.
            post_chars = 0
            for msg in self.memory:
                c = getattr(msg, 'content', '') or ''
                post_chars += len(c)
                tc = getattr(msg, 'tool_calls', None)
                if tc:
                    post_chars += len(json.dumps(tc, ensure_ascii=False))
            post_tokens = post_chars // 4
            print(f"  [Compaction] {compacted} messages compacted "
                  f"(~{estimated_tokens // 1000}K → "
                  f"~{post_tokens // 1000}K tokens, "
                  f"threshold {threshold_tokens // 1000}K of "
                  f"{ctx // 1000}K context)")

        # R06.58 BUGFIX: ALWAYS re-snapshot running totals from the
        # post-compaction memory state, regardless of whether compaction
        # actually truncated anything. Previously the reset only ran when
        # ``compacted > 0``, which meant that if compaction ran once and
        # truncated everything, the next call would return 0 (nothing to
        # truncate), and the stale inflated running totals would persist
        # — keeping ctx% pinned at 100% forever.
        self._snapshot_running_tokens()

        return compacted

    def _snapshot_running_tokens(self) -> None:
        """Recompute _running_tokens_in/out from the current memory state.

        R06.58: This is the single source of truth for the footer's ctx%
        display. Called after every step's generate (so the snapshot
        reflects the just-added assistant message + tool results), and
        after every compaction (so the snapshot reflects the truncated
        state).

        The split is ~90% input / ~10% output because most of the
        in-memory context is input (tool results, system prompt,
        conversation history). The just-generated output is small
        compared to the accumulated input.
        """
        total_chars = 0
        for msg in self.memory:
            content = getattr(msg, 'content', '') or ''
            total_chars += len(content)
            tc = getattr(msg, 'tool_calls', None)
            if tc:
                total_chars += len(json.dumps(tc, ensure_ascii=False))
        total_tokens = total_chars // 4
        self._running_tokens_in = int(total_tokens * 0.9)
        self._running_tokens_out = int(total_tokens * 0.1)

    def _generate_stream(self) -> dict:
        """Stream a response from the backend, printing deltas to stdout.

        Mirrors ``_generate()`` but uses ``backend.generate_completions_stream()``
        (when available) and prints content / reasoning_content chunks to
        stdout as they arrive — the typewriter effect users expect from
        ``stream=True``.

        Accumulates ``tool_calls`` fragments across SSE chunks (OpenAI
        streaming splits a single tool_call across many deltas: the first
        carries ``id`` + ``name``, subsequent ones append to ``arguments``
        as a partial JSON string). Returns the assembled call list in the
        same shape as ``_generate()`` so callers don't need to know
        whether streaming was used.

        If the backend has no streaming method, falls back to ``_generate()``
        and prints the content in one shot (with a leading marker so the
        user can tell streaming was requested but unavailable).

        Returns:
            dict with keys: content, tool_calls, usage, finish_reason,
            reasoning_content, _finish_reason, _cancelled
        """
        messages = self.memory.get_messages()

        # Resolve think / reasoning_effort / kwargs exactly like _generate()
        think = self._think
        if think is None and self.model_family:
            from .core.model_family_config import needs_no_think_directive
            if needs_no_think_directive(self.model_family):
                think = False

        backend_kwargs = {"think": think}
        if self._reasoning_effort is not None:
            backend_kwargs["reasoning_effort"] = self._reasoning_effort
        if self.num_ctx is not None:
            backend_kwargs["num_ctx"] = self.num_ctx
        if self._num_predict is not None:
            backend_kwargs["num_predict"] = self._num_predict
        if hasattr(self, '_runtime_kwargs') and self._runtime_kwargs:
            for k, v in self._runtime_kwargs.items():
                backend_kwargs[k] = v
        stops = self.model_config.stop_tokens if self.model_config else []
        if stops:
            backend_kwargs["stop"] = stops
        if self.tool_choice and self.tool_choice.type != ToolChoiceType.AUTO:
            backend_kwargs["tool_choice"] = self.tool_choice.to_dict()
        if self._response_format is not None:
            backend_kwargs["response_format"] = self._response_format
        backend_kwargs["truncation"] = self.truncation

        tools_for_backend = self.tools.all() if self.tools and len(self.tools) > 0 else None
        gen_temperature = self._temperature if self._temperature is not None else self.model_config.default_temperature
        gen_max_tokens = self._num_predict if self._num_predict is not None else self.model_config.default_max_tokens
        gen_top_p = self._top_p if self._top_p is not None else self.model_config.default_top_p

        # R06.57: Cap max_tokens to num_ctx/32 (same as non-streaming path)
        if self._num_predict is None and self.num_ctx and self.num_ctx > 0:
            capped = self.num_ctx // 32
            if gen_max_tokens > capped:
                gen_max_tokens = capped

        # Pick the streaming method. Order: OpenAI-compat (chat/completions
        # SSE) preferred because it carries tool_calls deltas. The native
        # Ollama generate_stream is text-only.
        stream_method = None
        if hasattr(self.backend, 'generate_completions_stream'):
            stream_method = 'openai_compat'
        elif hasattr(self.backend, 'generate_stream'):
            stream_method = 'native'

        # PERF-01 readability: print the "AgentKthx: " prefix once, before
        # the first content delta arrives. Tracked so subsequent iterations
        # of the agentic loop (after tool calls) don't re-print it — the
        # loop is one continuous answer from the user's perspective.
        # Reset at the start of each _run_core_streaming() call (new user
        # prompt) so the prefix appears on every new reply.
        _prefix_emitted = getattr(self, "_stream_prefix_emitted", False)

        # R06.56: reasoning is now displayed as a structured "reasoning:" panel
        # ABOVE the AgentKthx: prompt, not inline in dim-grey under the prefix.
        # This avoids duplicate reasoning display (the cmd_chat path used to
        # also print a "reasoning:" panel after the answer — now it's shown
        # once during streaming, before the answer).
        _reasoning_panel_started = False
        # Track whether we've emitted any reasoning line yet — used to add
        # the 4-space indent on the very first line of the panel (subsequent
        # lines get their indent from the "\n    " replacement below).
        _reasoning_first_line_emitted = False

        def _emit_reasoning_panel_header():
            """Emit the 'reasoning:' header once, before the first reasoning
            delta is printed. Subsequent reasoning deltas append to the panel."""
            nonlocal _reasoning_panel_started
            if not _reasoning_panel_started:
                sys.stdout.write(f"\033[90m  reasoning:\033[0m\n")
                sys.stdout.flush()
                _reasoning_panel_started = True

        def _indent_reasoning_delta(delta: str) -> str:
            """Indent a reasoning delta to match the non-streaming panel format
            (4 spaces under 'reasoning:').

            - On the first delta ever: prepend '    ' (4 spaces) so the first
              line is indented under the 'reasoning:' header.
            - For every delta: replace '\\n' with '\\n    ' so subsequent
              lines (mid-delta newlines) are also indented.
            """
            nonlocal _reasoning_first_line_emitted
            if not delta:
                return delta
            # Replace newlines with newline+4-spaces so each new line in
            # this delta is indented under the 'reasoning:' header.
            indented = delta.replace("\n", "\n    ")
            if not _reasoning_first_line_emitted:
                # First line ever — prepend the 4-space indent.
                indented = "    " + indented
                _reasoning_first_line_emitted = True
            return indented

        def _emit_prefix_once():
            nonlocal _prefix_emitted
            if not _prefix_emitted:
                # If we printed a reasoning panel above, add a newline
                # before the AgentKthx: prefix so they don't run together.
                if _reasoning_panel_started:
                    sys.stdout.write("\n")
                # Bright green to match the non-streaming "AgentKthx:" label.
                sys.stdout.write("\033[92mAgentKthx:\033[0m ")
                sys.stdout.flush()
                _prefix_emitted = True
                self._stream_prefix_emitted = True

        if stream_method is None:
            # Backend has no streaming — fall back to non-streaming and
            # print the result in one shot. Don't pretend to stream.
            if self.debug:
                print(f"  [Stream] backend has no streaming method — falling back to _generate()")
            response = self.backend.generate(
                model=self.model,
                messages=messages,
                tools=tools_for_backend,
                temperature=gen_temperature,
                max_tokens=gen_max_tokens,
                top_p=gen_top_p,
                **backend_kwargs,
            )
            # Print content as a single chunk (still gives the user feedback
            # that generation completed).
            content = response.get("content", "") or ""
            if content:
                _emit_prefix_once()
                sys.stdout.write(content)
                sys.stdout.flush()
                if not content.endswith("\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
            response["_finish_reason"] = response.get("finish_reason", "stop")
            return response

        if self.debug:
            print(f"  [Stream] using {stream_method} backend streaming")

        # ── Accumulators for SSE chunk merging ──────────────────────────
        # OpenAI streaming tool_calls arrive as a list of "delta" objects,
        # each carrying an index, optional id (first chunk only), optional
        # function.name (first chunk only), and function.arguments as a
        # partial JSON string that grows across subsequent chunks.
        content_acc = []
        reasoning_acc = []
        # tool_calls_acc[index] = {"id", "name", "arguments_str"}
        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None
        usage = {}
        try:
            if stream_method == 'openai_compat':
                stream_gen = self.backend.generate_completions_stream(
                    model=self.model,
                    messages=messages,
                    tools=tools_for_backend,
                    temperature=gen_temperature,
                    max_tokens=gen_max_tokens,
                    top_p=gen_top_p,
                    **backend_kwargs,
                )
                for chunk in stream_gen:
                    delta = chunk.get("delta", "") or ""
                    tc_delta = chunk.get("tool_calls")
                    fr = chunk.get("finish_reason")
                    if fr:
                        finish_reason = fr
                    # Capture usage from the final usage-only chunk
                    # (arrives when stream_options.include_usage=True)
                    chunk_usage = chunk.get("_usage")
                    if chunk_usage:
                        usage = chunk_usage
                    # Content delta — print immediately
                    if delta:
                        _emit_prefix_once()
                        content_acc.append(delta)
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                    # Reasoning delta — print with dim styling
                    reasoning_delta = ""
                    if isinstance(tc_delta, dict) and "reasoning_content" in tc_delta:
                        reasoning_delta = tc_delta["reasoning_content"] or ""
                    elif isinstance(chunk, dict) and chunk.get("reasoning_content"):
                        reasoning_delta = chunk["reasoning_content"]
                    if reasoning_delta:
                        # R06.56: reasoning is now shown as a structured
                        # "reasoning:" panel ABOVE the AgentKthx: prompt,
                        # not as inline dim-grey text under it. This avoids
                        # the duplicate reasoning display (cmd_chat used to
                        # print a "reasoning:" panel after the answer — now
                        # the panel is streamed first, before content).
                        _emit_reasoning_panel_header()
                        reasoning_acc.append(reasoning_delta)
                        # Indent each line under the "reasoning:" header
                        # (4 spaces, matching the non-streaming panel
                        # format in cmd_chat:1866-1870).
                        indented = _indent_reasoning_delta(reasoning_delta)
                        sys.stdout.write(f"\033[90m{indented}\033[0m")
                        sys.stdout.flush()
                    # Tool-call delta accumulation
                    if tc_delta:
                        # tc_delta is the raw OpenAI delta format:
                        # [{"index": 0, "id": "...", "function": {"name": "...", "arguments": "..."}}]
                        if isinstance(tc_delta, list):
                            for tc_d in tc_delta:
                                idx = tc_d.get("index", 0)
                                slot = tool_calls_acc.setdefault(idx, {
                                    "id": "", "name": "", "arguments_str": "",
                                })
                                if tc_d.get("id"):
                                    slot["id"] = tc_d["id"]
                                func = tc_d.get("function") or {}
                                if func.get("name"):
                                    slot["name"] = func["name"]
                                if func.get("arguments"):
                                    slot["arguments_str"] += func["arguments"]
                        elif isinstance(tc_delta, dict):
                            # Single tool call delta
                            idx = tc_delta.get("index", 0)
                            slot = tool_calls_acc.setdefault(idx, {
                                "id": "", "name": "", "arguments_str": "",
                            })
                            if tc_delta.get("id"):
                                slot["id"] = tc_delta["id"]
                            func = tc_delta.get("function") or {}
                            if isinstance(func, dict):
                                if func.get("name"):
                                    slot["name"] = func["name"]
                                if func.get("arguments"):
                                    slot["arguments_str"] += func["arguments"]
                            # Some backends stash reasoning_content on tool_calls dict
                            if "reasoning_content" in tc_delta:
                                rc = tc_delta.get("reasoning_content") or ""
                                if rc:
                                    reasoning_acc.append(rc)
                                    sys.stdout.write(f"\033[90m{rc}\033[0m")
                                    sys.stdout.flush()
            else:
                # native generate_stream — text only, no tool_calls in stream
                stream_gen = self.backend.generate_stream(
                    model=self.model,
                    messages=messages,
                    tools=tools_for_backend,
                    temperature=gen_temperature,
                    max_tokens=gen_max_tokens,
                    top_p=gen_top_p,
                    **backend_kwargs,
                )
                for chunk in stream_gen:
                    if isinstance(chunk, str):
                        _emit_prefix_once()
                        content_acc.append(chunk)
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                    elif isinstance(chunk, dict):
                        delta = chunk.get("delta", "") or chunk.get("content", "") or ""
                        if delta:
                            _emit_prefix_once()
                            content_acc.append(delta)
                            sys.stdout.write(delta)
                            sys.stdout.flush()
                        if chunk.get("finish_reason"):
                            finish_reason = chunk["finish_reason"]
        except KeyboardInterrupt:
            # User cancelled mid-stream. Close the stream generator
            # explicitly so the underlying HTTP connection is released
            # deterministically rather than waiting for GC. Without this,
            # the urllib response in the backend's _iter_sse_lines is
            # abandoned mid-iteration and may stay open until GC runs,
            # which can exhaust connection limits on long sessions with
            # many Ctrl+C interrupts. See ROB-05 (R06.57).
            try:
                if 'stream_gen' in locals() and stream_gen is not None:
                    stream_gen.close()
            except Exception:
                pass
            # Newline so the next prompt isn't on the same line
            sys.stdout.write("\n")
            sys.stdout.flush()
            return {
                "content": "".join(content_acc),
                "tool_calls": [],
                "usage": {},
                "reasoning_content": "".join(reasoning_acc),
                "_finish_reason": "cancelled",
                "_cancelled": True,
            }

        # End of stream — print a newline if content didn't end with one
        # so the next prompt / step summary appears on its own line.
        content_str = "".join(content_acc)
        if content_str and not content_str.endswith("\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()

        # Assemble tool_calls in index order, parsing the accumulated
        # arguments JSON string into a dict. If parsing fails (model emitted
        # malformed JSON across chunks), fall back to a raw wrapper so the
        # agent loop can surface the bad payload rather than crashing.
        assembled_tool_calls = []
        for idx in sorted(tool_calls_acc.keys()):
            slot = tool_calls_acc[idx]
            args_str = slot["arguments_str"]
            if not args_str:
                args = {}
            else:
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    # Surface the raw string so the agent loop / error
                    # recovery can teach the model about the format.
                    args = {"_raw_arguments": args_str}
            assembled_tool_calls.append({
                "id": slot["id"] or f"call_{idx}",
                "name": slot["name"],
                "arguments": args,
            })

        if self.debug:
            print(f"  [Stream] content: {content_str[:100]!r}")
            print(f"  [Stream] tool_calls assembled: {assembled_tool_calls}")
            print(f"  [Stream] finish_reason: {finish_reason}")

        return {
            "content": content_str,
            "tool_calls": assembled_tool_calls,
            "usage": usage,
            "reasoning_content": "".join(reasoning_acc),
            "_finish_reason": finish_reason or "stop",
        }

    def _run_core_streaming(self, prompt: str) -> AgentRun:
        """Streaming variant of ``_run_core()`` (PERF-01).

        Identical agentic loop (tool dispatch, error recovery, memory
        tracking, finish_reason handling, OpenResponses lifecycle), but
        each ``_generate()`` call is replaced with ``_generate_stream()``
        so content / reasoning deltas are printed to stdout as they
        arrive instead of being held until the full response is back.

        The returned ``AgentRun`` shape is identical to ``_run_core()``.

        Implementation note: this is intentionally a near-copy of
        ``_run_core()`` rather than a parameterized fork. The non-
        streaming path has years of bug fixes (R06.52 loop resilience,
        pairing-safe memory pruning, error tracker integration) that
        would be risky to thread through a single parameterized
        implementation. The duplication is the price of preserving
        those invariants — see audit PERF-01 for the trade-off.
        """
        start_time = time.time()
        steps = []
        total_tokens = 0
        tool_calls = 0
        successful_results = []

        # PERF-01 readability: reset the "AgentKthx:" prefix tracker for
        # each new user prompt. Inside a single _run_core_streaming() call
        # (which may span multiple agentic-loop iterations due to tool
        # calls), the prefix is emitted only once — before the first
        # content/reasoning delta of the first iteration. On the next user
        # prompt we want it to appear again.
        self._stream_prefix_emitted = False

        response = Response(
            model=self.model,
            status=ResponseStatus.QUEUED,
            tool_choice=self.tool_choice,
            allowed_tools=self._allowed_tools or [],
        )
        response.mark_in_progress()

        self.memory.add("user", prompt)
        user_item = create_message_item("user", prompt)
        response.input.append(user_item)

        if self.debug:
            print(f"\n[AgentKthx] Model: {self.model}")
            print(f"[AgentKthx] Backend: {self.backend.base_url}")
            print(f"[AgentKthx] Prompt: {prompt}\n")

        _expecting_final_answer = False
        _last_successful_result = None
        _last_tool_name = None
        _terminated = False
        self._error_tracker.reset()
        # Reset running token totals for this run
        self._running_tokens_in = 0
        self._running_tokens_out = 0

        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Step {step_num + 1}] (streaming)")

            # ROB-06: Check if memory needs compaction before generating.
            # This prevents context-length 400s on long agentic runs by
            # compacting older messages when estimated token usage exceeds
            # the compaction threshold (default 85% of num_ctx).
            self._check_compaction()

            # Streaming generate with API resilience retry.
            #
            # MAINT-04 Phase 1 (R06.59): the retry loop now lives in
            # ``_generate_with_retry`` so both _run_core and
            # _run_core_streaming share it. Streaming path passes
            # enable_compaction_recovery=True — the context-length-400
            # compaction handler (ROB-06 / R06.58) runs only here, because
            # streaming is the path that runs long enough to hit
            # input-too-large conditions during multi-tool agentic runs.
            gen_response, _terminated = self._generate_with_retry(
                self._generate_stream,
                step_num=step_num,
                steps=steps,
                response=response,
                enable_compaction_recovery=True,
            )
            if _terminated or gen_response is None:
                break

            content = gen_response.get("content", "")
            native_tool_calls = gen_response.get("tool_calls", [])
            tokens = gen_response.get("usage", {}).get("total_tokens", 0)
            total_tokens += tokens

            # Token tracking. The footer computes ctx% from
            # (_running_tokens_in + _running_tokens_out) / num_ctx, so these
            # MUST reflect the CURRENT memory size, not a cumulative total.
            #
            # R06.58 BUGFIX: previously the fallback path did
            #   ``self._running_tokens_in += _est_in`` every step, where
            # ``_est_in`` was the size of the ENTIRE history. After N steps
            # the running total was N× the actual memory size, so ctx%
            # climbed to 100% and stayed there forever (even after a
            # successful compaction reset, the very next step re-added the
            # whole history again). This made users report "compaction not
            # firing when ctx is 100%" — the display was lying, not the
            # compaction logic.
            #
            # Fix: always treat _running_tokens_in/out as a SNAPSHOT of the
            # current memory state. If the provider returns real usage we
            # still snapshot from memory (provider usage is per-request, so
            # it already reflects the post-compaction state for input).
            _est_in_chars = 0
            for msg in self.memory:
                c = getattr(msg, 'content', '') or ''
                _est_in_chars += len(c)
                tc = getattr(msg, 'tool_calls', None)
                if tc:
                    _est_in_chars += len(json.dumps(tc, ensure_ascii=False))
            _est_out_chars = len(content) + sum(
                len(json.dumps(tc, ensure_ascii=False))
                for tc in native_tool_calls
            )
            # If the provider returned real usage, prefer it for the OUTPUT
            # half (it's accurate for this turn's generated tokens). For
            # INPUT we always snapshot from memory — provider usage on
            # streaming :free models is often 0 or unreliable, and memory
            # size is what actually matters for the next compaction check.
            self._running_tokens_in = _est_in_chars // 4
            if tokens and tokens > 0:
                # Provider usage is prompt+completion combined; use the
                # completion portion if we can split it, else fall back to
                # the estimate. We add the new output tokens ON TOP of the
                # input snapshot so the footer reflects both halves of
                # the current in-memory state.
                self._running_tokens_out = _est_out_chars // 4
            else:
                self._running_tokens_out = _est_out_chars // 4

            # Refresh the CLI footer if a callback is registered
            if getattr(self, '_on_step_callback', None):
                try:
                    self._on_step_callback(
                        step_num + 1,
                        self._running_tokens_in,
                        self._running_tokens_out,
                    )
                except Exception:
                    pass  # footer update failure must not break the run

            reasoning_content = gen_response.get("reasoning_content", "") or ""

            if gen_response.get("_cancelled"):
                if self.debug:
                    print(f"  [Cancelled] Generation interrupted by user")
                steps.append(StepResult(
                    type=StepResultType.ERROR,
                    error="Cancelled by user",
                    tokens_used=tokens,
                ))
                response.mark_cancelled(debug=self.debug)
                break

            # MAINT-04 Phase 2 (R06.59): finish_reason handling now shared
            # with _run_core via ``_handle_finish_reason``. Returns True
            # if terminal (length / content_filter).
            if self._handle_finish_reason(gen_response, steps, response):
                break

            if self.debug:
                print(f"  Content: {content[:200] if content else '(empty)'}...")
                print(f"  Native tool calls: {native_tool_calls}")

            # ---- Process tool calls (native or ReAct) ----
            # MAINT-04 Phase 3b (R06.59): parsing now lives in
            # ``_parse_tool_calls`` so both paths produce the same
            # tool_calls_found shape.
            tool_calls_found = self._parse_tool_calls(
                content, native_tool_calls, response,
            )

            if tool_calls_found:
                # Final Answer enforcement (same as non-streaming path).
                # MAINT-04 Phase 4a (R06.59): now via shared _enforce_final_answer.
                if _expecting_final_answer and _last_successful_result is not None:
                    return self._enforce_final_answer(
                        _last_successful_result=_last_successful_result,
                        tokens=tokens,
                        reasoning_content=reasoning_content,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                    )

                pending_final_answer = None
                if native_tool_calls:
                    self.memory.add_tool_call("assistant", content, native_tool_calls)
                else:
                    self.memory.add("assistant", content)

                for tc in tool_calls_found:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    tool_call_id = tc.get("id", "") or ""

                    if tc.get("final_answer"):
                        pending_final_answer = tc["final_answer"]

                    if self._allowed_tools and tool_name not in self._allowed_tools:
                        error_msg = f"Tool '{tool_name}' not in allowed_tools: {self._allowed_tools}"
                        if native_tool_calls:
                            self.memory.add_tool_result(
                                tool_call_id=tool_call_id,
                                name=tool_name,
                                content=f"Error: {error_msg}",
                            )
                        else:
                            self.memory.add("user", f"Observation: Error: {error_msg}")
                        continue

                    # MAINT-04 Phase 4b (R06.59): now via shared _handle_blocked_tool_call.
                    if self._error_tracker.should_block_repeat(tool_name, tool_args):
                        _blocked, _term = self._handle_blocked_tool_call(
                            tool_name=tool_name,
                            tool_args=tool_args,
                            tool_call_id=tool_call_id,
                            native_tool_calls=native_tool_calls,
                            step_num=step_num,
                            tool_calls=tool_calls,
                            tokens=tokens,
                            steps=steps,
                            response=response,
                        )
                        if _term:
                            _terminated = True
                            break
                        continue

                    fc_item = create_function_call_item(tool_name, tool_args, tool_call_id)
                    fc_item.status = ItemStatus.IN_PROGRESS
                    response.add_output_item(fc_item, debug=not self._is_comp_mode and self.debug)

                    try:
                        result = self._execute_tool(tool_name, tool_args, prompt)
                    except KeyboardInterrupt:
                        fc_item.status = ItemStatus.FAILED
                        response.mark_cancelled(debug=self.debug)
                        steps.append(StepResult(
                            type=StepResultType.ERROR,
                            error="Cancelled by user during tool execution",
                        ))
                        break

                    # R06.55: Print tool call + result inline during streaming
                    # so the user sees progress as it happens (not just at
                    # the post-run summary). Matches the CLI's _print_agent_steps
                    # format: [N] tool name {args} → result
                    try:
                        args_str = json.dumps(tool_args, ensure_ascii=False)
                    except (TypeError, ValueError):
                        args_str = str(tool_args)
                    if len(args_str) > 120:
                        args_str = args_str[:117] + "..."
                    result_str = str(result)
                    if len(result_str) > 200:
                        result_str = result_str[:197] + "..."
                    sys.stdout.write(
                        f"\n  \033[90m[{tool_calls + 1}]\033[0m "
                        f"\033[36mtool\033[0m "
                        f"\033[33m{tool_name}\033[0m "
                        f"\033[90m{args_str}\033[0m\n"
                    )
                    if result_str:
                        sys.stdout.write(
                            f"      \033[90m\u2192 {result_str}\033[0m\n"
                        )
                    sys.stdout.flush()

                    tool_calls += 1
                    is_error = is_error_result(str(result))
                    if is_error:
                        self._error_tracker.record_failure(
                            tool_name=tool_name,
                            error_message=str(result),
                            step=step_num,
                            arguments=tool_args
                        )
                        if self._error_tracker.should_terminate():
                            fc_item.status = ItemStatus.FAILED
                            term_msg = (
                                f"Error: run terminated after "
                                f"{self._error_tracker.consecutive_all} consecutive steps in which "
                                f"every tool call failed. Review the observations above and "
                                f"adjust the approach."
                            )
                            if native_tool_calls:
                                self.memory.add_tool_result(
                                    tool_call_id=tool_call_id or f"terminated_{step_num}_{tool_calls}",
                                    name=tool_name,
                                    content=term_msg,
                                )
                            else:
                                self.memory.add("user", f"Observation: {term_msg}")
                            steps.append(StepResult(
                                type=StepResultType.ERROR,
                                error=term_msg,
                                tool_call=ToolCall(name=tool_name, arguments=tool_args),
                                tokens_used=tokens,
                            ))
                            response.mark_failed({"message": "Too many tool failures", "type": "error_recovery"})
                            _terminated = True
                            break
                    else:
                        self._error_tracker.record_success(tool_name)

                    fc_item.status = ItemStatus.COMPLETED
                    fco_item = create_function_call_output(fc_item.call_id, str(result))
                    response.add_output_item(fco_item, debug=not self._is_comp_mode and self.debug)

                    if native_tool_calls:
                        self.memory.add_tool_result(
                            tool_call_id=fc_item.call_id,
                            name=tool_name,
                            content=str(result),
                        )
                        if is_error and self._retry_on_error:
                            retry_msg = build_retry_context(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                tracker=self._error_tracker,
                                max_tool_retries=self._max_tool_retries,
                            )
                            if retry_msg:
                                if self.debug:
                                    print(f"  [Retry Context] Adding retry hint for native tool call: {tool_name}")
                                self.memory.add("user", retry_msg)
                    else:
                        observation_msg = build_enhanced_observation(
                            tool_name=tool_name,
                            result=str(result),
                            tracker=self._error_tracker,
                            available_tools=self.tools.names(),
                            is_error=is_error,
                            retry_on_error=self._retry_on_error,
                            tool_args=tool_args,
                        )
                        if is_error:
                            _expecting_final_answer = False
                            _last_tool_name = None
                        else:
                            from .core.error_recovery import _is_simple_result
                            if _is_simple_result(str(result), tool_name):
                                _expecting_final_answer = True
                                _last_successful_result = str(result)
                                _last_tool_name = tool_name
                            else:
                                _expecting_final_answer = False
                                _last_tool_name = None
                        self.memory.add("user", observation_msg)

                    if not is_error:
                        successful_results.append(f"{tool_name}: {result}")

                    steps.append(StepResult(
                        type=StepResultType.TOOL_CALL,
                        content=content,
                        tool_call=ToolCall(name=tool_name, arguments=tool_args),
                        tool_result=result,
                        tokens_used=tokens,
                    ))

                    # R06.58 BUGFIX: check compaction BETWEEN tool calls
                    # within a single assistant message. When the LLM emits
                    # multiple tool calls in one response (e.g., "read A,
                    # read B, read C"), each tool result is appended to
                    # memory inside this for-loop. Previously compaction
                    # only fired at the TOP of the next step — so a single
                    # assistant message with 5 large tool results could push
                    # memory well past the context window before compaction
                    # ever noticed. Now we check after each tool result is
                    # committed, so memory stays bounded mid-step too.
                    if len(tool_calls_found) > 1:
                        self._check_compaction()

                if _terminated:
                    # MAINT-04 Phase 3c (R06.59): finalize via shared helper.
                    return self._finalize_run(
                        final_answer="",
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                        success=False,
                        mark_completed=False,
                    )

                if pending_final_answer:
                    msg_item = create_message_item("assistant", pending_final_answer)
                    msg_item.status = ItemStatus.COMPLETED
                    response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)
                    steps.append(StepResult(
                        type=StepResultType.FINAL_ANSWER,
                        content=pending_final_answer,
                        tokens_used=tokens,
                        reasoning_content=reasoning_content,
                    ))
                    # MAINT-04 Phase 3c (R06.59): finalize via shared helper.
                    return self._finalize_run(
                        final_answer=pending_final_answer,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=tool_calls,
                        response=response,
                        success=True,
                    )

                continue

            # ---- Check for Final Answer (ReAct format) ----
            if self._parser.is_final_answer(content):
                # MAINT-04 Phase 3a (R06.59): shared _check_tool_choice_required.
                needs_tool, rejection_reason = self._check_tool_choice_required(tool_calls)
                if needs_tool:
                    # MAINT-04 Phase 4c (R06.59): now via shared _reject_for_tool_choice.
                    self._reject_for_tool_choice(
                        content,
                        is_final_answer_context=True,
                        include_format_hint=False,
                    )
                    continue

                answer = self._parser.extract_final_answer(content)
                _expecting_final_answer = False
                msg_item = create_message_item("assistant", answer)
                msg_item.status = ItemStatus.COMPLETED
                response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)
                steps.append(StepResult(
                    type=StepResultType.FINAL_ANSWER,
                    content=answer,
                    tokens_used=tokens,
                    reasoning_content=reasoning_content,
                ))
                break

            # ---- No tool call, no final answer ----
            # MAINT-04 Phase 3a (R06.59): shared _check_tool_choice_required.
            needs_tool, rejection_reason = self._check_tool_choice_required(tool_calls)
            if needs_tool:
                # MAINT-04 Phase 4c (R06.59): now via shared _reject_for_tool_choice.
                self._reject_for_tool_choice(
                    content,
                    is_final_answer_context=False,
                    include_format_hint=False,
                )
                continue

            # MAINT-04 Phase 4a (R06.59): now via shared _enforce_final_answer.
            if _expecting_final_answer and _last_successful_result is not None:
                return self._enforce_final_answer(
                    _last_successful_result=_last_successful_result,
                    tokens=tokens,
                    reasoning_content=reasoning_content,
                    steps=steps,
                    total_tokens=total_tokens,
                    start_time=start_time,
                    tool_calls=tool_calls,
                    response=response,
                )

            # Accept as final answer
            if content:
                msg_item = create_message_item("assistant", content)
                msg_item.status = ItemStatus.COMPLETED
                response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)
            steps.append(StepResult(
                type=StepResultType.FINAL_ANSWER,
                content=content,
                tokens_used=tokens,
                reasoning_content=reasoning_content,
            ))
            self.memory.add("assistant", content)
            break
        else:
            response.mark_incomplete()
            steps.append(StepResult(
                type=StepResultType.MAX_STEPS,
                content="Maximum steps reached without final answer",
            ))

        # MAINT-04 Phase 3c (R06.59): finalize via shared helper.
        # mark_completed=False because we already marked it above.
        return self._finalize_run(
            final_answer=self._extract_last_final_answer(steps),
            steps=steps,
            total_tokens=total_tokens,
            start_time=start_time,
            tool_calls=tool_calls,
            response=response,
            success=bool(self._extract_last_final_answer(steps)),
            mark_completed=False,
        )

    def _execute_tool(self, name: str, args: dict, user_prompt: str = "") -> Any:
        """
        Execute a tool by name with arguments.

        OpenResponses: Tool execution is straightforward - no synthesis.
        Arguments must come from the model.

        If the tool is marked dangerous=True and a confirm_dangerous
        callback is set, the callback is invoked before execution.
        Returning False from the callback blocks the tool call.
        """
        tool = self.tools.get(name)

        if tool is None:
            return f"Error: Unknown tool '{name}'. Available tools: {self.tools.names()}"

        # Confirmation gate for dangerous tools
        if getattr(tool, 'dangerous', False) and self._confirm_dangerous is not None:
            if not self._confirm_dangerous(name, args):
                return (
                    f"Tool '{name}' was blocked by user confirmation. "
                    f"The tool is marked as dangerous and was not approved."
                )

        # Normalize arguments with tool-specific aliases
        expected_params = [p.name for p in tool.params]
        
        from .core.helpers import normalize_args
        normalized_args = normalize_args(args, expected_params, tool_name=name)

        # R06.52: numeric-string coercion. Small models frequently emit
        # numeric arguments as strings ("depth": "3"); tools that expect
        # int/float then raise TypeError and the call is guaranteed to fail.
        for p in tool.params:
            if p.name not in normalized_args:
                continue
            val = normalized_args[p.name]
            if isinstance(val, str) and p.type in ("integer", "number", "float"):
                try:
                    normalized_args[p.name] = int(val) if p.type == "integer" else float(val)
                except (ValueError, TypeError):
                    pass  # leave as-is; the tool will report the real problem

        # R06.52: hallucinated-parameter stripping. Arguments that are not in
        # the tool's schema are removed before execution instead of causing a
        # guaranteed TypeError. The model is told what was ignored so it can
        # correct future calls.
        ignored_params = [k for k in list(normalized_args.keys()) if k not in expected_params]
        for k in ignored_params:
            del normalized_args[k]

        try:
            result = tool.execute(**normalized_args)

        except TypeError as e:
            return f"Error: {e}"

        except KeyboardInterrupt:
            raise  # Let the caller (run loop or CLI) handle cancellation

        except Exception as e:
            return f"Error executing tool: {e}"

        if ignored_params and isinstance(result, str):
            result = (
                f"{result}\n\n"
                f"(Note: ignored unknown parameter(s) {', '.join(ignored_params)} — "
                f"not part of the '{name}' tool schema. Valid parameters: "
                f"{', '.join(expected_params) if expected_params else '(none)'}.)"
            )
        return result

    def chat(self, message: str) -> str:
        """
        Send a message in chat mode (maintains conversation).

        Args:
            message: User message

        Returns:
            Agent response
        """
        result = self.run(message)
        return result.final_answer

    def clear_memory(self) -> None:
        """Clear conversation memory."""
        self.memory.clear()
        self.memory.add("system", self._custom_system_prompt)

    def add_tool(self, tool: Tool) -> None:
        """Add a tool to the registry."""
        self.tools.register_tool(tool)
        self._parser = ToolParser(self.tools.names())
        # Rebuild system prompt with new tool
        has_tools = len(self.tools) > 0
        if has_tools:
            from .soul.loader import _build_tool_section
            tool_section = _build_tool_section(self.tools.all(), native_tools=self._is_comp_mode)
            # Find and replace tool section in system prompt
            if "### Tool Reference" in self._custom_system_prompt:
                # Replace existing tool section
                import re
                pattern = r'### Tool Reference.*?(?=\n## |\n\*\*CRITICAL RULE|\Z)'
                self._custom_system_prompt = re.sub(pattern, tool_section.rstrip(), self._custom_system_prompt, flags=re.DOTALL)
            else:
                self._custom_system_prompt = self._custom_system_prompt + "\n\n" + tool_section
        # Update memory
        self.memory.clear()
        self.memory.add("system", self._custom_system_prompt)

    def get_response(self, response_id: str) -> Response | None:
        """Get a previous response by ID (for previous_response_id support)."""
        return self._response_history.get(response_id)

    def __repr__(self) -> str:
        return f"Agent(model={self.model}, tools={len(self.tools)}, tool_choice={self.tool_choice.type.value})"


__all__ = ["Agent"]