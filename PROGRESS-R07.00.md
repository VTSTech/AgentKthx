# R07.00 Modularization — Progress Snapshot

**Date:** 2026-09-25 | **Branch:** main (6 commits ahead of origin @ 27b9e5b)
**Suite:** 965 passed, 9 skipped, 0 failed (baseline at start: 933 passed)

## agent.py modularization: COMPLETE (Phases 5, 6, 7, 9, 10)

| Phase | Module | Lines | Commit |
|-------|--------|-------|--------|
| 7 | `core/compaction.py` (CompactionMixin) | 199 | 3a35ba3 |
| 9 | `core/agent_setup.py` (AgentSetupMixin) | 520 | 62500ce |
| 10 | `core/tool_execution.py` (ToolExecutionMixin) | 103 | 4143e29 |
| 6 | `core/streaming.py` (StreamingMixin) | 858 | f31358d + 518f6ae |
| 5 | `core/agentic_loop.py` (AgenticLoopMixin) | 760 | 7fcde77 |

**agent.py: 3466 → 1096 lines (−68%).** Remaining content: `run()`,
the MAINT-04 Phase 1-4 shared helpers, `_generate()`, `create_response()`,
thin `_run_core`/`_run_core_streaming` wrappers, small utility methods.
(The plan's ~300-line stretch target assumed also relocating the
Phase 1-4 helpers — not in the phase spec, candidate for R07.01.)

### What changed structurally
- `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)` — public API frozen, all call sites unchanged.
- `_run_core` (~571 lines) + `_run_core_streaming` (~464 lines) loop bodies **replaced by one unified loop** `_run_loop_iteration()`; per-path behavior via `LoopCallbacks` dataclass (streaming hooks: per-step compaction, token snapshot + footer, R06.55 inline tool print, R06.58 between-calls compaction).
- Real behavioral deltas preserved exactly: `include_format_hint` (T/F), `enable_compaction_recovery` (F/T), `mark_response_completed` (T/F — streaming never marked Response COMPLETED; preserved + documented + test-asserted).
- Debug output unified to the non-streaming superset — closes the MAINT-04 29-check debug divergence.
- +32 new tests across 5 new structural test files; 12 source-inspection tests retargeted (intent preserved: guard against helper bypass).
- Public API intact: `from agentkthx import Agent`, `from agentkthx.cli import main`.

### Flagged for separate fixes (preserved verbatim per plan principle 5)
1. `run_stream()` KeyboardInterrupt path references undefined `ResponseStateEvent` → would NameError (pre-existing, original agent.py:1499).
2. Streaming path never marks Response COMPLETED (pre-existing; now toggle-documented + test-asserted).
3. Streaming wrapper resets `_running_tokens_in/out` to 0 at run start; non-streaming doesn't (pre-existing).

## Phase 8 (cli.py → cli/ package): NOT STARTED
4270-line cli.py is untouched. Mapping analysis done: 15 `cmd_*` functions,
`create_parser` (~550 lines), `_build_agent`, footer machinery,
`_print_agent_steps`, `_is_externally_managed_error`. Compatibility
contract: tests import `_print_agent_steps`, `_is_externally_managed_error`;
`__main__.py` imports `main`; `test_agent_mode_footer` monkeypatches
`cli._build_agent`/`_init_acp`/`_print_session_header`/`_print_update_notice`
via module attribute.

## Contents of this zip
- `AgentKthx/` — full working tree at 7fcde77 (no .git, no caches)
- `patches/` — git format-patch series (6 commits), apply in order onto 27b9e5b
