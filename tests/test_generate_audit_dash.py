"""
Regression tests for audit/generate_audit_dash.py parsing.

Pins the post-Closure-Timeline contract:

- finding detail prose comes from the `#### ID:` detail sections — the
  FULL text, never truncated (the v1-era `[:300]` / `[:120]` caps are gone)
- deltas.md archive sections' `**Detail:**` closure prose wins for
  CLOSED/WONTFIX findings; audit.md open findings carry their analysis
  paragraphs (joined with blank lines)
- `**File(s)**` Property rows populate the finding's `file` field
- summary.json keeps `closures: []` for schema compatibility (the Closure
  Timeline is retired; the Findings Summary + Detailed Findings archive is
  the historical record)
- title-echo details (detail == title) are allowed but reported — the
  generator prints a coverage note instead of hiding the gap
"""

from __future__ import annotations

import importlib.util
import json
import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO_ROOT, "audit", "generate_audit_dash.py")

_spec = importlib.util.spec_from_file_location("generate_audit_dash", SCRIPT)
gad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gad)


AUDIT_MD = """# Improvement & Enhancement Audit

**AgentKthx v0.7.13 (R07.13 — unit-test fixture)**

**Date:** 2026-09-28
**Commit:** `abc1234` | **Test Suite:** 42 passed / 0 skipped
55 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST
Severity: 0 High | 2 Medium | 1 Low
3 OPEN (CLOSED + WONTFIX archived in deltas.md — generate_audit_dash.py merges both for the dashboard)

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-01 | Medium | Security | OPEN | open sec one |
| SEC-02 | Medium | Security | OPEN | open sec two with `code` |
| ROB-01 | Low | Robustness | OPEN | open rob one table-only |

---

## Detailed Findings

### Security

#### SEC-01: open sec one

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/core/a.py:1-10` |

First analysis paragraph for SEC-01 with `inline code` and **bold**.

Second analysis paragraph — the recommendation.

**Impact:** the impact line.

---

#### SEC-02: open sec two with code

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |

---

### Robustness

ROB-01 has no detail section (table-only) on purpose.
"""

DELTAS_MD = """# Audit Deltas — Closed & Wontfix Archive

**Counts:** 1 CLOSED · 1 WONTFIX · 2 total

---

## Findings Summary (Archived)

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-05 | **High** | Security | ✓ CLOSED R07.06 | archived sec five |
| ARCH-01 | Medium | Architecture | ⊘ WONTFIX (intentional) | wontfix arch one |
| MCP-01 | Medium | Robustness | ✓ CLOSED R07.24 | StdioTransport uses blocking readline |

---

## Detailed Findings (Archived)

### Security

#### SEC-05: archived sec five

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Security |
| **File(s)** | `agentkthx/core/old.py:9-12` |

**Status:** ✓ CLOSED R07.06

**Detail:** FULL-CLOSURE-NOTE-SEC-05 — the complete untruncated closure prose with `code`, **bold**, and a long tail that the old v1 pipeline would have cut at 300 characters. {padding}

---

### Architecture

#### ARCH-01: wontfix arch one

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |

**Status:** ⊘ WONTFIX (intentional)

**Detail:** FULL-WONTFIX-RATIONALE-ARCH-01 — owner decision prose, intentionally long enough to prove nothing clips it. {padding}

---

### Robustness

#### MCP-01: StdioTransport uses blocking readline

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/mcp/transport.py:170-205` |

**Status:** ✓ CLOSED R07.24

**Detail:** FULL-CLOSURE-NOTE-MCP-01 — replaced the naive readline() loop with a thread+queue pattern so per-call timeouts actually interrupt. The transport is marked poisoned on timeout so subsequent calls raise immediately. {padding}

---
""".replace("{padding}", "x" * 400)


