"""`agentkthx skills` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import bold, yellow, magenta, dim, cyan, red




def cmd_skills(args: argparse.Namespace) -> int:
    """List available skills."""
    from ...skills import SkillLoader

    loader = SkillLoader()
    skills = loader.list_skills()

    print(bold(f"\n⚛️ AgentKthx Skills") + dim(" · Written by VTSTech · https://kthx.vts-tech.org"))

    if not skills:
        print(yellow("  No skills found."))
        print(dim("  Skills are loaded from agentkthx/skills/*/SKILL.md"))
        return 0

    print(bold(f"{'Skill':<20} Description"))
    print(dim("─" * 70))

    for name in skills:
        try:
            skill = loader.load(name)
            desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
            print(f"  {magenta(name):<26} {desc}")

            # Show resources
            resources = []
            if skill.scripts_dir:
                scripts = list(skill.scripts_dir.glob("*.py"))
                if scripts:
                    resources.append(f"{len(scripts)} scripts")
            if skill.references_dir:
                refs = list(skill.references_dir.glob("*.md"))
                if refs:
                    resources.append(f"{len(refs)} refs")
            if resources:
                print(f"  {'':<26} {dim('Has: ' + ', '.join(resources))}")
        except Exception as e:
            print(f"  {magenta(name):<26} {red(f'Error: {e}')}")

    print()
    print(dim(f"  Use with: {cyan('--skills')} {','.join(skills[:2])}"))
    print(dim("  Skills provide knowledge/instructions to the agent."))
    print(dim(f"  Available commands support: {cyan('run --skills <list>')}, {cyan('chat --skills <list>')}, {cyan('agent --skills <list>')}"))
    print()

    return 0
