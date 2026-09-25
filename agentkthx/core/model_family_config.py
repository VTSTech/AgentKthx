"""
⚛️ AgentKthx — Model Family Configuration

Family-specific configurations for prompts, stop tokens, formatting, and tool handling.
Each model family has unique characteristics that affect how we construct prompts
and handle responses.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Literal

# Platform detection for cross-platform hints
_IS_WINDOWS = platform.system() == "Windows"
_PLATFORM_DIR_CMD = "cd" if _IS_WINDOWS else "pwd"


@dataclass
class ModelFamilyConfig:
    """Configuration for a specific model family.
    
    Unified configuration combining prompting, formatting, tool handling,
    and generation defaults. Single source of truth for all model-family-
    specific settings.
    """
    family: str
    # Prompting & formatting
    start_tokens: dict = field(default_factory=dict)
    stop_tokens: list[str] = field(default_factory=list)
    tool_format: Literal["native", "xml", "json", "none"] = "native"
    supports_native_tools: bool = True
    # Generation defaults
    preferred_temperature: float = 0.7
    default_temperature: float = 0.7  # Used by agent.py for generation
    default_top_p: float = 0.9
    default_max_tokens: int = 8192
    # Model behavior
    needs_think_directive: bool = False
    prefers_few_shot: bool = True
    few_shot_style: Literal["react", "native", "compact"] = "react"
    # Streaming / capabilities
    # Special behaviors
    has_schema_dump_issue: bool = False  # Some models dump tool schema as text
    truncate_json_args: bool = False  # Some models truncate JSON in ReAct
    prefers_user_system: bool = False  # Put system prompt in first user message
    # Override system prompt for models without tool support (pure reasoning)
    no_tools_system_prompt: str | None = None

# ═══════════════════════════════════════════════════════════════════════════════
# FAMILY CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════════════

FAMILY_CONFIGS: dict[str, ModelFamilyConfig] = {
    
    # GEMMA3 - Google's Gemma models (270m doesn't support tools)
    "gemma3": ModelFamilyConfig(
        family="gemma3",
        start_tokens={"user": "<start_of_turn>user", "assistant": "<start_of_turn>model"},
        stop_tokens=["<end_of_turn>"],
        tool_format="none",
        supports_native_tools=False,
        preferred_temperature=0.7,
        prefers_few_shot=False,
        few_shot_style="compact",
        # General-purpose prompt for models without tool support
        no_tools_system_prompt="""Answer questions directly. For math, show work then give answer.

Examples:
Q: What is 7 * 8?
A: 7 * 8 = 56

Q: What is 15 plus 27?
A: 15 + 27 = 42

Q: What is 17 divided by 4?
A: 17 / 4 = 4.25

Q: I have 10 apples. I give 3 to Bob and 2 to Alice. How many left?
A: 10 - 3 - 2 = 5

Q: Calculate (8 × 7) - 5.
A: 8 * 7 = 56, then 56 - 5 = 51

Q: What is the capital of Japan?
A: Tokyo