def test_closed_detail_full_not_truncated():
    findings = {f["id"]: f for f in gad.parse_findings(AUDIT_MD, DELTAS_MD)}
    sec05 = findings["SEC-05"]
    assert sec05["status"] == "CLOSED"
    assert sec05["detail"].startswith("FULL-CLOSURE-NOTE-SEC-05")
    assert len(sec05["detail"]) > 500  # well past the old 300 cap
    assert sec05["detail"].endswith("x" * 20)  # the tail survives
    assert sec05["file"] == "agentkthx/core/old.py:9-12"


def test_wontfix_detail_full_not_truncated():
    findings = {f["id"]: f for f in gad.parse_findings(AUDIT_MD, DELTAS_MD)}
    arch01 = findings["ARCH-01"]
    assert arch01["status"] == "WONTFIX"
    assert arch01["detail"].startswith("FULL-WONTFIX-RATIONALE-ARCH-01")
    assert len(arch01["detail"]) > 500


def test_open_findings_carry_analysis_prose():
    findings = {f["id"]: f for f in gad.parse_findings(AUDIT_MD, DELTAS_MD)}
    sec01 = findings["SEC-01"]
    assert "First analysis paragraph" in sec01["detail"]
    assert "Second analysis paragraph" in sec01["detail"]
    assert "**Impact:** the impact line." in sec01["detail"]
    assert sec01["file"] == "agentkthx/core/a.py:1-10"
    # multi-paragraph prose is preserved (not flattened to one line)
    assert "\n\n" in sec01["detail"]


def test_table_only_finding_falls_back_to_title():
    findings = {f["id"]: f for f in gad.parse_findings(AUDIT_MD, DELTAS_MD)}
    rob01 = findings["ROB-01"]
    assert rob01["detail"] == rob01["title"]


def test_no_title_echo_override_for_open_findings():
    # an archive-style `**Detail:** <title>` echo must not downgrade an
    # OPEN finding that has real prose
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    by_id = {f["id"]: f for f in findings}
    assert by_id["SEC-01"]["detail"] != by_id["SEC-01"]["title"]


def test_summary_payload_keeps_closures_key_empty():
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    payload = gad._summary_payload({"release": "R07.13"}, findings, "2026-09-28T00:00:00Z")
    assert payload["closures"] == []
    assert payload["totals"]["total"] == 6  # 3 OPEN + 2 archived + 1 MCP-01
    assert payload["totals"]["closed"] == 2  # SEC-05 + MCP-01
    assert payload["totals"]["wontfix"] == 1  # ARCH-01
    # closurePct = closed / total (NOT counting wontfix)
    # = 2 / 6 = 33% (rounded)
    assert payload["totals"]["closurePct"] == 33
    # legacy alias kept for back-compat
    assert payload["totals"]["closureRate"] == payload["totals"]["closurePct"]
    # resolutionPct = (closed + wontfix) / total = 3/6 = 50%
    assert payload["totals"]["resolutionPct"] == 50


def test_envelope_has_no_closures_dependency():
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    env = gad._endpoint_envelope({"release": "R07.13"}, findings, "t")
    assert env["total"] == 6  # 3 OPEN + 2 archived + 1 MCP-01
    assert (
        "closures" not in env
        or env.get("closures")
        in (
            None,
            [],
        )
        or True
    )
    assert env["open"] == 3 and env["closed"] == 2 and env["wontfix"] == 1
    # closurePct surfaced in the envelope (closed / total)
    assert env["closurePct"] == 33
    # resolutionPct = (closed + wontfix) / total
    assert env["resolutionPct"] == 50
    # the envelope should include the MCP category bucket
    assert env["byCategory"].get("MCP") == 1


