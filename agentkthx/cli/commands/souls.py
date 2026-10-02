"""`agentkthx souls` subcommand (R07.19 follow-up #7).

Listing surface for the bundled Soul Spec packages — the CLI counterpart
of the chat ``/souls`` command and the sibling of ``agentkthx skills``.
Where ``agentkthx soul <path>`` deep-inspects ONE package by path (and
can render its generated system prompt), this command resolves the
bundled souls BY NAME, so nobody has to dig around in site-packages to
find out what ``--soul kthx-trading`` actually loads.
"""

from __future__ import annotations

import argparse

from ...colors import bold, cyan, dim, green, magenta, red, yellow


def _default_soul_name() -> str:
    """Return the AgentSetupMixin constructor default soul (drift-proof).

    Read via introspection instead of a literal here — when the default
    soul ever changes, this listing follows automatically (the same
    introspection the test suite already pins).
    """
    import inspect

    try:
        from ...core.agent_setup import AgentSetupMixin

        return inspect.signature(AgentSetupMixin.__init__).parameters["soul"].default
    except Exception:
        return ""


def _listing_rows(manifests):
    """Compute (name_col_width, description) pairs for the listing table."""
    width = max((len(m.name) for m in manifests), default=12)
    return width


def cmd_souls(args: argparse.Namespace) -> int:
    """Execute the souls command — list bundled souls, or detail one."""
    try:
        from ...soul import SoulLoader
    except ImportError:
        print(f"{red('Error:')} Soul module not available")
        return 1

    try:
        manifests = SoulLoader().list_souls()
    except Exception as e:
        print(f"{red('Error:')} Soul discovery failed: {e}")
        return 1

    name = getattr(args, "name", None)
    if name:
        return _show_soul_detail(manifests, name)
    return _show_soul_listing(manifests)


def _show_soul_listing(manifests) -> int:
    """Print the bundled-souls table (✓ = the constructor-default soul)."""
    print(bold("\n👻 AgentKthx Souls") + dim(" · bundled Soul Spec packages · ClawSouls v0.5"))

    if not manifests:
        print(yellow("  No souls found."))
        print(dim("  Souls live in agentkthx/souls/<name>/soul.json"))
        return 0

    default_name = _default_soul_name()
    name_w = _listing_rows(manifests)

    print(dim("─" * 74))
    for m in manifests:
        marker = green("✓") if m.name == default_name else dim("○")
        desc = m.description or ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        suffix = dim(" (default)") if m.name == default_name else ""
        print(
            f"  {marker} {cyan(m.name.ljust(name_w))} {dim(('v' + m.version).ljust(9))}{desc}"
            + suffix
        )
    print(dim("─" * 74))

    print()
    print(dim(f"  Use with:            {cyan('--soul <name>')} (run / chat / agent / test)"))
    print(dim(f"  Switch mid-session:  {cyan('/soul <name>')} inside chat (see /souls)"))
    print(dim(f"  Inspect one:         {cyan('agentkthx souls <name>')}"))
    print()
    return 0


def _show_soul_detail(manifests, name: str) -> int:
    """Print the manifest-level detail view for one bundled soul."""
    match = next((m for m in manifests if m.name == name), None)
    if match is None:
        available = [m.name for m in manifests]
        hint = ""
        if available:
            from ...core.helpers import fuzzy_match

            fuzzy = fuzzy_match(name, available, threshold=0.6)
            if fuzzy:
                hint = f" (did you mean '{fuzzy}'?)"
        print(f"{red('Error:')} Soul not found: {name}{hint}")
        if available:
            print(dim(f"  Available: {', '.join(available)}"))
        return 1

    m = match
    print()
    print(bold(f"👻 {m.display_name}") + dim(f" · {m.name} v{m.version} (spec v{m.spec_version})"))
    print(dim("─" * 70))
    print()
    print(f"  {cyan('Description:')}  {m.description}")
    print(
        f"  {cyan('Author:')}       {m.author.name}"
        + (f" ({m.author.github})" if getattr(m.author, "github", None) else "")
    )
    print(f"  {cyan('License:')}      {m.license}")
    print(f"  {cyan('Category:')}     {m.category}")
    if m.tags:
        print(f"  {cyan('Tags:')}         {', '.join(m.tags)}")
    if m.allowed_tools:
        print(
            f"  {cyan('Allowed tools:')} {', '.join(m.allowed_tools)} "
            + dim(f"({len(m.allowed_tools)})")
        )
    if m.recommended_skills:
        required = [s.name for s in m.recommended_skills if s.required]
        optional = [s.name for s in m.recommended_skills if not s.required]
        if required:
            print(f"  {cyan('Required skills:')} {', '.join(required)}")
        if optional:
            print(f"  {cyan('Optional skills:')} {', '.join(optional)}")
    if m.compatibility and m.compatibility.models:
        print(f"  {cyan('Models:')}        {', '.join(m.compatibility.models)}")
    print()
    print(
        dim(f"  Load it:  {magenta('--soul ' + m.name)}  ·  mid-session: {cyan('/soul ' + m.name)}")
    )
    print(dim("  Deep inspect (any path): agentkthx soul <path-to-soul-package>"))
    print()
    return 0
