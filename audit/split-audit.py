#!/usr/bin/env python3
"""
split-audit.py — Archive CLOSED/WONTFIX findings from audit.md into deltas.md.

The audit.md grows over time as findings accumulate. Closed and wontfix
findings are historical — they don't need to be in the active audit.md that
the agent reads during re-audit. This script moves them to deltas.md,
keeping audit.md focused on OPEN findings (the active work items).

generate_audit_dash.py reads BOTH files and merges them into the full
register for the dashboard. The dashboard, JSON endpoints, and reconcile
checker all see the complete picture (open + closed + wontfix); audit.md
itself stays small and focused.

v2 — SURGICAL split. The earlier version regenerated both files from parsed
finding dicts, which flattened hand-curated content (File(s) rows, WONTFIX
rationale prose, per-release status text like "(R07.12, owner decision)",
New Findings tables, header delta blockquotes). v2 performs line-level
surgery instead:

  MOVES (verbatim, byte-for-byte):
    - Findings Summary table rows for every CLOSED/WONTFIX finding
    - `#### ID:` detail sections — with a `**Status:**` line inserted when
      the open-format section lacks one (status text taken verbatim from
      the summary-table cell, e.g. "✓ CLOSED R07.12" / "⊘ WONTFIX (R07.12,
      owner decision)"). Findings closed without a detail section in
      audit.md move as rows only — the closure prose is authored directly
      in deltas.md afterwards (warning printed).
    - `## Rxx.xx New Findings` tables whose first member just archived
      (established convention — generate_audit_dash.py parses those tables
      as OPEN, so an archived member left in audit.md double-counts and
      breaks reconcile). Still-open members ride along with the table.
    - `## Rxx.xx Closures` sections when present in audit.md (rare; they
      normally land in deltas.md directly at release time)
  UPDATES (mechanical counts only):
    - audit.md: "N Open Findings", "Severity: X High | Y Medium | Z Low",
      "N OPEN (CLOSED + WONTFIX archived …)", and the "> **Split:**" line
    - deltas.md: "**Counts:**", "**Archived:**", "**Release:**" header lines
  DOES NOT TOUCH (regenerate manually after a split):
    - Executive Summary, Rxx.xx delta blockquotes, Priority Matrix
    - closure-timeline prose / "## Rxx.xx Closures" sections in deltas.md
    - brief.md, CHANGELOG.md, version files

Insertion positions are canonical: summary rows land before the first
existing deltas.md row that sorts after them (severity → category → ID);
detail sections append at the end of their `### Category` group.

Usage:
    python3 audit/split-audit.py                          # split audit/audit.md
    python3 audit/split-audit.py --dry-run               # preview, writes nothing
    python3 audit/split-audit.py --audit path/to/audit.md

Idempotent: re-running on an already-split audit.md is a no-op.

Exit codes:
    0  split completed successfully (or --dry-run previewed)
    1  bad invocation (audit.md missing/unparseable, duplicate IDs)
    2  no-op (nothing to move — already split, or no findings)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from collections import Counter
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "generate_audit_dash", os.path.join(_HERE, "generate_audit_dash.py")
)
gad = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gad)

SEV_RANK = {"High": 0, "Medium": 1, "Low": 2}
CAT_RANK = {c: i for i, c in enumerate(gad.CATS)}

ROW_RE = re.compile(
    r"^\|\s*(`?~~)?([A-Z]+-\d+)(~~`?)?\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|?\s*$"
)
DETAIL_HEADING_RE = re.compile(r"^####\s+([A-Z]+-\d+):\s*(.*)$")
HEADING_RE = re.compile(r"^#{1,4}\s")
NEW_FINDINGS_HEADING_RE = re.compile(r"^##\s+(R[\d.]+)\s+New Findings\s*$")
CLOSURES_HEADING_RE = re.compile(r"^##\s+(R[\d.]+)\s+Closures")
RELEASE_RE = re.compile(r"R(\d+)\.(\d+)")

SPLIT_LINE = (
    "> **Split:** {archived} CLOSED/WONTFIX findings moved to `deltas.md`. "
    "`generate_audit_dash.py` reads both `audit.md` (open) and `deltas.md` "
    "(closed/wontfix) and merges them into the full register. "
    "The dashboard shows all {total} findings ({open} open + {archived} closed/wontfix)."
)


# ─── small helpers ──────────────────────────────────────────────────────────


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path, content):
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def parse_id(fid):
    m = re.match(r"^([A-Z]+)-(\d+)$", fid)
    if not m:
        return (fid, 0)
    return (m.group(1), int(m.group(2)))


def row_sort_key(severity, category, fid):
    return (SEV_RANK.get(severity, 9), CAT_RANK.get(category, 9), parse_id(fid))


def version_tuple(release):
    m = RELEASE_RE.match(release)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


class FileEdit:
    """Line-level surgery on a list of lines.

    All indices refer to the ORIGINAL line list. Edits are collected and
    rendered in one pass: every original line not explicitly dropped or
    replaced is emitted byte-for-byte, so untouched regions are preserved
    exactly.
    """

    def __init__(self, lines):
        self.lines = lines
        self.drops = set()
        self.replaces = {}
        self.before = {}   # idx -> [lines to insert before idx]
        self.after = {}    # idx -> [lines to insert after idx]

    def drop(self, a, b):
        """Drop original line indices [a, b] inclusive."""
        for i in range(a, b + 1):
            self.drops.add(i)

    def replace(self, i, text):
        self.replaces[i] = text

    def insert_before(self, i, new_lines):
        self.before.setdefault(i, []).extend(new_lines)

    def insert_after(self, i, new_lines):
        self.after.setdefault(i, []).extend(new_lines)

    def render(self):
        out = []
        n = len(self.lines)
        for i in range(n + 1):
            out.extend(self.before.get(i, []))
            if i == n:
                break
            if i in self.drops:
                continue
            out.append(self.replaces.get(i, self.lines[i]))
            out.extend(self.after.get(i, []))
        # after-inserts anchored at the (nonexistent) end position
        out.extend(self.after.get(n, []))
        return out


# ─── parsing: summary table ────────────────────────────────────────────────


class SummaryRow:
    __slots__ = ("id", "severity", "category", "status", "status_text",
                 "closed_in", "line_idx", "raw")

    def __init__(self, fid, severity, category, status, status_text,
                 closed_in, line_idx, raw):
        self.id = fid
        self.severity = severity
        self.category = category
        self.status = status
        self.status_text = status_text
        self.closed_in = closed_in
        self.line_idx = line_idx
        self.raw = raw


def parse_summary_table(lines):
    """Parse the '## Findings Summary' table (matches the '(Archived)'
    variant used by deltas.md).

    Returns (rows, table) where rows preserve file order and table holds
    header_idx / sep_idx / last_row_idx (None when absent).
    """
    rows = []
    table = {"header_idx": None, "sep_idx": None, "last_row_idx": None}
    in_summary = False
    for i, line in enumerate(lines):
        if line.startswith("## ") and "## Findings Summary" in line:
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            in_summary = False
            continue
        if not in_summary:
            continue
        if line.startswith("| ID"):
            table["header_idx"] = i
            continue
        if line.startswith("|---"):
            table["sep_idx"] = i
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        _, fid, _, sev, cat, status_cell, title = m.groups()
        sev = gad.strip_md(sev)
        cat = gad.strip_md(cat)
        if cat not in gad.CATS:
            continue
        status, closed_in = gad.parse_status_cell(status_cell)
        rows.append(SummaryRow(fid, sev, cat, status, status_cell.strip(),
                               closed_in, i, line))
        table["last_row_idx"] = i
    return rows, table


# ─── parsing: detail sections + category groups ────────────────────────────


class DetailSection:
    __slots__ = ("id", "heading_idx", "sep_idx", "end_idx")

    def __init__(self, fid, heading_idx, sep_idx, end_idx):
        self.id = fid
        self.heading_idx = heading_idx
        self.sep_idx = sep_idx      # trailing '---' separator (or None)
        self.end_idx = end_idx      # last non-blank content line


def scan_detail_sections(lines):
    """Map {id: DetailSection} for every '#### ID:' block in the file.

    A section spans from its heading to the next ####/###/## heading; the
    trailing '---' separator inside that span belongs to the section.
    """
    sections = {}
    n = len(lines)
    i = 0
    while i < n:
        m = DETAIL_HEADING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fid = m.group(1)
        j = i + 1
        while j < n and not HEADING_RE.match(lines[j]):
            j += 1
        last_content = j - 1
        while last_content > i and lines[last_content].strip() == "":
            last_content -= 1
        sep = None
        for k in range(j - 1, i, -1):
            if lines[k].strip() == "---":
                sep = k
                break
        sections[fid] = DetailSection(fid, i, sep, last_content)
        i = j
    return sections


def detailed_findings_span(lines):
    """(start, end) line indices of the '## Detailed Findings' section
    (matches the '(Archived)' variant). end is the index of the next '## '
    heading, or len(lines)."""
    for i, line in enumerate(lines):
        if line.startswith("## Detailed Findings"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            return i, j
    return None, None


def scan_category_groups(lines):
    """Map {category: {heading_idx, sections: [ids], last_section_idx,
    next_heading_idx}} for '### Category' groups inside Detailed Findings."""
    groups = {}
    start, end = detailed_findings_span(lines)
    if start is None:
        return groups
    cur_cat = None
    for i in range(start, end):
        line = lines[i]
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            cur_cat = m.group(1)
            g = groups.setdefault(
                cur_cat, {"heading_idx": i, "sections": [], "last_section_idx": None}
            )
            g["heading_idx"] = i
            continue
        dm = DETAIL_HEADING_RE.match(line)
        if dm and cur_cat is not None:
            g = groups[cur_cat]
            g["sections"].append(dm.group(1))
            g["last_section_idx"] = i
    # next_heading_idx: the heading that follows the group's last section
    n = len(lines)
    for cat, g in groups.items():
        if g["last_section_idx"] is None:
            g["next_heading_idx"] = g["heading_idx"] + 1
            continue
        j = g["last_section_idx"] + 1
        while j < n and not HEADING_RE.match(lines[j]):
            j += 1
        g["next_heading_idx"] = min(j, n)
    return groups


# ─── parsing: New Findings + Closures sections ─────────────────────────────


def _span_to_next_h2(lines, heading_idx):
    j = heading_idx + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    return j


def scan_new_findings_sections(lines):
    """List of {'release', 'heading_idx', 'span_end', 'content_end',
    'sep_idx', 'member_ids'} for '## Rxx.xx New Findings' sections."""
    out = []
    for i, line in enumerate(lines):
        m = NEW_FINDINGS_HEADING_RE.match(line)
        if not m:
            continue
        span_end = _span_to_next_h2(lines, i)
        content_end = span_end - 1
        while content_end > i and lines[content_end].strip() == "":
            content_end -= 1
        sep = None
        for k in range(span_end - 1, i, -1):
            if lines[k].strip() == "---":
                sep = k
                break
        member_ids = []
        for k in range(i + 1, span_end):
            rm = ROW_RE.match(lines[k])
            if rm:
                member_ids.append(rm.group(2))
        out.append({
            "release": m.group(1), "heading_idx": i, "span_end": span_end,
            "content_end": content_end, "sep_idx": sep,
            "member_ids": member_ids,
        })
    return out


def scan_closures_sections(lines):
    """List of {'release', 'heading_idx', 'content_end'} for '## Rxx.xx
    Closures' sections (content_end = last non-blank line before the next
    '## ' heading)."""
    out = []
    for i, line in enumerate(lines):
        m = CLOSURES_HEADING_RE.match(line)
        if not m:
            continue
        span_end = _span_to_next_h2(lines, i)
        content_end = span_end - 1
        while content_end > i and lines[content_end].strip() == "":
            content_end -= 1
        out.append({"release": m.group(1), "heading_idx": i,
                    "content_end": content_end})
    return out


def find_heading_idx(lines, pattern):
    for i, line in enumerate(lines):
        if re.match(pattern, line):
            return i
    return None


def last_non_blank_idx(lines):
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() != "":
            return i
    return 0


# ─── payload builders ──────────────────────────────────────────────────────


def insert_status_line(block, status_text):
    """Insert a '**Status:** …' line into a moved detail section that lacks
    one — placed right after the Property table (or after the heading when
    there is no table), matching the archive's section format."""
    if any(l.strip().startswith("**Status:**") for l in block):
        return block
    i = 1
    while i < len(block) and block[i].strip() == "":
        i += 1
    insert_at = 1
    if i < len(block) and block[i].startswith("|"):
        j = i
        while j < len(block) and block[j].startswith("|"):
            j += 1
        insert_at = j
    if insert_at < len(block) and block[insert_at].strip() == "":
        insert_at += 1
    return block[:insert_at] + [f"**Status:** {status_text}", ""] + block[insert_at:]


