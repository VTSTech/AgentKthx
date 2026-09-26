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
from typing import Callable, Optional

from .core.models import AgentRun, StepResult, Tool, ToolCall
from .core.types import StepResultType
from .core.tool_parse import ToolParser
from .core.api_resilience import (
    is_transient_api_error,
    backoff_delay,
    describe_wait,
    describe_terminal,
)
from .core.openresponses import (
    Response, ResponseStatus, ItemStatus,
    ToolChoiceType,
    ReasoningItem,
    OutputText,
    create_message_item,
)
from .core.compaction import CompactionMixin
from .core.agent_setup import AgentSetupMixin
from .core.tool_execution import ToolExecutionMixin
from .core.streaming import StreamingMixin
from .core.agentic_loop import AgenticLoopMixin, LoopCallbacks


class Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin):
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

        R07.00 Phase 5: the loop body now lives in
        ``AgenticLoopMixin._run_loop_iteration`` (core/agentic_loop.py);
        this is the non-streaming thin wrapper.

        Args:
            prompt: User prompt
            stream: When True, delegate to ``_run_core_streaming`` which
                prints model output chunks to stdout as they arrive (PERF-01).
                The returned ``AgentRun`` shape is identical to the non-
                streaming path; only the display behavior differs.

        Returns:
            AgentRun with final answer and execution details
        """
        # PERF-01: streaming path. Delegates to the streaming wrapper
        # (same AgentRun shape; only the display behavior differs).
        if stream:
            return self._run_core_streaming(prompt)

        return self._run_loop_iteration(
            prompt,
            self._generate,
            enable_compaction_recovery=False,
            callbacks=LoopCallbacks(
                # Non-streaming rejection messages include the ReAct
                # format hint (accidental variation preserved verbatim
                # since R06.58 Phase 4c).
                include_format_hint=True,
                # Non-streaming marks the OpenResponses Response COMPLETED
                # at end-of-run (with debug prints); streaming never did.
                mark_response_completed=True,
            ),
        )

    def _run_core_streaming(self, prompt: str) -> AgentRun:
        """Streaming variant of ``_run_core()`` (PERF-01).

        Identical agentic loop semantics (tool dispatch, error recovery,
        memory tracking, finish_reason handling, OpenResponses lifecycle),
        but each ``_generate()`` call is replaced with ``_generate_stream()``
        so content / reasoning deltas are printed to stdout as they
        arrive instead of being held until the full response is back.

        The returned ``AgentRun`` shape is identical to ``_run_core()``.

        R07.00 Phase 5: the duplicated loop body is GONE — this is now a
        thin wrapper over ``AgenticLoopMixin._run_loop_iteration`` with the
        streaming behaviors supplied as ``LoopCallbacks`` hooks. The old
        implementation note ("intentionally a near-copy of _run_core()")
        is obsolete; see core/agentic_loop.py for the unified loop.
        """
        # PERF-01 readability: reset the "AgentKthx:" prefix tracker for
        # each new user prompt. Inside a single run (which may span
        # multiple agentic-loop iterations due to tool calls), the prefix
        # is emitted only once — before the first content/reasoning delta
        # of the first iteration. On the next user prompt we want it to
        # appear again.
        self._stream_prefix_emitted = False
        # Reset running token totals for this run
        self._running_tokens_in = 0
        self._running_tokens_out = 0

        def _on_step_start(step_num: int) -> None:
            # ROB-06: Check if memory needs compaction before generating.
            # This prevents context-length 400s on long agentic runs by
            # compacting older messages when estimated token usage exceeds
            # the compaction threshold (default 85% of num_ctx).
            self._check_compaction()

        def _on_generated(step_num: int, gen_response: dict) -> None:
            # Token tracking (CompactionMixin._update_running_tokens —
            # snapshot semantics, never cumulative; see core/compaction.py
            # for the full R06.58 bugfix history).
            content = gen_response.get("content", "")
            native_tool_calls = gen_response.get("tool_calls", [])
            tokens = gen_response.get("usage", {}).get("total_tokens", 0)
            self._update_running_tokens(content, native_tool_calls, tokens)
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

        def _on_tool_executed(call_count: int, tool_name: str,
                              tool_args: dict, result) -> None:
            # R06.55: Print tool call + result inline during streaming
            # so the user sees progress as it happens (not just at the
            # post-run summary). Matches the CLI's _print_agent_steps
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
                f"\n  \033[90m[{call_count + 1}]\033[0m "
                f"\033[36mtool\033[0m "
                f"\033[33m{tool_name}\033[0m "
                f"\033[90m{args_str}\033[0m\n"
            )
            if result_str:
                sys.stdout.write(
                    f"      \033[90m\u2192 {result_str}\033[0m\n"
                )
            sys.stdout.flush()

        def _on_tool_result_committed(calls_this_step: int) -> None:
            # R06.58 BUGFIX: check compaction BETWEEN tool calls within a
            # single assistant message. When the LLM emits multiple tool
            # calls in one response, each tool result is appended to memory
            # inside the dispatch loop — compaction must keep memory bounded
            # mid-step too, not just at the top of the next step.
            if calls_this_step > 1:
                self._check_compaction()

        return self._run_loop_iteration(
            prompt,
            self._generate_stream,
            enable_compaction_recovery=True,
            callbacks=LoopCallbacks(
                on_step_start=_on_step_start,
                on_generated=_on_generated,
                on_tool_executed=_on_tool_executed,
                on_tool_result_committed=_on_tool_result_committed,
                include_format_hint=False,
                mark_response_completed=False,
            ),
        )




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

    def register_tool(self, tool: Tool) -> None:
        """Register a tool without clearing conversation memory.

        ROB-04 (R07.05): the old ``add_tool()`` cleared all conversation
        memory after registering the tool (because the system prompt
        includes the tool section and needs rebuilding). This was a
        footgun for third-party code that called ``agent.add_tool(tool)``
        mid-session — all conversation history was silently destroyed.

        This new method registers the tool in the registry AND rebuilds
        the system prompt (so the model sees the new tool's schema),
        but does NOT clear memory. The conversation continues with the
        updated tool set.

        Use ``rebuild_system_prompt()`` explicitly if you need the old
        clear-and-rebuild behavior (rare — mostly for soul swaps).
        """
        self.tools.register_tool(tool)
        self._parser = ToolParser(self.tools.names())
        self._rebuild_system_prompt_with_tools()

    def _rebuild_system_prompt_with_tools(self) -> None:
        """Rebuild the system prompt to include the current tool set.

        ROB-04 (R07.05): extracted from the old ``add_tool()`` so it can
        be called independently of the registry mutation. Does NOT clear
        memory — the caller decides whether to clear.
        """
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

    def rebuild_system_prompt(self) -> None:
        """Rebuild the system prompt AND clear conversation memory.

        ROB-04 (R07.05): this is the explicit "clear and rebuild" method.
        Use when you want the old ``add_tool()`` behavior — e.g. after
        swapping souls or making a breaking change to the tool set that
        invalidates prior conversation context.

        For most mid-session tool additions, use ``register_tool()``
        instead — it rebuilds the prompt without destroying history.
        """
        self._rebuild_system_prompt_with_tools()
        self.memory.clear()
        self.memory.add("system", self._custom_system_prompt)

    def add_tool(self, tool: Tool) -> None:
        """Add a tool to the registry.

        .. deprecated:: R07.05
            This method clears all conversation memory after registering
            the tool. Use :meth:`register_tool` instead (which rebuilds
            the system prompt without clearing memory), or call
            :meth:`rebuild_system_prompt` explicitly if you need the
            clear-and-rebuild behavior.

        Kept for backward compatibility — existing code that relied on
        the clear-on-add behavior continues to work. A future release
        will make ``add_tool`` an alias for ``register_tool``.
        """
        self.register_tool(tool)
        # The old behavior cleared memory after registering.
        # We preserve it here for backward compat.
        self.memory.clear()
        self.memory.add("system", self._custom_system_prompt)

    def get_response(self, response_id: str) -> Response | None:
        """Get a previous response by ID (for previous_response_id support)."""
        return self._response_history.get(response_id)

    def __repr__(self) -> str:
        return f"Agent(model={self.model}, tools={len(self.tools)}, tool_choice={self.tool_choice.type.value})"


__all__ = ["Agent"]