Keep answers brief. Show calculation first, then the final number.""",
    ),
    
    # GRANITE - IBM's Granite models (native tool support with XML format)
    "granite": ModelFamilyConfig(
        family="granite",
        start_tokens={
            "system": "<|start_of_role|>system<|end_of_role|>",
            "user": "<|start_of_role|>user<|end_of_role|>",
            "assistant": "<|start_of_role|>assistant<|end_of_role|>",
        },
        stop_tokens=["<|end_of_text|>"],
        tool_format="xml",
        supports_native_tools=True,
        preferred_temperature=0.7,
        prefers_few_shot=False,  # Native tools don't need few-shot
        few_shot_style="native",
    ),
    
    # GRANITEMOE - IBM's Granite MoE models
    "granitemoe": ModelFamilyConfig(
        family="granitemoe",
        start_tokens={
            "system": "<|start_of_role|>system<|end_of_role|>",
            "user": "<|start_of_role|>user<|end_of_role|>",
            "assistant": "<|start_of_role|>assistant<|end_of_role|>",
        },
        stop_tokens=["<|end_of_text|>"],
        tool_format="xml",
        supports_native_tools=True,
        preferred_temperature=0.6,
        prefers_few_shot=True,  # MoE benefits from examples
        few_shot_style="react",
        has_schema_dump_issue=True,
        truncate_json_args=True,
    ),
    
    # QWEN2 - Alibaba's Qwen 2.x models (ChatML format, native tools)
    "qwen2": ModelFamilyConfig(
        family="qwen2",
        start_tokens={
            "system": "<|im_start|>system",
            "user": "<|im_start|>user",
            "assistant": "<|im_start|>assistant",
        },
        stop_tokens=["<|im_end|>"],
        tool_format="xml",
        supports_native_tools=True,
        preferred_temperature=0.7,
        prefers_few_shot=False,  # Native tools don't need few-shot
        few_shot_style="native",
    ),
    
    # QWEN3 - Alibaba's Qwen 3.x models (ChatML + thinking directives)
    "qwen3": ModelFamilyConfig(
        family="qwen3",
        start_tokens={
            "system": "<|im_start|>system",
            "user": "<|im_start|>user",
            "assistant": "<|im_start|>assistant",
        },
        stop_tokens=["<|im_end|>"],
        tool_format="xml",
        supports_native_tools=True,
        preferred_temperature=0.6,
        needs_think_directive=True,
        prefers_few_shot=True,
        few_shot_style="react",
    ),
    
    # QWEN35 (Qwen 3.5) - Successor to Qwen3, NO thinking mode
    # Note: Unlike Qwen3, Qwen3.5 does NOT have thinking mode (simpler template)
    "qwen35": ModelFamilyConfig(
        family="qwen35",
        start_tokens={
            "system": "<|im_start|>system",
            "user": "<|im_start|>user",
            "assistant": "<|im_start|>assistant",
        },
        stop_tokens=["<|im_end|>"],
        tool_format="xml",
        supports_native_tools=True,
        preferred_temperature=0.6,
        needs_think_directive=False,  # Qwen3.5 does NOT have thinking mode
        prefers_few_shot=False,  # Native models should NOT have few-shot
        few_shot_style="react",
    ),
    
    # LLAMA - Meta's Llama models
    "llama": ModelFamilyConfig(
        family="llama",
        start_tokens={
            "system": "<|start_header_id|>system<|end_header_id|>\n\n",
            "user": "<|start_header_id|>user<|end_header_id|>\n\n",
            "assistant": "<|start_header_id|>assistant<|end_header_id|>\n\n",
        },
        stop_tokens=["<|eot_id|>", "<|end_of_text|>"],
        tool_format="native",
        supports_native_tools=True,
        preferred_temperature=0.7,
        prefers_few_shot=True,
        few_shot_style="react",
    ),
    
    # DOLPHIN - Dolphin fine-tunes (ChatML format, no tool support)
    "dolphin": ModelFamilyConfig(
        family="dolphin",
        start_tokens={
            "system": "<|im_start|>system",
            "user": "<|im_start|>user", 
            "assistant": "<|im_start|>assistant",
        },
        stop_tokens=["<|im_end|>", "<|im_start|>"],
        tool_format="none",
        supports_native_tools=False,
        preferred_temperature=0.7,
        prefers_few_shot=False,
        few_shot_style="compact",
        no_tools_system_prompt="""You are AI AgentKthx.

Be concise and direct. For math, show the calculation then the answer.
For questions, give short answers. For code, write Python functions.

Examples:
Q: What is 15 plus 27?
A: 15 + 27 = 42

Q: What is 17 divided by 4?
A: 17 / 4 = 4.25

Q: A store has 24 apples. They sell 8 and 6. How many left?
A: 24 - 8 - 6 = 10