def section_block(lines, sec, row):
    """Verbatim detail-section block for a moved finding (+ '---' terminator
    when the section lacks one, + Status line when missing)."""
    end = sec.sep_idx if sec.sep_idx is not None else sec.end_idx
    block = list(lines[sec.heading_idx:end + 1])
    if sec.sep_idx is None:
        block.append("---")
    return insert_status_line(block, row.status_text)


def nf_table_block(lines, nf):
    """Verbatim '## Rxx.xx New Findings' section block + clean separator."""
    content_end = nf["content_end"]
    # exclude the trailing '---' separator from the moved content — it is
    # re-emitted as part of the block terminator below
    end = content_end
    if nf["sep_idx"] is not None and nf["sep_idx"] == content_end:
        end = content_end - 1
        while end > nf["heading_idx"] and lines[end].strip() == "":
            end -= 1
    block = list(lines[nf["heading_idx"]:end + 1])
    return block + ["", "---", ""]


def closure_block(lines, cs):
    return list(lines[cs["heading_idx"]:cs["content_end"] + 1]) + ["", "---", ""]


# ─── meta (for the from-scratch deltas.md case) ────────────────────────────


def extract_meta(audit_md):
    """Extract release, date, project, version, commit — mirrors parse_meta."""
    meta = {"project": "Unknown", "release": "R??", "version": "0.0.0",
            "date": "", "commit": "", "tests": 0, "testsSkipped": 0,
            "author": "", "license": "", "repo": ""}
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
    m = re.search(r"\*\*Author:\*\*\s*(\S+).*?\|\s*\*\*License:\*\*\s*(\S+)", audit_md)
    if m:
        meta["author"] = m.group(1)
        meta["license"] = m.group(2)
    m = re.search(r"\*\*Repository:\*\*\s*(\S+)", audit_md)
    if m:
        meta["repo"] = m.group(1)
    return meta


