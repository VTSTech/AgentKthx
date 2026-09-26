"""
⚛️ AgentKthx — OpenResponses Types
Implements the OpenResponses specification for multi-provider, interoperable LLM interfaces.
Specification: https://www.openresponses.org/specification

Key Concepts:
1. Items: Atomic units of context
   - message: A conversation turn (user or assistant)
   - function_call: A tool invocation request
   - function_call_output: The result of a tool execution
   - reasoning: Model's internal thought process (optional)

2. State Machines: Objects have lifecycle states
   - Response: queued → in_progress → completed/failed/incomplete/cancelled
   - Items: in_progress → completed/failed/incomplete

3. tool_choice: Control tool invocation behavior
   - "auto": Model may call tools or respond directly (default)
   - "required": Model MUST call at least one tool
   - "none": Model MUST NOT call any tools
   - {"type": "function", "name": "tool"}: Force specific tool
   - {"type": "allowed_tools", "tools": [...]}: Restrict to tool list

4. allowed_tools: Hard constraint on which tools can be invoked
   - Server MUST reject/suppress calls to tools not in this list

5. Agentic Loop:
   - Model samples from input
   - If tool call: execute tool, return observation, continue
   - If no tool call: return final output items

6. Streaming Events: Semantic delta events for state transitions
   - response.queued, response.in_progress, response.completed
   - response.output_item.added, response.output_item.done
   - response.output_text.delta, response.output_text.done

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Literal, Optional, Union


def _should_show_openresponses_debug(debug: bool) -> bool:
    """Check if OpenResponses debug output should be shown."""
    if not debug:
        return False
    # Suppress OpenResponses debug when using Chat-Completions mode
    return os.environ.get("AGENTKTHX_API_MODE", "resp") != "comp"


# ============================================================================
# Response Status (State Machine)
# ============================================================================

class ResponseStatus(Enum):
    """
    Response lifecycle states as defined by OpenResponses.
    
    States:
        QUEUED: Response is waiting to be processed
        IN_PROGRESS: Model is currently generating
        COMPLETED: Response finished successfully
        FAILED: Response encountered an error
        INCOMPLETE: Response exceeded token budget
        CANCELLED: Response was cancelled by user
    """
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


class ItemStatus(Enum):
    """
    Item lifecycle states as defined by OpenResponses.
    
    States:
        IN_PROGRESS: Model is emitting tokens for this item
        COMPLETED: Item is fully sampled/executed
        INCOMPLETE: Token budget exhausted while emitting
        FAILED: Item execution failed
    """
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


# ============================================================================
# Tool Choice
# ============================================================================

class ToolChoiceType(Enum):
    """
    Controls whether and how the model can invoke tools.
    
    AUTO: Model may call tools or respond directly (default)
    REQUIRED: Model MUST call at least one tool
    NONE: Model MUST NOT call any tools
    SPECIFIC: Model must call a specific tool
    ALLOWED_TOOLS: Model can only use tools from a specific list
    """
    AUTO = "auto"
    REQUIRED = "required"
    NONE = "none"
    SPECIFIC = "function"  # Per spec: {"type": "function", "name": "fn_name"}
    ALLOWED_TOOLS = "allowed_tools"  # Per spec: {"type": "allowed_tools", "tools": [...]}


@dataclass
class ToolChoice:
    """
    Tool choice configuration per OpenResponses spec.
    
    Modes:
        "auto" (default): Model may call tools or respond directly
        "required": Model MUST call at least one tool
        "none": Model MUST NOT call any tools
        {"type": "function", "name": "fn_name"}: Force specific tool
        {"type": "allowed_tools", "tools": [...]}: Restrict to tool list
    
    Examples:
        ToolChoice("auto")  # Default
        ToolChoice("required")  # Must call at least one tool
        ToolChoice("none")  # No tools allowed
        ToolChoice.specific("calculator")  # Must call calculator
        ToolChoice.allowed_tools(["calculator", "shell"])  # Only these tools
    """
    type: ToolChoiceType = ToolChoiceType.AUTO
    name: str | None = None  # For SPECIFIC type
    tools: list[str] | None = None  # For ALLOWED_TOOLS type
    
    def __init__(
        self, 
        value: str | ToolChoiceType = "auto",
        name: str | None = None,
        tools: list[str] | None = None,
    ):
        if isinstance(value, ToolChoiceType):
            self.type = value
        elif value == "auto":
            self.type = ToolChoiceType.AUTO
        elif value == "required":
            self.type = ToolChoiceType.REQUIRED
        elif value == "none":
            self.type = ToolChoiceType.NONE
        elif value == "function":
            self.type = ToolChoiceType.SPECIFIC
        elif value == "allowed_tools":
            self.type = ToolChoiceType.ALLOWED_TOOLS
        else:
            # Treat as specific tool name
            self.type = ToolChoiceType.SPECIFIC
            self.name = value
        # Only override name if explicitly provided
        if name is not None:
            self.name = name
        if tools is not None:
            self.tools = tools
    
    @classmethod
    def specific(cls, tool_name: str) -> "ToolChoice":
        """Create a tool choice that forces a specific tool."""
        instance = cls.__new__(cls)
        instance.type = ToolChoiceType.SPECIFIC
        instance.name = tool_name
        instance.tools = None
        return instance
    
    @classmethod
    def allowed_tools(cls, tool_names: list[str]) -> "ToolChoice":
        """Create a tool choice that restricts to a list of tools."""
        instance = cls.__new__(cls)
        instance.type = ToolChoiceType.ALLOWED_TOOLS
        instance.name = None
        instance.tools = tool_names
        return instance
    
    def to_dict(self) -> dict:
        """Serialize to dict for API."""
        if self.type == ToolChoiceType.SPECIFIC:
            return {
                "type": "function",
                "name": self.name
            }
        if self.type == ToolChoiceType.ALLOWED_TOOLS:
            return {
                "type": "allowed_tools",
                "tools": [{"type": "function", "name": t} for t in (self.tools or [])]
            }
        return {"type": self.type.value}


# ============================================================================
# Content Types (UserContent vs ModelContent)
# ============================================================================

@dataclass
class InputText:
    """Text content from user."""
    type: Literal["input_text"] = "input_text"
    text: str = ""
    
    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}


@dataclass
class InputImage:
    """Image content from user (base64 or URL)."""
    type: Literal["input_image"] = "input_image"
    source: str = ""  # base64 data or URL
    media_type: str = "image/png"
    
    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "source": self.source,
            "media_type": self.media_type
        }


@dataclass
class OutputText:
    """Text content from model."""
    type: Literal["output_text"] = "output_text"
    text: str = ""
    annotations: list[dict] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "annotations": self.annotations
        }


@dataclass
class SummaryText:
    """Summary text for reasoning items."""
    type: Literal["summary_text"] = "summary_text"
    text: str = ""
    
    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}


# Union types
UserContent = Union[InputText, InputImage]
ModelContent = Union[OutputText]


# ============================================================================
# Items
# ============================================================================

def _generate_id(prefix: str = "item") -> str:
    """Generate a unique item ID."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