Q: What comes next: 2, 4, 6, 8, ?
A: 10

Q: What is the capital of Japan?
A: Tokyo

Keep answers brief. One word when possible.""",
    ),
    
    # DEEPSEEK-R1 - DeepSeek's reasoning models (thinking mode)
    # These models have extended reasoning capabilities with think tokens
    "deepseek-r1": ModelFamilyConfig(
        family="deepseek-r1",
        start_tokens={
            "system": "<｜begin▁of▁sentence｜>",
            "user": "<｜User｜>",
            "assistant": "<｜Assistant｜>",
        },
        stop_tokens=["<｜end▁of▁sentence｜>"],
        tool_format="native",
        supports_native_tools=True,
        preferred_temperature=0.6,
        needs_think_directive=True,  # DeepSeek-R1 has thinking mode
        prefers_few_shot=True,
        few_shot_style="react",
    ),
    
    # DEEPSEEK - DeepSeek's standard models (coder, v3, etc.)
    "deepseek": ModelFamilyConfig(
        family="deepseek",
        start_tokens={
            "system": "<｜begin▁of▁sentence｜>",
            "user": "<｜User｜>",
            "assistant": "<｜Assistant｜>",
        },
        stop_tokens=["<｜end▁of▁sentence｜>"],
        tool_format="native",
        supports_native_tools=True,
        preferred_temperature=0.7,
        needs_think_directive=False,  # Standard DeepSeek models don't have thinking mode
        prefers_few_shot=True,
        few_shot_style="react",
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# FAMILY ALIASES
# ═══════════════════════════════════════════════════════════════════════════════

# Some detected families are not stored in FAMILY_CONFIGS but share the
# same tokenizer/architecture as another family.  These aliases map the
# detected family string to the canonical config key.
_FAMILY_ALIASES: dict[str, str] = {
    "bitnet": "llama",  # BitNet 1.58 uses LLaMA 3 tokenizer (128K vocab, <|eot_id|>/<|end_of_text|> EOS)
}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_family_config(family: str) -> ModelFamilyConfig:
    """Get configuration for a model family."""
    family_lower = family.lower() if family else ""

    # Resolve aliases first (e.g., "bitnet" → "qwen2")
    family_lower = _FAMILY_ALIASES.get(family_lower, family_lower)

    # Direct match
    if family_lower in FAMILY_CONFIGS:
        return FAMILY_CONFIGS[family_lower]

    # Partial match
    for key in FAMILY_CONFIGS:
        if key in family_lower or family_lower in key:
            return FAMILY_CONFIGS[key]

    # Default config
    return ModelFamilyConfig(family=family or "unknown")


def get_stop_tokens(family: str) -> list[str]:
    """Get stop tokens for a family."""
    return get_family_config(family).stop_tokens


def supports_tools(family: str) -> bool:
    """Check if family supports native tools."""
    return get_family_config(family).supports_native_tools


def get_tool_format(family: str) -> str:
    """Get tool format for a family."""
    return get_family_config(family).tool_format


def get_no_tools_system_prompt(family: str) -> str | None:
    """Get the system prompt override for models without tool support."""
    return get_family_config(family).no_tools_system_prompt


def get_preferred_temperature(family: str) -> float:
    """Get preferred temperature for a family."""
    return get_family_config(family).preferred_temperature


def should_use_few_shot(family: str) -> bool:
    """Determine if few-shot prompting should be used."""
    config = get_family_config(family)
    
    # If family explicitly prefers/dislikes few-shot, respect that
    if not config.prefers_few_shot:
        return False
    
    return config.prefers_few_shot


def get_few_shot_style(family: str) -> str:
    """Get preferred few-shot style for a family."""
    return get_family_config(family).few_shot_style


def has_known_issues(family: str) -> dict:
    """Get known issues for a family."""
    config = get_family_config(family)
    return {
        "schema_dump": config.has_schema_dump_issue,
        "truncate_json": config.truncate_json_args,
    }


def needs_no_think_directive(family: str) -> bool:
    """Check if a family needs /no_think directive to disable thinking mode."""
    config = get_family_config(family)
    return config.needs_think_directive


def get_react_system_suffix(family: str) -> str:
    """Get family-specific ReAct format instructions."""
    config = get_family_config(family)
    
    base_suffix = """You have access to tools. Use the following format:

