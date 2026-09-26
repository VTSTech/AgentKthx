"""
AgentKthx Core Module

This module contains the core types, models, and utilities
that form the foundation of the AgentKthx framework.
"""

from .types import StepResultType, ToolSupportLevel, BackendType, ApiMode
from .models import StepResult, AgentRun, Tool, ToolParam, ToolCall
from .memory import Memory, MemoryConfig
from .tool_parse import ToolParser
from .helpers import (
    fuzzy_match, normalize_args, validate_path, is_safe_url,
    strip_tool_prefix, is_simple_answered_query, is_greeting_or_simple,
    is_small_model, detect_and_fix_repetition, synthesize_tool_args,
    set_security_mode, get_security_mode, SecurityMode,
)
from .prompts import (
    get_system_prompt, get_tool_prompt, get_react_prompt,
    TOOL_ARG_ALIASES, FEW_SHOT_SUFFIX, FEW_SHOT_COMPACT,
    PLATFORM_DIR_CMD,
)
from .model_family_config import (
    ModelFamilyConfig, get_model_config,
    get_family_config, get_stop_tokens, supports_tools,
    get_tool_format, get_no_tools_system_prompt, get_preferred_temperature,
    should_use_few_shot, get_few_shot_style, has_known_issues,
    get_react_system_suffix, get_native_tool_hints,
    FAMILY_CONFIGS,
)
# OpenResponses types
from .openresponses import (
    Response, ResponseStatus, ItemStatus,
    ToolChoice, ToolChoiceType,
    MessageItem, FunctionCallItem, FunctionCallOutputItem,
    OutputText, InputText,
    RequestConfig, Error,
    create_message_item, create_function_call_item, create_function_call_output,
    create_function_call_output_item,
)
# Error Recovery
from .error_recovery import (
    ErrorRecoveryTracker,
    ToolFailureRecord,
    build_enhanced_observation,
    is_error_result,
    extract_error_type,
    get_tool_suggestion,
    TOOL_ERROR_HINTS,
    GENERIC_ERROR_HINTS,
    TOOL_NAME_SUGGESTIONS,
    TOOL_ALTERNATIVES,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_TOTAL_FAILURES,
)

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