@dataclass
class MessageItem:
    """
    Message item - represents a conversation turn.
    
    Items are the fundamental unit of context in OpenResponses.
    They can be provided as inputs or outputs.
    """
    id: str = field(default_factory=lambda: _generate_id("msg"))
    type: Literal["message"] = "message"
    role: str = "assistant"  # "user", "assistant", "system"
    status: ItemStatus = ItemStatus.COMPLETED
    content: list[ModelContent | UserContent] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "role": self.role,
            "status": self.status.value,
            "content": [c.to_dict() for c in self.content]
        }


@dataclass
class FunctionCallItem:
    """
    Function call item - represents a tool invocation.
    
    For developer-hosted tools (OpenResponses):
    - The developer executes the tool
    - Returns the result in a follow-up request via FunctionCallOutputItem
    
    Status transitions:
    - in_progress: Tool is being invoked
    - completed: Tool executed successfully
    - failed: Tool execution failed (error field contains details)
    - incomplete: Token budget exhausted
    """
    id: str = field(default_factory=lambda: _generate_id("fc"))
    type: Literal["function_call"] = "function_call"
    name: str = ""
    call_id: str = ""  # Unique identifier for the call
    arguments: str = "{}"  # JSON string
    status: ItemStatus = ItemStatus.COMPLETED
    error: dict | None = None  # Error details if status is FAILED
    
    def __post_init__(self):
        if not self.call_id:
            self.call_id = f"call_{uuid.uuid4().hex[:12]}"
    
    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "call_id": self.call_id,
            "arguments": self.arguments,
            "status": self.status.value
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class FunctionCallOutputItem:
    """
    Function call output - the result of a tool execution.
    
    For developer-hosted tools (OpenResponses):
    - This is provided as input when returning tool results
    - The call_id must match the function_call's call_id
    - The output field contains the tool result (or error message)
    
    Status transitions:
    - completed: Tool executed and result available
    - failed: Tool execution failed (output contains error message)
    """
    id: str = field(default_factory=lambda: _generate_id("fco"))
    type: Literal["function_call_output"] = "function_call_output"
    call_id: str = ""  # Must match the function_call's call_id
    output: str = ""  # Result of the tool call (or error message if failed)
    status: ItemStatus = ItemStatus.COMPLETED
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "call_id": self.call_id,
            "output": self.output,
            "status": self.status.value
        }