Thought: <your reasoning>
Action: <tool_name>
Action Input: <JSON with arguments>
Observation: <result>
... (repeat as needed)
Thought: I have the answer.
Final Answer: <your response>"""
    
    # Family-specific modifications
    if family == "granitemoe":
        return base_suffix + """

CRITICAL: Keep Action Input JSON SHORT. Use compact format:
{"expression": "2**10"}  <- GOOD
{"expression": "calculate 2 to the power of 10"}  <- BAD, too long"""
    
    return base_suffix


def get_native_tool_hints(family: str) -> str:
    """Get hints for models using native tool calling."""
    config = get_family_config(family)
    
    if not config.supports_native_tools:
        return ""
    
    hints = f"""TOOL USAGE RULES - YOU MUST CALL TOOLS:

1. MATH QUESTIONS: Always call calculator tool
   - "times/multiplied" → expression="A * B"
   - "power of" → expression="A ** B"
   - "square root" → expression="sqrt(N)"
   - "divided by" → expression="A / B"

2. SHELL: Use shell tool
   - "echo X" → command="echo X"
   - "directory" → command="{_PLATFORM_DIR_CMD}"

3. DATE/TIME: Use python_repl (works on all platforms)
   - python_repl(code="from datetime import datetime; print(datetime.now())")

4. PYTHON: Use python_repl
   - Power is ** not ^

NEVER respond with empty content. ALWAYS call a tool when asked to compute."""
    
    return hints


def detect_family(model_name: str) -> str | None:
    """Detect model family from model name."""
    name_lower = model_name.lower()
    families = [
        "qwen2.5", "qwen2", "qwen35", "qwen3", "qwen",
        "llama3.3", "llama3.2", "llama3.1", "llama3", "llama",
        "mistral", "mixtral",
        "gemma3", "gemma2", "gemma",
        "granitemoe", "granite",
        "phi3", "phi",
        "codellama",
        "command-r", "command",
        "deepseek-r1", "deepseek",  # deepseek-r1 must come before deepseek
        "dolphin",
        "bitnet",  # BitNet 1.58 (LLaMA 3 tokenizer, 128K vocab)
    ]
    for f in families:
        if f in name_lower:
            return f
    return None


def get_model_config(model_name: str) -> ModelFamilyConfig:
    """
    Get unified configuration for a model.
    
    This replaces the separate get_model_config() from model_config.py.
    Uses detect_family() for consistent family resolution, then falls back
    to get_family_config() for partial matching.
    
    detect_family() returns the most specific family string (e.g., "qwen2.5",
    "llama3.2") but FAMILY_CONFIGS only stores base families (e.g., "qwen2",
    "llama"). Partial matching bridges this gap: "qwen2.5" → "qwen2" config.
    
    Args:
        model_name: Name of the model (e.g., "qwen2.5:7b")
    
    Returns:
        ModelFamilyConfig for the model
    """
    family = detect_family(model_name)
    if not family:
        return ModelFamilyConfig(family="unknown")
    return get_family_config(family)


__all__ = [
    "ModelFamilyConfig",
    "FAMILY_CONFIGS",
    "get_family_config",
    "get_stop_tokens",
    "supports_tools",
    "get_tool_format",
    "get_preferred_temperature",
    "should_use_few_shot",
    "get_few_shot_style",
    "has_known_issues",
    "get_react_system_suffix",
    "get_native_tool_hints",
    "get_no_tools_system_prompt",
    "detect_family",
    "get_model_config",
    "needs_no_think_directive",
]