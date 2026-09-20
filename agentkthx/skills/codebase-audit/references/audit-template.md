# Improvement & Enhancement Audit

**{PROJECT_NAME} v{VERSION}**

**Repository:** {REPO_URL}  
**Author:** {AUTHOR} | **License:** {LICENSE} | **Date:** {TIMESTAMP}  
{FINDING_COUNT} Findings | {CATEGORY_COUNT} Categories | {CATEGORY_LIST}

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
  - [Security](#security)
  - [Robustness](#robustness)
  - [Maintainability](#maintainability)
  - [Performance](#performance)
  - [New Features](#new-features)
  - [Architecture](#architecture)
  - [Testing](#testing)
- [Priority Matrix](#priority-matrix)
- [Architecture Strengths](#architecture-strengths)

---

## Executive Summary

A concise overview of the audit. Cover:

- What the project is and the audit scope (files reviewed, lines analyzed)
- Total findings count and category breakdown
- Severity distribution (X High, Y Medium, Z Low)
- The most impactful findings that should be addressed first
- Any noteworthy patterns observed across the codebase

Write this as a narrative paragraph, not a bullet list. The Findings Summary
table below provides the structured overview — the executive summary should
synthesize and contextualize.

---

## Findings Summary

A master table of every finding, sorted by severity (High first), then by
category, then by ID. Each finding gets a unique ID using the pattern
`{CATEGORY_PREFIX}-{NUMBER}`:

| Prefix | Category |
|--------|----------|
| SEC | Security |
| ROB | Robustness |
| MAINT | Maintainability |
| PERF | Performance |
| FEAT | New Feature |
| ARCH | Architecture |
| TEST | Testing |

| ID | Severity | Category | Title |
|----|----------|----------|-------|
| SEC-01 | **High** | Security | {Finding title} |
| MAINT-01 | **High** | Maintainability | {Finding title} |
| SEC-02 | Medium | Security | {Finding title} |
| ROB-01 | Medium | Robustness | {Finding title} |
| PERF-01 | Low | Performance | {Finding title} |

Severity levels:
- **High** — Affects correctness, security, or data integrity. Fix soon.
- Medium — Impacts maintainability, reliability, or UX. Address in planned work.
- Low — Nice-to-have improvement. Address opportunistically.

Omit categories with no findings from the detailed findings section below,
but keep them in the TOC with a note like "No findings in this category."

---

## Detailed Findings

Each finding follows this structure. Use the `### {CATEGORY}` heading for
the category section, then `#### {ID}: {Title}` for each finding within it.

### Security

#### SEC-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Security |
| **File(s)** | `{path/to/file.ts}` |

The finding description. Explain:
- What the current behavior is and why it's a problem
- Specific code references (function names, line numbers, patterns)
- The failure mode or attack vector

End with a concrete recommendation: what to change and how.
If multiple approaches are viable, mention the trade-offs.

**Impact:** One sentence summarizing what fixing this achieves.

---

### Robustness

Findings related to error handling, retry logic, edge cases, resilience
under failure conditions, and graceful degradation.

#### ROB-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Robustness |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

### Maintainability

Findings related to code organization, complexity, duplication, consistency,
documentation, and developer experience.

#### MAINT-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Maintainability |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

### Performance

Findings related to efficiency, caching, streaming, resource usage, and
optimization opportunities grounded in actual code observations.

#### PERF-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Performance |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

### New Features

Suggestions for new capabilities that would meaningfully improve the project.
These should be grounded in gaps or friction points observed during the audit —
not wishlist items. Each feature suggestion should explain why it's needed based
on actual codebase patterns or user-facing limitations observed.

#### FEAT-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | New Feature |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

### Architecture

Findings related to structural patterns, inter-module communication,
extensibility, and design decisions that affect long-term evolution.

#### ARCH-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Architecture |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

### Testing

Findings related to test coverage gaps, missing test types (unit, integration,
e2e), and CI/CD pipeline improvements.

#### TEST-01: {Finding Title}

| Property | Value |
|----------|-------|
| **Severity** | {High\|Medium\|Low} |
| **Category** | Testing |
| **File(s)** | `{path/to/file.ts}` |

{Description}

**Impact:** {One-sentence impact summary}

---

## Priority Matrix

A timeline-based prioritization of all findings. Group findings into release
windows based on severity and effort:

| Timeline | Findings |
|----------|----------|
| **Near term (vX.Y.Z–vX.Y.Z)** | {ID} ({short title}), {ID} ({short title}) |
| **Short term (vX.Y.Z–vX.Y.Z)** | {ID} ({short title}), {ID} ({short title}), ... |
| **Medium term (vX.Y.Z+)** | {ID} ({short title}), {ID} ({short title}), ... |

Guidelines for timeline assignment:
- **Near term** — High severity findings that should be fixed in the next 1-2 releases
- **Short term** — Medium severity findings addressable within 2-4 releases
- **Medium term** — Low severity findings that can be picked up during other work

If the project doesn't use semantic versioning or the user hasn't indicated a
versioning scheme, use relative timelines instead: "Immediate", "Next sprint",
"Backlog".

---

## Architecture Strengths

End the audit on a constructive note. Document the patterns, design decisions,
and structural choices that are working well and should be preserved during
any refactoring. This section serves two purposes:

1. **Guidance for contributors** — ensures good patterns aren't accidentally
   removed during improvement work
2. **Balance** — the audit shouldn't read as purely negative; acknowledging
   strengths builds trust in the findings

Look for:
- Well-designed module boundaries or separation of concerns
- Clever abstractions or patterns that reduce complexity
- Good defensive coding practices (atomic writes, input validation, etc.)
- Extensible design choices (plugin patterns, provider registries, etc.)
- Thoughtful configuration or defaults

Be specific — reference actual code patterns and files. Don't use generic praise.