@dataclass
class ReasoningItem:
    """
    Reasoning item - exposes the model's internal thought process.
    
    Providers may expose:
    - content: raw reasoning trace
    - encrypted_content: protected reasoning (opaque to client)
    - summary: summarized reasoning steps
    """
    id: str = field(default_factory=lambda: _generate_id("rsn"))
    type: Literal["reasoning"] = "reasoning"
    status: ItemStatus = ItemStatus.COMPLETED
    content: list[OutputText] = field(default_factory=list)
    encrypted_content: str | None = None
    summary: list[SummaryText] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "content": [c.to_dict() for c in self.content],
            "encrypted_content": self.encrypted_content,
            "summary": [s.to_dict() for s in self.summary]
        }


# Union of all item types
Item = Union[MessageItem, FunctionCallItem, FunctionCallOutputItem, ReasoningItem]


# ============================================================================
# Response Object
# ============================================================================

@dataclass
class Response:
    """
    Response object - the main output of the API.
    
    A Response contains:
    - id: Unique identifier for the response
    - status: Current state in the lifecycle
    - model: Model used for generation
    - input: The input items that were provided
    - output: The output items generated
    - usage: Token usage statistics
    - created_at: Timestamp of creation
    - completed_at: Timestamp of completion (if completed)
    - error: Error information (if failed)
    """
    id: str = field(default_factory=lambda: _generate_id("resp"))
    status: ResponseStatus = ResponseStatus.QUEUED
    model: str = ""
    input: list[Item] = field(default_factory=list)
    output: list[Item] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: dict | None = None
    
    # Configuration
    tool_choice: ToolChoice = field(default_factory=ToolChoice)
    allowed_tools: list[str] = field(default_factory=list)
    previous_response_id: str | None = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "model": self.model,
            "input": [i.to_dict() for i in self.input],
            "output": [i.to_dict() for i in self.output],
            "usage": self.usage,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "tool_choice": self.tool_choice.to_dict(),
            "allowed_tools": self.allowed_tools,
            "previous_response_id": self.previous_response_id
        }
    
    def mark_in_progress(self, debug: bool = False) -> None:
        """Transition to in_progress state."""
        if self.status == ResponseStatus.QUEUED:
            old_status = self.status
            self.status = ResponseStatus.IN_PROGRESS
            if _should_show_openresponses_debug(debug):
                print(f"[OpenResponses.Response] State transition: {old_status.value} -> {self.status.value}")
    
    def mark_completed(self, debug: bool = False) -> None:
        """Transition to completed state."""
        old_status = self.status
        self.status = ResponseStatus.COMPLETED
        self.completed_at = time.time()
        if _should_show_openresponses_debug(debug):
            print(f"[OpenResponses.Response] State transition: {old_status.value} -> {self.status.value}")
    
    def mark_failed(self, error: dict, debug: bool = False) -> None:
        """Transition to failed state."""
        old_status = self.status
        self.status = ResponseStatus.FAILED
        self.error = error
        self.completed_at = time.time()
        if _should_show_openresponses_debug(debug):
            print(f"[OpenResponses.Response] State transition: {old_status.value} -> {self.status.value}")
            print(f"[OpenResponses.Response] Error: {error}")
    
    def mark_incomplete(self, debug: bool = False) -> None:
        """Transition to incomplete state (token budget exhausted)."""
        old_status = self.status
        self.status = ResponseStatus.INCOMPLETE
        self.completed_at = time.time()
        if _should_show_openresponses_debug(debug):
            print(f"[OpenResponses.Response] State transition: {old_status.value} -> {self.status.value}")

    def mark_cancelled(self, debug: bool = False) -> None:
        """Transition to cancelled state (user interrupted generation)."""
        old_status = self.status
        self.status = ResponseStatus.CANCELLED
        self.completed_at = time.time()
        if _should_show_openresponses_debug(debug):
            print(f"[OpenResponses.Response] State transition: {old_status.value} -> {self.status.value}")
    
    def add_output_item(self, item: Item, debug: bool = False) -> None:
        """Add an output item."""
        self.output.append(item)
        if _should_show_openresponses_debug(debug):
            print(f"[OpenResponses.Response] Output item added: id={item.id}, type={item.type}")
    
