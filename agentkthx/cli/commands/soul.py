"""`agentkthx soul` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import bold, yellow, dim, green, cyan, red




def cmd_soul(args: argparse.Namespace) -> int:
    """Inspect a Soul Spec package."""
    try:
        from ...soul import load_soul, build_system_prompt, SoulLoader
    except ImportError:
        print(f"{red('Error:')} Soul module not available")
        return 1
    
    path = args.path
    level = args.level
    
    try:
        loader = SoulLoader()
        soul = loader.load(path, level=level)
    except FileNotFoundError as e:
        print(f"{red('Error:')} Soul package not found: {e}")
        return 1
    except Exception as e:
        print(f"{red('Error:')} Failed to load soul: {e}")
        return 1
    
    # Display soul info
    print()
    print(bold(f"👻 Soul Spec Package") + dim(" · ClawSouls v0.5"))
    print(dim("─" * 70))
    print()
    
    # Basic info
    print(f"  {cyan('Name:')}        {soul.display_name} ({dim(soul.name)})")
    print(f"  {cyan('Version:')}     {soul.version}")
    print(f"  {cyan('Spec:')}        v{soul.spec_version}")
    print(f"  {cyan('Author:')}      {soul.author.name}" + (f" ({soul.author.github})" if soul.author.github else ""))
    print(f"  {cyan('License:')}     {soul.license}")
    print()
    
    print(f"  {cyan('Description:')}")
    print(f"    {soul.description}")
    print()
    
    # Disclosure summary
    if soul.disclosure and soul.disclosure.summary:
        print(f"  {cyan('Summary:')}")
        print(f"    {soul.disclosure.summary}")
        print()
    
    # Tags and category
    if soul.tags:
        print(f"  {cyan('Tags:')}       {', '.join(soul.tags)}")
    print(f"  {cyan('Category:')}   {soul.category}")
    print()
    
    # Environment
    if soul.environment.value != "virtual":
        print(f"  {yellow('Environment:')} {soul.environment.value}")
        print(f"  {yellow('Interaction:')} {soul.interaction_mode.value}")
        if soul.hardware_constraints:
            hc = soul.hardware_constraints
            caps = []
            if hc.has_display: caps.append("display")
            if hc.has_speaker: caps.append("speaker")
            if hc.has_microphone: caps.append("microphone")
            if hc.has_camera: caps.append("camera")
            if caps:
                print(f"  {yellow('Hardware:')}   {', '.join(caps)}")
        if soul.safety and soul.safety.physical:
            print(f"  {yellow('Safety:')}     {soul.safety.physical.contact_policy.value}")
        print()
    
    # Allowed tools
    if soul.allowed_tools:
        print(f"  {cyan('Allowed Tools:')} {', '.join(soul.allowed_tools)}")
    
    # Recommended skills
    if soul.recommended_skills:
        required = [s.name for s in soul.recommended_skills if s.required]
        optional = [s.name for s in soul.recommended_skills if not s.required]
        if required:
            print(f"  {cyan('Required Skills:')} {', '.join(required)}")
        if optional:
            print(f"  {cyan('Optional Skills:')} {', '.join(optional)}")
    
    # Compatibility
    if soul.compatibility.frameworks:
        print(f"  {cyan('Frameworks:')}   {', '.join(soul.compatibility.frameworks)}")
    if soul.compatibility.models:
        print(f"  {cyan('Models:')}       {', '.join(soul.compatibility.models)}")
    
    print()
    
    # Validation
    if args.validate:
        issues = soul.validate()
        if issues:
            print(f"  {red('Validation Issues:')}")
            for issue in issues:
                print(f"    {red('✗')} {issue}")
        else:
            print(f"  {green('✓')} Validation passed")
        print()
    
    # Loaded content
    if level >= 2:
        print(dim("─" * 70))
        if soul.soul_content:
            print(f"\n  {cyan('SOUL.md:')}")
            lines = soul.soul_content.split("\n")
            for line in lines[:10]:
                print(f"    {line}")
            if len(lines) > 10:
                remaining = len(lines) - 10
                print(f"    {dim('...')} ({remaining} more lines)")
        
        if soul.identity_content:
            print(f"\n  {cyan('IDENTITY.md:')}")
            lines = soul.identity_content.split("\n")
            for line in lines[:10]:
                print(f"    {line}")
            if len(lines) > 10:
                remaining = len(lines) - 10
                print(f"    {dim('...')} ({remaining} more lines)")
    
    # Show generated system prompt
    if args.prompt:
        prompt = loader.build_system_prompt(soul, level=level)
        print()
        print(dim("─" * 70))
        print(f"\n  {cyan('Generated System Prompt:')}")
        print(dim("─" * 70))
        print(prompt)
    
    print()
    return 0