def derive_batch_release(rows):
    """The release the archived rows were closed in — the latest Rxx.xx
    appearing in their status cells (e.g. '✓ CLOSED R07.12' or
    '⊘ WONTFIX (R07.12, owner decision)'). None when unparseable."""
    rels = []
    for r in rows:
        m = re.search(r"R[\d.]+", r.status_text)
        if m:
            rels.append(m.group(0))
    if not rels:
        return None
    return max(rels, key=version_tuple)


# ─── header count updates ──────────────────────────────────────────────────


def apply_audit_header_updates(edit, open_rows, total_archived, total_findings):
    """Rewrite audit.md's mechanical count lines (first match wins)."""
    n_open = len(open_rows)
    sev = Counter(r.severity for r in open_rows)
    h, m, l = sev.get("High", 0), sev.get("Medium", 0), sev.get("Low", 0)
    updated = set()
    for i, line in enumerate(edit.lines):
        if "open_n" not in updated and re.match(r"^\d+\s+Open Findings\s*\|", line):
            edit.replace(i, re.sub(r"^\d+", str(n_open), line, count=1))
            updated.add("open_n")
        elif "sev" not in updated and re.match(r"^Severity:\s*\d+\s+High\s*\|", line):
            mm = re.match(
                r"^(Severity:\s*)\d+(\s+High\s*\|\s*)\d+(\s+Medium\s*\|\s*)\d+(\s+Low.*)$",
                line)
            if mm:
                edit.replace(i, f"{mm.group(1)}{h}{mm.group(2)}{m}"
                                 f"{mm.group(3)}{l}{mm.group(4)}")
            updated.add("sev")
        elif "open_cap" not in updated and re.match(r"^\d+\s+OPEN\s+\(CLOSED", line):
            edit.replace(i, re.sub(r"^\d+", str(n_open), line, count=1))
            updated.add("open_cap")
        elif "split" not in updated and line.startswith("> **Split:**"):
            edit.replace(i, SPLIT_LINE.format(
                archived=total_archived, total=total_findings, open=n_open))
            updated.add("split")
    return updated


