"""
AgentKthx Core Module

This module contains the core types, models, and utilities
that form the foundation of the AgentKthx framework.
"""

# Error Recovery
from .error_recovery import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_TOTAL_FAILURES,
    GENERIC_ERROR_HINTS,
    TOOL_ALTERNATIVES,
    TOOL_ERROR_HINTS,
    TOOL_NAME_SUGGESTIONS,
    ErrorRecoveryTracker,
    ToolFailureRecord,
    build_enhanced_observation,
    extract_error_type,
    get_tool_suggestion,
    is_error_result,
)
from .helpers import (
    SecurityMode,
    detect_and_fix_repetition,
    fuzzy_match,
    get_security_mode,
    is_greeting_or_simple,
    is_safe_url,
    is_simple_answered_query,
    is_small_model,
    normalize_args,
    set_security_mode,
    strip_tool_prefix,
    synthesize_tool_args,
    validate_path,
)
from .memory import Memory, MemoryConfig
from .model_family_config import (
    FAMILY_CONFIGS,
    ModelFamilyConfig,
    get_family_config,
    get_few_shot_style,
    get_model_config,
    get_native_tool_hints,
    get_no_tools_system_prompt,
    get_preferred_temperature,
    get_react_system_suffix,
    get_stop_tokens,
    get_tool_format,
    has_known_issues,
    should_use_few_shot,
    supports_tools,
)
from .models import AgentRun, StepResult, Tool, ToolCall, ToolParam

# OpenResponses types
from .openresponses import (
    Error,
    FunctionCallItem,
    FunctionCallOutputItem,
    InputText,
    ItemStatus,
    MessageItem,
    OutputText,
    RequestConfig,
    Response,
    ResponseStatus,
    ToolChoice,
    ToolChoiceType,
    create_function_call_item,
    create_function_call_output,
    create_function_call_output_item,
    create_message_item,
)
from .prompts import (
    FEW_SHOT_COMPACT,
    FEW_SHOT_SUFFIX,
    PLATFORM_DIR_CMD,
    TOOL_ARG_ALIASES,
    get_react_prompt,
    get_system_prompt,
    get_tool_prompt,
)
from .tool_parse import ToolParser
from .types import ApiMode, BackendType, StepResultType, ToolSupportLevel

__all__ = [
    # Types
    "StepResultType",
    "ToolSupportLevel",
    "BackendType",
    "ApiMode",
    # Models
    "StepResult",
    "AgentRun",
    "Tool",
    "ToolParam",
    # Memory
    "Memory",
    "MemoryConfig",
    # Tool Parsing
    "ToolParser",
    "ToolCall",
    # Helpers
    "fuzzy_match",
    "normalize_args",
    "validate_path",
    "is_safe_url",
    "strip_tool_prefix",
    "is_simple_answered_query",
    "is_greeting_or_simple",
    "is_small_model",
    "detect_and_fix_repetition",
    "synthesize_tool_args",
    "set_security_mode",
    "get_security_mode",
    "SecurityMode",
    # Prompts
    "get_system_prompt",
    "get_tool_prompt",
    "get_react_prompt",
    "TOOL_ARG_ALIASES",
    "FEW_SHOT_SUFFIX",
    "FEW_SHOT_COMPACT",
    "PLATFORM_DIR_CMD",
    # Model Config
    "ModelFamilyConfig",
    "get_model_config",
    # Family Config
    "get_family_config",
    "get_stop_tokens",
    "supports_tools",
    "get_tool_format",
    "get_no_tools_system_prompt",
    "get_preferred_temperature",
    "should_use_few_shot",
    "get_few_shot_style",
    "has_known_issues",
    "get_react_system_suffix",
    "get_native_tool_hints",
    "FAMILY_CONFIGS",
    # OpenResponses
    "Response",
    "ResponseStatus",
    "ItemStatus",
    "ToolChoice",
    "ToolChoiceType",
    "MessageItem",
    "FunctionCallItem",
    "FunctionCallOutputItem",
    "OutputText",
    "InputText",
    "RequestConfig",
    "Error",
    "create_message_item",
    "create_function_call_item",
    "create_function_call_output",
    "create_function_call_output_item",
    # Error Recovery
    "ErrorRecoveryTracker",
    "ToolFailureRecord",
    "build_enhanced_observation",
    "is_error_result",
    "extract_error_type",
    "get_tool_suggestion",
    "TOOL_ERROR_HINTS",
    "GENERIC_ERROR_HINTS",
    "TOOL_NAME_SUGGESTIONS",
    "TOOL_ALTERNATIVES",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DEFAULT_MAX_TOTAL_FAILURES",
]
