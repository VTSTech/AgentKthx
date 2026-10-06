"""
R07.25 smoke-test contract regression — pins the TEST-09 closure.

TEST-09 (Plugin scaffolds miss agent-loop streaming-path integration test)
was closed by `scripts/smoke_test_r07_25.sh` — a superset of the R07.21 smoke
test that adds a 4th step per backend: re-runs the `--tools shell`
invocation WITHOUT `--no-stream`, forcing the streaming path
(`generate_completions_stream` → `_iter_sse_lines` → SSE chunk parse →
tool-call extraction).

The R07.09 streaming bug (missing `_iter_sse_lines` abstract hook — first
shipped as `NotImplementedError` at chat invocation) was caught by the
user's live `agentkthx chat --backend mistral` run, NOT by the 64-test
suite: tests asserted the method existed and unit-tested its pieces, but
nothing exercised the agent loop → `generate_completions_stream` →
`_iter_sse_lines` call-through.

These tests pin the smoke-test contract so a future refactor that drops
the streaming step (or breaks the streaming-vs-non-streaming marker
parity that makes a streaming-only regression observable as step-4-fail
+ step-3-pass) trips a test failure before the contract drifts.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "smoke_test_r07_25.sh"
LEGACY_R07_21 = REPO_ROOT / "scripts" / "smoke_test_r07_21.sh"


def _read_script() -> str:
    """Read the smoke-test script body (cached for the test session)."""
    return SCRIPT.read_text(encoding="utf-8")


def test_smoke_test_r07_25_script_exists_and_is_executable():
    """The R07.25 smoke test must exist at the canonical path and be
    marked executable (chmod +x) so CI can invoke it directly."""
    assert SCRIPT.is_file(), f"missing smoke test at {SCRIPT}"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, f"{SCRIPT} is not user-executable (chmod +x)"


def test_help_documents_the_streaming_step():
    """`--help` must mention the streaming step (step 4) so operators
    running the smoke test know it covers the streaming path. The R07.09
    bug was a streaming-only regression — without explicit documentation
    a future operator might think `--no-stream` is the default and miss
    that step 4 exists."""
    body = _read_script()
    # The --help block must list step 4 explicitly.
    assert "Step 4" in body or "step 4" in body, (
        "--help block must document step 4 (the streaming path)"
    )
    # And it must mention the streaming path explicitly.
    assert "STREAMING" in body, (
        "--help block must mention the STREAMING path"
    )
    # And reference TEST-09 in the comment header so the closure is traceable.
    assert "TEST-09" in body, (
        "script header must reference TEST-09 so the closure is traceable"
    )


def test_script_body_contains_both_streaming_and_non_streaming_invocations():
    """The script body must contain BOTH `--no-stream` (the non-streaming
    flag for step 3) AND a `run_tool_step` invocation that omits it
    (step 4, the streaming path). A future refactor that drops one of
    these flags silently would break the contract."""
    body = _read_script()
    # The non-streaming flag is on step 3.
    assert "--no-stream" in body, (
        "step 3 (non-streaming) must use the --no-stream flag"
    )
    # The streaming step (step 4) is a `run_tool_step` call WITHOUT the
    # --no-stream flag — verified by the presence of the empty-flags
    # invocation pattern.
    # Look for: run_tool_step ... "streaming" "" ...
    pattern = re.compile(
        r'run_tool_step\s+"[^"]+"\s+"[^"]+"\s+"streaming"\s+""',
        re.MULTILINE,
    )
    assert pattern.search(body), (
        "step 4 (streaming) must be a run_tool_step call with empty extra_flags "
        "(no --no-stream) — the streaming path"
    )
    # And the non-streaming step must be a `run_tool_step` call with
    # "--no-stream" as extra_flags.
    pattern_ns = re.compile(
        r'run_tool_step\s+"[^"]+"\s+"[^"]+"\s+"non-streaming"\s+"--no-stream"',
        re.MULTILINE,
    )
    assert pattern_ns.search(body), (
        "step 3 (non-streaming) must be a run_tool_step call with --no-stream "
        "as extra_flags"
    )


def test_streaming_step_timeout_is_at_least_120s():
    """The streaming step has a longer timeout than the non-streaming
    step (180s vs 120s) because the stream chunk decode + tool-call arg
    assembly adds latency on the first invocation per backend. A future
    refactor that lowers STREAM_TIMEOUT below the NONSTREAM_TIMEOUT
    would cause step 4 to spuriously fail on slow backends — pin the
    invariant: STREAM_TIMEOUT >= 120 AND STREAM_TIMEOUT > NONSTREAM_TIMEOUT."""
    body = _read_script()
    # Find the STREAM_TIMEOUT default at the START of a line (the variable
    # declaration). Use a word-boundary regex so we don't accidentally
    # match NONSTREAM_TIMEOUT (the substring 'STREAM_TIMEOUT' appears in
    # both). The `(?m)^` anchor ensures we match only at line start.
    m = re.search(r'(?m)^STREAM_TIMEOUT=(\d+)\s*$', body)
    assert m, "STREAM_TIMEOUT must be declared as a top-level assignment in the script body"
    stream_timeout = int(m.group(1))
    assert stream_timeout >= 120, (
        f"STREAM_TIMEOUT must be >= 120s (currently {stream_timeout}s) — "
        f"streaming path needs the longer timeout to avoid spurious failures "
        f"on cold-cache + TLS-handshake invocations"
    )
    # And it must be strictly greater than NONSTREAM_TIMEOUT (the R07.25
    # design calls for a longer streaming timeout, not equal).
    m_ns = re.search(r'(?m)^NONSTREAM_TIMEOUT=(\d+)\s*$', body)
    assert m_ns, "NONSTREAM_TIMEOUT must be declared as a top-level assignment in the script body"
    nonstream_timeout = int(m_ns.group(1))
    assert stream_timeout > nonstream_timeout, (
        f"STREAM_TIMEOUT ({stream_timeout}s) must be strictly greater than "
        f"NONSTREAM_TIMEOUT ({nonstream_timeout}s) — the streaming path is "
        f"strictly slower than the non-streaming path"
    )


def test_smoke_marker_is_the_same_for_both_paths():
    """The smoke marker (`smoke-test-marker-$$`) must be the SAME for
    steps 3 and 4. The point of the R07.25 fix is that a streaming-only
    regression (e.g. NotImplementedError on `generate_completions_stream`,
    or SSE chunks without the `tool_calls` delta) shows up as
    step-4-fail + step-3-pass — exactly the R07.09 bug shape. If the
    markers differed, a streaming-path bug might pass step 4 by
    accidentally surfacing a different marker from a different code path.
    Pin: SMOKE_MARKER is defined exactly ONCE in the script body."""
    body = _read_script()
    # Count occurrences of SMOKE_MARKER= assignment (must be exactly 1).
    assigns = re.findall(r'^\s*SMOKE_MARKER=\S+', body, re.MULTILINE)
    assert len(assigns) == 1, (
        f"SMOKE_MARKER must be assigned exactly once (found {len(assigns)}) "
        f"— the same marker is shared by steps 3 and 4 so a streaming-only "
        f"regression is observable as step-4-fail + step-3-pass"
    )
    # Both steps must use $SMOKE_MARKER (not a literal or different var).
    # The shared `run_tool_step` helper builds the prompt with $SMOKE_MARKER,
    # so a single use-site covers both step 3 and step 4.
    uses = re.findall(r'echo \$\{?SMOKE_MARKER\}?', body)
    assert len(uses) >= 2, (
        f"SMOKE_MARKER must be used in at least 2 places (the run_tool_step "
        f"helper's echo invocation + the summary print); found {len(uses)}"
    )


def test_legacy_r07_21_smoke_test_is_preserved_for_back_compat():
    """The R07.21 smoke test is kept for back-compat (operators with
    existing CI scripts that invoke it shouldn't break). R07.25
    supersedes it for any future streaming-adjacent change but doesn't
    delete the original. Pin: the R07.21 script still exists.

    Note: the R07.21 script is checked in as 100644 (not executable)
    because it's invoked via `bash scripts/smoke_test_r07_21.sh` in CI,
    not via direct `./scripts/...`. Don't assert the executable bit —
    the back-compat contract is just "the file still exists"."""
    assert LEGACY_R07_21.is_file(), (
        f"legacy smoke_test_r07_21.sh must be preserved at {LEGACY_R07_21} "
        f"for back-compat — R07.25 supersedes but does not delete"
    )


def test_script_does_not_hardcode_backend_specific_skips_in_streaming_step():
    """The streaming step (step 4) must run for ALL backends — no
    per-backend skip list that would silently exempt a backend from
    streaming-path coverage. The R07.09 bug was on Mistral; a future
    skip-list could re-introduce the same gap on a different backend.

    We only forbid backend-name conditionals that gate step 4 directly
    (i.e. the conditional body references 'step 4' or 'streaming' as a
    label). The existing `openai && $model_count -eq 0` conditional
    gates the model-listing step (step 1) — it's about OpenAI having
    no free tier, NOT about streaming. Don't flag it."""
    body = _read_script()
    # Find blocks of: if [[ "$backend" == <X> ... ]]; then ... fi
    # where the block body contains 'step 4' or 'streaming' or
    # 'run_tool_step ... "streaming"'.
    #
    # We use a simple state machine: walk lines, track when we're inside
    # a `if [[ "$backend" == ... ]]` block, and check if the block body
    # contains a step-4 reference.
    in_backend_cond = False
    cond_body = []
    offending = []
    for line in body.splitlines():
        if not in_backend_cond:
            # Detect start: `if [[ "$backend" == <something> ]] ...`
            # (may span multiple lines via line continuations, but the
            # existing script has single-line conditions).
            if re.search(r'if\s+\[\s*\[\s*"\$backend"\s*==', line):
                in_backend_cond = True
                cond_body = [line]
            continue
        # We're inside the block.
        cond_body.append(line)
        # Detect end: a line that starts with `fi` (possibly with a `;;`
        # for case blocks, but those use `esac`).
        if re.match(r'^\s*fi\b', line):
            block_text = "\n".join(cond_body)
            # Check if this block gates step 4 (the streaming step).
            # The step-4 invocation is `run_tool_step ... "streaming"`,
            # and the test would catch a conditional that wraps it.
            if 'step 4' in block_text.lower() or '"streaming"' in block_text:
                # Found a backend-specific conditional that gates the
                # streaming step — that's the bug shape we're guarding
                # against.
                offending.append(block_text)
            in_backend_cond = False
            cond_body = []
    assert not offending, (
        f"step 4 must run for ALL backends — found backend-specific "
        f"conditional(s) that gate the streaming step: {offending}"
    )


def test_script_handles_unknown_backend_gracefully():
    """An unknown --backend value (typo) must NOT crash the script with
    a bash indirect-expansion error — it should be skipped cleanly
    with a "no API key" message. The R07.25 fix added an empty-envvar
    guard in `has_key`; this test pins that guard against regression."""
    # The script body must contain the empty-envvar guard.
    body = _read_script()
    assert 'if [[ -z "$envvar" ]]; then' in body, (
        "has_key() must guard against empty envvar (unknown backend) "
        "before the indirect expansion `${!envvar:-}` — without this "
        "guard, bash raises `: invalid variable name` on empty var"
    )