def apply_deltas_header_updates(edit, closed, wontfix, total, batch, today):
    updated = set()
    for i, line in enumerate(edit.lines):
        if "counts" not in updated and re.match(r"^\*\*Counts:\*\*", line):
            edit.replace(i, f"**Counts:** {closed} CLOSED · {wontfix} WONTFIX · {total} total")
            updated.add("counts")
        elif batch and "archived" not in updated and re.match(r"^\*\*Archived:\*\*", line):
            edit.replace(i, f"**Archived:** {today} ({batch} closure batch)")
            updated.add("archived")
        elif batch and "release" not in updated and re.match(r"^\*\*Release:\*\*", line):
            edit.replace(i, f"**Release:** {batch}")
            updated.add("release")
    return updated


# ─── deltas.md row insertion ───────────────────────────────────────────────


def plan_row_insertions(edit, existing_rows, new_rows, table):
    """Insert moved rows into the deltas.md summary table.

    Each row lands before the first existing row that sorts after it
    (severity → category → ID); rows sorting after every existing row
    append at the end of the table. Existing rows are never reordered.
    """
    if not new_rows:
        return []
    ordered = sorted(new_rows, key=lambda r: row_sort_key(r.severity, r.category, r.id))
    cur = [(row_sort_key(r.severity, r.category, r.id), r.line_idx) for r in existing_rows]
    placed = []
    for r in ordered:
        k = row_sort_key(r.severity, r.category, r.id)
        target = None
        for pos, (ek, e_line) in enumerate(cur):
            if ek > k:
                target = e_line
                cur.insert(pos, (k, None))
                break
        if target is None:
            cur.append((k, None))
        placed.append((target, r))
    anchor_end = (existing_rows[-1].line_idx if existing_rows
                  else (table["sep_idx"] if table["sep_idx"] is not None
                        else table["header_idx"]))
    for target, r in placed:
        if target is None:
            edit.insert_after(anchor_end, [r.raw])
        else:
            edit.insert_before(target, [r.raw])
    return placed


