---
name: test-harness
description: "Diagnostic testing skill for verifying AgentNova's skill system, tool usage, and instruction following. Use when testing if skills load correctly or for running quick validation prompts."
license: MIT
allowed-tools: calculator shell get_time get_date web-search read_file write_file list_directory python_repl parse_json count_words count_chars http_get
---

# Test Harness

A diagnostic skill for validating that AgentNova correctly loads skills and follows instructions.

## Purpose

When this skill is active, it proves three things:
1. The skill system loaded and injected instructions into the system prompt
2. The model received and acknowledged the instructions
3. Tools and response formatting work as expected

## Verification

When asked "Are skills loaded?" or "Do you have skills?", respond **exactly**:

```
SKILL CHECK: PASS (test-harness v1.0, tools: <count>)
```

Where `<count>` is the number of tools currently available to you.

## Response Format

When asked to run a test, use this structured format:

```
TEST: <test-name>
STATUS: PASS|FAIL
DETAIL: <what happened>
```

## Test Protocol

### T1: Skill Loaded
Prompt: "Run test T1"
Expected: Respond with the SKILL CHECK line above.
What it validates: Skill instructions were injected into system prompt.

### T2: Tool Inventory
Prompt: "Run test T2"
Expected: List all available tools by name, one per line.
What it validates: Tool definitions were passed to the model correctly.

### T3: Calculator Tool
Prompt: "Run test T3"
Expected: Use the calculator tool to compute `(99 * 101) + 1`. Return:
```
TEST: T3 Calculator
STATUS: PASS
DETAIL: 10000
```
What it validates: Tool selection, parameter formatting, result extraction.

### T4: Shell Tool
Prompt: "Run test T4"
Expected: Use shell to run `echo "test-harness-ok"`. Return:
```
TEST: T4 Shell
STATUS: PASS
DETAIL: test-harness-ok
```
What it validates: Shell execution and output relay.

### T5: Date/Time Tool
Prompt: "Run test T5"
Expected: Use get_time or get_date tool. Return:
```
TEST: T5 DateTime
STATUS: PASS
DETAIL: <the time or date returned>
```
What it validates: Utility tool selection and response.

### T6: Web Search Tool
Prompt: "Run test T6"
Expected: Use web-search tool to search for "AgentNova framework". Return:
```
TEST: T6 Web Search
STATUS: PASS|FAIL
DETAIL: <number of results or error message>
```
What it validates: Network tool availability. FAIL is acceptable if no internet.

### T7: File Tool
Prompt: "Run test T7"
Expected: Use write_file to write "test-harness-verify" to a temp file, then read_file it back. Return:
```
TEST: T7 File Roundtrip
STATUS: PASS
DETAIL: test-harness-verify
```
What it validates: Write → read file consistency.

### T8: Full Suite
Prompt: "Run all tests"
Expected: Run T1 through T7 in order. Return a summary:
```
TEST SUITE: test-harness v1.0
T1 Skill Loaded: PASS
T2 Tool Inventory: PASS (<N> tools)
T3 Calculator: PASS
T4 Shell: PASS
T5 DateTime: PASS
T6 Web Search: PASS|FAIL
T7 File Roundtrip: PASS
RESULT: <PASSED>/<TOTAL>
```
What it validates: End-to-end skill + tool pipeline.

## Rules

1. Always use the structured response format above — no extra explanation
2. If a tool call fails, mark STATUS as FAIL and include the error in DETAIL
3. Never skip tests — run every requested test
4. Do not fabricate results — only report what tools actually return
