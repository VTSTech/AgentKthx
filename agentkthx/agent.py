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

import time
from typing import Any, Generator, Optional

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
            **kwargs: Additional configuration (persistent, session_id, memory_db, confirm_dangerous, response_format)
        """
        # Ensure max_steps is never None (defensive fix)
        if max_steps is None:
            max_steps = 25  # Default value
        
        self.model = model
        self.max_steps = max_steps
        self.debug = debug

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
        Run the agent on a prompt.

        This method implements the agentic loop following OpenResponses specification:
        1. Model samples from input
        2. If tool call: execute tool, return observation, continue
        3. If no tool call: return final output items

        IMPORTANT: No fallbacks that bypass the AI model are used.
        All tool calls must come from the model itself.

        Args:
            prompt: User prompt
            stream: Whether to stream output

        Returns:
            AgentRun with final answer and execution details
        """
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
        
        # Reset error tracker for new run
        self._error_tracker.reset()
        
        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Step {step_num + 1}]")

            # Generate response from model
            try:
                gen_response = self._generate()
            except Exception as e:
                if self.debug:
                    print(f"  ERROR: {e}")
                steps.append(StepResult(
                    type=StepResultType.ERROR,
                    error=str(e),
                ))
                response.mark_failed({"message": str(e), "type": "model_error"})
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

            # OpenResponses: Handle finish_reason from backend
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
                break
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
                break

            if self.debug:
                print(f"  Content: {content[:200] if content else '(empty)'}...")
                print(f"  Native tool calls: {native_tool_calls}")

            # ---- Process tool calls (native or ReAct) ----
            tool_calls_found = []

            # Check for native tool calls from backend
            if native_tool_calls:
                for tc in native_tool_calls:
                    tool_calls_found.append({
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", {}),
                        "id": tc.get("id", ""),
                    })

            # Check for tool calls in model output (ReAct, JSON, or XML format)
            elif content:
                parsed_calls = self._parser.parse(content)
                if self.debug and parsed_calls and not self._is_comp_mode:
                    print(f"  [OpenResponses] Tool calls detected: {len(parsed_calls)}")
                for call in parsed_calls:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] Parsed: name={call.name}, args={call.arguments}, final_answer={call.final_answer}")
                    
                    # OpenResponses: Capture ReasoningItem if thought is present
                    if hasattr(call, 'thought') and call.thought:
                        if self.debug and not self._is_comp_mode:
                            print(f"  [OpenResponses] Captured thought for ReasoningItem: {call.thought[:50]}...")
                        reasoning_item = ReasoningItem(
                            content=[OutputText(text=call.thought)]
                        )
                        reasoning_item.status = ItemStatus.COMPLETED
                        response.add_output_item(reasoning_item, debug=not self._is_comp_mode and self.debug)
                    
                    tool_calls_found.append({
                        "name": call.name,
                        "arguments": call.arguments,
                        "id": "",
                        "final_answer": call.final_answer,  # May be None
                    })

            # Execute tool calls if found
            if tool_calls_found:
                # OpenResponses Enhancement: Final Answer Enforcement
                # If we asked for Final Answer but model tried to call tools again,
                # intercept and force Final Answer extraction
                if _expecting_final_answer and _last_successful_result is not None:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] FINAL ANSWWER ENFORCEMENT: Model tried to call tools instead of Final Answer")
                        print(f"  [OpenResponses] Forcing Final Answer from last result: {_last_successful_result}")
                    
                    # Force Final Answer
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
                    
                    # Mark response as completed
                    if response.status == ResponseStatus.IN_PROGRESS:
                        response.mark_completed()
                    
                    # Store response for previous_response_id support
                    self._response_history[response.id] = response
                    response.usage["total_tokens"] = total_tokens
                    
                    total_ms = (time.time() - start_time) * 1000
                    
                    return AgentRun(
                        final_answer=final_answer,
                        steps=steps,
                        total_tokens=total_tokens,
                        total_ms=total_ms,
                        tool_calls=tool_calls,
                        success=True,
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
                                print(f"  [ErrorRecovery] Terminating: total failures ({self._error_tracker.total_failures}) >= max ({self._error_tracker.max_total_failures})")
                            fc_item.status = ItemStatus.FAILED
                            response.mark_failed({"message": "Too many tool failures", "type": "error_recovery"})
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
                    
                    # Mark response as completed
                    if response.status == ResponseStatus.IN_PROGRESS:
                        response.mark_completed()
                    
                    # Get final answer
                    final_answer = pending_final_answer
                    
                    # Store response for previous_response_id support
                    self._response_history[response.id] = response
                    response.usage["total_tokens"] = total_tokens
                    
                    total_ms = (time.time() - start_time) * 1000
                    
                    return AgentRun(
                        final_answer=final_answer,
                        steps=steps,
                        total_tokens=total_tokens,
                        total_ms=total_ms,
                        tool_calls=tool_calls,
                        success=True,
                    )

                # Continue the agentic loop
                continue

            # ---- Check for Final Answer ----
            # The model explicitly signals completion with "Final Answer:"
            if self._parser.is_final_answer(content):
                # OpenResponses: Check tool_choice enforcement
                needs_tool = False
                rejection_reason = ""
                
                if self.tool_choice.type == ToolChoiceType.REQUIRED and tool_calls == 0:
                    needs_tool = True
                    rejection_reason = "tool_choice='required' but no tool was called"
                elif self.tool_choice.type == ToolChoiceType.SPECIFIC and tool_calls == 0:
                    needs_tool = True
                    rejection_reason = f"tool_choice requires '{self.tool_choice.name}' but no tool was called"
                
                if needs_tool:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                        print(f"  [OpenResponses] Enforcing tool requirement...")
                    # Tell model to use tools
                    self.memory.add("assistant", content)
                    if self.tool_choice.type == ToolChoiceType.SPECIFIC:
                        self.memory.add("user", f"You must use the '{self.tool_choice.name}' tool before providing a final answer. Use the Action/Action Input format.")
                    else:
                        self.memory.add("user", "You must use at least one tool before providing a final answer. Use the Action/Action Input format to call a tool.")
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
            # Check tool_choice enforcement before accepting
            needs_tool = False
            rejection_reason = ""
            
            if self.tool_choice.type == ToolChoiceType.REQUIRED and tool_calls == 0:
                needs_tool = True
                rejection_reason = "tool_choice='required' but no tool was called"
            elif self.tool_choice.type == ToolChoiceType.SPECIFIC and tool_calls == 0:
                needs_tool = True
                rejection_reason = f"tool_choice requires '{self.tool_choice.name}' but no tool was called"
            
            if needs_tool:
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                    print(f"  [OpenResponses] Enforcing tool requirement...")
                # Tell model to use tools
                self.memory.add("assistant", content)
                if self.tool_choice.type == ToolChoiceType.SPECIFIC:
                    self.memory.add("user", f"You must use the '{self.tool_choice.name}' tool. Use the Action/Action Input format.")
                else:
                    self.memory.add("user", "You must use at least one tool. Use the Action/Action Input format to call a tool.")
                continue
            
            # OpenResponses Enhancement: Final Answer Enforcement
            # If we were expecting Final Answer but model responded without "Final Answer:" format,
            # use the last successful result instead of accepting the model's potentially wrong answer
            if _expecting_final_answer and _last_successful_result is not None:
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] FINAL ANSWWER ENFORCEMENT: Model responded without Final Answer format")
                    print(f"  [OpenResponses] Using last successful result: {_last_successful_result}")
                
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
                
                # Mark response as completed
                if response.status == ResponseStatus.IN_PROGRESS:
                    response.mark_completed()
                
                # Store response for previous_response_id support
                self._response_history[response.id] = response
                response.usage["total_tokens"] = total_tokens
                
                total_ms = (time.time() - start_time) * 1000
                
                return AgentRun(
                    final_answer=final_answer,
                    steps=steps,
                    total_tokens=total_tokens,
                    total_ms=total_ms,
                    tool_calls=tool_calls,
                    success=True,
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

        # Store response for previous_response_id support
        self._response_history[response.id] = response

        # Get final answer
        final_answer = ""
        for step in reversed(steps):
            if step.type == StepResultType.FINAL_ANSWER:
                final_answer = step.content or ""
                break

        # Update usage in response
        response.usage["total_tokens"] = total_tokens

        return AgentRun(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            total_ms=total_ms,
            tool_calls=tool_calls,
            success=bool(final_answer),
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

        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Stream Step {step_num + 1}]")

            # Collect the full streamed response
            full_content = ""

            # Stream model response, collecting content for tool-call detection
            try:
                for chunk in self._generate_stream_chunks(prompt):
                    full_content += chunk
            except Exception as e:
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

        try:
            return tool.execute(**normalized_args)

        except TypeError as e:
            return f"Error: {e}"

        except KeyboardInterrupt:
            raise  # Let the caller (run loop or CLI) handle cancellation

        except Exception as e:
            return f"Error executing tool: {e}"

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