# ─── from-scratch deltas.md ────────────────────────────────────────────────


def build_deltas_from_scratch(meta, moved_rows, blocks_by_cat, nf_blocks,
                              closure_blocks, batch, today):
    closed = sum(1 for r in moved_rows if r.status == "CLOSED")
    wontfix = sum(1 for r in moved_rows if r.status == "WONTFIX")
    lines = [
        "# Audit Deltas — Closed & Wontfix Archive",
        "",
        f"**Project:** {meta['project']}  ",
        f"**Release:** {batch or meta['release']}  ",
        f"**Date:** {meta['date']}  ",
        f"**Archived:** {today}" + (f" ({batch} closure batch)" if batch else ""),
        f"**Counts:** {closed} CLOSED · {wontfix} WONTFIX · {len(moved_rows)} total",
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
        "| ID | Severity | Category | Status | Title |",
        "|----|----------|----------|--------|-------|",
    ]
    for r in sorted(moved_rows, key=lambda r: row_sort_key(r.severity, r.category, r.id)):
        lines.append(r.raw)
    lines += ["", "---", "", "## Detailed Findings (Archived)", "",
              "<!-- Closed + WONTFIX detail sections. -->", ""]
    for cat in sorted(blocks_by_cat, key=lambda c: CAT_RANK.get(c, 9)):
        lines.append(f"### {cat}")
        lines.append("")
        for block in blocks_by_cat[cat]:
            lines.extend(block)
            lines.append("")
    if closure_blocks:
        lines += ["## Closure Timeline", "",
                  "<!-- The dashboard's closure-timeline cards parse these sections. -->", ""]
        for block in closure_blocks:
            lines.extend(block)
            lines.append("")
    if nf_blocks:
        for block in nf_blocks:
            lines.extend(block)
            lines.append("")
    return "\n".join(lines) + "\n"