# ============================================================================
# Streaming Events
# ============================================================================

class EventType(Enum):
    """
    Streaming event types as defined by OpenResponses.
    
    State Machine Events:
        RESPONSE_QUEUED: Response is queued
        RESPONSE_IN_PROGRESS: Response started
        RESPONSE_COMPLETED: Response finished
        RESPONSE_FAILED: Response failed
        RESPONSE_INCOMPLETE: Response incomplete
    
    Delta Events:
        OUTPUT_ITEM_ADDED: New output item added
        OUTPUT_ITEM_DONE: Output item completed
        CONTENT_PART_ADDED: New content part added
        CONTENT_PART_DONE: Content part completed
        OUTPUT_TEXT_DELTA: Text delta
        OUTPUT_TEXT_DONE: Text completed
        FUNCTION_CALL_ARGUMENTS_DELTA: Function args delta
        FUNCTION_CALL_ARGUMENTS_DONE: Function args completed
    """
    # State machine events
    RESPONSE_QUEUED = "response.queued"
    RESPONSE_IN_PROGRESS = "response.in_progress"
    RESPONSE_COMPLETED = "response.completed"
    RESPONSE_FAILED = "response.failed"
    RESPONSE_INCOMPLETE = "response.incomplete"
    
    # Delta events
    OUTPUT_ITEM_ADDED = "response.output_item.added"
    OUTPUT_ITEM_DONE = "response.output_item.done"
    CONTENT_PART_ADDED = "response.content_part.added"
    CONTENT_PART_DONE = "response.content_part.done"
    OUTPUT_TEXT_DELTA = "response.output_text.delta"
    OUTPUT_TEXT_DONE = "response.output_text.done"
    FUNCTION_CALL_ARGUMENTS_DELTA = "response.function_call_arguments.delta"
    FUNCTION_CALL_ARGUMENTS_DONE = "response.function_call_arguments.done"


