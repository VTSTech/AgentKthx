"""Tool execution subsystem — lookup → execute → format result.

R07.00 Phase 10 extraction (from ``agent.py``). Pure move, no logic change.

``_execute_tool()`` is a self-contained concern that doesn't depend on
the agentic loop: registry lookup, dangerous-tool confirmation gate,
argument normalization (aliases + R06.52 numeric-string coercion +
hallucinated-parameter stripping), execution, and error formatting.

The loops call it as ``is_error_result(self._execute_tool(...))`` —
that interpretation layer stays with the agentic loop (Phase 5 will
consolidate it).

Host contract (attributes the mixin expects on ``self``):
- ``self.tools`` — a ``ToolRegistry`` (``.get(name)``, ``.names()``).
- ``self._confirm_dangerous`` — callback ``(tool_name, args) -> bool``
  or None (set by the constructor in ``core/agent_setup.py``).
"""

from __future__ import annotations

from typing import Any


class ToolExecutionMixin:
    """Mixin providing ``_execute_tool`` for Agent.

    R07.00 Phase 10: moved verbatim from ``agent.py`` (R06.58 state).
    All call sites (``self._execute_tool(...)``) are unchanged — the
    Agent class simply inherits the method now.
    """

    def _execute_tool(self, name: str, args: dict) -> Any:
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

        from .helpers import normalize_args
        normalized_args = normalize_args(args, expected_params, tool_name=name)

        # R06.52: numeric-string coercion. Small models frequently emit
        # numeric arguments as strings ("depth": "3"); tools that expect
        # int/float then raise TypeError and the call is guaranteed to fail.
        for p in tool.params:
            if p.name not in normalized_args:
                continue
            val = normalized_args[p.name]
            if isinstance(val, str) and p.type in ("integer", "number", "float"):
                try:
                    normalized_args[p.name] = int(val) if p.type == "integer" else float(val)
                except (ValueError, TypeError):
                    pass  # leave as-is; the tool will report the real problem

        # R06.52: hallucinated-parameter stripping. Arguments that are not in
        # the tool's schema are removed before execution instead of causing a
        # guaranteed TypeError. The model is told what was ignored so it can
        # correct future calls.
        ignored_params = [k for k in list(normalized_args.keys()) if k not in expected_params]
        for k in ignored_params:
            del normalized_args[k]

        try:
            result = tool.execute(**normalized_args)

        except TypeError as e:
            return f"Error: {e}"

        except KeyboardInterrupt:
            raise  # Let the caller (run loop or CLI) handle cancellation

        except Exception as e:
            return f"Error executing tool: {e}"

        if ignored_params and isinstance(result, str):
            result = (
                f"{result}\n\n"
                f"(Note: ignored unknown parameter(s) {', '.join(ignored_params)} — "
                f"not part of the '{name}' tool schema. Valid parameters: "
                f"{', '.join(expected_params) if expected_params else '(none)'}.)"
            )
        return result