# ─── main split ────────────────────────────────────────────────────────────


def _log(msg):
    print(f"[split] {msg}", file=sys.stderr)


def split(audit_path, deltas_path, dry_run=False):
    if not os.path.exists(audit_path):
        _log(f"ERROR: {audit_path} not found")
        return 1
    audit_content = read_file(audit_path)
    if not audit_content.strip():
        _log(f"ERROR: {audit_path} is empty")
        return 1
    audit_lines = audit_content.split("\n")

    audit_rows, audit_table = parse_summary_table(audit_lines)
    if audit_table["header_idx"] is None:
        _log("ERROR: no '## Findings Summary' table found in audit.md")
        return 1

    archived_rows = [r for r in audit_rows if r.status != "OPEN"]
    open_rows = [r for r in audit_rows if r.status == "OPEN"]
    if not archived_rows:
        _log("no CLOSED/WONTFIX findings to move — audit.md is already open-only")
        return 2

    deltas_exists = os.path.exists(deltas_path)
    deltas_content = read_file(deltas_path) if deltas_exists else ""
    deltas_lines = deltas_content.split("\n") if deltas_exists else []

    existing_rows = []
    deltas_table = {"header_idx": None, "sep_idx": None, "last_row_idx": None}
    if deltas_exists:
        existing_rows, deltas_table = parse_summary_table(deltas_lines)
        if deltas_table["header_idx"] is None:
            _log(f"ERROR: {deltas_path} exists but has no '## Findings Summary' table")
            return 1
        existing_ids = {r.id for r in existing_rows}
        dup = sorted(r.id for r in archived_rows if r.id in existing_ids)
        if dup:
            _log(f"ERROR: already archived in deltas.md (stale audit.md rows?): "
                 f"{', '.join(dup)}")
            return 1

    # detail sections present in audit.md
    audit_details = scan_detail_sections(audit_lines)
    moved_sections = []
    missing_details = []
    for r in archived_rows:
        sec = audit_details.get(r.id)
        if sec is not None:
            moved_sections.append((r, sec))
        else:
            missing_details.append(r.id)

    # New Findings tables whose first member just archived
    archived_ids = {r.id for r in archived_rows}
    nf_sections = scan_new_findings_sections(audit_lines)
    nf_to_move = [s for s in nf_sections if archived_ids & set(s["member_ids"])]

    # Closures sections still in audit.md (rare)
    closure_secs = scan_closures_sections(audit_lines)

    # counts
    moved_closed = sum(1 for r in archived_rows if r.status == "CLOSED")
    moved_wontfix = sum(1 for r in archived_rows if r.status == "WONTFIX")
    existing_closed = sum(1 for r in existing_rows if r.status == "CLOSED")
    existing_wontfix = sum(1 for r in existing_rows if r.status == "WONTFIX")
    total_closed = existing_closed + moved_closed
    total_wontfix = existing_wontfix + moved_wontfix
    total_archived = len(existing_rows) + len(archived_rows)
    open_after = len(open_rows)
    total_findings = open_after + total_archived
    batch = derive_batch_release(archived_rows)
    today = datetime.now().strftime("%Y-%m-%d")

    # ── plan report (shared by dry-run and real run) ──
    _log(f"audit.md: {len(audit_rows)} findings → {open_after} OPEN "
         f"({len(archived_rows)} archived this run)")
    _log(f"  CLOSED : {', '.join(r.id for r in archived_rows if r.status == 'CLOSED')}")
    _log(f"  WONTFIX: {', '.join(r.id for r in archived_rows if r.status == 'WONTFIX')}")
    if moved_sections:
        _log(f"  detail sections moved verbatim: "
             f"{', '.join(r.id for r, _ in moved_sections)}")
    if missing_details:
        _log(f"  WARN no detail section in audit.md (author the archived section "
             f"in deltas.md): {', '.join(missing_details)}")
    if nf_to_move:
        _log(f"  New Findings tables moving to deltas.md: "
             f"{', '.join(s['release'] for s in nf_to_move)}")
    if closure_secs:
        _log(f"  Closures sections moving to deltas.md: "
             f"{', '.join(s['release'] for s in closure_secs)}")
    _log(f"deltas.md: {len(existing_rows)} existing + {len(archived_rows)} new = "
         f"{total_archived} archived ({total_closed} CLOSED · {total_wontfix} WONTFIX)")
    if batch:
        _log(f"  deltas.md header → Release/Archived: {batch} ({today})")
    _log(f"audit.md header → {open_after} Open Findings · "
         f"{Counter(r.severity for r in open_rows).get('High', 0)}H/"
         f"{Counter(r.severity for r in open_rows).get('Medium', 0)}M/"
         f"{Counter(r.severity for r in open_rows).get('Low', 0)}L · "
         f"Split line: {total_findings} = {open_after} open + {total_archived} archived")

    if dry_run:
        _log("DRY RUN — no files written. Manual follow-ups after a real split:")
        _log("  - author closure/WONTFIX detail prose for the sections flagged above")
        _log("  - regenerate the Executive Summary + Rxx.xx delta blockquote + "
             "Priority Matrix markers")
        _log("  - add the '## Rxx.xx Closures' timeline section in deltas.md "
             "(drives the dashboard closure card)")
        _log("  - refresh brief.md / CHANGELOG at release time")
        return 0

    # ── audit.md surgery ──
    ae = FileEdit(audit_lines)
    for r in archived_rows:
        ae.drop(r.line_idx, r.line_idx)
    for row, sec in moved_sections:
        end = sec.sep_idx if sec.sep_idx is not None else sec.end_idx
        ae.drop(sec.heading_idx, end)
        if (sec.sep_idx is not None and end + 1 < len(audit_lines)
                and audit_lines[end + 1].strip() == ""):
            ae.drop(end + 1, end + 1)
    audit_groups = scan_category_groups(audit_lines)
    for cat, g in audit_groups.items():
        if g["sections"] and all(fid in archived_ids for fid in g["sections"]):
            ae.drop(g["heading_idx"], g["heading_idx"])
            if (g["heading_idx"] + 1 < len(audit_lines)
                    and audit_lines[g["heading_idx"] + 1].strip() == ""):
                ae.drop(g["heading_idx"] + 1, g["heading_idx"] + 1)
    for s in nf_to_move:
        end = s["sep_idx"] if s["sep_idx"] is not None else s["content_end"]
        ae.drop(s["heading_idx"], end)
        if (s["sep_idx"] is not None and end + 1 < len(audit_lines)
                and audit_lines[end + 1].strip() == ""):
            ae.drop(end + 1, end + 1)
    for s in closure_secs:
        ae.drop(s["heading_idx"], s["content_end"])
        if (s["content_end"] + 1 < len(audit_lines)
                and audit_lines[s["content_end"] + 1].strip() == ""):
            ae.drop(s["content_end"] + 1, s["content_end"] + 1)
    apply_audit_header_updates(ae, open_rows, total_archived, total_findings)

    # ── deltas.md build ──
    blocks_by_cat = {}
    for row, sec in moved_sections:
        blocks_by_cat.setdefault(row.category, []).append((row, sec))
    for cat in blocks_by_cat:
        blocks_by_cat[cat] = [
            section_block(audit_lines, sec, row)
            for row, sec in sorted(blocks_by_cat[cat], key=lambda rs: parse_id(rs[0].id))
        ]
    nf_blocks = [nf_table_block(audit_lines, s)
                 for s in sorted(nf_to_move, key=lambda s: version_tuple(s["release"]))]
    closure_blocks = [closure_block(audit_lines, s) for s in closure_secs]

    if not deltas_exists:
        meta = extract_meta(audit_content)
        deltas_out = build_deltas_from_scratch(
            meta, archived_rows, blocks_by_cat, nf_blocks, closure_blocks,
            batch, today)
    else:
        de = FileEdit(deltas_lines)
        plan_row_insertions(de, existing_rows, archived_rows, deltas_table)
        deltas_groups = scan_category_groups(deltas_lines)
        for cat in sorted(blocks_by_cat, key=lambda c: CAT_RANK.get(c, 9)):
            payload = []
            for block in blocks_by_cat[cat]:
                payload.extend(block)
                payload.append("")
            if cat in deltas_groups:
                g = deltas_groups[cat]
                if g["sections"]:
                    de.insert_before(g["next_heading_idx"], payload)
                else:
                    # empty group heading already present — fill in under it
                    nxt = (deltas_lines[g["heading_idx"] + 1]
                           if g["heading_idx"] + 1 < len(deltas_lines) else "")
                    de.insert_after(g["heading_idx"],
                                    payload if nxt.strip() == "" else [""] + payload)
            else:
                anchor = _new_group_anchor(deltas_groups, cat, deltas_lines)
                de.insert_before(anchor, [f"### {cat}", ""] + payload)
        if nf_blocks:
            anchor = find_heading_idx(deltas_lines, r"^##\s+Closure Timeline")
            payload = []
            for block in nf_blocks:
                payload.extend(block)
            if anchor is not None:
                de.insert_before(anchor, payload)
            else:
                de.insert_after(last_non_blank_idx(deltas_lines), payload)
        if closure_blocks:
            payload = []
            if find_heading_idx(deltas_lines, r"^##\s+Closure Timeline") is None:
                payload += ["## Closure Timeline", "",
                            "<!-- The dashboard's closure-timeline cards parse "
                            "these sections. -->", ""]
            for block in closure_blocks:
                payload.extend(block)
            de.insert_after(last_non_blank_idx(deltas_lines), payload)
        apply_deltas_header_updates(de, total_closed, total_wontfix,
                                    total_archived, batch, today)
        deltas_out = "\n".join(de.render())
        if deltas_content.endswith("\n"):
            deltas_out += "\n"

    audit_out = "\n".join(ae.render())
    if audit_content.endswith("\n"):
        audit_out += "\n"

    write_file(audit_path, audit_out)
    write_file(deltas_path, deltas_out)
    _log(f"wrote {audit_path} ({open_after} open) + {deltas_path} "
         f"({total_archived} archived)")
    _log("MANUAL FOLLOW-UP (not done by this tool):")
    _log("  - author closure/WONTFIX detail prose for the sections flagged above")
    _log("  - regenerate the Executive Summary + Rxx.xx delta blockquote + "
         "Priority Matrix markers")
    _log("  - add the '## Rxx.xx Closures' timeline section in deltas.md "
         "(drives the dashboard closure card)")
    _log("  - refresh brief.md / CHANGELOG at release time")
    return 0


def _new_group_anchor(deltas_groups, cat, lines):
    """Line index where a new '### Category' group should be inserted: before
    the first existing group whose category sorts after it, else at the end
    of the Detailed Findings section."""
    for gcat, g in deltas_groups.items():
        if CAT_RANK.get(gcat, 9) > CAT_RANK.get(cat, 9):
            return g["heading_idx"]
    _, end = detailed_findings_span(lines)
    return end if end is not None else len(lines)


def main():
    ap = argparse.ArgumentParser(
        description="Split audit.md into open findings (audit.md) + "
                    "closed/wontfix (deltas.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 audit/split-audit.py                          # split audit/audit.md
  python3 audit/split-audit.py --dry-run               # preview, writes nothing
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