@dataclass
class StreamEvent:
    """
    Base class for streaming events.
    
    Events describe meaningful transitions in the response lifecycle.
    They are either state transitions or deltas from previous state.
    """
    type: EventType
    sequence_number: int = 0
    
    def to_sse(self) -> str:
        """Serialize to Server-Sent Events format."""
        import json
        data = self.to_dict()
        return f"event: {self.type.value}\ndata: {json.dumps(data)}\n\n"
    
    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "type": self.type.value,
            "sequence_number": self.sequence_number
        }


@dataclass
class ResponseEvent(StreamEvent):
    """State machine event for response lifecycle."""
    response: Response | None = None
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.response:
            d["response"] = self.response.to_dict()
        return d


@dataclass
class OutputItemEvent(StreamEvent):
    """Event for output item changes."""
    item: Item | None = None
    output_index: int = 0
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["output_index"] = self.output_index
        if self.item:
            d["item"] = self.item.to_dict()
        return d


@dataclass
class ContentPartEvent(StreamEvent):
    """Event for content part changes."""
    item_id: str = ""
    output_index: int = 0
    content_index: int = 0
    part: ModelContent | UserContent | None = None
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["item_id"] = self.item_id
        d["output_index"] = self.output_index
        d["content_index"] = self.content_index
        if self.part:
            d["part"] = self.part.to_dict()
        return d


@dataclass
class TextDeltaEvent(StreamEvent):
    """Event for text delta."""
    item_id: str = ""
    output_index: int = 0
    content_index: int = 0
    delta: str = ""
    text: str = ""  # Full text so far (for output_text.done)
    
    def to_dict(self) -> dict:
        d = super().to_dict()
        d["item_id"] = self.item_id
        d["output_index"] = self.output_index
        d["content_index"] = self.content_index
        if self.delta:
            d["delta"] = self.delta
        if self.text:
            d["text"] = self.text
        return d


# ============================================================================
# Error Types
# ============================================================================

@dataclass
class Error:
    """
    Error object as defined by OpenResponses.
    
    Types:
        server_error: Internal server failure (500)
        invalid_request: Malformed request (400)
        not_found: Resource not found (404)
        model_error: Model execution error (500)
        too_many_requests: Rate limited (429)
    """
    message: str
    type: str = "server_error"  # server_error, invalid_request, not_found, model_error, too_many_requests
    code: str | None = None  # Specific error code
    param: str | None = None  # Related parameter
    
    def to_dict(self) -> dict:
        d = {"message": self.message, "type": self.type}
        if self.code:
            d["code"] = self.code
        if self.param:
            d["param"] = self.param
        return d


# ============================================================================
# Request Configuration
# ============================================================================

@dataclass
class RequestConfig:
    """
    Configuration for an API request.
    
    This replaces the various kwargs and configuration options
    with a structured, OpenResponses-compliant configuration.
    """
    model: str = ""
    
    # Tool configuration
    tools: list[dict] = field(default_factory=list)  # Tool definitions
    tool_choice: ToolChoice = field(default_factory=ToolChoice)
    allowed_tools: list[str] = field(default_factory=list)
    
    # Conversation continuation
    previous_response_id: str | None = None
    
    # Generation parameters
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    
    # Truncation behavior
    truncation: Literal["auto", "disabled"] = "auto"
    
    # Service tier (priority hint)
    service_tier: Literal["standard", "priority", "batch"] = "standard"
    
    # Debug options
    debug: bool = False
    
    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "tools": self.tools,
            "tool_choice": self.tool_choice.to_dict(),
            "allowed_tools": self.allowed_tools,
            "previous_response_id": self.previous_response_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "truncation": self.truncation,
            "service_tier": self.service_tier
        }


# ============================================================================
# Helper Functions
# ============================================================================

def create_message_item(
    role: str,
    text: str,
    status: ItemStatus = ItemStatus.COMPLETED
) -> MessageItem:
    """Create a simple message item with text content."""
    content = [OutputText(text=text)] if role == "assistant" else [InputText(text=text)]
    return MessageItem(
        role=role,
        status=status,
        content=content
    )


