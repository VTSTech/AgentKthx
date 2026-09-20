"""
⚛️ AgentKthx — Tool Registry
Registry for managing available tools.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..core.models import Tool, ToolParam


class ToolRegistry:
    """
    Registry for managing available tools.

    Features:
    - Register tools by name or decorator
    - Subset creation for specific tools
    - JSON Schema generation for function calling
    - Fuzzy matching for tool names
    """

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register_tool(tool)

    def register(
        self,
        name: str | None = None,
        description: str = "",
        params: list[ToolParam] | None = None,
        dangerous: bool = False,
        category: str = "general",
    ) -> Callable:
        """
        Register a tool, can be used as decorator.

        Args:
            name: Tool name (defaults to function name)
            description: Tool description
            params: List of parameters
            dangerous: Whether tool has side effects
            category: Tool category

        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or func.__doc__ or ""

            # Auto-detect params from function signature if not provided
            tool_params = params or self._extract_params(func)

            tool = Tool(
                name=tool_name,
                description=tool_desc,
                params=tool_params,
                handler=func,
                dangerous=dangerous,
                category=category,
            )

            self._tools[tool_name] = tool
            return func

        return decorator

    def register_tool(self, tool: Tool) -> None:
        """Register a Tool object directly."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_fuzzy(self, name: str, threshold: float = 0.6) -> Tool | None:
        """Get a tool by name with fuzzy matching."""
        # Try exact match first
        if name in self._tools:
            return self._tools[name]

        # Fuzzy match
        from ..core.helpers import fuzzy_match
        matched = fuzzy_match(name, list(self._tools.keys()), threshold)
        if matched:
            return self._tools[matched]

        return None

    def all(self) -> list[Tool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """Get all tool names."""
        return list(self._tools.keys())

    def subset(self, names: list[str], warn: bool = True) -> "ToolRegistry":
        """
        Create a new registry with only the specified tools.

        Uses exact name matching only (no fuzzy matching) to ensure
        deterministic behavior. Names that don't match any registered
        tool are silently skipped, with an optional warning.

        Args:
            names: List of tool names to include
            warn: If True, print a warning for names that don't match

        Returns:
            New ToolRegistry with subset of tools
        """
        tools = []
        for name in names:
            tool = self.get(name)
            if tool:
                tools.append(tool)
            elif warn:
                available = list(self._tools.keys())
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"[ToolRegistry] subset: '{name}' not found in registry "
                          f"(available: {available})")
        return ToolRegistry(tools)

    def to_json_schema(self) -> list[dict]:
        """Get JSON Schema for all tools."""
        return [t.to_json_schema() for t in self._tools.values()]

    def _extract_params(self, func: Callable) -> list[ToolParam]:
        """Extract parameters from function signature."""
        import inspect

        params = []
        sig = inspect.signature(func)

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue

            # Determine type
            param_type = "string"
            if param.annotation != inspect.Parameter.empty:
                annotation = param.annotation
                if annotation in (int, float):
                    param_type = "number"
                elif annotation == bool:
                    param_type = "boolean"
                elif annotation in (list, list):
                    param_type = "array"
                elif annotation in (dict, dict):
                    param_type = "object"

            # Determine if required
            required = param.default == inspect.Parameter.empty

            params.append(ToolParam(
                name=name,
                type=param_type,
                required=required,
                default=None if required else param.default,
            ))

        return params

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry(tools={list(self._tools.keys())})"


def tool(
    name: str | None = None,
    description: str = "",
    dangerous: bool = False,
):
    """Convenience decorator for registering tools."""
    registry = ToolRegistry()
    return registry.register(
        name=name,
        description=description,
        dangerous=dangerous,
    )
