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
    describe_wait,
    describe_terminal,
    is_transient_api_error,
)
from .error_recovery import build_enhanced_observation, is_error_result
from .models import Tool
from .openresponses import (
    Response, ResponseStatus, ItemStatus,
    FunctionCallItem, ReasoningItem,
    OutputText,
    EventType, ResponseEvent, OutputItemEvent,
    ToolChoiceType,
    create_message_item, create_function_call_item, create_function_call_output,
    stream_response_events,
)


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

