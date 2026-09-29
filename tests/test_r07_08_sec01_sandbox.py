"""
R07.08 Security batch — regression tests for SEC-01:

  SEC-01 (Medium): ``sandboxed_repl.py`` ``SAFE_BUILTINS`` included
    ``getattr``/``setattr``/``delattr``/``super``/``object``. With
    ``getattr`` + ``object`` reachable, prompt-injected ``python_repl``
    code could walk ``object.__subclasses__()`` → find a class whose
    ``__init__.__globals__['__builtins__']['__import__']`` is reachable →
    ``import os`` → arbitrary code execution with the user's privileges.

    Fix: drop the five attribute-traversal primitives from ``SAFE_BUILTINS``.
    ``hasattr`` stays (returns a bool, doesn't expose ``getattr``).
    ``vars``/``dir`` stay (residual surface documented in the audit; the
    classic ``object.__subclasses__()`` escape is closed).

These tests pin both the set membership (unit) and the actual subprocess
sandbox behaviour (integration). The integration tests spawn ``python3``
and assert the runner blocks the classic PoC attempts.

Written by VTSTech — https://www.vts-tech.org
"""

import textwrap

import pytest

from agentkthx.tools.sandboxed_repl import (
    SAFE_BUILTINS,
    SandboxConfig,
    sandboxed_exec,
)

# ------------------------------------------------------------------ #
#  Unit: the unsafe builtins are no longer in SAFE_BUILTINS          #
# ------------------------------------------------------------------ #

UNSAFE_BUILTINS_REMOVED = {
    "object",
    "super",
    "getattr",
    "setattr",
    "delattr",
}


@pytest.mark.parametrize("name", sorted(UNSAFE_BUILTINS_REMOVED))
def test_unsafe_builtin_removed_from_safe_builtins(name):
    """Each of the five attribute-traversal primitives must be absent."""
    assert name not in SAFE_BUILTINS, (
        f"SEC-01 regression: `{name}` is still in SAFE_BUILTINS — "
        f"it enables the object.__subclasses__() sandbox escape."
    )


def test_safe_builtins_still_has_hasattr_and_vars():
    """The audit's recommendation only drops the five traversal primitives.

    ``hasattr`` stays (returns a bool; doesn't expose ``getattr`` to user
    code). ``vars``/``dir`` stay (residual surface documented; the classic
    escape is closed).
    """
    assert "hasattr" in SAFE_BUILTINS
    assert "vars" in SAFE_BUILTINS
    assert "dir" in SAFE_BUILTINS


def test_safe_builtins_still_has_basics():
    """Legitimate REPL primitives are untouched."""
    for name in (
        "print",
        "len",
        "range",
        "sum",
        "min",
        "max",
        "sorted",
        "int",
        "str",
        "list",
        "dict",
        "tuple",
        "set",
        "isinstance",
        "callable",
        "iter",
        "next",
        "True",
        "False",
        "None",
    ):
        assert name in SAFE_BUILTINS, f"regression: `{name}` was dropped"


# ------------------------------------------------------------------ #
#  Integration: the actual sandbox subprocess blocks escape attempts  #
# ------------------------------------------------------------------ #
#
# These spawn ``python3`` via ``sandboxed_exec``. The runner script
# builds ``_safe_builtins`` from the real ``__builtins__`` filtered by
# the ``SAFE_BUILTINS`` set, so a dropped name is unreachable as a bare
# global. Each PoC below is the canonical SEC-01 escape; all must fail.


def _run(code: str) -> str:
    """Run code in the sandbox with a short timeout (keeps the suite fast)."""
    return sandboxed_exec(textwrap.dedent(code), config=SandboxConfig(timeout_seconds=5))


