---
name: codebase-audit
description: Audit, analyze, and produce a condensed intelligence brief for any codebase.
  Use this skill whenever you need to understand, review, audit, or get oriented on a codebase or project.
  Triggers on phrases like "audit this repo", "review the codebase", "what does this project do",
  "get me up to speed", "brief me on", "understand this code", "code review", or when starting work
  on an unfamiliar codebase. Also triggers when the user asks to clone and review a repo.
  This skill handles two modes: if a brief.md already exists, load it for instant orientation;
  if not, perform a full audit and generate both a brief.md (condensed orientation) and an
  audit.md (detailed findings report). This enables efficient context transfer between
  sessions and agents without re-reading the entire codebase, while also producing a
  comprehensive audit artifact with bugs, security findings, and recommendations.
metadata:
  author: VTSTech
  version: "0.2.1"
---

# Codebase Audit & Intelligence Brief

## Purpose

This skill solves a core problem: understanding a codebase exhausts context window budget before
the real work begins. By producing a structured intelligence brief, it allows subsequent
agents or sessions to skip the discovery phase entirely and start with most of their context
window available for execution.

## Two Modes

On skill invocation, the agent should first check for an existing brief:

```
Check /workspace/brief.md (or project root for brief.md)
    │
    ├── EXISTS → Load mode (instant orientation)
    │
    └── MISSING → Audit mode (full analysis & brief generation)
```

---

## Mode 1: Load (Brief Exists)

If `brief.md` is found in `/workspace/` or the project root:

1. Read `brief.md` in full
2. This document IS your orientation — treat it as authoritative
3. Skip the file-discovery phase entirely
4. Proceed directly to the user's task with your full context window available
5. If you need deeper understanding of a specific file, use the **Critical Files Index**
   in the brief to know exactly which files to read — do not blindly scan

### What to do if the brief seems stale

If the brief references files that don't exist, or the project state has clearly changed
since the brief was generated, inform the user and offer to regenerate it.

---

## Mode 2b: Re-audit (audit.md exists but code has changed)

If an `audit.md` already exists AND the code has changed since it was
generated (the brief's commit hash doesn't match `HEAD`, or files referenced
in the brief have been modified/added/removed), the audit is stale. A fresh
audit is needed — but the existing audit.md contains CLOSED + WONTFIX
findings (with release tags, closure notes, reasoning) that are still valid
and must be preserved. Rather than carrying them in the active audit.md
(where they dilute the focus on OPEN work), move them to `deltas.md`.

### The split design

- **`audit.md`** = OPEN findings only (the active work items). Small, focused.
- **`deltas.md`** = CLOSED + WONTFIX findings (the historical archive). Grows
  over time as findings are resolved. Includes the closure timeline sections
  (`## Rxx.xx Closures`).
- **`generate_audit_dash.py`** reads BOTH files and merges them into the full
  register for the dashboard. The dashboard, JSON endpoints, and reconcile
  checker all see the complete picture (open + closed + wontfix); `audit.md`
  itself stays small.

### The archive-then-fresh workflow

```
audit.md exists + code changed
    │
    ├── 1. split audit.md → audit.md (open) + deltas.md (closed/wontfix)
    │      via audit/split-audit.py — moves CLOSED + WONTFIX out of audit.md
    │
    ├── 2. re-audit the code: update OPEN findings, add new ones, close resolved ones
    │      closed/wontfix findings stay in deltas.md; new closures get added there
    │
    └── 3. regenerate brief.md (the brief references audit findings)
```

### Step 1: Split the existing audit

Run the split script to move CLOSED + WONTFIX findings from `audit.md` to
`deltas.md`. This keeps `audit.md` focused on OPEN findings (the active work)
while preserving the closure history in `deltas.md`. `generate_audit_dash.py`
reads both files and merges them for the dashboard — the full register is
unchanged.

