"""`agentkthx sessions` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import dim, green, cyan, bright_green, red, bright_cyan




def cmd_sessions(args: argparse.Namespace) -> int:
    """List or delete saved sessions."""
    try:
        from ...core.persistent_memory import PersistentMemory
    except ImportError:
        print(f"{red('Error:')} Persistent memory not available (requires sqlite3)")
        return 1

    if args.delete:
        # Delete mode
        session_id = args.delete
        deleted = PersistentMemory.delete_session(session_id)
        if deleted:
            print(f"{green('✓')} Deleted session: {cyan(session_id)}")
        else:
            print(f"{red('✗')} Session not found: {cyan(session_id)}")
            return 1
    else:
        # List mode
        sessions = PersistentMemory.list_sessions()
        if not sessions:
            print("No saved sessions found.")
            _hint = 'agentkthx run --session <name> "<prompt>"'
            print(f"\n  Start a session with: {cyan(_hint)}")
            return 0

        ID_W = 20
        MSGS_W = 8
        CREATED_W = 19
        UPDATED_W = 19

        print()
        print(f"{bright_cyan('⚛ AgentKthx')} - Saved Sessions")
        print(f"{dim('  DB:')} ~/.agentkthx/memory.db")
        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))
        print(f"  {'Session':<{ID_W}} {'Msgs':>{MSGS_W}}  {'Created':<{CREATED_W}}  {'Updated':<{UPDATED_W}}")
        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))

        for s in sessions:
            sid = s["session_id"]
            msgs = s["message_count"]
            created = s["created_at"][:19].replace("T", " ")
            updated = s["updated_at"][:19].replace("T", " ")
            print(f"  {cyan(sid):<{ID_W}} {msgs:>{MSGS_W}}  {dim(created):<{CREATED_W}}  {dim(updated):<{UPDATED_W}}")

        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))
        print(f"Total: {bright_green(str(len(sessions)))} sessions")
        _resume = 'agentkthx run --session <name> "<prompt>"'
        print(f"\n{dim('Resume a session:')} {cyan(_resume)}")
        print(f"{dim('Delete a session:')} {cyan('agentkthx sessions --delete <name>')}")

    return 0