def test_escape_object_subclasses_blocked():
    """The classic PoC entry point: ``object.__subclasses__()``."""
    out = _run("""
        subs = object.__subclasses__()
        print("ESCAPED: found", len(subs), "subclasses")
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    # NameError is the expected failure mode — `object` is not in scope.
    assert "NameError" in out or "not defined" in out, f"expected NameError, got:\n{out}"


def test_escape_getattr_object_blocked():
    """``getattr(object, '__subclasses__')`` — both halves now missing."""
    out = _run("""
        subs = getattr(object, '__subclasses__')()
        print("ESCAPED via getattr:", len(subs))
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    # getattr is gone too, so one of the two names triggers the NameError.
    assert "NameError" in out or "not defined" in out, f"expected NameError, got:\n{out}"


def test_escape_super_mro_traversal_blocked():
    """``super`` enables MRO traversal to dunder attributes — now dropped."""
    out = _run("""
        # walk super -> __self_class__ -> __subclasses__
        cls = super.__self_class__
        subs = cls.__subclasses__()
        print("ESCAPED via super:", len(subs))
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    assert "NameError" in out or "not defined" in out, f"expected NameError, got:\n{out}"


def test_escape_setattr_class_attribute_blocked():
    """``setattr`` could mutate an imported module's internals — now dropped.

    Uses ``math`` (a whitelisted import) as the mutation target rather than
    a user-defined class, because the sandbox never exposed ``__build_class__``
    (a pre-existing limitation unrelated to SEC-01) so ``class`` statements
    can't run. The real setattr escape vector is mutating imported modules.
    """
    out = _run("""
        import math
        setattr(math, 'sqrt', lambda x: 999)
        print("ESCAPED via setattr:", math.sqrt(16))
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    assert "NameError" in out or "not defined" in out, f"expected NameError on setattr, got:\n{out}"


def test_escape_delattr_blocked():
    """``delattr`` could delete security-relevant attrs on an imported module."""
    out = _run("""
        import math
        delattr(math, 'sqrt')
        print("ESCAPED via delattr")
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    assert "NameError" in out or "not defined" in out, f"expected NameError on delattr, got:\n{out}"


def test_escape_full_classic_poc_blocked():
    """The full canonical PoC: subclasses → find os-loader → import os.

    Without ``object`` reachable, the very first step (``object.__subclasses__()``)
    raises NameError, so the rest of the chain never executes.
    """
    out = _run("""
        subs = object.__subclasses__()
        for cls in subs:
            try:
                g = cls.__init__.__globals__
                imp = g['__builtins__']['__import__']
                os = imp('os')
                print("ESCAPED: ran", os.system('echo pwned'))
                break
            except Exception:
                continue
        else:
            print("no escape vector found")
    """)
    assert "ESCAPED" not in out, f"SEC-01 escape succeeded:\n{out}"
    assert "pwned" not in out, f"SEC-01 escape ran a shell command:\n{out}"


# ------------------------------------------------------------------ #
#  Regression: legitimate REPL code still works                       #
# ------------------------------------------------------------------ #


def test_legit_arithmetic_works():
    out = _run("print(sum([1, 2, 3, 4]))")
    assert "10" in out


def test_legit_comprehension_works():
    out = _run("print([x * 2 for x in range(5)])")
    assert "[0, 2, 4, 6, 8]" in out


def test_legit_math_import_works():
    out = _run("import math; print(math.sqrt(16))")
    assert "4.0" in out


def test_legit_class_definition_documented_gap():
    """Pre-existing limitation (NOT a SEC-01 regression): the sandbox never
    exposed ``__build_class__`` (the CPython internal the ``class`` statement
    compiles to), so ``class`` statements have always raised NameError.

    This test documents the gap so a future fix (adding ``__build_class__``
    to SAFE_BUILTINS) is a deliberate, reviewed change rather than an
    accidental one. SEC-01 does not touch this — it only *removes* the
    five traversal primitives.
    """
    out = _run("""
        class Foo:
            pass
        print("class defined")
    """)
    assert "class defined" not in out, "unexpected: class def worked"
    assert (
        "__build_class__" in out or "NameError" in out
    ), f"expected __build_class__ NameError, got:\n{out}"


def test_legit_hasattr_works():
    """``hasattr`` was deliberately kept — verify it still functions on
    builtin objects (lists have ``append``, not ``foo``)."""
    out = _run("print(hasattr([], 'append'), hasattr([], 'foo'))")
    assert "True False" in out, f"hasattr behaviour changed:\n{out}"


# ------------------------------------------------------------------ #
#  Regression: import restrictions are unchanged                       #
# ------------------------------------------------------------------ #


def test_os_import_still_blocked():
    """SEC-01 is about attribute traversal, not import restrictions —
    the existing import block (os/subprocess/etc.) must still hold."""
    out = _run("import os; os.system('echo pwned')")
    assert "Import blocked" in out or "not allowed" in out, f"os import was not blocked:\n{out}"
    assert "pwned" not in out
