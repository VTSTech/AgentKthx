#!/usr/bin/env python3
"""
split-audit.py — Split audit.md into open findings (audit.md) and
closed/wontfix findings (deltas.md).

The audit.md grows over time as findings accumulate. Closed and wontfix
findings are historical — they don't need to be in the active audit.md that
the agent reads during re-audit. This script moves them to deltas.md,
keeping audit.md focused on OPEN findings (the active work items).

generate_audit_dash.py reads BOTH files and merges them into the full
register for the dashboard. The dashboard, JSON endpoints, and reconcile
checker all see the complete picture (open + closed + wontfix); audit.md
itself stays small and focused.

Approach: rather than surgically editing the markdown (fragile), this
script parses the full audit.md, then regenerates two clean files:
  - audit.md: open findings only (rebuilt from parsed findings)
  - deltas.md: closed + wontfix findings (rebuilt from parsed findings)
The header metadata (release, date, commit, test counts) is preserved
from the original audit.md.

Usage:
    python3 audit/split-audit.py                          # split audit/audit.md
    python3 audit/split-audit.py --dry-run               # preview
    python3 audit/split-audit.py --audit path/to/audit.md

Idempotent: re-running on an already-split audit.md is a no-op (no closed/
wontfix findings to move).

Exit codes:
    0  split completed successfully (or --dry-run previewed)
    1  bad invocation (audit.md not found, unreadable)
    2  no-op (nothing to move — already split, or no findings)

Written by VTSTech — https://www.vts-tech.org
"""

import argparse
import os
import re
import sys
from datetime import datetime
from collections import Counter