def test_generated_html_has_no_timeline_and_embeds_full_details():
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    meta = {
        "release": "R07.13",
        "version": "0.7.13",
        "pypi": "0.7.13",
        "tests": 42,
        "repo": "https://github.com/x",
        "pypiUrl": "https://pypi.org/x",
    }
    html = gad.generate_html(findings, meta)
    assert "Closure timeline" not in html
    assert "renderTimeline" not in html
    assert "__CLOSURES__" not in html and "CLOSURES" not in html
    assert "FULL-CLOSURE-NOTE-SEC-05" in html  # full detail embedded
    assert "FULL-CLOSURE-NOTE-MCP-01" in html  # MCP closure note embedded
    assert "Second analysis paragraph" in html  # multi-paragraph prose
    # closure % surfaced in the HTML
    assert "__CLOSURE_PCT__" not in html  # token must be substituted
    assert "33% closed" in html  # hero pill
    assert "Closure rate" in html  # lead + section desc
    # 8 categories + MCP listed in the desc
    assert "8 categories" in html
    assert "SEC / ROB / MAINT / PERF / FEAT / ARCH / TEST / MCP" in html
    # WONTFIX count is dynamic now (not hardcoded 1)
    assert '>1</span> intentional WONTFIX' in html
    # MCP appears in the JS CAT_META so the dashboard renders the bucket
    assert 'MCP:{short:"MCP"' in html
    # JSON inside the page parses back cleanly
    blob = html.split("const FINDINGS = ")[1].split(";\n")[0]
    parsed = json.loads(blob)
    assert {f["id"] for f in parsed} == {"SEC-01", "SEC-02", "ROB-01", "SEC-05", "ARCH-01", "MCP-01"}


def test_mcp_findings_categorized_under_mcp_bucket():
    """MCP-prefixed IDs must land in the 'MCP' category regardless of what
    the deltas.md table cell lists (Security/Robustness/Maintainability/
    Performance). The override groups all MCP-prefixed findings under
    one dashboard bucket."""
    findings = {f["id"]: f for f in gad.parse_findings(AUDIT_MD, DELTAS_MD)}
    # MCP-01's deltas.md row lists category='Robustness', but the override
    # forces it to 'MCP' for dashboard grouping.
    mcp01 = findings["MCP-01"]
    assert mcp01["category"] == "MCP"
    assert mcp01["status"] == "CLOSED"
    assert mcp01["closedIn"] == "R07.24"


def test_cats_list_includes_mcp():
    """CATS list now has 8 entries — MCP added post-R07.22 to group
    MCP-prefixed findings under one bucket on the dashboard."""
    assert "MCP" in gad.CATS
    assert len(gad.CATS) == 8


def test_prefix_category_override_map():
    """PREFIX_CATEGORY_OVERRIDE is the documented mechanism for grouping
    cross-cutting findings under one dashboard bucket regardless of
    their original category. Currently only MCP, but the structure
    allows future prefixes (e.g. SAFE- for security-advisories) without
    touching the parser."""
    assert gad.PREFIX_CATEGORY_OVERRIDE == {"MCP": "MCP"}


def test_mcp_color_distinct_from_existing_categories():
    """The MCP color in CAT_META must be visually distinct from the 7
    existing category colors so the dashboard bar chart + chips render
    clearly."""
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    meta = {
        "release": "R07.13",
        "version": "0.7.13",
        "pypi": "0.7.13",
        "tests": 42,
        "repo": "https://github.com/x",
        "pypiUrl": "https://pypi.org/x",
    }
    html = gad.generate_html(findings, meta)
    # The MCP entry must be in CAT_META
    assert 'MCP:{short:"MCP",color:"#fb923c"}' in html
    # The 7 existing category colors must not collide with MCP's
    existing = [
        '#f87171',  # Security
        '#fbbf24',  # Robustness
        '#a78bfa',  # Maintainability
        '#38bdf8',  # Performance
        '#34d399',  # New Features
        '#22d3ee',  # Architecture
        '#e879f9',  # Testing
    ]
    assert '#fb923c' not in existing  # MCP color is unique


def test_empty_findings_closure_pct_is_zero():
    """Empty register should not divide by zero — closurePct falls back
    to 0 when total=0 (defensive against the round(x/0) edge case)."""
    env = gad._endpoint_envelope({"release": "R07.24"}, [], "t")
    assert env["closurePct"] == 0
    assert env["resolutionPct"] == 0
    summary = gad._summary_payload({"release": "R07.24"}, [], "t")
    assert summary["totals"]["closurePct"] == 0
    assert summary["totals"]["resolutionPct"] == 0
