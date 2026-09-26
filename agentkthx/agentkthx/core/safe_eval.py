"""
\u269b\ufe0f AgentKthx \u2014 Safe Expression Evaluator

AST-walking evaluator that replaces Python's ``eval()`` for math-style
expressions. Closes the SEC-01 sandbox bypass: ``eval(expr, {"__builtins__":
{}}, ns)`` is bypassable via attribute traversal
(e.g. ``().__class__.__bases__[0].__subclasses__()``) because ``eval`` itself
parses and executes attribute access. This module rejects attribute and
subscript AST nodes outright, so the bypass payload cannot even be parsed
into an evaluatable form.

Written by VTSTech \u2014 https://www.vts-tech.org
"""
from __future__ import annotations

import ast
import operator
from typing import Any, Callable


# Mapping of allowed ast binary-operator class -> Python operator function.
_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}

# Mapping of allowed ast unary-operator class -> Python operator function.
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
    ast.Invert: operator.invert,
}

# Mapping of allowed ast comparison operator class -> Python operator function.
# Note: ``in`` / ``not in`` / ``is`` / ``is not`` are deliberately NOT here \u2014
# they could be used to probe for specific types in the sandbox escape.
_COMPARE_OPS: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class _SafeEvaluator:
    """Recursive AST walker that only allows whitelisted node types.

    The whitelist is enforced centrally in :meth:`visit` \u2014 any node type
    not explicitly allowed raises ``ValueError``. This includes:

    - ``ast.Attribute``  \u2014 blocks ``().__class__``, ``os.system``, etc.
    - ``ast.Subscript``  \u2014 blocks ``().__class__.__bases__[0]``
    - ``ast.Lambda``, ``ast.ListComp``, ``ast.SetComp``, ``ast.DictComp``,
      ``ast.GeneratorExp`` \u2014 blocks comprehension-based escapes
    - ``ast.JoinedStr``, ``ast.FormattedValue`` \u2014 blocks f-strings
    - ``ast.Starred`` \u2014 blocks ``*args`` unpacking
    - ``ast.Import``, ``ast.ImportFrom`` \u2014 blocks imports
    - ``ast.Slice`` \u2014 blocks ``[a:b]`` slicing
    - ``ast.Await``, ``ast.Yield``, ``ast.YieldFrom`` \u2014 blocks async
    - ``ast.NamedExpr`` \u2014 blocks walrus operator
    - ``ast.List``, ``ast.Tuple``, ``ast.Set``, ``ast.Dict`` \u2014 blocks
      collection literals (not needed for math; could be used as containers
      for sandbox-escape payloads)
    """

    def __init__(self, allowed_names: dict[str, Any]):
        self.names = allowed_names or {}

    def visit(self, node: ast.AST) -> Any:
        # Root of an ``ast.parse(expr, mode="eval")`` tree.
        if isinstance(node, ast.Expression):
            return self.visit(node.body)

        # Literals \u2014 only numeric / bool / None constants.
        # Strings, bytes, complex, Ellipsis are blocked to keep the surface
        # minimal (math expressions don't need them).
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
                return v
            raise ValueError(
                f"Literal of type {type(v).__name__!r} not allowed in safe_eval"
            )

        # Name lookup \u2014 only names in the allowlist resolve.
        if isinstance(node, ast.Name):
            if node.id in self.names:
                return self.names[node.id]
            raise NameError(
                f"Name {node.id!r} is not defined in the safe_eval namespace"
            )

        # Binary operations \u2014 all standard math operators.
        if isinstance(node, ast.BinOp):
            op_t = type(node.op)
            if op_t not in _BIN_OPS:
                raise ValueError(
                    f"Binary operator {op_t.__name__} not allowed in safe_eval"
                )
            left = self.visit(node.left)
            right = self.visit(node.right)
            return _BIN_OPS[op_t](left, right)

        # Unary operations \u2014 -, +, not, ~.
        if isinstance(node, ast.UnaryOp):
            op_t = type(node.op)
            if op_t not in _UNARY_OPS:
                raise ValueError(
                    f"Unary operator {op_t.__name__} not allowed in safe_eval"
                )
            operand = self.visit(node.operand)
            return _UNARY_OPS[op_t](operand)

        # Boolean operations (``and`` / ``or``) with short-circuit semantics.
        if isinstance(node, ast.BoolOp):
            op_t = type(node.op)
            if op_t is ast.And:
                result: Any = True
                for v in node.values:
                    result = self.visit(v)
                    if not result:
                        return result  # short-circuit
                return result
            if op_t is ast.Or:
                last: Any = False
                for v in node.values:
                    last = self.visit(v)
                    if last:
                        return last  # short-circuit
                return last
            raise ValueError(
                f"Boolean operator {op_t.__name__} not allowed in safe_eval"
            )

        # Comparisons \u2014 chained comparisons (``a < b < c``) work natively.
        if isinstance(node, ast.Compare):
            left = self.visit(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                op_t = type(op)
                if op_t not in _COMPARE_OPS:
                    raise ValueError(
                        f"Comparison operator {op_t.__name__} not allowed "
                        f"(only ==, !=, <, <=, >, >=)"
                    )
                right = self.visit(comparator)
                if not _COMPARE_OPS[op_t](left, right):
                    return False
                left = right
            return True

        # Conditional expression \u2014 ``x if cond else y``.
        if isinstance(node, ast.IfExp):
            if self.visit(node.test):
                return self.visit(node.body)
            return self.visit(node.orelse)

        # Function calls \u2014 only direct calls to names in the allowlist.
        # ``obj.method(...)`` is rejected because ``ast.Attribute`` is not
        # in our allowed set (the visit method falls through to the
        # "Disallowed AST node" branch).
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError(
                    "Only direct function calls to allowed names are permitted "
                    "(no attribute or subscript calls)"
                )
            func_name = node.func.id
            if func_name not in self.names:
                raise NameError(
                    f"Function {func_name!r} is not defined in the safe_eval "
                    f"namespace"
                )
            func = self.names[func_name]
            if not callable(func):
                raise TypeError(f"Name {func_name!r} is not callable")
            # Block ``*args`` / ``**kwargs`` unpacking \u2014 the payload could
            # be an attacker-controlled iterable/dict that triggers attribute
            # access on its elements.
            if any(isinstance(a, ast.Starred) for a in node.args):
                raise ValueError("Starred (*args) unpacking not allowed in safe_eval")
            if any(kw.arg is None for kw in node.keywords):
                raise ValueError("**kwargs unpacking not allowed in safe_eval")
            args = [self.visit(a) for a in node.args]
            kwargs = {
                kw.arg: self.visit(kw.value)
                for kw in node.keywords
                if kw.arg is not None
            }
            return func(*args, **kwargs)

        # Anything else is rejected. This is the explicit security boundary.
        raise ValueError(f"Disallowed AST node: {type(node).__name__}")


def safe_eval(expression: str, allowed_names: dict[str, Any] | None = None) -> Any:
    """Safely evaluate a Python expression using AST walking.

    Replaces ``eval(expr, {"__builtins__": {}}, ns)`` \u2014 the sandbox
    ``{"__builtins__": {}}`` is bypassable via attribute traversal
    (``().__class__.__bases__[0].__subclasses__()``). This function rejects
    attribute and subscript AST nodes at parse time, so the bypass payload
    cannot be evaluated at all.

    Allowed:
        - Numeric, boolean, and ``None`` constants
        - Names from ``allowed_names``
        - Binary operators: ``+ - * / // % ** << >> & | ^``
        - Unary operators: ``- + not ~``
        - Boolean operators: ``and or`` (short-circuit)
        - Comparisons: ``== != < <= > >=`` (chained comparisons supported)
        - Conditional expressions: ``x if cond else y``
        - Direct function calls to names in ``allowed_names`` (with
          positional and keyword arguments)

    Rejected (not exhaustive):
        - Attribute access (``x.y``) \u2014 blocks ``__class__``, ``__bases__``
        - Subscript access (``x[i]``) \u2014 blocks ``__bases__[0]``
        - Slicing (``x[a:b]``)
        - Lambda, list/set/dict comprehensions, generator expressions
        - F-strings (``f"{...}"``)
        - Starred / double-star unpacking (``*args``, ``**kwargs``)
        - Imports (``import``, ``from x import y``)
        - Collection literals (``[]``, ``()``, ``{}``, ``{x: y}``)
        - Walrus operator (``:=``)
        - Await / yield

    Args:
        expression: Python expression string to evaluate.
        allowed_names: Dict mapping name -> value (functions and constants).
            ``None`` is treated as an empty dict \u2014 only pure arithmetic
            with numeric literals will work.

    Returns:
        The result of evaluating the expression.

    Raises:
        SyntaxError: If the expression is not valid Python syntax.
        ValueError: If the expression contains a disallowed AST node or
            operator (e.g., attribute access, subscript, lambda, etc.).
        NameError: If a name is referenced that is not in ``allowed_names``.
        TypeError: If a referenced name is not callable.
        ZeroDivisionError: On division or modulo by zero.
        OverflowError: On numerical overflow.
    """
    tree = ast.parse(expression, mode="eval")
    evaluator = _SafeEvaluator(allowed_names or {})
    return evaluator.visit(tree)


__all__ = ["safe_eval"]