```bash
python3 audit/split-audit.py
# → moves CLOSED + WONTFIX findings from audit.md to deltas.md
# → audit.md becomes open-only (small, focused)
# → deltas.md accumulates the closed/wontfix archive + closure timeline
# → header delta blockquotes move to deltas.md's `## Release Delta Log`
#   (ALL of them — any `> **Rxx.xx …` blockquote in the header zone,
#   regardless of title wording)
# → --dry-run previews without writing
# → idempotent: re-running on an already-split audit.md is a no-op
```

The script DROPS any `## Rxx.xx Closures` sections still in audit.md (the
Closure Timeline is retired as of script v2 — author the closure narrative
into the archived findings' `**Detail:**` prose BEFORE splitting; the
script warns with a list of what was discarded).

### Step 2: Re-audit the code

With CLOSED/WONTFIX findings safely in `deltas.md`, re-audit the current code:

1. **Regenerate the Executive Summary** — the narrative paragraph(s) under
   `## Executive Summary` must be rewritten to reflect the CURRENT release,
   not the release the audit was originally generated against. The
   `split-audit.py` preserves the old Executive Summary verbatim (it
   shouldn't rewrite prose), so the agent must regenerate it manually
   during the re-audit. Update: the commit hash, test count, codebase
   stats (Python files, LOC, test LOC), what the current release closed,
   and what the next-priority OPEN findings are. Reference `deltas.md` for
   the full closure history (releases + counts). The Executive Summary
   is the first thing a reader sees — it must be current.

2. **Re-evaluate OPEN findings** (still in `audit.md`): does the issue still
   exist in the current code? If yes, keep it OPEN. If the code was refactored
   and the issue no longer applies, note it in a delta block and omit it from
   the Findings Summary table.

3. **Close resolved findings**: if an OPEN finding has been fixed in the
   current code, mark it `✓ CLOSED Rxx.xx` in `audit.md`'s Findings Summary,
   add the `**FIXED (Rxx.xx):**` closure prose to its detail section, then
   re-run `split-audit.py` to move it to `deltas.md`.

4. **Add new findings**: assign new IDs (continue the numbering — if
   `deltas.md` + `audit.md` together have SEC-01 through SEC-11, the first
   new security finding is SEC-12) and mark them OPEN in `audit.md`.

5. **Add a delta block** at the top of `audit.md` documenting the re-audit:
   which findings were re-confirmed, which were closed, which no longer apply,
   and which are new.

**`audit.md` stays self-contained for OPEN findings.** `generate_audit_dash.py`
reads `audit.md` (open) + `deltas.md` (closed/wontfix) and merges them — the
dashboard shows the full register. You do NOT need to manually merge
`deltas.md` into `audit.md`; the parser handles it.

### Step 3: Regenerate brief.md

The brief references audit findings (in the "What's Missing / Incomplete"
section). Update it to reflect the fresh audit's OPEN findings + counts.

### Why this matters

Without the split, a re-audit would either:
- **Lose history** — start fresh, forgetting which findings were closed
  and why (the dashboard's closure timeline would reset to zero)
- **Keep stale findings** — carry forward closed/wontfix findings that
  dilute the focus on active OPEN work, making audit.md grow unboundedly

The split preserves the closure history (in `deltas.md`) while keeping
`audit.md` small and focused on OPEN findings. The dashboard sees the full
register because `generate_audit_dash.py` merges both files.

### deltas.md format

`deltas.md` is the CLOSED + WONTFIX archive. `generate_audit_dash.py`
reads it (alongside `audit.md`) and merges — it is NOT reference-only.
The format:

```markdown
# Audit Deltas — Closed & Wontfix Archive

**Project:** AgentKthx  
**Release:** R07.08  
**Date:** 2026-09-27  
**Archived:** 2026-09-27 16:14 UTC+0  
**Counts:** 30 CLOSED · 5 WONTFIX · 35 total

This file is the archive of CLOSED and WONTFIX findings moved out of
`audit.md` to keep the active audit focused on OPEN findings.
`generate_audit_dash.py` reads BOTH `audit.md` (open) and `deltas.md`
(closed/wontfix) and merges them into the full register for the dashboard.

---

## Findings Summary (Archived)

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-01 | Medium | Security | ✓ CLOSED R07.08 | sandbox escape |
| SEC-08 | Low | Security | ⊘ WONTFIX (intentional) | audit log plaintext |
| ...

---

## Detailed Findings (Archived)

### Security

#### SEC-01: sandbox escape

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |

**Status:** ✓ CLOSED R07.08

**Detail:** {closure note}

---

...

## Closure Timeline

<!-- Preserved from audit.md. The dashboard's closure-timeline cards parse these. -->

## R07.08 Closures

| ID | Severity | Status | Notes |
|----|----------|--------|-------|
| SEC-01 | Medium | ✓ CLOSED R07.08 | dropped 5 unsafe builtins |
| ...

Suite 1506 → 1567 passed.

---

## Release Delta Log

<!-- Per-release delta notes, moved verbatim from audit.md's header at
     split time. ALL header blockquotes move here, oldest release first;
     several notes per release (split closure batches) is normal. -->

> **R07.08 delta (...):** one-sentence summary per closed/wontfixed finding + the test-suite delta
```

The split script (`audit/split-audit.py`) generates this structure automatically.
`deltas.md` accumulates as findings are closed/wontfixed over time — re-run
the split after each closure batch to move newly-resolved findings out of
`audit.md`.

---

## Mode 2: Audit (Brief Missing)

If no `brief.md` exists, perform a full codebase audit and generate one.

### Step 1: Identify the Target

Ask the user for the codebase source. This could be:
- A git repo URL to clone
- A local directory path
- An already-cloned repo in the workspace

Clone if necessary, then proceed.

### Step 2: Scan & Understand

The goal is NOT to read every file. The goal is to build an efficient mental model of the
project by reading the minimum set of files that maximizes understanding.

**Start here (in order):**
1. Package/lock files → identify tech stack, dependencies, runtime
2. Config files → build tools, environment settings, frameworks
3. Directory structure (top 2-3 levels) → understand organization
4. README / docs → if they exist, they may save you reading code
5. Entry points → main files, server startup, route definitions
6. Type definitions / schemas → if applicable (Prisma, TypeScript interfaces, etc.)

**Then selectively read:**
- Files that appear in dependency chains from entry points
- Files referenced by config or routing
- Files that seem unusually large or complex (likely core logic)
- Test files (sample a few to understand expected behavior)

**Skip immediately:**
- `node_modules/`, `vendor/`, `__pycache__/`, `.git/`
- Generated files, build output, dist folders
- Migration files (unless they reveal schema evolution)
- Lock files beyond initial scan
- Image assets, fonts, static media

### Step 3: Build the Brief

Use the template in `references/brief-template.md` to structure your output.

The brief should be a **map, not a diary**. You are telling another agent where to look
and what to watch out for — not narrating what you read. Every section should answer the
question: "What does the next agent need to know to be effective?"

### Token Budget

The brief must not exceed **16,000 tokens** (approximately 56,000 characters / 11,000 words).
This ensures the consuming agent retains at least half its context window on even the smallest
realistic model (32K context). On larger models (128K, 256K) the brief is effectively free.

After generating the brief, estimate the token count (characters ÷ 3.5) and verify it is
under the limit. If over budget, compress before saving.

### Content Principles

Prioritize **information density** over brevity. You have headroom to be thorough within
the 16K limit — use it for things that genuinely help the next agent:

- **Be specific** — "auth fails silently on expired tokens" not "auth has issues"
- **Include code snippets** for critical functions, tricky logic, or non-obvious patterns
- **Be action-oriented** — "edit this file to change X" not "this file contains X"
- **Be honest about gaps** — if you couldn't understand something, say so
- **Don't pad** — if a section doesn't apply, omit it rather than writing filler

Sections where depth is most valuable:
1. **Critical Files Index** — include key function signatures, important constants
2. **Known Landmines** — these save real debugging time, be thorough
3. **Request Lifecycle** — include actual function call chains, not just descriptions

### Step 4: Save

Save the completed brief to:
- `/workspace/brief.md` (preferred, if ACP workspace is available)
- `{project_root}/brief.md` (fallback)

Inform the user the brief has been generated and is ready for use by other sessions/agents.

---

## Step 5: Build the Audit Report (audit.md)

After saving the brief, generate a detailed audit report using the template in
`references/audit-template.md`. This is the expanded counterpart to the brief — where
the brief is a concise map for orientation, the audit.md is the full findings document.

The audit report is always generated as part of the audit workflow. It is NOT optional.

### Structure and Categories

Findings are organized into 7 categories, each with a unique ID prefix:

| Prefix | Category | What it covers |
|--------|----------|----------------|
| SEC | Security | Vulnerabilities, input validation, SSRF, injection, data exposure |
| ROB | Robustness | Error handling, retry logic, edge cases, graceful degradation |
| MAINT | Maintainability | Complexity, duplication, consistency, developer experience |
| PERF | Performance | Caching, streaming, resource usage, optimization |
| FEAT | New Feature | Capabilities suggested by gaps/friction observed in the codebase |
| ARCH | Architecture | Structural patterns, inter-module communication, extensibility |
| TEST | Testing | Coverage gaps, missing test types, CI/CD improvements |

Each finding gets a property table (Severity, Category, File(s)), a description
with concrete code references, and a one-sentence impact statement. Findings are
summarized in a master table (sorted by severity), detailed by category, and
prioritized in a timeline-based Priority Matrix.

The report always ends with an **Architecture Strengths** section — document what
the codebase does well so good patterns aren't lost during refactoring.

### Dashboard Parser Contract (machine-readable audit.md)

`generate_audit_dash.py` (in `audit/`) parses `audit.md` to produce the
self-contained dashboard HTML + JSON API endpoints. The audit.md **is the
single source of truth** — the dashboard, the `/api/findings/*.json`
endpoints, the preview panel's live audit card, and the reconcile drift
checker all read from it. To stay machine-parseable, the audit.md you
generate **must** conform to these format rules:

1. **Header counts line.** The prose header must include count tokens the
   parser can find: `{N} CLOSED ... | {N} WONTFIX ... | {N} OPEN` and
   `{N} Findings | {N} Categories | ...`. The reconcile endpoint compares
   these prose counts against the actual table — if they drift, the
   dashboard flags `matches: false`. **Always recompute the prose counts
   after changing the findings table.**

2. **Findings Summary table — 5 columns, Status mandatory.** The table
   under `## Findings Summary` must have exactly these columns:
   `| ID | Severity | Category | Status | Title |`. The parser's regex
   captures 5 groups. The **Status** column drives the closed/open/wontfix
   counts. Status cell formats the parser recognises:
   - `OPEN`
   - `✓ CLOSED R07.04` (release tag captured as `closedIn`)
   - `CLOSED` or `✓` (CLOSED, `closedIn` null)
   - `⊘ WONTFIX (intentional)` or `WONTFIX` or `⊘`
   - Strikethrough `~~SEC-01~~` marks a finding as removed/renumbered
     (parser still reads the row but the strikethrough is preserved).

3. **New Findings delta table — 5 columns, File(s) instead of Status.**
   Under `## Rxx.xx New Findings`: `| ID | Severity | Category | File(s) | Title |`.
   The parser uses this to pick up `file` info for findings added since
   the last audit pass.

4. **Closures section — 4 columns.** Under `## Rxx.xx Closures`:
   `| ID | Severity | Status | Notes |`. Findings marked CLOSED here
   inherit the release tag as `closedIn`. The prose around the table
   (e.g. `1132 → 1290 passed`) is parsed for the closure timeline's
   test-count deltas.

5. **Closure / WONTFIX prose.** Under each finding's detail section,
   append a `**FIXED (Rxx.xx):** ...` or `**WONTFIX (Rxx.xx, owner decision):** ...`
   paragraph before the `---` separator. The parser does NOT read this
   prose (it reads the Status column), but it's the human-readable
   closure record — keep it specific: what changed, which file, +N
   regression tests, and the release tag. WONTFIX prose must explain
   *why* the fix would make things worse or what existing mechanism
   already covers the use case.

6. **Release delta blockquotes.** Prepend a `> **Rxx.xx …:**` blockquote
   per release at the top of the file (newest first, below the header
   counts line). The wording after the release token is free-form —
   `delta`, `feature delta`, `re-audit delta`, `closure batch N` all
   parse; the one hard rule is that the bold text starts with the
   release token (`> **Rxx.xx`). One-sentence summary per
   closed/wontfixed finding + the test-suite delta. The full closure
   detail lives in the `**FIXED (Rxx.xx):**` paragraph under each
   finding. **Retirement:** these blockquotes are transient, not
   permanent residents — `split-audit.py` moves every header-zone
   blockquote verbatim into deltas.md's `## Release Delta Log` at
   split time, so the header only ever holds deltas released since the
   last split. (Historical note: v2 of the script required the exact
   title `Rxx.xx delta` and matched none of the real titles — that
   drift is why the header stack once grew unbounded.)

7. **Categories must be exact.** The parser only recognises these 7
   category strings: `Security`, `Robustness`, `Maintainability`,
   `Performance`, `New Features`, `Architecture`, `Testing`. Any other
   category value causes the finding to be silently dropped from the
   dashboard. (Note: the FEAT category is "New Features", not "New Feature".)

**If you're unsure whether your audit.md parses cleanly**, run:
```bash
python3 audit/generate_audit_dash.py --audit path/to/audit.md --brief path/to/brief.md --output /tmp/test.html
# then check /tmp/api/findings/reconcile.json — matches: true means the
# prose header counts agree with the parsed table.
```

### Content Principles for audit.md:

- **Every finding must reference a specific file and line/section** — no vague claims
- **Use the ID system** (SEC-01, ROB-01, etc.) — it makes findings trackable across iterations
- **Severity must be grounded** — High means correctness/security/data integrity at stake;
  Medium means reliability or DX impact; Low means nice-to-have
- **New Features must be grounded in observations** — suggest features because you saw
  a gap or friction point in the code, not because it sounds cool
- **Architecture Strengths must be specific** — reference actual code patterns, not generic praise
- **Only include findings you actually observed** — don't speculate or list generic best practices
- **Be honest about what you couldn't assess** — if a file was too large to read fully, say so
- **Omit categories with no findings** from the detailed section (note in TOC instead)

### Token Budget

The audit report has no hard token limit since it is saved as a file, not loaded into
context. However, aim for comprehensiveness without padding. A good audit report for a
medium codebase (~5-10K lines) is typically 4,000-10,000 words.

### Save Location

Save the completed audit report to:
- `/workspace/audit.md` (preferred, if ACP workspace is available)
- `{project_root}/audit.md` (fallback)

Inform the user both files have been generated.

---

## Optional: PDF/DOCX Deliverable

If the user explicitly requests a formatted document (PDF or DOCX), the audit.md serves
as the source content. Use the appropriate document skill (pdf or docx) to produce a
polished, styled version of the audit findings. The brief and audit.md are always
generated as Markdown regardless — the formatted document is an additional deliverable
built from the audit.md content.

---

## Brief Freshness

A brief is only as good as its currency. The brief should include a generated timestamp.
If the project has significant changes, the brief should be regenerated.

Guidance: if more than ~20% of the files referenced in the brief have changed or been
renamed since the brief was generated, it's time for a refresh.

---

## External Endpoints

This skill makes NO network requests. It only accesses local files and the user's codebase.

## Security & Privacy