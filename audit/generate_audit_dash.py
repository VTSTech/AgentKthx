#!/usr/bin/env python3
"""
generate_audit_dash.py — Generate a self-contained AgentKthx audit dashboard.

Fetches audit.md + brief.md from the AgentKthx GitHub repo (main branch),
parses the findings register, and writes a single self-contained index.html
(no CDN, no build step, no external deps).

Finding detail prose comes from the `#### ID:` detail sections — FULL text,
never truncated: audit.md carries the open findings' analysis, deltas.md
(the CLOSED + WONTFIX archive) carries each finding's closure/WONTFIX
`**Detail:**` prose. The Findings Summary + Detailed Findings sections are
the historical record (the old Closure Timeline is retired).

Since the dashboard is a static site, it also emits a small set of static JSON
"API endpoint" files into <output-dir>/api/findings/ so external clients (the
preview panel, CI, curl) can consume the parsed register without scraping the
page:

    api/findings.json            all findings + totals
    api/findings/open.json        open findings only
    api/findings/closed.json      closed findings only
    api/findings/wontfix.json     wontfix findings only
    api/findings/summary.json     counts rollup (closures: [] for schema compat)
    api/findings/reconcile.json   prose-vs-table drift check
    api/findings/<ID>.json        per-finding detail (one per finding)

Pass --no-endpoints to skip the JSON files and write only the HTML.

Usage:
    python3 generate_audit_dash.py
    python3 generate_audit_dash.py --output audit-dash/index.html
    python3 generate_audit_dash.py --audit ./audit.md --brief ./brief.md
    python3 generate_audit_dash.py --base-url https://raw.githubusercontent.com/VTSTech/AgentKthx/main/audit
    python3 generate_audit_dash.py --no-endpoints

Stdlib only. Python 3.8+.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

DEFAULT_BASE = "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/audit"

# ─── fetching ──────────────────────────────────────────────────────────────

def fetch(url):
    """Fetch a URL and return its text content."""
    req = urllib.request.Request(url, headers={"User-Agent": "generate_audit_dash/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def get_audit_brief(args):
    """Return (audit_md, brief_md, deltas_md) from URL or local file.

    deltas.md holds the CLOSED + WONTFIX findings (the archive), while
    audit.md holds the OPEN findings (the active register). The dashboard
    merges both into the full register. If deltas.md is absent, audit.md
    is treated as the complete register (backward-compatible).
    """
    if args.audit and args.brief:
        audit_md = read_file(args.audit)
        brief_md = read_file(args.brief)
        deltas_md = read_deltas(audit_md, args)
        return audit_md, brief_md, deltas_md
    base = args.base_url.rstrip("/")
    audit_url = f"{base}/audit.md"
    brief_url = f"{base}/brief.md"
    deltas_url = f"{base}/deltas.md"
    print(f"[gen] fetching {audit_url}", file=sys.stderr)
    audit_md = fetch(audit_url)
    print(f"[gen] fetching {brief_url}", file=sys.stderr)
    brief_md = fetch(brief_url)
    deltas_md = read_deltas(audit_md, args)
    return audit_md, brief_md, deltas_md


def read_deltas(audit_md, args):
    """Read deltas.md if it exists alongside audit.md (local or URL).

    deltas.md is the CLOSED + WONTFIX archive. If absent, return "" —
    the parser then falls back to reading all findings from audit.md alone
    (the pre-split behaviour, backward-compatible).
    """
    deltas_path = getattr(args, "deltas", None)
    if deltas_path is None:
        # Default: look for deltas.md next to audit.md
        if args.audit:
            deltas_path = os.path.join(os.path.dirname(args.audit) or ".", "deltas.md")
        else:
            base = args.base_url.rstrip("/")
            try:
                print(f"[gen] fetching {base}/deltas.md", file=sys.stderr)
                return fetch(f"{base}/deltas.md")
            except Exception:
                return ""
    if deltas_path and os.path.exists(deltas_path):
        return read_file(deltas_path)
    return ""

# ─── parsing: metadata ─────────────────────────────────────────────────────

def parse_meta(audit_md, brief_md):
    """Extract project metadata from the audit/brief headers."""
    meta = {
        "release": "R??",
        "version": "0.7.??",
        "pypi": "0.7.??",
        "date": "",
        "tests": 0,
        "testsSkipped": 0,
        "commit": "",
        "author": "VTSTech",
        "license": "MIT",
        "repo": "https://github.com/VTSTech/AgentKthx",
        "pypiUrl": "https://pypi.org/project/agentkthx/",
    }
    # **AgentKthx v0.7.07 (R07.07 — released)**
    m = re.search(r"\*\*AgentKthx\s+v([\d.]+)\s+\((R[\d.]+)", audit_md)
    if m:
        meta["version"] = m.group(1)
        meta["release"] = m.group(2)
        meta["pypi"] = m.group(1)
    # **Date:** 2026-09-27
    m = re.search(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", audit_md)
    if m:
        meta["date"] = m.group(1)
    # **Test Suite:** 1506 passed / 9 skipped in ~25s
    m = re.search(r"\*\*Test Suite:\*\*\s*(\d+)\s+passed\s*/\s*(\d+)\s+skipped", audit_md)
    if m:
        meta["tests"] = int(m.group(1))
        meta["testsSkipped"] = int(m.group(2))
    # **Commit:** post-R07.07 fixes (working tree)  OR  Commit: `97fa6cc`
    m = re.search(r"\*\*Commit:\*\*\s*`?([a-f0-9]{7,40})", audit_md)
    if m:
        meta["commit"] = m.group(1)
    # fallback: brief.md header has Commit: `97fa6cc` (R07.06, PyPI 0.7.06)
    if not meta["commit"]:
        m = re.search(r"Commit:\s*`([a-f0-9]{7,40})`", brief_md)
        if m:
            meta["commit"] = m.group(1)
    return meta

# ─── parsing: findings ─────────────────────────────────────────────────────

CATS = ["Security", "Robustness", "Maintainability", "Performance",
        "New Features", "Architecture", "Testing"]

def strip_md(s):
    """Strip markdown formatting from inline text."""
    s = re.sub(r"`([^`]+)`", r"\1", s)          # inline code
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)       # bold
    s = s.replace("\\|", "|")                    # escaped pipes
    s = s.strip()
    return s

def parse_status_cell(cell):
    """Parse a status table cell → (status, closedIn)."""
    c = cell.strip()
    if "WONTFIX" in c or "⊘" in c:
        return "WONTFIX", None
    m = re.search(r"CLOSED\s+(R[\d.]+)", c)
    if m:
        return "CLOSED", m.group(1)
    if "CLOSED" in c or "✓" in c:
        return "CLOSED", None
    return "OPEN", None

def parse_findings(audit_md, deltas_md=""):
    """
    Parse findings from:
      1. The Findings Summary table (| ID | Severity | Category | Status | Title |)
      2. The Rxx.xx delta tables (| ID | Severity | Category | File(s) | Title |)
      3. Closure section tables (pre-split audit.md — to mark CLOSED findings)
      4. The `#### ID:` detail sections — FULL detail prose + File(s) row
         (never truncated; the sections are the historical record)

    If deltas_md is provided, also parse findings from it (the CLOSED + WONTFIX
    archive) and merge. audit.md holds OPEN findings; deltas.md holds CLOSED +
    WONTFIX. The merge is by finding ID — deltas.md findings supplement
    audit.md's (they don't overlap because the split moves them out).

    If deltas_md is empty (pre-split audit.md, or the file doesn't exist),
    all findings come from audit.md alone (backward-compatible).
    Returns a list of finding dicts.
    """
    findings = _parse_findings_from_md(audit_md)
    if deltas_md:
        deltas_findings = _parse_findings_from_md(deltas_md)
        for fid, f in deltas_findings.items():
            if fid not in findings:
                findings[fid] = f
            # If the finding is in both (shouldn't happen post-split, but
            # be defensive), the deltas version wins for CLOSED/WONTFIX status.
            else:
                existing = findings[fid]
                if existing["status"] == "OPEN" and f["status"] != "OPEN":
                    findings[fid] = f
    return list(findings.values())


def _parse_findings_from_md(md_text):
    """Parse findings from a single markdown text (audit.md OR deltas.md).

    Factored out so parse_findings can call it twice (audit + deltas) and
    merge. Returns a dict {id: finding} (not a list) for easy merge.
    """
    findings = {}  # id → finding dict

    # detail-section patterns (used by step 4 below)
    detail_heading_pat = re.compile(r"^####\s+([A-Z]+-\d+):\s*(.*)$")
    detail_line_pat = re.compile(r"^\*\*Detail:\*\*\s*(.*)$")
    file_row_pat = re.compile(r"^\|\s*\*\*File\(s\)\*\*\s*\|\s*(.+?)\s*\|\s*$")

    # 1. Findings Summary table
    # | SEC-02 | **High** | Security | ✓ CLOSED R07.04 | title |
    summary_pat = re.compile(
        r"^\|\s*(`?~~)?([A-Z]+-\d+)(~~`?)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|?\s*$"
    )
    in_summary = False
    for line in md_text.split("\n"):
        if "## Findings Summary" in line:
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            in_summary = False
            continue
        if not in_summary:
            continue
        if line.startswith("|---") or line.startswith("| ID") or not line.startswith("|"):
            continue
        m = summary_pat.match(line)
        if not m:
            continue
        _, fid, _, sev, cat, status_cell, title = m.groups()
        sev = strip_md(sev)
        cat = strip_md(cat)
        title = strip_md(title)
        if cat not in CATS:
            continue
        status, closed_in = parse_status_cell(status_cell)
        if fid not in findings:
            findings[fid] = {
                "id": fid, "severity": sev, "category": cat,
                "status": status, "closedIn": closed_in,
                "title": title, "detail": "", "file": None,
            }
        else:
            f = findings[fid]
            f["severity"] = sev
            f["category"] = cat
            f["title"] = title
            if status != "OPEN":
                f["status"] = status
                f["closedIn"] = closed_in

    # 2. R07.07 (or later) delta tables — | ID | Severity | Category | File(s) | Title |
    # These appear under "## R07.07 New Findings" etc.
    delta_pat = re.compile(
        r"^\|\s*(`?~~)?([A-Z]+-\d+)(~~`?)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(`[^`]+`|.+?)\s*\|\s*(.+?)\s*\|?\s*$"
    )
    in_delta = False
    for line in md_text.split("\n"):
        if re.match(r"## R[\d.]+ New Findings", line):
            in_delta = True
            continue
        if in_delta and line.startswith("## "):
            in_delta = False
            continue
        if not in_delta:
            continue
        if line.startswith("|---") or line.startswith("| ID") or not line.startswith("|"):
            continue
        m = delta_pat.match(line)
        if not m:
            continue
        _, fid, _, sev, cat, file_cell, title = m.groups()
        sev = strip_md(sev)
        cat = strip_md(cat)
        title = strip_md(title)
        if cat not in CATS:
            continue
        file_val = strip_md(file_cell) if file_cell else None
        if fid not in findings:
            findings[fid] = {
                "id": fid, "severity": sev, "category": cat,
                "status": "OPEN", "closedIn": None,
                "title": title, "detail": "", "file": file_val,
            }
        else:
            f = findings[fid]
            # delta may update file info
            if file_val:
                f["file"] = file_val
            # don't overwrite a CLOSED status from the summary with OPEN
            if f["status"] == "OPEN":
                f["severity"] = sev or f["severity"]
                f["category"] = cat or f["category"]
                f["title"] = title or f["title"]

    # 3. Closure section tables — | ID | Severity | Status | Notes |
    # Mark findings as CLOSED + capture closedIn + detail
    closure_section_pat = re.compile(r"## (R[\d.]+)\s+Closures")
    closure_row_pat = re.compile(
        r"^\|\s*(`?~~)?([A-Z]+-\d+)(~~`?)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|?\s*$"
    )
    current_release = None
    in_closure_table = False
    for line in md_text.split("\n"):
        m_sec = closure_section_pat.match(line)
        if m_sec:
            current_release = m_sec.group(1)
            in_closure_table = True
            continue
        if line.startswith("## "):
            in_closure_table = False
            current_release = None
            continue
        if not in_closure_table or not current_release:
            continue
        if line.startswith("|---") or line.startswith("| ID") or not line.startswith("|"):
            continue
        m = closure_row_pat.match(line)
        if not m:
            continue
        _, fid, _, _sev, status_cell, notes = m.groups()
        status, _ = parse_status_cell(status_cell)
        if status == "CLOSED":
            if fid in findings:
                findings[fid]["status"] = "CLOSED"
                findings[fid]["closedIn"] = current_release
                # FULL closure note — never truncated
                if len(strip_md(notes)) > len(findings[fid].get("detail", "")):
                    findings[fid]["detail"] = strip_md(notes)
            else:
                # finding not in summary/delta — add it
                findings[fid] = {
                    "id": fid, "severity": strip_md(_sev), "category": "",
                    "status": "CLOSED", "closedIn": current_release,
                    "title": strip_md(notes), "detail": strip_md(notes),
                    "file": None,
                }

    # 4. `#### ID:` detail sections — the authoritative detail prose.
    # audit.md: open findings' analysis; deltas.md: closure/WONTFIX prose
    # (**Detail:** …). File(s) Property rows populate the file field.
    # Sections never truncate: the whole body is the finding's detail.
    cur = None
    body = []  # (kind, text) paragraphs for the current section
    section_files = None

    def _flush_section():
        if cur is None or cur not in findings:
            return
        f = findings[cur]
        detail_text = None
        prose = []
        for kind, text in body:
            if kind == "detail":
                detail_text = text
            elif kind == "prose":
                prose.append(text)
        if detail_text:
            # archive closure prose (**Detail:** …) — always wins for
            # CLOSED/WONTFIX; for OPEN findings prefer non-echo prose
            if f["status"] != "OPEN" or detail_text.strip() != f["title"].strip():
                f["detail"] = detail_text
        elif prose:
            joined = "\n\n".join(prose)
            # don't downgrade a real closure note to a title echo
            if joined.strip() != f["title"].strip():
                f["detail"] = joined
        if section_files and not f.get("file"):
            f["file"] = section_files

    for line in md_text.split("\n"):
        hm = detail_heading_pat.match(line)
        if hm:
            _flush_section()
            cur = hm.group(1)
            body = []
            section_files = None
            # capture the heading title as a fallback detail for OPEN rows
            continue
        if line.startswith(("## ", "### ")):
            _flush_section()
            cur = None
            continue
        if cur is None:
            continue
        if line.startswith("|"):
            if line.startswith("|---") or line.startswith("| Property"):
                continue
            fm = file_row_pat.match(line)
            if fm:
                section_files = strip_md(fm.group(1))
            continue
        dm = detail_line_pat.match(line)
        if dm:
            body.append(("detail", dm.group(1).strip()))
            continue
        if line.strip().startswith("**Status:**"):
            continue
        if line.strip() in ("", "---"):
            continue
        body.append(("prose", line.rstrip()))
    _flush_section()

    # Fill empty details with the title for findings that have no closure notes
    for f in findings.values():
        if not f["detail"]:
            f["detail"] = f["title"]
        if not f["category"]:
            # try to infer from ID prefix
            prefix = f["id"].split("-")[0]
            cat_map = {"SEC": "Security", "ROB": "Robustness", "MAINT": "Maintainability",
                       "PERF": "Performance", "FEAT": "New Features",
                       "ARCH": "Architecture", "TEST": "Testing"}
            f["category"] = cat_map.get(prefix, "Maintainability")

    return findings

# ─── HTML generation ───────────────────────────────────────────────────────

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentKthx — Audit Dashboard</title>
<meta name="description" content="AgentKthx __RELEASE__ audit register — __TOTAL__ findings across 7 categories, tracked release-over-release.">
<style>
:root{--radius:.75rem;--bg:oklch(.165 .012 165);--fg:oklch(.965 .006 155);--card:oklch(.215 .014 165);--muted:oklch(.25 .012 165);--muted-fg:oklch(.72 .015 160);--border:oklch(1 0 0 / 9%);--primary:oklch(.8 .16 158);--primary-fg:oklch(.18 .04 165);--accent:oklch(.3 .05 162);--emerald:oklch(.8 .16 158);--amber:oklch(.769 .188 70.08);--rose:oklch(.645 .246 16.439);--sky:oklch(.696 .17 200);--violet:oklch(.627 .265 303.9);--fuchsia:oklch(.7 .22 320);--cyan:oklch(.7 .15 200);--zinc:oklch(.6 .01 260)}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--fg);line-height:1.5;-webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","cv04","cv11"}
::selection{background:oklch(.8 .16 158 / .35)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-feature-settings:"calt" 0}
a{color:inherit;text-decoration:none}
button{font:inherit;cursor:pointer;color:inherit;background:none;border:none}
input{font:inherit;color:inherit}
.wrap{max-width:80rem;margin:0 auto;padding:0 1rem}
@media(min-width:640px){.wrap{padding:0 1.5rem}}
@media(min-width:1024px){.wrap{padding:0 2rem}}
.nav{position:sticky;top:0;z-index:50;backdrop-filter:blur(14px);background:oklch(.215 .014 165 / .72);border-bottom:1px solid var(--border);transition:box-shadow .3s}
.nav.scrolled{box-shadow:0 8px 30px -20px rgba(0,0,0,.6)}
.nav-inner{display:flex;align-items:center;justify-content:space-between;height:4rem}
.brand{display:flex;align-items:center;gap:.625rem}
.brand-mark{display:flex;align-items:center;justify-content:center;width:2.25rem;height:2.25rem;border-radius:.75rem;border:1px solid oklch(.8 .16 158 / .3);background:oklch(.8 .16 158 / .1);font-size:1.125rem}
.brand-text{display:flex;flex-direction:column;line-height:1}
.brand-name{font-size:.875rem;font-weight:700}
.brand-sub{font-size:.625rem;color:var(--muted-fg);font-family:ui-monospace,monospace}
.nav-links{display:flex;align-items:center;gap:.25rem}
.nav-links a{padding:.5rem .75rem;border-radius:.375rem;font-size:.875rem;font-weight:500;color:var(--muted-fg);transition:background .15s,color .15s}
.nav-links a:hover{background:var(--accent);color:var(--fg)}
.nav-pill{display:inline-flex;align-items:center;gap:.375rem;padding:.375rem .75rem;border-radius:9999px;border:1px solid oklch(.8 .16 158 / .25);background:oklch(.8 .16 158 / .1);font-size:.75rem;color:var(--primary)}
@media(max-width:768px){.nav-links{display:none}}
header.hero{position:relative;overflow:hidden;border-bottom:1px solid var(--border);padding:2.5rem 0}
@media(min-width:1024px){header.hero{padding:3.5rem 0}}
.hero-bg{position:absolute;inset:0;z-index:-1;opacity:.7;background-image:linear-gradient(to right,oklch(.8 .16 158 / .07) 1px,transparent 1px),linear-gradient(to bottom,oklch(.8 .16 158 / .07) 1px,transparent 1px);background-size:44px 44px;-webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 55%,transparent 100%);mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 55%,transparent 100%)}
.hero-glow{position:absolute;inset-inline:0;top:0;height:280px;z-index:-1;background:radial-gradient(circle at 50% 0%,oklch(.8 .16 158 / .28),transparent 62%)}
.hero-inner{display:flex;flex-direction:column;gap:1rem}
@media(min-width:640px){.hero-inner{flex-direction:row;align-items:flex-end;justify-content:space-between}}
.eyebrow{display:inline-flex;align-items:center;gap:.5rem;border:1px solid oklch(.8 .16 158 / .3);background:oklch(.8 .16 158 / .1);color:var(--primary);padding:.25rem .75rem;border-radius:9999px;font-size:.75rem;font-weight:500;width:fit-content;margin-bottom:.5rem}
.dot{position:relative;display:inline-flex;width:.5rem;height:.5rem}
.dot::before{content:"";position:absolute;inset:0;border-radius:9999px;background:var(--primary);animation:ping 1.5s cubic-bezier(0,0,.2,1) infinite}
.dot::after{content:"";position:relative;display:inline-flex;width:.5rem;height:.5rem;border-radius:9999px;background:var(--primary)}
@keyframes ping{75%,100%{transform:scale(2);opacity:0}}
h1{font-size:1.875rem;font-weight:800;line-height:1.1;letter-spacing:-.02em}
@media(min-width:640px){h1{font-size:2.25rem}}
.grad{background:linear-gradient(120deg,var(--primary),oklch(.8 .16 158 / .5));-webkit-background-clip:text;background-clip:text;color:transparent}
.muted{color:var(--muted-fg)}
.lead{margin-top:.5rem;max-width:42rem;font-size:.875rem;color:var(--muted-fg)}
@media(min-width:640px){.lead{font-size:1rem}}
.pills{display:flex;flex-wrap:wrap;gap:.625rem;flex-shrink:0}
.pill{display:inline-flex;align-items:center;gap:.375rem;border:1px solid var(--border);background:oklch(.215 .014 165 / .5);padding:.375rem .75rem;border-radius:9999px;font-size:.75rem;font-weight:500}
.pill .ic{color:var(--primary)}
section{padding:4rem 0;border-top:1px solid oklch(1 0 0 / .06)}
@media(min-width:1024px){section{padding:6rem 0}}
.eyebrow-s{display:inline-flex;align-items:center;gap:.5rem;border:1px solid oklch(.8 .16 158 / .25);background:oklch(.8 .16 158 / .1);color:var(--primary);padding:.25rem .75rem;border-radius:9999px;font-size:.75rem;font-weight:500;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.75rem}
.eyebrow-s::before{content:"";width:.375rem;height:.375rem;border-radius:9999px;background:var(--primary)}
h2{font-size:1.875rem;font-weight:700;letter-spacing:-.02em}
@media(min-width:640px){h2{font-size:2.25rem}}
.desc{margin-top:1rem;max-width:48rem;font-size:1rem;color:var(--muted-fg);line-height:1.6}
@media(min-width:640px){.desc{font-size:1.125rem}}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:.75rem;margin-bottom:2rem}
@media(min-width:1024px){.stats{grid-template-columns:repeat(4,1fr)}}
.stat{border:1px solid var(--border);background:oklch(.215 .014 165 / .4);border-radius:var(--radius);padding:1.25rem}
.stat-val{font-size:1.875rem;font-weight:700;font-variant-numeric:tabular-nums}
@media(min-width:640px){.stat-val{font-size:2.25rem}}
.stat-lbl{margin-top:.25rem;font-size:.875rem;font-weight:500}
.stat-hint{font-size:.6875rem;color:var(--muted-fg)}
.stat.primary{border-color:oklch(.8 .16 158 / .3);color:var(--primary)}
.stat.emerald{border-color:oklch(.769 .188 70.08 / .3);color:oklch(.769 .188 70.08)}
.stat.amber{border-color:oklch(.769 .188 70.08 / .3);color:oklch(.769 .188 70.08)}
.stat.zinc{border-color:oklch(.6 .01 260 / .3);color:oklch(.6 .01 260)}
.charts{display:grid;grid-template-columns:1fr;gap:1rem;margin-bottom:2.5rem}
@media(min-width:1024px){.charts{grid-template-columns:2fr 3fr}}
.chart-card{border:1px solid var(--border);background:oklch(.215 .014 165 / .4);border-radius:var(--radius);padding:1.25rem}
.chart-card h3{font-size:.875rem;font-weight:600}
.chart-card .sub{font-size:.75rem;color:var(--muted-fg);margin-bottom:.75rem}
.chart-box{height:220px;position:relative}
.legend{margin-top:.5rem;display:flex;justify-content:center;gap:1rem;flex-wrap:wrap}
.legend-item{display:flex;align-items:center;gap:.375rem;font-size:.75rem}
.legend-sw{width:.625rem;height:.625rem;border-radius:.125rem}
.chips{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-bottom:1rem}
.chip{display:inline-flex;align-items:center;gap:.375rem;border:1px solid var(--border);background:oklch(.215 .014 165 / .4);color:var(--muted-fg);padding:.375rem .75rem;border-radius:9999px;font-size:.75rem;font-weight:500;transition:all .15s;cursor:pointer}
.chip:hover{color:var(--fg)}
.chip.active{border-color:var(--primary);background:var(--primary);color:var(--primary-fg)}
.chip .cnt{opacity:.7;font-family:ui-monospace,monospace}
.filters{display:flex;flex-direction:column;gap:.75rem;margin-bottom:1rem}
@media(min-width:640px){.filters{flex-direction:row;align-items:center}}
.search{position:relative;flex:1}
.search svg{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);width:1rem;height:1rem;color:var(--muted-fg);pointer-events:none}
.search input{width:100%;height:2.5rem;padding:0 2.25rem 0 2.25rem;border-radius:.5rem;border:1px solid oklch(1 0 0 / .06);background:oklch(.215 .014 165 / .4);font-size:.875rem;font-family:ui-monospace,monospace}
.search input:focus{outline:none;border-color:var(--primary)}
.search .clear{position:absolute;right:.625rem;top:50%;transform:translateY(-50%);padding:.25rem;color:var(--muted-fg);cursor:pointer}
.search .clear:hover{color:var(--fg)}
.seg{display:inline-flex;flex-shrink:0;border:1px solid oklch(1 0 0 / .06);background:oklch(.215 .014 165 / .4);border-radius:.5rem;padding:.125rem}
.seg button{padding:.375rem .625rem;border-radius:.375rem;font-size:.75rem;font-weight:500;color:var(--muted-fg);transition:all .15s}
.seg button.active{background:var(--primary);color:var(--primary-fg)}
.seg button:hover:not(.active){color:var(--fg)}
.reset{display:inline-flex;align-items:center;gap:.375rem;border:1px solid var(--border);padding:.5rem .75rem;border-radius:.5rem;font-size:.75rem;color:var(--muted-fg);cursor:pointer}
.reset:hover{color:var(--fg)}
.table-wrap{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.table-head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);background:oklch(.215 .014 165 / .4);padding:.625rem 1rem}
.table-head .cnt{font-size:.75rem;color:var(--muted-fg)}
.table-scroll{max-height:560px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:oklch(.8 .16 158 / .35) transparent}
.table-scroll::-webkit-scrollbar{width:8px;height:8px}
.table-scroll::-webkit-scrollbar-thumb{background:oklch(.8 .16 158 / .35);border-radius:9999px}
table{width:100%;border-collapse:collapse;font-size:.875rem}
thead{position:sticky;top:0;background:oklch(.215 .014 165 / .95);backdrop-filter:blur(8px);z-index:1}
thead th{text-align:left;padding:.625rem .5rem;font-size:.6875rem;font-weight:500;text-transform:uppercase;letter-spacing:.05em;color:var(--muted-fg);border-bottom:1px solid var(--border)}
tbody tr{border-bottom:1px solid oklch(1 0 0 / .04);background:oklch(.165 .012 165 / .3);cursor:pointer;transition:background .15s}
tbody tr:hover{background:oklch(.3 .05 162 / .4)}
td{padding:.75rem .5rem;vertical-align:top}
td.id{font-family:ui-monospace,monospace;font-size:.75rem;font-weight:600;white-space:nowrap}
td.file{display:none}
@media(min-width:640px){td.file{display:table-cell}}
.badge{display:inline-flex;align-items:center;gap:.25rem;border:1px solid;border-radius:9999px;padding:.125rem .5rem;font-size:.625rem;font-weight:500;white-space:nowrap}
.badge .d{width:.375rem;height:.375rem;border-radius:9999px}
.sev-High{border-color:oklch(.645 .246 16.439 / .3);background:oklch(.645 .246 16.439 / .1);color:oklch(.645 .246 16.439)}
.sev-High .d{background:oklch(.645 .246 16.439)}
.sev-Medium{border-color:oklch(.769 .188 70.08 / .3);background:oklch(.769 .188 70.08 / .1);color:oklch(.769 .188 70.08)}
.sev-Medium .d{background:oklch(.769 .188 70.08)}
.sev-Low{border-color:oklch(.696 .17 200 / .3);background:oklch(.696 .17 200 / .1);color:oklch(.696 .17 200)}
.sev-Low .d{background:oklch(.696 .17 200)}
.st-CLOSED{border-color:oklch(.8 .16 158 / .3);background:oklch(.8 .16 158 / .1);color:oklch(.8 .16 158)}
.st-OPEN{border-color:oklch(.769 .188 70.08 / .3);background:oklch(.769 .188 70.08 / .1);color:oklch(.769 .188 70.08)}
.st-WONTFIX{border-color:oklch(.6 .01 260 / .3);background:oklch(.6 .01 260 / .1);color:oklch(.6 .01 260)}
.cat-short{font-family:ui-monospace,monospace;font-size:.625rem;color:var(--muted-fg)}
.finding-title{font-weight:500;color:oklch(.965 .006 155 / .9);line-height:1.3}
.finding-detail{margin-top:.125rem;font-size:.75rem;color:var(--muted-fg);line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.finding-detail.expanded{margin-top:.375rem;display:block;-webkit-line-clamp:unset;overflow:visible;white-space:pre-line}
.finding-file{font-family:ui-monospace,monospace;font-size:.625rem;color:oklch(.8 .16 158 / .8);margin-top:.25rem}
.empty{padding:3rem 1rem;text-align:center;color:var(--muted-fg);font-size:.875rem}
footer{margin-top:auto;border-top:1px solid var(--border);background:oklch(.215 .014 165 / .3);padding:3rem 0}
.footer-grid{display:grid;grid-template-columns:1fr;gap:2.5rem}
@media(min-width:768px){.footer-grid{grid-template-columns:1.4fr 1fr 1fr}}
.footer-brand{display:flex;align-items:center;gap:.625rem}
.footer-name{font-size:.875rem;font-weight:700}
.footer-sub{font-size:.625rem;color:var(--muted-fg);font-family:ui-monospace,monospace}
.footer-p{margin-top:1rem;max-width:24rem;font-size:.875rem;color:var(--muted-fg);line-height:1.6}
.footer-h{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--muted-fg);margin-bottom:.75rem}
.footer-ul{list-style:none;display:flex;flex-direction:column;gap:.5rem}
.footer-ul a{font-size:.875rem;color:var(--muted-fg);display:inline-flex;align-items:center;gap:.375rem}
.footer-ul a:hover{color:var(--fg)}
.footer-bottom{margin-top:2.5rem;padding-top:1.5rem;border-top:1px solid oklch(1 0 0 / .06);font-size:.75rem;color:var(--muted-fg);display:flex;flex-direction:column;gap:.75rem}
@media(min-width:640px){.footer-bottom{flex-direction:row;align-items:center;justify-content:space-between}}
.btt{position:fixed;bottom:1.5rem;right:1.5rem;z-index:40;width:2.75rem;height:2.75rem;border-radius:9999px;border:1px solid oklch(.8 .16 158 / .4);background:oklch(.215 .014 165 / .8);color:var(--primary);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px);box-shadow:0 4px 12px rgba(0,0,0,.3);transition:all .3s;opacity:0;pointer-events:none;transform:translateY(1rem);cursor:pointer}
.btt.show{opacity:1;pointer-events:auto;transform:translateY(0)}
.btt:hover{background:oklch(.8 .16 158 / .2)}
.gen-note{margin-top:1.25rem;padding:.75rem 1rem;border:1px solid oklch(.8 .16 158 / .2);background:oklch(.8 .16 158 / .05);border-radius:.5rem;font-size:.75rem;color:var(--muted-fg)}
.gen-note code{font-family:ui-monospace,monospace;background:var(--muted);padding:.125rem .375rem;border-radius:.25rem;font-size:.7rem}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<nav class="nav" id="nav">
  <div class="wrap nav-inner">
    <a href="#top" class="brand">
      <span class="brand-mark">⚛️</span>
      <span class="brand-text">
        <span class="brand-name">AgentKthx</span>
        <span class="brand-sub">__BRAND_SUB__</span>
      </span>
    </a>
    <div class="nav-links">
      <a href="#audit">Audit</a>
      <a href="__REPO__" target="_blank" rel="noreferrer">GitHub</a>
    </div>
    <span class="nav-pill">internal audit bench · Alpha</span>
  </div>
</nav>
<header class="hero" id="top">
  <div class="hero-bg"></div>
  <div class="hero-glow"></div>
  <div class="wrap hero-inner">
    <div>
      <div class="eyebrow"><span class="dot"></span> internal audit bench · Alpha</div>
      <h1>⚛️ <span class="grad">AgentKthx</span> <span class="muted" style="font-weight:400">audit dashboard</span></h1>
      <p class="lead">Live audit register for the __RELEASE__ working tree — <b id="hd-total">__TOTAL__</b> findings across 7 categories, tracked release-over-release. Filter, search, and inspect the register below.</p>
    </div>
    <div class="pills">
      <span class="pill"><span class="ic">⎇</span> __RELEASE__</span>
      <span class="pill"><span class="ic">✓</span> __TESTS__ tests</span>
      <span class="pill"><span class="ic">⊘</span> __TOTAL__ findings</span>
    </div>
  </div>
</header>
<main>
<section id="audit" class="wrap">
  <div class="eyebrow-s">Audit dashboard</div>
  <h2><span class="grad" id="hd-total2">__TOTAL__</span> findings, tracked release-over-release</h2>
  <p class="desc">Every finding has a stable ID (SEC / ROB / MAINT / PERF / FEAT / ARCH / TEST), a severity, and a status. <span id="hd-closed">__CLOSED__</span> closed across the tracked releases, 1 intentional WONTFIX. Filter, search, and inspect the register below.</p>
  <div class="stats" id="stats"></div>
  <div class="charts">
    <div class="chart-card">
      <h3>By status</h3>
      <p class="sub">Closure progress at a glance</p>
      <div class="chart-box" id="pie-box"></div>
      <div class="legend" id="pie-legend"></div>
    </div>
    <div class="chart-card">
      <h3>By category</h3>
      <p class="sub">Closed vs open per category · click a bar to filter</p>
      <div class="chart-box" id="bar-box"></div>
      <div class="legend">
        <div class="legend-item"><span class="legend-sw" style="background:var(--primary)"></span> Closed</div>
        <div class="legend-item"><span class="legend-sw" style="background:var(--amber)"></span> Open</div>
      </div>
    </div>
  </div>
  <div class="chips" id="chips"></div>
  <div class="filters">
    <div class="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="q" type="text" placeholder="Search findings by ID, title, file…" aria-label="Search findings">
      <span class="clear" id="clear-q" style="display:none" role="button" aria-label="Clear search">✕</span>
    </div>
    <div class="seg" id="seg-status"></div>
    <div class="seg" id="seg-sev"></div>
    <button class="reset" id="reset" style="display:none">✕ Reset</button>
  </div>
  <div class="table-wrap">
    <div class="table-head">
      <span class="cnt">Showing <b id="shown">__TOTAL__</b> of <span id="total">__TOTAL__</span> findings</span>
      <span class="mono" style="font-size:.625rem;color:var(--muted-fg)">audit.md · __RELEASE__</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr><th>ID</th><th>Sev</th><th class="file">Cat</th><th>Status</th><th>Finding</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>
  <div class="gen-note">
    Generated by <code>generate_audit_dash.py</code> from <code>audit/audit.md</code> + <code>audit/brief.md</code> on the <code>main</code> branch. Re-run to regenerate with the latest audit.
  </div>
</section>
</main>
<footer>
  <div class="wrap footer-grid">
    <div>
      <div class="footer-brand">
        <span class="brand-mark">⚛️</span>
        <div class="brand-text"><span class="footer-name">AgentKthx</span><span class="footer-sub">__BRAND_SUB__</span></div>
      </div>
      <p class="footer-p">A minimal, hackable, stdlib-only agentic framework for tool-calling AI agents. This audit dashboard is a static export generated from the live audit files.</p>
    </div>
    <div>
      <div class="footer-h">Project</div>
      <ul class="footer-ul">
        <li><a href="__REPO__" target="_blank" rel="noreferrer">↗ GitHub</a></li>
        <li><a href="__PYPI__" target="_blank" rel="noreferrer">↗ PyPI</a></li>
      </ul>
    </div>
    <div>
      <div class="footer-h">This page</div>
      <ul class="footer-ul">
        <li><a href="#audit">Audit dashboard</a></li>
      </ul>
    </div>
  </div>
  <div class="wrap footer-bottom">
    <div>© <span id="yr"></span> VTSTech · MIT · generated __GENTIME__</div>
    <div class="mono">audit baseline <span style="color:var(--primary)">__RELEASE__</span></div>
  </div>
</footer>
<button class="btt" id="btt" aria-label="Back to top">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m18 15-6-6-6 6"/></svg>
</button>
<script>
const FINDINGS = __FINDINGS__;
const CAT_META = {Security:{short:"SEC",color:"#f87171"},Robustness:{short:"ROB",color:"#fbbf24"},Maintainability:{short:"MAINT",color:"#a78bfa"},Performance:{short:"PERF",color:"#38bdf8"},"New Features":{short:"FEAT",color:"#34d399"},Architecture:{short:"ARCH",color:"#22d3ee"},Testing:{short:"TEST",color:"#e879f9"}};
const STATUS_META = {CLOSED:{label:"Closed",color:"var(--primary)"},OPEN:{label:"Open",color:"var(--amber)"},WONTFIX:{label:"Won't fix",color:"var(--zinc)"}};
const CATS = Object.keys(CAT_META);
const state = {q:"",cat:"All",status:"All",sev:"All"};
function computeStats(){const s={total:FINDINGS.length,byStatus:{CLOSED:0,OPEN:0,WONTFIX:0},bySev:{High:0,Medium:0,Low:0},byCat:{}};for(const f of FINDINGS){s.byStatus[f.status]++;s.bySev[f.severity]=(s.bySev[f.severity]||0)+1;s.byCat[f.category]=s.byCat[f.category]||{total:0,closed:0,open:0};s.byCat[f.category].total++;if(f.status==="CLOSED")s.byCat[f.category].closed++;else s.byCat[f.category].open++;}return s;}
const stats=computeStats();
document.getElementById("yr").textContent=new Date().getFullYear();
function statCard(l,v,h,c){return '<div class="stat '+c+'"><div class="stat-val">'+v+'</div><div class="stat-lbl">'+l+'</div><div class="stat-hint">'+h+'</div></div>';}
document.getElementById("stats").innerHTML=statCard("Findings",stats.total,"in this register","primary")+statCard("Closed",stats.byStatus.CLOSED,"tracked releases","emerald")+statCard("Open",stats.byStatus.OPEN,"active work items","amber")+statCard("Won't fix",stats.byStatus.WONTFIX,"intentional","zinc");
function renderPie(){const d=[["Closed",stats.byStatus.CLOSED,STATUS_META.CLOSED.color],["Open",stats.byStatus.OPEN,STATUS_META.OPEN.color],["Won't fix",stats.byStatus.WONTFIX,STATUS_META.WONTFIX.color]];const t=d.reduce((s,x)=>s+x[1],0);const cx=110,cy=110,ro=85,ri=55;let h='<svg viewBox="0 0 220 220" width="100%" height="100%" style="max-height:220px">';let a=-90;for(const[n,v,c] of d){if(!v)continue;const sw=(v/t)*360;const a1=a*Math.PI/180,a2=(a+sw)*Math.PI/180;const lg=sw>180?1:0;const x1o=cx+ro*Math.cos(a1),y1o=cy+ro*Math.sin(a1),x2o=cx+ro*Math.cos(a2),y2o=cy+ro*Math.sin(a2);const x1i=cx+ri*Math.cos(a2),y1i=cy+ri*Math.sin(a2),x2i=cx+ri*Math.cos(a1),y2i=cy+ri*Math.sin(a1);h+='<path d="M '+x1o+' '+y1o+' A '+ro+' '+ro+' 0 '+lg+' 1 '+x2o+' '+y2o+' L '+x1i+' '+y1i+' A '+ri+' '+ri+' 0 '+lg+' 0 '+x2i+' '+y2i+' Z" fill="'+c+'" stroke="none"/>';a+=sw;}h+='<text x="'+cx+'" y="'+(cy-5)+'" text-anchor="middle" fill="var(--fg)" font-size="28" font-weight="700">'+t+'</text>';h+='<text x="'+cx+'" y="'+(cy+18)+'" text-anchor="middle" fill="var(--muted-fg)" font-size="11">findings</text>';h+='</svg>';document.getElementById("pie-box").innerHTML=h;document.getElementById("pie-legend").innerHTML=d.map(x=>'<div class="legend-item"><span class="legend-sw" style="background:'+x[2]+'"></span>'+x[0]+' <b>'+x[1]+'</b></div>').join("");}
renderPie();
function renderBar(){const cd=CATS.map(c=>({name:c,short:CAT_META[c].short,total:stats.byCat[c]?.total||0,closed:stats.byCat[c]?.closed||0,open:stats.byCat[c]?.open||0})).sort((a,b)=>b.total-a.total);const mx=Math.max(...cd.map(d=>d.total));const bh=22,gp=8,lw=44,cw=300;const th=cd.length*(bh+gp)+10;let h='<svg viewBox="0 0 '+(lw+cw+40)+' '+th+'" width="100%" height="100%" style="max-height:220px" preserveAspectRatio="xMidYMid meet">';cd.forEach((d,i)=>{const y=i*(bh+gp)+4;const cw2=cw*(d.closed/mx);const ow=cw*(d.open/mx);h+='<text x="'+(lw-6)+'" y="'+(y+bh/2+3)+'" text-anchor="end" fill="var(--muted-fg)" font-size="10" font-family="monospace">'+d.short+'</text>';h+='<rect x="'+lw+'" y="'+y+'" width="'+cw2+'" height="'+bh+'" fill="var(--primary)" rx="0"/>';h+='<rect x="'+(lw+cw2)+'" y="'+y+'" width="'+ow+'" height="'+bh+'" fill="var(--amber)" rx="3" style="cursor:pointer" data-cat="'+d.name+'"/>';h+='<text x="'+(lw+cw2+ow+6)+'" y="'+(y+bh/2+3)+'" fill="var(--muted-fg)" font-size="10">'+d.total+'</text>';});h+='</svg>';document.getElementById("bar-box").innerHTML=h;document.querySelectorAll('#bar-box rect[data-cat]').forEach(r=>{r.addEventListener("click",()=>{state.cat=state.cat===r.dataset.cat?"All":r.dataset.cat;render();});});}
renderBar();
function renderChips(){let h='<button class="chip '+(state.cat==="All"?"active":"")+'" data-cat="All">All categories</button>';for(const c of CATS){const cnt=stats.byCat[c]?.total||0;h+='<button class="chip '+(state.cat===c?"active":"")+'" data-cat="'+c+'"><span style="color:'+CAT_META[c].color+'">●</span> '+c+' <span class="cnt">'+cnt+'</span></button>';}document.getElementById("chips").innerHTML=h;document.querySelectorAll("#chips .chip").forEach(b=>b.addEventListener("click",()=>{state.cat=state.cat===b.dataset.cat?"All":b.dataset.cat;render();}));}
function renderSeg(id,opts,field){let h="";for(const o of opts){h+='<button class="'+(state[field]===o.v?"active":"")+'" data-v="'+o.v+'">'+o.l+'</button>';}document.getElementById(id).innerHTML=h;document.querySelectorAll("#"+id+" button").forEach(b=>b.addEventListener("click",()=>{state[field]=b.dataset.v;render();}));}
function filtered(){const q=state.q.trim().toLowerCase();return FINDINGS.filter(f=>{if(state.cat!=="All"&&f.category!==state.cat)return false;if(state.status!=="All"&&f.status!==state.status)return false;if(state.sev!=="All"&&f.severity!==state.sev)return false;if(q){const hay=(f.id+" "+f.title+" "+f.detail+" "+(f.file||"")).toLowerCase();if(!hay.includes(q))return false;}return true;});}
const expanded=new Set();
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}
function render(){const list=filtered();document.getElementById("shown").textContent=list.length;document.getElementById("q").value=state.q;document.getElementById("clear-q").style.display=state.q?"inline-block":"none";const any=state.q||state.cat!=="All"||state.status!=="All"||state.sev!=="All";document.getElementById("reset").style.display=any?"inline-flex":"none";renderChips();renderSeg("seg-status",[{v:"All",l:"All"},{v:"OPEN",l:"Open"},{v:"CLOSED",l:"Closed"},{v:"WONTFIX",l:"Wontfix"}],"status");renderSeg("seg-sev",[{v:"All",l:"Any sev"},{v:"High",l:"High"},{v:"Medium",l:"Med"},{v:"Low",l:"Low"}],"sev");const tb=document.getElementById("rows");if(!list.length){tb.innerHTML='<tr><td colspan="5" class="empty">No findings match these filters.</td></tr>';return;}tb.innerHTML=list.map(f=>{const ex=expanded.has(f.id);const sl=f.closedIn||STATUS_META[f.status].label;return '<tr data-id="'+f.id+'"><td class="id">'+f.id+'</td><td><span class="badge sev-'+f.severity+'"><span class="d"></span>'+f.severity+'</span></td><td class="file"><span class="cat-short">'+CAT_META[f.category].short+'</span></td><td><span class="badge st-'+f.status+'">'+esc(sl)+'</span></td><td><div class="finding-title">'+esc(f.title)+'</div>'+(ex?'<div class="finding-detail expanded">'+esc(f.detail)+'</div>'+(f.file?'<div class="finding-file">'+esc(f.file)+'</div>':''):'<div class="finding-detail">'+esc(f.detail)+'</div>')+'</td></tr>';}).join("");document.querySelectorAll("#rows tr").forEach(tr=>tr.addEventListener("click",()=>{const id=tr.dataset.id;if(expanded.has(id))expanded.delete(id);else expanded.add(id);render();}));}
document.getElementById("q").addEventListener("input",e=>{state.q=e.target.value;render();});
document.getElementById("clear-q").addEventListener("click",()=>{state.q="";render();});
document.getElementById("reset").addEventListener("click",()=>{state.q="";state.cat="All";state.status="All";state.sev="All";render();});
render();
window.addEventListener("scroll",()=>{document.getElementById("nav").classList.toggle("scrolled",window.scrollY>8);document.getElementById("btt").classList.toggle("show",window.scrollY>600);},{passive:true});
document.getElementById("btt").addEventListener("click",()=>window.scrollTo({top:0,behavior:"smooth"}));
</script>
</body>
</html>'''

def generate_html(findings, meta):
    """Fill the HTML template with parsed data."""
    closed_count = sum(1 for f in findings if f["status"] == "CLOSED")
    html = HTML_TEMPLATE
    html = html.replace("__RELEASE__", meta["release"])
    html = html.replace("__VERSION__", meta["version"])
    html = html.replace("__BRAND_SUB__", f'{meta["release"]} · PyPI {meta["pypi"]}')
    html = html.replace("__TOTAL__", str(len(findings)))
    html = html.replace("__CLOSED__", str(closed_count))
    html = html.replace("__TESTS__", str(meta["tests"]))
    html = html.replace("__REPO__", meta["repo"])
    html = html.replace("__PYPI__", meta["pypiUrl"])
    html = html.replace("__GENTIME__", datetime.now().strftime("%Y-%m-%d %H:%M"))
    html = html.replace("__FINDINGS__", json.dumps(findings, ensure_ascii=False))
    return html

# ─── API endpoints (static JSON) ──────────────────────────────────────────
#
# The dashboard is a self-contained static site, so "API endpoints" are
# static JSON files written into <output-dir>/api/findings/ alongside
# index.html. They expose the same parsed register the HTML dashboard
# renders, so external clients (the preview panel, CI, curl) can consume
# the findings without scraping the page.
#
# Layout (relative to the dashboard output dir):
#   api/findings.json            all findings + totals
#   api/findings/open.json        open findings only
#   api/findings/closed.json      closed findings only
#   api/findings/wontfix.json     wontfix findings only
#   api/findings/summary.json     counts rollup (closures: [] — schema compat)
#   api/findings/reconcile.json   prose-vs-table drift check
#   api/findings/<ID>.json        per-finding detail (one per finding)
#
# Each list endpoint shares one envelope shape: callers can switch between
# /open, /closed, /wontfix, /all with a single client implementation.

def _endpoint_envelope(meta, findings, generated_at):
    """Common envelope for list endpoints — counts across the FULL register."""
    by_status = {"OPEN": 0, "CLOSED": 0, "WONTFIX": 0}
    by_severity = {}
    by_category = {}
    for f in findings:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        cat = f["category"]
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "meta": meta,
        "generatedAt": generated_at,
        "total": len(findings),
        "open": by_status.get("OPEN", 0),
        "closed": by_status.get("CLOSED", 0),
        "wontfix": by_status.get("WONTFIX", 0),
        "high": by_severity.get("High", 0),
        "medium": by_severity.get("Medium", 0),
        "low": by_severity.get("Low", 0),
        "byStatus": by_status,
        "bySeverity": by_severity,
        "byCategory": by_category,
    }


def _summary_payload(meta, findings, generated_at):
    """Counts-only rollup with per-category / per-severity breakdowns.

    ``closures`` stays in the payload as an empty list for schema
    compatibility with existing clients — the Closure Timeline is retired
    and the Findings Summary + Detailed Findings are the historical record.
    """
    by_category = {}
    by_severity = {}
    by_status = {"OPEN": 0, "CLOSED": 0, "WONTFIX": 0}
    for f in findings:
        st = f["status"]
        by_status[st] = by_status.get(st, 0) + 1
        cat = f["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "open": 0, "closed": 0, "wontfix": 0}
        by_category[cat]["total"] += 1
        by_category[cat][st.lower()] += 1
        sev = f["severity"]
        if sev not in by_severity:
            by_severity[sev] = {"total": 0, "open": 0, "closed": 0, "wontfix": 0}
        by_severity[sev]["total"] += 1
        by_severity[sev][st.lower()] += 1
    closed = by_status.get("CLOSED", 0)
    return {
        "meta": meta,
        "generatedAt": generated_at,
        "totals": {
            "total": len(findings),
            "open": by_status.get("OPEN", 0),
            "closed": closed,
            "wontfix": by_status.get("WONTFIX", 0),
            "closureRate": round(closed / len(findings) * 100) if findings else 0,
        },
        "byCategory": by_category,
        "bySeverity": by_severity,
        "byStatus": by_status,
        "closures": [],
    }


def _reconcile_payload(meta, findings, audit_md, generated_at, deltas_md=""):
    """Compare the audit.md header prose counts vs the parsed table counts.

    Flags drift so a stale hand-written prose summary can't silently mislead.
    The table is always the source of truth — it's what this very script
    parses to render the dashboard.

    When deltas.md is present (the split is active), audit.md contains only
    OPEN findings. The prose comparison is then against audit.md-only counts
    (open), and the archived counts (closed/wontfix from deltas.md) are
    reported separately. The ``matches`` flag is True if audit.md's prose
    open count matches its parsed open count — the archived counts aren't
    expected to appear in audit.md's prose (they're in deltas.md).
    """
    header = audit_md[:4096]
    open_m = re.search(r"(\d+)\s+OPEN\b", header)
    closed_m = re.search(r"(\d+)\s+CLOSED\b", header)
    wontfix_m = re.search(r"(\d+)\s+WONTFIX\b", header)
    total_m = re.search(r"(\d+)\s+Findings\b", header)

    # Parse audit.md alone (open-only when split) for the audit-only table.
    audit_only_findings = _parse_findings_from_md(audit_md)
    audit_only_open = sum(1 for f in audit_only_findings.values() if f["status"] == "OPEN")

    # The merged table (audit.md + deltas.md) = the full register.
    table = {
        "closed": sum(1 for f in findings if f["status"] == "CLOSED"),
        "open": sum(1 for f in findings if f["status"] == "OPEN"),
        "wontfix": sum(1 for f in findings if f["status"] == "WONTFIX"),
        "total": len(findings),
    }
    audit_only_table = {
        "open": audit_only_open,
        "archivedInDeltas": table["closed"] + table["wontfix"],
    }

    if deltas_md:
        # Split mode: audit.md prose should mention only OPEN count.
        # The archived (closed/wontfix) counts live in deltas.md.
        prose_open = int(open_m.group(1)) if open_m else None
        matches = (prose_open is None or prose_open == audit_only_open)
        prose = {
            "open": prose_open,
            "closed": int(closed_m.group(1)) if closed_m else None,  # shouldn't be in audit.md post-split
            "wontfix": int(wontfix_m.group(1)) if wontfix_m else None,
            "total": int(total_m.group(1)) if total_m else None,
            "raw": {
                "open": open_m.group(0) if open_m else None,
                "closed": closed_m.group(0) if closed_m else None,
                "wontfix": wontfix_m.group(0) if wontfix_m else None,
                "total": total_m.group(0) if total_m else None,
            },
        }
        drift = {
            "open": (None if prose_open is None else prose_open - audit_only_open),
            "note": "split mode — audit.md prose open vs audit.md-only open",
        }
        note = (
            "Split mode: audit.md open count matches. "
            f"({table['closed']} closed + {table['wontfix']} wontfix archived in deltas.md.)"
            if matches
            else f"Split mode DRIFT: audit.md prose says {prose_open} open but audit.md table has {audit_only_open} open. Update the prose."
        )
    else:
        # Pre-split mode: audit.md has all findings, prose should match full table.
        prose = {
            "closed": int(closed_m.group(1)) if closed_m else None,
            "open": int(open_m.group(1)) if open_m else None,
            "wontfix": int(wontfix_m.group(1)) if wontfix_m else None,
            "total": int(total_m.group(1)) if total_m else None,
            "raw": {
                "closed": closed_m.group(0) if closed_m else None,
                "open": open_m.group(0) if open_m else None,
                "wontfix": wontfix_m.group(0) if wontfix_m else None,
                "total": total_m.group(0) if total_m else None,
            },
        }
        drift = {
            k: (None if prose[k] is None else prose[k] - table[k])
            for k in ("closed", "open", "wontfix", "total")
        }
        matches = all(
            (prose[k] is None or prose[k] == table[k])
            for k in ("closed", "open", "wontfix", "total")
        )
        note = (
            "Prose header matches the parsed table."
            if matches
            else "Prose header has drifted from the parsed table — the table is the source of truth (it's what generate_audit_dash.py reads). Update the prose."
        )

    closed_findings = sorted(
        [{"id": f["id"], "closedIn": f["closedIn"]} for f in findings if f["status"] == "CLOSED"],
        key=lambda x: x["closedIn"] or "",
    )
    return {
        "meta": meta,
        "generatedAt": generated_at,
        "prose": prose,
        "table": table,
        "auditOnly": audit_only_table,
        "drift": drift,
        "matches": matches,
        "closedFindings": closed_findings,
        "note": note,
    }


def write_endpoints(out_dir, meta, findings, audit_md, generated_at, deltas_md=""):
    """Write static JSON endpoint files alongside index.html.

    Returns the list of file paths written.
    """
    api_dir = os.path.join(out_dir, "api", "findings")
    os.makedirs(api_dir, exist_ok=True)

    written = []

    # list endpoints — open / closed / wontfix / all
    for status, slug in [
        ("OPEN", "open"),
        ("CLOSED", "closed"),
        ("WONTFIX", "wontfix"),
        (None, "findings"),  # all
    ]:
        subset = findings if status is None else [f for f in findings if f["status"] == status]
        payload = _endpoint_envelope(meta, findings, generated_at)
        payload["filtered"] = len(subset)
        payload["count"] = len(subset)
        payload["status"] = slug
        payload["findings"] = subset
        path = os.path.join(api_dir, f"{slug}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        written.append(path)

    # summary
    path = os.path.join(api_dir, "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_summary_payload(meta, findings, generated_at), f, ensure_ascii=False, indent=2)
    written.append(path)

    # reconcile
    path = os.path.join(api_dir, "reconcile.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_reconcile_payload(meta, findings, audit_md, generated_at, deltas_md), f, ensure_ascii=False, indent=2)
    written.append(path)

    # per-finding detail files (siblings = same category, for navigation)
    for f in findings:
        siblings = [
            {"id": g["id"], "title": g["title"], "status": g["status"]}
            for g in findings
            if g["category"] == f["category"] and g["id"] != f["id"]
        ]
        payload = {
            "meta": meta,
            "generatedAt": generated_at,
            "finding": f,
            "siblings": siblings,
        }
        path = os.path.join(api_dir, f"{f['id']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        written.append(path)

    return written

# ─── main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Generate a self-contained AgentKthx audit dashboard HTML from audit.md + brief.md.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 generate_audit_dash.py                          # fetch from GitHub main, write ./audit-dash/index.html
  python3 generate_audit_dash.py -o dash.html             # custom output path
  python3 generate_audit_dash.py --audit a.md --brief b.md  # use local files
  python3 generate_audit_dash.py --base-url https://raw.githubusercontent.com/VTSTech/AgentKthx/main/audit
  python3 generate_audit_dash.py --no-endpoints           # HTML only, skip the JSON API files
        """.strip(),
    )
    ap.add_argument("-o", "--output", default="audit-dash/index.html",
                    help="output HTML path (default: audit-dash/index.html)")
    ap.add_argument("--audit", help="local audit.md path (overrides --base-url)")
    ap.add_argument("--brief", help="local brief.md path (overrides --base-url)")
    ap.add_argument("--deltas", help="local deltas.md path (closed/wontfix archive; defaults to alongside audit.md)")
    ap.add_argument("--base-url", default=DEFAULT_BASE,
                    help=f"base URL for audit.md + brief.md (default: {DEFAULT_BASE})")
    ap.add_argument("--no-endpoints", action="store_true",
                    help="skip writing the JSON API endpoint files (default: write them)")
    args = ap.parse_args()

    audit_md, brief_md, deltas_md = get_audit_brief(args)

    meta = parse_meta(audit_md, brief_md)
    print(f"[gen] parsed metadata: {meta['release']} · v{meta['version']} · "
          f"{meta['tests']} tests · {meta['date']}", file=sys.stderr)

    # Parse OPEN findings from audit.md + CLOSED/WONTFIX from deltas.md.
    # If deltas.md is absent (pre-split audit.md), parse_findings handles
    # the fallback (all findings from audit.md alone).
    findings = parse_findings(audit_md, deltas_md)
    deltas_count = sum(1 for f in findings if f["status"] != "OPEN") if deltas_md else 0
    audit_count = sum(1 for f in findings if f["status"] == "OPEN")
    if deltas_md:
        print(f"[gen] parsed {len(findings)} findings: {audit_count} open (audit.md) + {deltas_count} archived (deltas.md)", file=sys.stderr)
    else:
        print(f"[gen] parsed {len(findings)} findings (no deltas.md — all from audit.md)", file=sys.stderr)

    closures = parse_closures(audit_md, findings)
    # Also parse closure sections from deltas.md (archived audits may carry them)
    if deltas_md:
        deltas_closures = parse_closures(deltas_md, findings)
        # Merge: dedupe by release (audit.md wins if overlap)
        seen = {c["release"] for c in closures}
        for c in deltas_closures:
            if c["release"] not in seen:
                closures.append(c)
                seen.add(c["release"])
    print(f"[gen] parsed {len(closures)} closure sections: "
          f"{', '.join(c['release'] for c in closures)}", file=sys.stderr)

    html = generate_html(findings, closures, meta)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(args.output)
    print(f"[gen] wrote {args.output} ({size:,} bytes, {len(findings)} findings)", file=sys.stderr)

    # JSON API endpoints — static files written into <out_dir>/api/findings/
    # so the dashboard host serves them at /audit-dash/api/findings/...
    if not args.no_endpoints:
        ep_dir = out_dir if out_dir else "."
        generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        written = write_endpoints(ep_dir, meta, findings, closures, audit_md, generated_at, deltas_md)
        api_root = os.path.join(ep_dir, "api", "findings")
        print(f"[gen] wrote {len(written)} endpoint JSON files into {api_root}/ "
              f"(findings.json, open.json, closed.json, wontfix.json, summary.json, "
              f"reconcile.json, +{len(findings)} per-finding files)", file=sys.stderr)
    else:
        print("[gen] --no-endpoints: skipped JSON API files", file=sys.stderr)

if __name__ == "__main__":
    main()