def create_function_call_item(
    name: str,
    arguments: dict | str,
    call_id: str | None = None
) -> FunctionCallItem:
    """Create a function call item."""
    import json
    args_str = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return FunctionCallItem(
        name=name,
        arguments=args_str,
        call_id=call_id or ""
    )


def create_function_call_output(
    call_id: str,
    output: str
) -> FunctionCallOutputItem:
    """Create a function call output item."""
    return FunctionCallOutputItem(
        call_id=call_id,
        output=output
    )


# Alias for consistency
create_function_call_output_item = create_function_call_output


# ============================================================================
# OpenResponses Streaming Generator (v1.0 - 100% Spec Compliance)
# ============================================================================

from typing import Generator as TypingGenerator


def stream_response_events(
    response: Response,
    text_chunks: TypingGenerator[str, None, None],
    debug: bool = False,
) -> TypingGenerator[str, None, None]:
    """
    Generate OpenResponses-compliant SSE events from text chunks.
    
    This generator wraps a text stream and emits proper OpenResponses
    streaming events following the specification:
    
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
        response: The Response object to stream
        text_chunks: Generator yielding text chunks
        debug: Enable debug output
    
    Yields:
        SSE-formatted strings (event: ...\\ndata: ...\\n\\n)
    """
    sequence = 0
    
    # 1. Emit response.queued event
    response.status = ResponseStatus.QUEUED
    event = ResponseEvent(
        type=EventType.RESPONSE_QUEUED,
        sequence_number=sequence,
        response=response,
    )
    yield event.to_sse()
    sequence += 1
    
    # 2. Emit response.in_progress event
    response.mark_in_progress(debug=debug)
    event = ResponseEvent(
        type=EventType.RESPONSE_IN_PROGRESS,
        sequence_number=sequence,
        response=response,
    )
    yield event.to_sse()
    sequence += 1
    
    # Create output message item
    msg_item = MessageItem(
        role="assistant",
        status=ItemStatus.IN_PROGRESS,
        content=[],
    )
    output_index = len(response.output)
    response.output.append(msg_item)
    
    # 3. Emit output_item.added event
    event = OutputItemEvent(
        type=EventType.OUTPUT_ITEM_ADDED,
        sequence_number=sequence,
        item=msg_item,
        output_index=output_index,
    )
    yield event.to_sse()
    sequence += 1
    
    # Create output text content
    output_text = OutputText(text="")
    content_index = 0
    msg_item.content.append(output_text)
    
    # 4. Emit content_part.added event
    event = ContentPartEvent(
        type=EventType.CONTENT_PART_ADDED,
        sequence_number=sequence,
        item_id=msg_item.id,
        output_index=output_index,
        content_index=content_index,
        part=output_text,
    )
    yield event.to_sse()
    sequence += 1
    
    # 5. Stream text deltas
    full_text = ""
    try:
        for chunk in text_chunks:
            if chunk:
                full_text += chunk
                output_text.text = full_text
                
                # Emit output_text.delta event
                event = TextDeltaEvent(
                    type=EventType.OUTPUT_TEXT_DELTA,
                    sequence_number=sequence,
                    item_id=msg_item.id,
                    output_index=output_index,
                    content_index=content_index,
                    delta=chunk,
                )
                yield event.to_sse()
                sequence += 1
    except Exception as e:
        # Handle streaming error
        response.mark_failed({"message": str(e), "type": "stream_error"}, debug=debug)
        event = ResponseEvent(
            type=EventType.RESPONSE_FAILED,
            sequence_number=sequence,
            response=response,
        )
        yield event.to_sse()
        return
    
    # 6. Emit output_text.done event
    output_text.text = full_text
    event = TextDeltaEvent(
        type=EventType.OUTPUT_TEXT_DONE,
        sequence_number=sequence,
        item_id=msg_item.id,
        output_index=output_index,
        content_index=content_index,
        text=full_text,
    )
    yield event.to_sse()
    sequence += 1
    
    # 7. Emit content_part.done event
    event = ContentPartEvent(
        type=EventType.CONTENT_PART_DONE,
        sequence_number=sequence,
        item_id=msg_item.id,
        output_index=output_index,
        content_index=content_index,
        part=output_text,
    )
    yield event.to_sse()
    sequence += 1
    
    # Mark item as completed
    msg_item.status = ItemStatus.COMPLETED
    
    # 8. Emit output_item.done event
    event = OutputItemEvent(
        type=EventType.OUTPUT_ITEM_DONE,
        sequence_number=sequence,
        item=msg_item,
        output_index=output_index,
    )
    yield event.to_sse()
    sequence += 1
    
    # 9. Emit response.completed event
    response.mark_completed(debug=debug)
    event = ResponseEvent(
        type=EventType.RESPONSE_COMPLETED,
        sequence_number=sequence,
        response=response,
    )
    yield event.to_sse()