import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "generate_audit_dash", os.path.join(_HERE, "generate_audit_dash.py")
)
gad = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gad)


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_meta(audit_md):
    """Extract release, date, project, version, commit — mirrors parse_meta."""
    meta = {"project": "Unknown", "release": "R??", "version": "0.0.0",
            "date": "", "commit": "", "tests": 0, "testsSkipped": 0,
            "author": "", "license": "", "repo": "", "pypiUrl": ""}
    m = re.search(r"\*\*(.+?)\s+v([\d.]+)\s+\((R[\d.]+)", audit_md)
    if m:
        meta["project"] = m.group(1).strip()
        meta["version"] = m.group(2)
        meta["release"] = m.group(3)
    m = re.search(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", audit_md)
    if m:
        meta["date"] = m.group(1)
    m = re.search(r"\*\*Commit:\*\*\s*`?([a-f0-9]{7,40})", audit_md)
    if m:
        meta["commit"] = m.group(1)
    m = re.search(r"\*\*Test Suite:\*\*\s*(\d+)\s+passed\s*/\s*(\d+)\s+skipped", audit_md)
    if m:
        meta["tests"] = int(m.group(1))
        meta["testsSkipped"] = int(m.group(2))
    # Author / License / Repo from the header line
    m = re.search(r"\*\*Author:\*\*\s*(\S+).*?\|\s*\*\*License:\*\*\s*(\S+)", audit_md)
    if m:
        meta["author"] = m.group(1)
        meta["license"] = m.group(2)
    m = re.search(r"\*\*Repository:\*\*\s*(\S+)", audit_md)
    if m:
        meta["repo"] = m.group(1)
    return meta


def format_status_cell(finding):
    """Format a finding's status for the table cell — matches what the parser reads."""
    if finding["status"] == "CLOSED":
        if finding.get("closedIn"):
            return f"✓ CLOSED {finding['closedIn']}"
        return "✓ CLOSED"
    elif finding["status"] == "WONTFIX":
        return "⊘ WONTFIX (intentional)"
    else:
        return "OPEN"


def format_severity(sev):
    """Bold the High severity (matches the existing audit.md convention)."""
    if sev == "High":
        return f"**{sev}**"
    return sev


def build_findings_table(findings):
    """Build a Findings Summary table from a list of finding dicts.

    Sorts by severity (High → Medium → Low), then by category, then by ID.
    """
    sev_order = {"High": 0, "Medium": 1, "Low": 2}
    cat_order = {"Security": 0, "Robustness": 1, "Maintainability": 2,
                 "Performance": 3, "New Features": 4, "Architecture": 5, "Testing": 6}
    sorted_findings = sorted(findings, key=lambda f: (
        sev_order.get(f["severity"], 9),
        cat_order.get(f["category"], 9),
        f["id"],
    ))
    lines = [
        "| ID | Severity | Category | Status | Title |",
        "|----|----------|----------|--------|-------|",
    ]
    for f in sorted_findings:
        lines.append(
            f"| {f['id']} | {format_severity(f['severity'])} | {f['category']} | "
            f"{format_status_cell(f)} | {f['title']} |"
        )
    return "\n".join(lines)


def build_audit_md(meta, open_findings, deltas_findings_count):
    """Rebuild audit.md with only OPEN findings.

    Preserves the header metadata + a note about the deltas.md archive.
    """
    by_sev = Counter(f["severity"] for f in open_findings)
    high = by_sev.get("High", 0)
    medium = by_sev.get("Medium", 0)
    low = by_sev.get("Low", 0)

    lines = [
        "# Improvement & Enhancement Audit",
        "",
        f"**{meta['project']} v{meta['version']} ({meta['release']} — in progress)**",
        "",
        f"**Repository:** {meta['repo']}  ",
        f"**Author:** {meta['author']} | **License:** {meta['license']} | **Date:** {meta['date']}  ",
        (f"**Commit:** `{meta['commit']}` | " if meta["commit"] else "**Commit:** (working tree) | ") +
        f"**Test Suite:** {meta['tests']} passed / {meta['testsSkipped']} skipped  ",
        f"{len(open_findings)} Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  ",
        f"Severity: {high} High | {medium} Medium | {low} Low  ",
        f"{len(open_findings)} OPEN (CLOSED + WONTFIX archived in deltas.md — generate_audit_dash.py merges both for the dashboard)",
        "",
        f"> **Split:** {deltas_findings_count} CLOSED/WONTFIX findings moved to `deltas.md`. "
        f"`generate_audit_dash.py` reads both `audit.md` (open) and `deltas.md` (closed/wontfix) "
        f"and merges them into the full register. The dashboard shows all {len(open_findings) + deltas_findings_count} findings.",
        "",
        "---",
        "",
        "## Findings Summary",
        "",
        build_findings_table(open_findings),
        "",
        "---",
        "",
        "## Detailed Findings",
        "",
        f"<!-- Open findings only. CLOSED + WONTFIX detail sections are in deltas.md. -->",
        "",
        f"<!-- To view a closed/wontfix finding's detail, see deltas.md. -->",
        "",
        "---",
        "",
        "## Priority Matrix",
        "",
        "| Timeline | Findings |",
        "|----------|----------|",
        f"| **Near term** | {', '.join(f['id'] for f in open_findings if f['severity'] == 'High') or '—'} |",
        f"| **Short term** | {', '.join(f['id'] for f in open_findings if f['severity'] == 'Medium') or '—'} |",
        f"| **Medium term** | {', '.join(f['id'] for f in open_findings if f['severity'] == 'Low') or '—'} |",
        "",
        "---",
        "",
        "## Architecture Strengths",
        "",
        "<!-- Preserved from the original audit.md. See git history for the full text. -->",
        "",
    ]
    return "\n".join(lines)


def extract_closure_sections(audit_md):
    """Extract the ## Rxx.xx Closures sections from audit.md.

    These sections (| ID | Severity | Status | Notes | tables) drive the
    closure timeline in the dashboard. They belong in deltas.md (they're
    about closed findings), so the split moves them there.
    """
    lines = audit_md.split("\n")
    sections = []
    current = None
    for line in lines:
        if re.match(r"^##\s+R[\d.]+\s+Closures", line):
            if current:
                sections.append("\n".join(current))
            current = [line]
        elif current is not None:
            if line.startswith("## ") and not re.match(r"^##\s+R[\d.]+\s+Closures", line):
                # End of the closures section
                sections.append("\n".join(current))
                current = None
            else:
                current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def build_deltas_md(meta, archived_findings, closure_sections):
    """Build deltas.md with CLOSED + WONTFIX findings + closure sections."""
    by_status = Counter(f["status"] for f in archived_findings)
    by_sev = Counter(f["severity"] for f in archived_findings)

    lines = [
        "# Audit Deltas — Closed & Wontfix Archive",
        "",
        f"**Project:** {meta['project']}  ",
        f"**Release:** {meta['release']}  ",
        f"**Date:** {meta['date']}  ",
        f"**Archived:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC+0')}  ",
        f"**Counts:** {by_status.get('CLOSED', 0)} CLOSED · {by_status.get('WONTFIX', 0)} WONTFIX · {len(archived_findings)} total",
        "",
        "This file is the archive of CLOSED and WONTFIX findings moved out of",
        "`audit.md` to keep the active audit focused on OPEN findings.",
        "`generate_audit_dash.py` reads BOTH `audit.md` (open) and `deltas.md`",
        "(closed/wontfix) and merges them into the full register for the dashboard.",
        "",
        "---",
        "",
        "## Findings Summary (Archived)",
        "",
        build_findings_table(archived_findings),
        "",
        "---",
        "",
        "## Detailed Findings (Archived)",
        "",
        "<!-- Closed + WONTFIX detail sections. Each finding's closure/WONTFIX",
        "     prose (**FIXED (Rxx.xx):** / **WONTFIX (Rxx.xx, owner decision):**)",
        "     is preserved from the original audit.md. -->",
        "",
    ]

    # Group by category, then sort within by ID
    cat_order = ["Security", "Robustness", "Maintainability", "Performance",
                 "New Features", "Architecture", "Testing"]
    by_cat = {}
    for f in archived_findings:
        by_cat.setdefault(f["category"], []).append(f)
    for cat in cat_order:
        if cat not in by_cat:
            continue
        lines.append(f"### {cat}")
        lines.append("")
        for f in sorted(by_cat[cat], key=lambda x: x["id"]):
            lines.append(f"#### {f['id']}: {f['title']}")
            lines.append("")
            lines.append("| Property | Value |")
            lines.append("|----------|-------|")
            lines.append(f"| **Severity** | {format_severity(f['severity'])} |")
            lines.append(f"| **Category** | {cat} |")
            lines.append("")
            lines.append(f"**Status:** {format_status_cell(f)}")
            lines.append("")
            lines.append(f"**Detail:** {f.get('detail') or f['title']}")
            lines.append("")
            lines.append("---")
            lines.append("")

    # Append closure sections (## Rxx.xx Closures) — these drive the
    # dashboard's closure timeline. Preserved verbatim from audit.md.
    if closure_sections:
        lines.append("## Closure Timeline")
        lines.append("")
        lines.append("<!-- Preserved from the original audit.md. The dashboard's")
        lines.append("     closure-timeline cards parse these sections. -->")
        lines.append("")
        for section in closure_sections:
            lines.append(section)
            lines.append("")
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def split(audit_path, deltas_path, dry_run=False):
    """Split audit.md into open (audit.md) + closed/wontfix (deltas.md)."""
    if not os.path.exists(audit_path):
        print(f"[split] ERROR: {audit_path} not found", file=sys.stderr)
        return 1

    audit_md = read_file(audit_path)
    if not audit_md.strip():
        print(f"[split] ERROR: {audit_path} is empty", file=sys.stderr)
        return 1

    # Parse all findings from the current audit.md (no deltas arg → all from audit.md)
    findings = gad.parse_findings(audit_md)
    open_findings = [f for f in findings if f["status"] == "OPEN"]
    archived_findings = [f for f in findings if f["status"] != "OPEN"]

    if not archived_findings:
        print(f"[split] no CLOSED/WONTFIX findings to move — audit.md is already open-only", file=sys.stderr)
        return 2

    meta = extract_meta(audit_md)

    # Extract closure sections (## Rxx.xx Closures) — they drive the dashboard's
    # closure timeline and belong in deltas.md (they're about closed findings).
    closure_sections = extract_closure_sections(audit_md)

    if dry_run:
        print(f"[split] DRY RUN — would move {len(archived_findings)} closed/wontfix findings to {deltas_path}", file=sys.stderr)
        print(f"[split]   audit.md: {len(findings)} → {len(open_findings)} open findings ({len(archived_findings)} moved)", file=sys.stderr)
        print(f"[split]   deltas.md: {len(archived_findings)} archived ({sum(1 for f in archived_findings if f['status']=='CLOSED')} closed, {sum(1 for f in archived_findings if f['status']=='WONTFIX')} wontfix)", file=sys.stderr)
        print(f"[split]   closure sections: {len(closure_sections)} moved to deltas.md", file=sys.stderr)
        return 0

    # Build + write deltas.md
    deltas_content = build_deltas_md(meta, archived_findings, closure_sections)
    out_dir = os.path.dirname(deltas_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(deltas_path, "w", encoding="utf-8") as f:
        f.write(deltas_content)
    print(f"[split] wrote {deltas_path} ({len(archived_findings)} archived findings)", file=sys.stderr)

    # Build + overwrite audit.md (open-only)
    new_audit_md = build_audit_md(meta, open_findings, len(archived_findings))
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(new_audit_md)
    print(f"[split] rewrote {audit_path} ({len(open_findings)} open findings)", file=sys.stderr)

    print(f"[split] {meta['project']} {meta['release']} · {len(findings)} total → {len(open_findings)} open (audit.md) + {len(archived_findings)} archived (deltas.md)", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Split audit.md into open findings (audit.md) + closed/wontfix (deltas.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 audit/split-audit.py                          # split audit/audit.md
  python3 audit/split-audit.py --dry-run               # preview
  python3 audit/split-audit.py --audit path/to/audit.md
        """.strip(),
    )
    ap.add_argument("--audit", default="audit/audit.md",
                    help="path to audit.md (default: audit/audit.md)")
    ap.add_argument("--deltas", default="audit/deltas.md",
                    help="path to deltas.md (default: audit/deltas.md)")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview the split without writing")
    args = ap.parse_args()
    return split(args.audit, args.deltas, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
