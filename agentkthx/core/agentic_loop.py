"""Agentic loop subsystem — the unified step loop (R07.00 Phase 5).

This is the MAINT-04 flagship extraction: ``_run_core()`` and
``_run_core_streaming()`` were ~573-line and ~466-line near-duplicates
(after R06.58's Phase 1-4 helper extractions had already deduplicated
the retry loop, finish_reason, tool-call parsing, finalization, final-
answer enforcement, blocked-call handling, and tool_choice rejection).

What remains duplicated after Phase 1-4 is the loop body itself:
the per-step orchestration and the ~150-line per-tool-call dispatch.
Phase 5 unifies them into ONE loop parameterized by:

- ``generate_fn`` — ``self._generate`` (non-streaming) or
  ``self._generate_stream`` (streaming)
- ``LoopCallbacks`` — optional hooks for the streaming-only behaviors
  (per-step compaction, token tracking + footer refresh, inline tool
  printing, between-calls compaction) plus the two real behavioral
  toggles that differ per path:

  - ``include_format_hint`` — the ReAct format hint in the
    tool_choice rejection message (non-streaming includes it,
    streaming doesn't — an accidental variation preserved verbatim
    by R06.58 Phase 4c and kept configurable here)
  - ``mark_response_completed`` — non-streaming marks the
    OpenResponses ``Response`` COMPLETED at end-of-run (with debug
    prints); streaming never does (pre-existing discrepancy,
    preserved exactly and flagged in the Phase 5 commit)

Debug output is UNIFIED to the non-streaming superset (per the audit's
MAINT-04 recommendation) — the streaming path in ``--debug`` mode now
shows the same OpenResponses lifecycle lines as non-streaming.

Loop-method map (what lives where after Phase 5):

    agent.py::_run_core             → thin wrapper (~15 lines)
    agent.py::_run_core_streaming   → thin wrapper (~35 lines)
    AgenticLoopMixin::
        _run_loop_iteration()       → the unified step loop (this module)
        _execute_single_tool_call() → per-tool-call dispatch (this module)
        _process_tool_result()      → post-execution memory + error
                                      recovery tracking (this module)

The Phase 1-4 helpers (``_generate_with_retry``, ``_handle_finish_reason``,
``_parse_tool_calls``, ``_finalize_run``, ``_enforce_final_answer``,
``_handle_blocked_tool_call``, ``_reject_for_tool_choice``,
``_check_tool_choice_required``) remain methods on ``Agent`` — this mixin
calls them through ``self``, same as the old loop bodies did.

Host contract: everything ``Agent`` already provides — ``self.memory``,
``self.tools``, ``self._parser``, ``self._error_tracker``,
``self._allowed_tools``, ``self.tool_choice``, ``self.debug``,
``self.max_steps``, the Phase 1-4 helpers, and (for the streaming
callbacks) the CompactionMixin + footer state.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .error_recovery import build_enhanced_observation, build_retry_context, is_error_result
from .models import AgentRun, StepResult, ToolCall
from .openresponses import (
    ItemStatus,
    Response,
    ResponseStatus,
    create_function_call_item,
    create_function_call_output,
    create_message_item,
)
from .types import StepResultType

# ═══════════════════════════════════════════════════════════════════════
# FEAT-02 (R07.15): per-tool timeouts + concurrent tool execution
# ═══════════════════════════════════════════════════════════════════════

# Worker cap for the parallel path. 4 covers the common multi-http_get
# batch without unbounded thread spawn on pathological (30+ call) turns.
_TOOL_POOL_MAX_WORKERS = 4

# Tools whose calls mutate shared filesystem/state — ALWAYS executed
# sequentially, even in an otherwise-independent batch. The audit named
# shell/write_file/edit_file (filesystem mutations); ``todo`` is included
# for the same reason (it rewrites the todo state file). python_repl is
# deliberately NOT here — it runs in a sandboxed subprocess with file
# system and network access blocked.
_SEQUENTIAL_ONLY_TOOLS = frozenset({"shell", "write_file", "edit_file", "todo"})


def _calls_independent(tool_calls: list) -> bool:
    """FEAT-02: can this batch of tool calls run concurrently?

    Rules (from the audit's proposal, plus one hardening rule):
      - calls to DIFFERENT tools are independent
      - calls to the same tool with DIFFERENT arguments are independent
      - identical (tool, arguments) pairs are NOT — the R06.52
        identical-repeat guard counts per (tool, args); letting a
        duplicate run concurrently with its twin would race the tracker
      - any call to a _SEQUENTIAL_ONLY_TOOLS member forces the whole
        batch sequential (filesystem/state mutations must not interleave)

    Note the memory-ordering caveat that makes this safe: results are
    always committed (memory, step records, callbacks) in ORIGINAL call
    order after all executions complete, so the transcript is
    byte-identical to the sequential path regardless of completion order.
    """
    seen: set = set()
    for tc in tool_calls:
        name = tc.get("name", "")
        if name in _SEQUENTIAL_ONLY_TOOLS:
            return False
        key = (name, json.dumps(tc.get("arguments", {}), sort_keys=True, default=str))
        if key in seen:
            return False
        seen.add(key)
    return True


@dataclass
class LoopCallbacks:
    """Per-path hooks + toggles for the unified agentic loop.

    All hooks default to None (non-streaming path: no-op). The streaming
    wrapper fills them in. Hooks must never raise — the loop guards the
    footer refresh defensively; other hooks are best-effort.
    """

    # Streaming-only hooks ------------------------------------------------
    # Top of each step, before generate. Streaming: preventive compaction.
    on_step_start: Optional[Callable[[int], None]] = None
    # After a successful generate, before the _cancelled check. Streaming:
    # _update_running_tokens + CLI footer refresh.
    on_generated: Optional[Callable[[int, dict], None]] = None
    # After a tool executed successfully, before the call counter
    # increments. Streaming: inline "[N] tool name {args} → result" print.
    # Receives (current_call_count, tool_name, tool_args, result) — the
    # streaming implementation renders the index as count + 1.
    on_tool_executed: Optional[Callable[[int, str, dict, Any], None]] = None
    # After a tool result is committed to memory + steps. Streaming:
    # compaction between calls when the step carried multiple calls.
    # Receives the number of tool calls in the current step.
    on_tool_result_committed: Optional[Callable[[int], None]] = None

    # Behavioral toggles (differ between paths) ---------------------------
    # ReAct format hint in tool_choice rejection messages.
    # Non-streaming: True. Streaming: False.
    include_format_hint: bool = False
    # Mark the OpenResponses Response COMPLETED at end-of-run.
    # Non-streaming: True. Streaming: False (pre-existing behavior).
    mark_response_completed: bool = False


@dataclass
class _LoopState:
    """Mutable state threaded through the tool-call loop.

    The old loop bodies carried these as closure locals; the unified
    loop passes them explicitly so ``_execute_single_tool_call`` can
    update them.
    """

    terminated: bool = False
    tool_calls: int = 0
    expecting_final_answer: bool = False
    last_successful_result: Optional[str] = None
    last_tool_name: Optional[str] = None
    pending_final_answer: Optional[str] = None
    successful_results: list = field(default_factory=list)


class AgenticLoopMixin:
    """Mixin providing the unified agentic loop for Agent.

    R07.00 Phase 5: replaces the duplicated loop bodies of
    ``_run_core()`` and ``_run_core_streaming()`` with a single
    ``_run_loop_iteration()`` parameterized by generate function +
    ``LoopCallbacks``.
    """

    def _run_loop_iteration(
        self,
        prompt: str,
        generate_fn: Callable[[], dict],
        *,
        enable_compaction_recovery: bool,
        callbacks: LoopCallbacks,
    ) -> AgentRun:
        """The unified OpenResponses agentic loop.

        1. Model samples from input
        2. If tool call: execute tool, return observation, continue
        3. If no tool call: return final output items

        IMPORTANT: No fallbacks that bypass the AI model are used.
        All tool calls must come from the model itself.

        Returns:
            AgentRun with final answer and execution details
        """
        start_time = time.time()
        steps: list = []
        total_tokens = 0
        state = _LoopState()

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
            print(
                f"[OpenResponses] Input item added: id={user_item.id}, type={user_item.type}, role={user_item.role}"
            )

        if self.debug:
            print(f"\n[AgentKthx] Model: {self.model}")
            print(f"[AgentKthx] Backend: {self.backend.base_url}")
            print(f"[AgentKthx] tool_choice: {self.tool_choice.type.value}")
            print(f"[AgentKthx] Tools: {self.tools.names()}")
            print(f"[AgentKthx] Prompt: {prompt}\n")

        # Reset error tracker for new run
        self._error_tracker.reset()

        for step_num in range(self.max_steps):
            if self.debug:
                print(f"[Step {step_num + 1}]")

            # Streaming path: preventive compaction before generating
            # (ROB-06) — a no-op on the non-streaming path.
            if callbacks.on_step_start is not None:
                callbacks.on_step_start(step_num)

            # Generate response from model.
            # R06.54: transient API errors (rate limits, empty responses,
            # connection blips, provider 5xx) no longer kill the run — the
            # same step is retried after an escalating back-off. Only a
            # persistent failure (max_api_retries consecutive) or a permanent
            # error (auth, bad request) terminates the run, and it does so
            # with a clean history (nothing was announced for this step).
            gen_response, state.terminated = self._generate_with_retry(
                generate_fn,
                step_num=step_num,
                steps=steps,
                response=response,
                enable_compaction_recovery=enable_compaction_recovery,
            )
            if state.terminated or gen_response is None:
                break

            content = gen_response.get("content", "")
            native_tool_calls = gen_response.get("tool_calls", [])
            tokens = gen_response.get("usage", {}).get("total_tokens", 0)
            total_tokens += tokens
            # R05.8: Capture reasoning_content (chain-of-thought) if the
            # backend surfaced it. Displayed in the CLI when --think is set.
            reasoning_content = gen_response.get("reasoning_content", "") or ""

            # Streaming path: token snapshot + CLI footer refresh —
            # a no-op on the non-streaming path.
            if callbacks.on_generated is not None:
                callbacks.on_generated(step_num, gen_response)

            # Handle user cancellation (Ctrl+C during generate)
            if gen_response.get("_cancelled"):
                if self.debug:
                    print("  [Cancelled] Generation interrupted by user")
                steps.append(
                    StepResult(
                        type=StepResultType.ERROR,
                        error="Cancelled by user",
                        tokens_used=tokens,
                    )
                )
                response.mark_cancelled(debug=self.debug)
                break

            # OpenResponses: Handle finish_reason from backend.
            # Returns True if terminal (length / content_filter).
            if self._handle_finish_reason(gen_response, steps, response):
                break

            if self.debug:
                print(f"  Content: {content[:200] if content else '(empty)'}...")
                print(f"  Native tool calls: {native_tool_calls}")

            # ---- Process tool calls (native or ReAct) ----
            tool_calls_found = self._parse_tool_calls(
                content,
                native_tool_calls,
                response,
            )

            # Execute tool calls if found
            if tool_calls_found:
                # OpenResponses Enhancement: Final Answer Enforcement
                # If we asked for Final Answer but model tried to call tools again,
                # intercept and force Final Answer extraction.
                if state.expecting_final_answer and state.last_successful_result is not None:
                    return self._enforce_final_answer(
                        _last_successful_result=state.last_successful_result,
                        tokens=tokens,
                        reasoning_content=reasoning_content,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=state.tool_calls,
                        response=response,
                        debug_context="Model tried to call tools instead of Final Answer",
                    )

                # For native calls, use special memory format
                if native_tool_calls:
                    self.memory.add_tool_call("assistant", content, native_tool_calls)
                else:
                    self.memory.add("assistant", content)

                # FEAT-02 (R07.15): batch dispatch — sequential per-call
                # lifecycle unless the batch is independent and parallel
                # tools are enabled; parallel batches gate first, execute
                # concurrently (max 4 workers), and commit in call order.
                action = self._execute_tool_calls(
                    tool_calls_found,
                    state=state,
                    prompt=prompt,
                    step_num=step_num,
                    tokens=tokens,
                    content=content,
                    native_tool_calls=native_tool_calls,
                    steps=steps,
                    response=response,
                    callbacks=callbacks,
                )
                if action == "break":
                    break

                # R06.52: if the run was terminated inside the tool loop,
                # stop the WHOLE run. The old code only broke the inner loop
                # and then called the model again with dangling tool_calls —
                # an illegal API sequence (OpenRouter 400 / ZAI 1214).
                if state.terminated:
                    return self._finalize_run(
                        final_answer="",
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=state.tool_calls,
                        response=response,
                        success=False,
                        mark_completed=False,
                    )

                # Check if model provided final_answer along with tool call
                if state.pending_final_answer:
                    if self.debug and not self._is_comp_mode:
                        print("  [OpenResponses] Model provided final_answer with tool call")
                        print(
                            f"  [OpenResponses] Using final_answer: {state.pending_final_answer[:100]}..."
                        )

                    # Create output message item
                    msg_item = create_message_item("assistant", state.pending_final_answer)
                    msg_item.status = ItemStatus.COMPLETED
                    response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)

                    steps.append(
                        StepResult(
                            type=StepResultType.FINAL_ANSWER,
                            content=state.pending_final_answer,
                            tokens_used=tokens,
                            reasoning_content=reasoning_content,
                        )
                    )

                    return self._finalize_run(
                        final_answer=state.pending_final_answer,
                        steps=steps,
                        total_tokens=total_tokens,
                        start_time=start_time,
                        tool_calls=state.tool_calls,
                        response=response,
                        success=True,
                    )

                # Continue the agentic loop
                continue

            # ---- Check for Final Answer ----
            # The model explicitly signals completion with "Final Answer:"
            if self._parser.is_final_answer(content):
                # OpenResponses: Check tool_choice enforcement.
                needs_tool, rejection_reason = self._check_tool_choice_required(state.tool_calls)

                if needs_tool:
                    if self.debug and not self._is_comp_mode:
                        print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                        print("  [OpenResponses] Enforcing tool requirement...")
                    # Tell model to use tools.
                    self._reject_for_tool_choice(
                        content,
                        is_final_answer_context=True,
                        include_format_hint=callbacks.include_format_hint,
                    )
                    continue

                answer = self._parser.extract_final_answer(content)

                # Reset the expecting_final_answer flag
                state.expecting_final_answer = False

                # Create output message item
                msg_item = create_message_item("assistant", answer)
                msg_item.status = ItemStatus.COMPLETED
                response.add_output_item(msg_item, debug=not self._is_comp_mode and self.debug)

                if self.debug and not self._is_comp_mode:
                    print(
                        f"  [OpenResponses] MessageItem created: id={msg_item.id}, role={msg_item.role}"
                    )
                    print(f"  [OpenResponses] MessageItem status: {msg_item.status.value}")

                steps.append(
                    StepResult(
                        type=StepResultType.FINAL_ANSWER,
                        content=answer,
                        tokens_used=tokens,
                        reasoning_content=reasoning_content,
                    )
                )

                if self.debug:
                    print(f"  Final answer: {answer}")

                break

            # ---- No tool call, no final answer ----
            # Model responded directly without explicit final answer format
            # Check tool_choice enforcement before accepting.
            needs_tool, rejection_reason = self._check_tool_choice_required(state.tool_calls)

            if needs_tool:
                if self.debug and not self._is_comp_mode:
                    print(f"  [OpenResponses] REJECTED: {rejection_reason}")
                    print("  [OpenResponses] Enforcing tool requirement...")
                # Tell model to use tools.
                self._reject_for_tool_choice(
                    content,
                    is_final_answer_context=False,
                    include_format_hint=callbacks.include_format_hint,
                )
                continue

            # OpenResponses Enhancement: Final Answer Enforcement
            # If we were expecting Final Answer but model responded without "Final Answer:" format,
            # use the last successful result instead of accepting the model's potentially wrong answer.
            if state.expecting_final_answer and state.last_successful_result is not None:
                return self._enforce_final_answer(
                    _last_successful_result=state.last_successful_result,
                    tokens=tokens,
                    reasoning_content=reasoning_content,
                    steps=steps,
                    total_tokens=total_tokens,
                    start_time=start_time,
                    tool_calls=state.tool_calls,
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
                print("  No tool calls detected, accepting as final answer")

            steps.append(
                StepResult(
                    type=StepResultType.FINAL_ANSWER,
                    content=content,
                    tokens_used=tokens,
                    reasoning_content=reasoning_content,
                )
            )
            self.memory.add("assistant", content)
            break

        else:
            # Max steps reached
            response.mark_incomplete()
            if self.debug and not self._is_comp_mode:
                print(
                    f"\n[OpenResponses] Response status: {response.status.value} (max steps reached)"
                )
            steps.append(
                StepResult(
                    type=StepResultType.MAX_STEPS,
                    content="Maximum steps reached without final answer",
                )
            )

        # Mark response as completed (non-streaming path; the streaming
        # path preserves its historical never-mark behavior).
        if callbacks.mark_response_completed and response.status == ResponseStatus.IN_PROGRESS:
            response.mark_completed()

        if self.debug and not self._is_comp_mode:
            print(f"\n[OpenResponses] Response completed: id={response.id}")
            print(f"[OpenResponses] Final status: {response.status.value}")
            print(f"[OpenResponses] Output items: {len(response.output)}")
            print(f"[OpenResponses] Tool calls made: {state.tool_calls}")

        # Get final answer via shared helper.
        final_answer = self._extract_last_final_answer(steps)

        # mark_completed=False because we already marked it above (to
        # keep the debug print ordering intact).
        return self._finalize_run(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            start_time=start_time,
            tool_calls=state.tool_calls,
            response=response,
            success=bool(final_answer),
            mark_completed=False,
        )

    def _gate_and_prepare_tool_call(
        self,
        tc: dict,
        state: _LoopState,
        *,
        native_tool_calls: list,
        steps: list,
        response: "Response",
        step_num: int,
        tokens: int,
    ) -> tuple[str, Any]:
        """Model-visible gates + FunctionCallItem creation for ONE call.

        FEAT-02 (R07.15) extraction: the batch dispatcher needs to run
        these for every call BEFORE submitting any execution to the
        worker pool, so both the sequential and parallel paths share
        this method verbatim (no gate drift). Performs: final_answer
        stash → allowed_tools gate → R06.52 identical-repeat guard →
        FunctionCallItem creation.

        Returns:
            ("run", fc_item)     — approved for execution
            ("continue", None)   — blocked (not allowed / repeat guard),
                                   bookkeeping already done
            ("break", None)      — terminal (repeat-guard termination);
                                   ``state.terminated`` already set
        """
        tool_name = tc["name"]
        tool_args = tc["arguments"]
        tool_call_id = tc.get("id", "") or ""

        # Check if this tool call also has a final_answer
        if tc.get("final_answer"):
            state.pending_final_answer = tc["final_answer"]

        # OpenResponses: Check allowed_tools
        if self._allowed_tools and tool_name not in self._allowed_tools:
            error_msg = f"Tool '{tool_name}' not in allowed_tools: {self._allowed_tools}"
            if self.debug and not self._is_comp_mode:
                print(
                    f"  [OpenResponses] BLOCKED by allowed_tools: '{tool_name}' not in {self._allowed_tools}"
                )
            if native_tool_calls:
                self.memory.add_tool_result(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    content=f"Error: {error_msg}",
                )
            else:
                self.memory.add("user", f"Observation: Error: {error_msg}")
            return "continue", None

        # R06.52: identical-repeat guard. If this exact call
        # (tool + arguments) already failed max_identical_failures
        # times, block it BEFORE execution and teach the model to
        # change approach. The result is recorded in memory so the
        # sequence stays paired.
        if self._error_tracker.should_block_repeat(tool_name, tool_args):
            _blocked, _term = self._handle_blocked_tool_call(
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                native_tool_calls=native_tool_calls,
                step_num=step_num,
                tool_calls=state.tool_calls,
                tokens=tokens,
                steps=steps,
                response=response,
            )
            if _term:
                state.terminated = True
                return "break", None
            return "continue", None

        # Create FunctionCallItem
        fc_item = create_function_call_item(tool_name, tool_args, tool_call_id)
        fc_item.status = ItemStatus.IN_PROGRESS
        response.add_output_item(fc_item, debug=not self._is_comp_mode and self.debug)

        if self.debug and not self._is_comp_mode:
            print(
                f"  [OpenResponses] FunctionCallItem created: id={fc_item.id}, call_id={fc_item.call_id}"
            )
            print(f"  [OpenResponses] FunctionCallItem status: {fc_item.status.value}")

        return "run", fc_item

    def _execute_single_tool_call(
        self,
        tc: dict,
        state: _LoopState,
        *,
        prompt: str,
        step_num: int,
        tokens: int,
        content: str,
        native_tool_calls: list,
        steps: list,
        response: "Response",
        callbacks: LoopCallbacks,
        calls_this_step: int = 1,
    ) -> str:
        """Dispatch ONE tool call from the model's response.

        Covers the full per-call lifecycle: gates + FunctionCallItem
        creation (``_gate_and_prepare_tool_call``) → execution (with
        Ctrl+C handling) → streaming inline print hook → error-recovery
        tracking + memory commit → step record → between-calls compaction
        hook (``_commit_tool_result``).

        FEAT-02 (R07.15): the batch dispatcher (``_execute_tool_calls``)
        runs this for sequential batches and drives the gate/execute/
        commit phases itself for parallel batches — the per-call
        semantics are identical either way.

        Returns:
            "continue" — process the next tool call in this step
            "break"    — stop processing tool calls. Every "break" path
                         also sets ``state.terminated`` (terminal tool
                         failure, repeat-guard, or Ctrl+C cancellation —
                         ROB-01), so the caller stops the WHOLE run, not
                         just the inner tool loop.
        """
        tool_name = tc["name"]
        tool_args = tc["arguments"]
        tool_call_id = tc.get("id", "") or ""

        action, fc_item = self._gate_and_prepare_tool_call(
            tc,
            state,
            native_tool_calls=native_tool_calls,
            steps=steps,
            response=response,
            step_num=step_num,
            tokens=tokens,
        )
        if action != "run":
            return action

        try:
            result = self._execute_tool(tool_name, tool_args)
        except KeyboardInterrupt:
            # ROB-01 (R07.06): Ctrl+C during tool execution used to return
            # "break" WITHOUT setting ``state.terminated`` — the inner tool
            # loop stopped, but the outer step loop then called the model
            # again with the cancelled state still in memory (half-cancelled:
            # the response was already marked CANCELLED, yet the run could
            # fire more tool calls before the user could interrupt again).
            # Mark the run terminated so the caller's
            # ``if state.terminated:`` check finalizes the whole run
            # immediately. ``response.mark_cancelled()`` below keeps the
            # response in CANCELLED status (``_finalize_run`` is called with
            # ``mark_completed=False`` on this path, so it won't be flipped
            # to COMPLETED).
            state.terminated = True
            fc_item.status = ItemStatus.FAILED
            response.mark_cancelled(debug=self.debug)
            steps.append(
                StepResult(
                    type=StepResultType.ERROR,
                    error="Cancelled by user during tool execution",
                )
            )
            return "break"

        return self._commit_tool_result(
            result,
            fc_item,
            state=state,
            steps=steps,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            step_num=step_num,
            tokens=tokens,
            content=content,
            native_tool_calls=native_tool_calls,
            response=response,
            callbacks=callbacks,
            calls_this_step=calls_this_step,
        )

    def _commit_tool_result(
        self,
        result: Any,
        fc_item,
        *,
        state: _LoopState,
        steps: list,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        step_num: int,
        tokens: int,
        content: str,
        native_tool_calls: list,
        response: "Response",
        callbacks: LoopCallbacks,
        calls_this_step: int = 1,
    ) -> str:
        """Post-execution bookkeeping for ONE tool call, in call order.

        FEAT-02 (R07.15) extraction: streaming inline print hook →
        tool-call counter → error-recovery tracking + memory commit →
        terminal check → step record → debug prints → between-calls
        compaction hook. Shared verbatim by the sequential path and the
        parallel path's commit phase (which always commits in ORIGINAL
        call order — memory pairing and the step timeline stay
        deterministic regardless of worker completion order).

        Returns:
            "continue" — process the next tool call in this step
            "break"    — terminal tool failure; ``state.terminated`` set
        """
        # Streaming path: print tool call + result inline so the user
        # sees progress as it happens — no-op on the non-streaming path.
        if callbacks.on_tool_executed is not None:
            callbacks.on_tool_executed(state.tool_calls, tool_name, tool_args, result)

        state.tool_calls += 1

        # Post-execution: error recovery, memory commit, observation.
        terminated = self._process_tool_result(
            result,
            fc_item,
            state=state,
            steps=steps,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            step_num=step_num,
            tokens=tokens,
            native_tool_calls=native_tool_calls,
            response=response,
        )
        if terminated:
            state.terminated = True
            return "break"

        steps.append(
            StepResult(
                type=StepResultType.TOOL_CALL,
                content=content,
                tool_call=ToolCall(name=tool_name, arguments=tool_args),
                tool_result=result,
                tokens_used=tokens,
            )
        )

        if self.debug:
            print(f"  Tool: {tool_name}({tool_args})")
            print(f"  Result: {str(result)[:200]}...")

        # Streaming path: R06.58 BUGFIX — check compaction BETWEEN tool
        # calls within a single assistant message. No-op on the
        # non-streaming path.
        if callbacks.on_tool_result_committed is not None:
            callbacks.on_tool_result_committed(calls_this_step)

        return "continue"

    def _execute_tool_calls(
        self,
        tool_calls_found: list,
        *,
        state: _LoopState,
        prompt: str,
        step_num: int,
        tokens: int,
        content: str,
        native_tool_calls: list,
        steps: list,
        response: "Response",
        callbacks: LoopCallbacks,
    ) -> str:
        """FEAT-02 (R07.15): dispatch a step's tool calls.

        Sequential (unchanged per-call lifecycle) unless ALL of:
          - the batch has ≥ 2 calls,
          - AGENTKTHX_PARALLEL_TOOLS is not disabled (default: enabled),
          - the batch is independent per ``_calls_independent``
            (no stateful tools, no duplicate (tool, args) pairs).

        Parallel path: gates + FunctionCallItem creation run first in
        call order, the tool handlers execute concurrently in a
        ThreadPoolExecutor (max 4 workers), and post-processing commits
        in ORIGINAL call order — the transcript is byte-identical to the
        sequential path; only wall-clock latency shrinks.

        Returns:
            "continue" — all calls processed
            "break"    — stop; ``state.terminated`` set (terminal gate,
                         terminal tool failure, or Ctrl+C — ROB-01 parity)
        """
        parallel_enabled = os.environ.get("AGENTKTHX_PARALLEL_TOOLS", "1").lower() in (
            "1",
            "true",
            "yes",
        )
        if (
            not parallel_enabled
            or len(tool_calls_found) < 2
            or not _calls_independent(tool_calls_found)
        ):
            for tc in tool_calls_found:
                action = self._execute_single_tool_call(
                    tc,
                    state,
                    prompt=prompt,
                    step_num=step_num,
                    tokens=tokens,
                    content=content,
                    native_tool_calls=native_tool_calls,
                    steps=steps,
                    response=response,
                    callbacks=callbacks,
                    calls_this_step=len(tool_calls_found),
                )
                if action == "break":
                    return "break"
            return "continue"

        return self._execute_tool_calls_parallel(
            tool_calls_found,
            state=state,
            step_num=step_num,
            tokens=tokens,
            content=content,
            native_tool_calls=native_tool_calls,
            steps=steps,
            response=response,
            callbacks=callbacks,
        )

    def _execute_tool_calls_parallel(
        self,
        tool_calls_found: list,
        *,
        state: _LoopState,
        step_num: int,
        tokens: int,
        content: str,
        native_tool_calls: list,
        steps: list,
        response: "Response",
        callbacks: LoopCallbacks,
    ) -> str:
        """FEAT-02 parallel execution phase (only reached for independent
        batches — see ``_execute_tool_calls``).

        Phase 1 — gates + FunctionCallItem creation, call order
            (``_gate_and_prepare_tool_call``; a terminal gate aborts the
            batch BEFORE any execution, matching sequential semantics).
        Phase 2 — tool handlers run concurrently via
            ThreadPoolExecutor(max_workers=min(4, batch size)). Each
            worker runs the same ``_execute_tool`` the sequential path
            uses (registry lookup, dangerous-tool gate, argument
            normalization, error formatting).
        Phase 3 — ``_commit_tool_result`` per call in ORIGINAL order.

        Ctrl+C parity (ROB-01): a KeyboardInterrupt while collecting
        results cancels pending futures, abandons in-flight workers
        (threads cannot be killed — their tool calls run to their own
        timeout), marks the response CANCELLED, records the ERROR step,
        and breaks the run exactly like the sequential path.
        """
        calls_this_step = len(tool_calls_found)

        # ---- Phase 1: gates + FunctionCallItem creation (call order) ----
        approved: list[tuple[dict, Any]] = []  # (tc, fc_item)
        for tc in tool_calls_found:
            action, fc_item = self._gate_and_prepare_tool_call(
                tc,
                state,
                native_tool_calls=native_tool_calls,
                steps=steps,
                response=response,
                step_num=step_num,
                tokens=tokens,
            )
            if action == "break":
                return "break"
            if action == "continue":
                continue
            approved.append((tc, fc_item))

        if len(approved) < 2:
            # Gates swallowed the batch down to a single runnable call —
            # no pool needed; finish it sequentially for identical
            # lifecycle (Ctrl+C handling included).
            for tc, _fc in approved:
                action = self._execute_single_tool_call(
                    tc,
                    state,
                    prompt="",
                    step_num=step_num,
                    tokens=tokens,
                    content=content,
                    native_tool_calls=native_tool_calls,
                    steps=steps,
                    response=response,
                    callbacks=callbacks,
                    calls_this_step=calls_this_step,
                )
                if action == "break":
                    return "break"
            return "continue"

        # ---- Phase 2: concurrent execution ----
        results: dict[int, Any] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(_TOOL_POOL_MAX_WORKERS, len(approved)),
            thread_name_prefix="agentkthx-tool",
        )
        futures = {
            idx: executor.submit(self._execute_tool, tc["name"], tc["arguments"])
            for idx, (tc, _fc) in enumerate(approved)
        }
        try:
            for idx, fut in futures.items():
                results[idx] = fut.result()
        except KeyboardInterrupt:
            executor.shutdown(wait=False, cancel_futures=True)
            state.terminated = True
            for _tc, fc_item in approved:
                fc_item.status = ItemStatus.FAILED
            response.mark_cancelled(debug=self.debug)
            steps.append(
                StepResult(
                    type=StepResultType.ERROR,
                    error="Cancelled by user during tool execution",
                )
            )
            return "break"
        except BaseException:
            # Never leak a half-alive pool on unexpected errors.
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)

        # ---- Phase 3: commit in ORIGINAL call order ----
        for idx, (tc, fc_item) in enumerate(approved):
            action = self._commit_tool_result(
                results[idx],
                fc_item,
                state=state,
                steps=steps,
                tool_name=tc["name"],
                tool_args=tc["arguments"],
                tool_call_id=tc.get("id", "") or "",
                step_num=step_num,
                tokens=tokens,
                content=content,
                native_tool_calls=native_tool_calls,
                response=response,
                callbacks=callbacks,
                calls_this_step=calls_this_step,
            )
            if action == "break":
                return "break"
        return "continue"

    def _process_tool_result(
        self,
        result: Any,
        fc_item,
        *,
        state: _LoopState,
        steps: list,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        step_num: int,
        tokens: int,
        native_tool_calls: list,
        response: "Response",
    ) -> bool:
        """Post-execution handling: error tracking + memory commit.

        Mirrors the block that was duplicated between the two loop
        bodies: ``is_error_result`` classification, failure recording +
        termination check, success recording, FunctionCallItem status
        transitions, memory writes (native tool_result vs ReAct
        enhanced observation), retry hints, and the
        expecting_final_answer state machine.

        Returns:
            True if the run must terminate (too many consecutive
            all-failure steps); the caller sets ``state.terminated``.
        """
        # Track success/failure for error recovery
        is_error = is_error_result(str(result))
        if is_error:
            self._error_tracker.record_failure(
                tool_name=tool_name, error_message=str(result), step=step_num, arguments=tool_args
            )

            # Check if we should terminate due to too many failures
            if self._error_tracker.should_terminate():
                if self.debug:
                    print(
                        f"  [ErrorRecovery] Terminating: {self._error_tracker.consecutive_all} consecutive all-failure steps >= max ({self._error_tracker.max_total_failures})"
                    )
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
                        tool_call_id=tool_call_id or f"terminated_{step_num}_{state.tool_calls}",
                        name=tool_name,
                        content=term_msg,
                    )
                else:
                    self.memory.add("user", f"Observation: {term_msg}")
                steps.append(
                    StepResult(
                        type=StepResultType.ERROR,
                        error=term_msg,
                        tool_call=ToolCall(name=tool_name, arguments=tool_args),
                        tokens_used=tokens,
                    )
                )
                response.mark_failed(
                    {"message": "Too many tool failures", "type": "error_recovery"}
                )
                return True
        else:
            # Record success to reset consecutive failure counter
            self._error_tracker.record_success(tool_name)

        # Update FunctionCallItem status
        fc_item.status = ItemStatus.COMPLETED

        if self.debug and not self._is_comp_mode:
            print(f"  [OpenResponses] FunctionCallItem status: {fc_item.status.value}")

        # SEC-10 / FEAT-01 (R07.05): sanitize and wrap the tool result
        # before it enters model context. The wrapper:
        # (a) wraps in <tool_output> tags so the system prompt can
        #     instruct the model to treat the contents as untrusted data,
        # (b) truncates to 8KB to prevent context exhaustion,
        # (c) redacts secret-looking lines (password=, api_key:, Bearer),
        # (d) strips ANSI escapes that could manipulate the user's
        #     terminal during chat display.
        # The wrapped result is what the model sees; the original `result`
        # is still stored in the StepResult and FunctionCallOutputItem
        # below for debugging / OpenResponses clients.
        from .helpers import sanitize_tool_output

        sanitized_output = sanitize_tool_output(
            result,
            tool_name=tool_name,
            tool_call_id=fc_item.call_id,
        )

        # Create FunctionCallOutputItem
        fco_item = create_function_call_output(fc_item.call_id, sanitized_output)
        response.add_output_item(fco_item, debug=not self._is_comp_mode and self.debug)

        if self.debug and not self._is_comp_mode:
            print(
                f"  [OpenResponses] FunctionCallOutputItem created: id={fco_item.id}, call_id={fco_item.call_id}"
            )

        # Add tool result to memory with enhanced guidance
        if native_tool_calls:
            self.memory.add_tool_result(
                tool_call_id=fc_item.call_id,
                name=tool_name,
                content=sanitized_output,
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
                        print(
                            f"  [Retry Context] Adding retry hint for native tool call: {tool_name}"
                        )
                    self.memory.add("user", retry_msg)
        else:
            # Use error recovery module for enhanced observation
            observation_msg = build_enhanced_observation(
                tool_name=tool_name,
                result=sanitized_output,
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
                state.expecting_final_answer = False
                state.last_tool_name = None
            else:
                from .error_recovery import _is_simple_result

                if _is_simple_result(sanitized_output, tool_name):
                    state.expecting_final_answer = True
                    state.last_successful_result = sanitized_output
                    state.last_tool_name = tool_name
                else:
                    # Complex/intermediate result — allow more tool calls
                    state.expecting_final_answer = False
                    state.last_tool_name = None

            self.memory.add("user", observation_msg)

        if not is_error:
            state.successful_results.append(f"{tool_name}: {result}")

        return False
