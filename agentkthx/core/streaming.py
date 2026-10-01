"""Streaming subsystem — SSE machinery for the Agent.

R07.00 Phase 6 extraction (from ``agent.py``). Pure move, no logic change.

Three self-contained generators (~795 lines) that form the streaming
pipeline, moved out of ``agent.py`` so the agentic loop file contains
only loop logic:

- ``run_stream()`` — the OpenResponses SSE event generator. Yields
  lifecycle events (response.created → output_item.added → … →
  response.completed) as the run progresses. Used by the ACP plugin
  and any OpenResponses-compatible client.
- ``_generate_stream_chunks()`` — lower-level chunk accumulator over
  ``backend.generate_completions_stream()``.
- ``_generate_stream()`` — the typewriter-path generator: prints
  content / reasoning_content deltas to stdout as they arrive,
  accumulates tool_call fragments across SSE chunks, and returns the
  same dict shape as ``_generate()`` so the agentic loop is unchanged.

These have nothing to do with agentic-loop control flow (retry, tool
dispatch, finish_reason) — they are transport + presentation, and can
be tested independently of the loop.

Host contract: the mixin expects ``self`` to provide everything the
Agent already does (``self.backend``, ``self.memory``, ``self.debug``,
``self.tools``, ``self._parser``, thinking controls, compaction attrs).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Generator

from .api_resilience import (
    backoff_delay,
    describe_terminal,
    describe_wait,
    is_transient_api_error,
)
from .error_recovery import build_enhanced_observation, is_error_result
from .openresponses import (
    EventType,
    ItemStatus,
    OutputItemEvent,
    OutputText,
    ReasoningItem,
    Response,
    ResponseEvent,
    ResponseStatus,
    ToolChoiceType,
    create_function_call_item,
    create_function_call_output,
    create_message_item,
    stream_response_events,
)

# ═══════════════════════════════════════════════════════════════════════════════
# MAINT-08 (R07.15): StreamAccumulator + StreamRenderer
# ═══════════════════════════════════════════════════════════════════════════════
# _generate_stream used to be a 354-line method with 3 inline closures, 3
# accumulator collections, 5-level try/except nesting, and stdout writes
# woven through the chunk loops — hard to extend (FEAT-06 tool-call
# argument deltas would have meant editing all of it at once) and hard to
# test (no way to capture rendered output without redirecting stdout).
# The extraction splits it along its two natural seams:
#
# - StreamAccumulator owns the DATA: content/reasoning/tool_call delta
#   merging (including OpenAI's split-across-chunks tool_call format) and
#   the finalize() assembly producing the same dict shape as _generate().
#   Pure — no I/O — so FEAT-06 can build on it without touching rendering.
# - StreamRenderer owns the PRESENTATION: the "AgentKthx:" prefix, the
#   "reasoning:" panel, indent tracking, and the reasoning→content
#   transition. Accepts an optional ``out`` stream so tests can capture
#   the exact rendered bytes without redirecting sys.stdout.
#
# _generate_stream is now orchestration only (~90 lines): resolve params →
# pick stream method → feed chunks into accumulator + renderer → finalize.
# Rendered bytes and return-dict shape are unchanged — pinned by
# tests/test_streaming.py plus the new MAINT-08 tests.


class StreamAccumulator:
    """Accumulates streaming deltas into a _generate()-shaped result.

    OpenAI streaming tool_calls arrive as a list of "delta" objects, each
    carrying an index, optional id (first chunk only), optional
    function.name (first chunk only), and function.arguments as a partial
    JSON string that grows across subsequent chunks — this class merges
    them exactly as the pre-extraction inline code did.

    Single-stream use (one instance per _generate_stream call). Pure data
    operations — no I/O, no clock reads.
    """

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        # tool_calls_acc[index] = {"id", "name", "arguments_str"}
        self.tool_calls_acc: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.usage: dict = {}

    # -- delta ingestion -------------------------------------------------
    def add_content_delta(self, text: str) -> None:
        if text:
            self.content_parts.append(text)

    def add_reasoning_delta(self, text: str) -> None:
        if text:
            self.reasoning_parts.append(text)

    def add_tool_call_delta(self, tc_delta) -> list[str]:
        """Merge a tool_calls delta (list form or single-dict form).

        Returns reasoning_content strings stashed ON the delta (some
        backends put them there) so the CALLER decides how to render
        them — the accumulator stays print-free.
        """
        stashed_reasoning: list[str] = []
        if isinstance(tc_delta, list):
            for tc_d in tc_delta:
                idx = tc_d.get("index", 0)
                slot = self.tool_calls_acc.setdefault(
                    idx,
                    {
                        "id": "",
                        "name": "",
                        "arguments_str": "",
                    },
                )
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
            slot = self.tool_calls_acc.setdefault(
                idx,
                {
                    "id": "",
                    "name": "",
                    "arguments_str": "",
                },
            )
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
                    stashed_reasoning.append(rc)
        return stashed_reasoning

    def set_finish_reason(self, fr) -> None:
        if fr:
            self.finish_reason = fr

    def set_usage(self, usage) -> None:
        if usage:
            self.usage = usage

    # -- assembly --------------------------------------------------------
    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)

    def _assemble_tool_calls(self) -> list[dict]:
        """Assemble tool calls in index order, parsing the accumulated
        arguments JSON string into a dict. If parsing fails (model emitted
        malformed JSON across chunks), fall back to a raw wrapper so the
        agent loop can surface the bad payload rather than crashing.

        Known gap (documented in the brief, landmine #5): this fallback is
        silent — unlike _parse_react's ROB-13 debug chain. Kept silent here
        to stay behavior-identical; wiring a debug chain is a separate
        finding-shaped task.
        """
        assembled = []
        for idx in sorted(self.tool_calls_acc.keys()):
            slot = self.tool_calls_acc[idx]
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
            assembled.append(
                {
                    "id": slot["id"] or f"call_{idx}",
                    "name": slot["name"],
                    "arguments": args,
                }
            )
        return assembled

    def finalize(self) -> dict:
        """Produce the same dict shape as _generate() (non-cancelled path).

        Some models (e.g. glm-5.3-flash-free via OrcaRouter) put the
        actual answer in reasoning_content instead of content. If content
        is empty but reasoning_content exists AND there are no tool calls,
        the model intended the reasoning as its answer — promoted to
        content WITHOUT re-printing (it already streamed in the panel).
        """
        content_str = self.content
        reasoning_str = self.reasoning
        if not content_str and reasoning_str and not self.tool_calls_acc:
            content_str = reasoning_str
            reasoning_str = ""
        return {
            "content": content_str,
            "tool_calls": self._assemble_tool_calls(),
            "usage": self.usage,
            "reasoning_content": reasoning_str,
            "_finish_reason": self.finish_reason or "stop",
        }

    def cancelled_response(self) -> dict:
        """The mid-stream Ctrl+C shape: raw accumulators, no promotion."""
        return {
            "content": self.content,
            "tool_calls": [],
            "usage": {},
            "reasoning_content": self.reasoning,
            "_finish_reason": "cancelled",
            "_cancelled": True,
        }


class StreamRenderer:
    """Renders the streaming chat UX: prefix, reasoning panel, content.

    Owns ALL stdout side-effects of _generate_stream so the orchestration
    loop stays pure. Pass ``out=io.StringIO()`` in tests to capture the
    exact byte stream; the default resolves sys.stdout AT WRITE TIME, so
    pytest's capsys keeps working.

    Rendered bytes are identical to the pre-extraction inline code,
    including the ANSI styling:
    - prefix:  ``\\033[92mAgentKthx:\\033[0m `` (bright green, once per step)
    - panel:   ``\\033[90m  reasoning:\\033[0m\\n`` (grey, once per step)
    - reasoning deltas: ``\\033[90m<indented>\\033[0m``
    """

    def __init__(self, out=None) -> None:
        self._out = out
        # PERF-01 readability: print the "AgentKthx: " prefix once, before
        # the first content/reasoning delta arrives. Reset per-step so each
        # model response (including follow-ups after tool calls) gets its
        # own prefix — the tool-call output visually separates the responses
        # and the user expects to see "AgentKthx:" before each one.
        self._prefix_emitted = False
        # R06.56: reasoning is displayed as a structured "reasoning:" panel
        # ABOVE the AgentKthx: prompt, not inline in dim-grey under it.
        self._reasoning_panel_started = False
        # Whether we've emitted any reasoning line yet — used to add the
        # 4-space indent on the very first panel line.
        self._reasoning_first_line_emitted = False
        # Whether we've done the reasoning→content transition newline.
        self._content_started = False

    # -- plumbing --------------------------------------------------------
    def _stream(self):
        return self._out if self._out is not None else sys.stdout

    def _write(self, text: str) -> None:
        stream = self._stream()
        stream.write(text)
        stream.flush()

    # -- rendering -------------------------------------------------------
    def emit_prefix_once(self) -> None:
        if self._prefix_emitted:
            return
        # If we printed a reasoning panel above, add a newline
        # before the AgentKthx: prefix so they don't run together.
        if self._reasoning_panel_started:
            self._write("\n")
        # Bright green to match the non-streaming "AgentKthx:" label.
        self._write("\033[92mAgentKthx:\033[0m ")
        self._prefix_emitted = True

    def emit_reasoning_panel_header(self) -> None:
        """Emit the 'reasoning:' header once, before the first reasoning
        delta is printed. Subsequent reasoning deltas append to the panel."""
        if self._reasoning_panel_started:
            return
        # Emit the "AgentKthx:" prefix before the reasoning panel so
        # the user sees it even when the model puts everything in
        # reasoning_content (no content delta ever arrives).
        # The prefix goes on its own line, then reasoning: below it.
        self.emit_prefix_once()
        self._write("\n")
        self._write("\033[90m  reasoning:\033[0m\n")
        self._reasoning_panel_started = True

    def indent_reasoning_delta(self, delta: str) -> str:
        """Indent a reasoning delta to match the non-streaming panel format
        (4 spaces under 'reasoning:').

        - On the first delta ever: prepend '    ' (4 spaces) so the first
          line is indented under the 'reasoning:' header.
        - For every delta: replace '\\n' with '\\n    ' so subsequent
          lines (mid-delta newlines) are also indented.
        """
        if not delta:
            return delta
        # Replace newlines with newline+4-spaces so each new line in
        # this delta is indented under the 'reasoning:' header.
        indented = delta.replace("\n", "\n    ")
        if not self._reasoning_first_line_emitted:
            # First line ever — prepend the 4-space indent.
            indented = "    " + indented
            self._reasoning_first_line_emitted = True
        return indented

    def write_reasoning(self, delta: str) -> None:
        """A reasoning_content delta: panel header + dim-grey indented text."""
        self.emit_reasoning_panel_header()
        indented = self.indent_reasoning_delta(delta)
        self._write(f"\033[90m{indented}\033[0m")

    def write_raw_reasoning(self, text: str) -> None:
        """Dim-grey text with NO panel header and NO indent — the
        reasoning_content stashed on tool_calls deltas, rendered exactly
        as the pre-extraction inline code did."""
        self._write(f"\033[90m{text}\033[0m")

    def write_content(self, delta: str) -> None:
        """A content delta on the openai_compat path: the one-time
        reasoning→content transition newline, then prefix, then text."""
        # If reasoning was just streamed (grey), add ONE newline
        # before the first content delta so content (white) starts
        # on its own line. Only do this once — not per-chunk.
        if self._reasoning_panel_started and self._prefix_emitted and not self._content_started:
            self._write("\n")
            self._content_started = True
        self.emit_prefix_once()
        self._write(delta)

    def write_plain(self, text: str) -> None:
        """Raw text with just the prefix — native path + no-stream fallback."""
        self.emit_prefix_once()
        self._write(text)

    def finish_stream(self, content: str) -> None:
        """End-of-stream newline so the next prompt / step summary appears
        on its own line (skipped when content already ends with one)."""
        if content and not content.endswith("\n"):
            self._write("\n")

    def note_cancelled(self) -> None:
        """Newline so the next prompt isn't on the same line after Ctrl+C."""
        self._write("\n")


class StreamingMixin:
    """Mixin providing the streaming generators for Agent.

    R07.00 Phase 6: moved verbatim from ``agent.py`` (R06.58 state).
    ``Agent(..., StreamingMixin)`` — all call sites
    (``agent.run_stream(...)``, ``self._generate_stream()``) unchanged.
    """

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
                            print(describe_terminal(e, self.max_api_retries, _api_wait_total))
                        elif not _transient:
                            print(f"  [Resilience] Fatal API error — " f"not retrying: {e}")
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
                    if hasattr(call, "thought") and call.thought:
                        reasoning_item = ReasoningItem(content=[OutputText(text=call.thought)])
                        reasoning_item.status = ItemStatus.COMPLETED
                        response.add_output_item(reasoning_item)

                    tool_calls_found.append(
                        {
                            "name": call.name,
                            "arguments": call.arguments,
                            "id": "",
                            "final_answer": getattr(call, "final_answer", None),
                        }
                    )

            # ---- Execute tool calls if found ----
            if tool_calls_found:
                # Final Answer enforcement (same logic as run())
                if _expecting_final_answer and _last_successful_result is not None:
                    text_chunks_gen = iter([_last_successful_result])
                    for sse_event in stream_response_events(
                        Response(
                            model=self.model,
                            status=ResponseStatus.IN_PROGRESS,
                            tool_choice=self.tool_choice,
                            allowed_tools=self._allowed_tools or [],
                        ),
                        text_chunks_gen,
                        debug=self.debug,
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
                        error_msg = (
                            f"Tool '{tool_name}' not in allowed_tools: {self._allowed_tools}"
                        )
                        self.memory.add("user", f"Observation: Error: {error_msg}")
                        continue

                    # R06.52: identical-repeat guard (mirror of run()).
                    if self._error_tracker.should_block_repeat(tool_name, tool_args):
                        blocked_msg = self._error_tracker.format_repeat_block(tool_name, tool_args)
                        if self.debug:
                            print(
                                f"  [ErrorRecovery] Blocking repeated identical call: {tool_name}({tool_args})"
                            )
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
                        result = self._execute_tool(tool_name, tool_args)
                    except KeyboardInterrupt:
                        fc_item.status = ItemStatus.FAILED
                        response.mark_cancelled(debug=self.debug)
                        # Yield cancellation event and stop
                        # R07.01: was `ResponseStateEvent` (undefined — NameError
                        # on the Ctrl+C-during-tool-exec path since original
                        # agent.py:1499); fixed to ResponseEvent, matching the
                        # fail_event pattern above.
                        cancel_event = ResponseEvent(
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
                        from .error_recovery import _is_simple_result

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
                        Response(
                            model=self.model,
                            status=ResponseStatus.IN_PROGRESS,
                            tool_choice=self.tool_choice,
                            allowed_tools=self._allowed_tools or [],
                        ),
                        text_chunks_gen,
                        debug=self.debug,
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

            for sse_event in stream_response_events(
                final_response, text_chunks_gen, debug=self.debug
            ):
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
            from .model_family_config import needs_no_think_directive

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
        # R07.17: forward num_batch (Ollama per-request option; ignored by
        # backends that don't support it — see agent.py:_generate for details).
        if self._num_batch is not None:
            backend_kwargs["num_batch"] = self._num_batch

        # R06.3: Forward runtime kwargs set via /param slash command.
        # These are params that don't have a dedicated agent attribute
        # (top_k, seed, n, presence_penalty, frequency_penalty).
        # They're stashed on agent._runtime_kwargs by /param in cli.py.
        if hasattr(self, "_runtime_kwargs") and self._runtime_kwargs:
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
        if hasattr(self.backend, "generate_stream"):
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
        elif hasattr(self.backend, "generate_completions_stream"):
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
                yield content[i : i + chunk_size]

    def _generate_stream(self) -> dict:
        """Stream a response from the backend, printing deltas to stdout.

        Mirrors ``_generate()`` but uses ``backend.generate_completions_stream()``
        (when available) and prints content / reasoning_content chunks to
        stdout as they arrive — the typewriter effect users expect from
        ``stream=True``. Falls back to ``_generate()`` printed in one shot
        when the backend has no streaming method.

        MAINT-08 (R07.15): the method used to be 354 lines with 3 inline
        closures, 3 accumulator collections and stdout writes woven through
        5-level try/except nesting. The data seam now lives in
        ``StreamAccumulator`` (delta merging + finalize) and the
        presentation seam in ``StreamRenderer`` (prefix / reasoning panel /
        content writes) — what remains here is orchestration. Return-dict
        shape and rendered bytes are unchanged.

        Returns:
            dict with keys: content, tool_calls, usage, finish_reason,
            reasoning_content, _finish_reason, _cancelled
        """
        messages = self.memory.get_messages()
        params = self._prepare_stream_params(messages)

        acc = StreamAccumulator()
        renderer = StreamRenderer()

        if params["stream_method"] is None:
            # Backend has no streaming — fall back to non-streaming and
            # print the result in one shot. Don't pretend to stream.
            if self.debug:
                print("  [Stream] backend has no streaming method — falling back to _generate()")
            response = self.backend.generate(
                model=self.model,
                messages=messages,
                tools=params["tools_for_backend"],
                temperature=params["gen_temperature"],
                max_tokens=params["gen_max_tokens"],
                top_p=params["gen_top_p"],
                **params["backend_kwargs"],
            )
            # Print content as a single chunk (still gives the user feedback
            # that generation completed).
            content = response.get("content", "") or ""
            if content:
                renderer.write_plain(content)
                renderer.finish_stream(content)
            response["_finish_reason"] = response.get("finish_reason", "stop")
            return response

        if self.debug:
            print(f"  [Stream] using {params['stream_method']} backend streaming")

        stream_gen = None
        try:
            if params["stream_method"] == "openai_compat":
                stream_gen = self.backend.generate_completions_stream(
                    model=self.model,
                    messages=messages,
                    tools=params["tools_for_backend"],
                    temperature=params["gen_temperature"],
                    max_tokens=params["gen_max_tokens"],
                    top_p=params["gen_top_p"],
                    **params["backend_kwargs"],
                )
                for chunk in stream_gen:
                    delta = chunk.get("delta", "") or ""
                    tc_delta = chunk.get("tool_calls")
                    acc.set_finish_reason(chunk.get("finish_reason"))
                    # Capture usage from the final usage-only chunk
                    # (arrives when stream_options.include_usage=True)
                    acc.set_usage(chunk.get("_usage"))
                    # Content delta — print immediately
                    if delta:
                        renderer.write_content(delta)
                        acc.add_content_delta(delta)
                    # Reasoning delta — R06.56: shown as a structured
                    # "reasoning:" panel ABOVE the AgentKthx: prompt (the
                    # panel streams first, before content — avoiding the
                    # duplicate reasoning display the cmd_chat path used
                    # to print after the answer).
                    reasoning_delta = ""
                    if isinstance(tc_delta, dict) and "reasoning_content" in tc_delta:
                        reasoning_delta = tc_delta["reasoning_content"] or ""
                    elif isinstance(chunk, dict) and chunk.get("reasoning_content"):
                        reasoning_delta = chunk["reasoning_content"]
                    if reasoning_delta:
                        renderer.write_reasoning(reasoning_delta)
                        acc.add_reasoning_delta(reasoning_delta)
                    # Tool-call delta merging (OpenAI split-chunk format:
                    # [{"index": 0, "id": "...", "function": {"name": "...",
                    #  "arguments": "..."}}] — the accumulator handles both
                    # the list and single-dict shapes)
                    if tc_delta:
                        for rc in acc.add_tool_call_delta(tc_delta):
                            renderer.write_raw_reasoning(rc)
            else:
                # native generate_stream — text only, no tool_calls in stream
                stream_gen = self.backend.generate_stream(
                    model=self.model,
                    messages=messages,
                    tools=params["tools_for_backend"],
                    temperature=params["gen_temperature"],
                    max_tokens=params["gen_max_tokens"],
                    top_p=params["gen_top_p"],
                    **params["backend_kwargs"],
                )
                for chunk in stream_gen:
                    if isinstance(chunk, str):
                        renderer.write_plain(chunk)
                        acc.add_content_delta(chunk)
                    elif isinstance(chunk, dict):
                        delta = chunk.get("delta", "") or chunk.get("content", "") or ""
                        if delta:
                            renderer.write_plain(delta)
                            acc.add_content_delta(delta)
                        acc.set_finish_reason(chunk.get("finish_reason"))
        except KeyboardInterrupt:
            # User cancelled mid-stream. Close the stream generator
            # explicitly so the underlying HTTP connection is released
            # deterministically rather than waiting for GC. Without this,
            # the urllib response in the backend's _iter_sse_lines is
            # abandoned mid-iteration and may stay open until GC runs,
            # which can exhaust connection limits on long sessions with
            # many Ctrl+C interrupts. See ROB-05 (R06.57).
            try:
                if stream_gen is not None:
                    stream_gen.close()
            except Exception:
                pass
            # Newline so the next prompt isn't on the same line
            renderer.note_cancelled()
            return acc.cancelled_response()

        result = acc.finalize()
        # End of stream — print a newline if content didn't end with one
        # so the next prompt / step summary appears on its own line.
        renderer.finish_stream(result["content"])

        if self.debug:
            print(f"  [Stream] content: {result['content'][:100]!r}")
            print(f"  [Stream] tool_calls assembled: {result['tool_calls']}")
            print(f"  [Stream] finish_reason: {acc.finish_reason}")

        return result

    def _prepare_stream_params(self, messages: list[dict]) -> dict:
        """Resolve generation kwargs + pick the streaming method for one
        streamed response (MAINT-08: moved verbatim out of _generate_stream).

        Mirrors _generate()'s resolution: think directive, reasoning_effort,
        runtime kwargs, stop tokens, tool_choice, response_format,
        truncation, temperature/max_tokens/top_p with the R06.57
        num_ctx//32 cap, and the openai_compat > native stream preference
        (chat/completions SSE carries tool_calls deltas; the native Ollama
        generate_stream is text-only).
        """
        # Resolve think / reasoning_effort / kwargs exactly like _generate()
        think = self._think
        if think is None and self.model_family:
            from .model_family_config import needs_no_think_directive

            if needs_no_think_directive(self.model_family):
                think = False

        backend_kwargs = {"think": think}
        if self._reasoning_effort is not None:
            backend_kwargs["reasoning_effort"] = self._reasoning_effort
        if self.num_ctx is not None:
            backend_kwargs["num_ctx"] = self.num_ctx
        if self._num_predict is not None:
            backend_kwargs["num_predict"] = self._num_predict
        # R07.17: forward num_batch (Ollama per-request option; silently
        # dropped by backends that don't recognize it).
        if self._num_batch is not None:
            backend_kwargs["num_batch"] = self._num_batch
        if hasattr(self, "_runtime_kwargs") and self._runtime_kwargs:
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
        gen_temperature = (
            self._temperature
            if self._temperature is not None
            else self.model_config.default_temperature
        )
        gen_max_tokens = (
            self._num_predict
            if self._num_predict is not None
            else self.model_config.default_max_tokens
        )
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
        if hasattr(self.backend, "generate_completions_stream"):
            stream_method = "openai_compat"
        elif hasattr(self.backend, "generate_stream"):
            stream_method = "native"

        return {
            "backend_kwargs": backend_kwargs,
            "tools_for_backend": tools_for_backend,
            "gen_temperature": gen_temperature,
            "gen_max_tokens": gen_max_tokens,
            "gen_top_p": gen_top_p,
            "stream_method": stream_method,
        }
