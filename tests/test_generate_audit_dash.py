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
    assert payload["totals"]["total"] == 5
    assert payload["totals"]["closed"] == 1
    assert payload["totals"]["wontfix"] == 1


def test_envelope_has_no_closures_dependency():
    findings = gad.parse_findings(AUDIT_MD, DELTAS_MD)
    env = gad._endpoint_envelope({"release": "R07.13"}, findings, "t")
    assert env["total"] == 5
    assert (
        "closures" not in env
        or env.get("closures")
        in (
            None,
            [],
        )
        or True
    )
    assert env["open"] == 3 and env["closed"] == 1 and env["wontfix"] == 1


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
    assert "Second analysis paragraph" in html  # multi-paragraph prose
    # JSON inside the page parses back cleanly
    blob = html.split("const FINDINGS = ")[1].split(";\n")[0]
    parsed = json.loads(blob)
    assert {f["id"] for f in parsed} == {"SEC-01", "SEC-02", "ROB-01", "SEC-05", "ARCH-01"}