def stream_function_call_events(
    response: Response,
    fc_item: FunctionCallItem,
    arguments_chunks: TypingGenerator[str, None, None],
    debug: bool = False,
) -> TypingGenerator[str, None, None]:
    """
    Generate OpenResponses-compliant SSE events for function call arguments streaming.
    
    Args:
        response: The Response object
        fc_item: The FunctionCallItem being streamed
        arguments_chunks: Generator yielding argument string chunks
        debug: Enable debug output
    
    Yields:
        SSE-formatted strings
    """
    sequence = 0
    output_index = len(response.output) - 1  # Already added
    
    # Emit output_item.added if not already done
    event = OutputItemEvent(
        type=EventType.OUTPUT_ITEM_ADDED,
        sequence_number=sequence,
        item=fc_item,
        output_index=output_index,
    )
    yield event.to_sse()
    sequence += 1
    
    # Stream argument deltas
    full_args = ""
    for chunk in arguments_chunks:
        if chunk:
            full_args += chunk
            fc_item.arguments = full_args
            
            event = TextDeltaEvent(
                type=EventType.FUNCTION_CALL_ARGUMENTS_DELTA,
                sequence_number=sequence,
                item_id=fc_item.id,
                output_index=output_index,
                content_index=0,
                delta=chunk,
            )
            yield event.to_sse()
            sequence += 1
    
    # Emit function_call_arguments.done
    event = TextDeltaEvent(
        type=EventType.FUNCTION_CALL_ARGUMENTS_DONE,
        sequence_number=sequence,
        item_id=fc_item.id,
        output_index=output_index,
        content_index=0,
        text=full_args,
    )
    yield event.to_sse()
    sequence += 1
    
    # Mark item as completed and emit output_item.done
    fc_item.status = ItemStatus.COMPLETED
    event = OutputItemEvent(
        type=EventType.OUTPUT_ITEM_DONE,
        sequence_number=sequence,
        item=fc_item,
        output_index=output_index,
    )
    yield event.to_sse()


__all__ = [
    # Status enums
    "ResponseStatus",
    "ItemStatus",
    
    # Tool choice
    "ToolChoiceType",
    "ToolChoice",
    
    # Content types
    "InputText",
    "InputImage",
    "OutputText",
    "SummaryText",
    "UserContent",
    "ModelContent",
    
    # Items
    "Item",
    "MessageItem",
    "FunctionCallItem",
    "FunctionCallOutputItem",
    "ReasoningItem",
    
    # Response
    "Response",
    
    # Events
    "EventType",
    "StreamEvent",
    "ResponseEvent",
    "OutputItemEvent",
    "ContentPartEvent",
    "TextDeltaEvent",
    
    # Error
    "Error",
    
    # Configuration
    "RequestConfig",
    
    # Helpers
    "create_message_item",
    "create_function_call_item",
    "create_function_call_output",
    "create_function_call_output_item",
    
    # Streaming (v1.0 - 100% Spec Compliance)
    "stream_response_events",
    "stream_function_call_events",
]