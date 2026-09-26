---
name: codebase-audit
description: >
  Audit, analyze, and produce a condensed intelligence brief for any codebase.
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
  version: "0.2.0"
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

All analysis is local. The brief and audit report contain only structural, architectural,
and quality information about the codebase — no credentials, no secrets, no sensitive
runtime data. The audit report may reference code patterns that resemble security issues
but will not expose actual secrets or credentials found in the code.
