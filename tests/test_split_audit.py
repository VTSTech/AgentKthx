"""
Regression tests for ``audit/split-audit.py`` (v2 — the surgical split).

The v1 script regenerated audit.md + deltas.md from parsed finding dicts,
which destroyed hand-curated content: ``File(s)`` rows, multi-paragraph
closure/WONTFIX rationale prose, per-release status text like
"⊘ WONTFIX (R07.12, owner decision)", New Findings tables, and the
audit.md header's release annotation and delta blockquotes. v2 performs
line-level surgery instead. These tests pin the v2 contract:

- summary rows and ``#### ID:`` detail sections move VERBATIM (a
  ``**Status:**`` line is inserted when the open-format section lacks one)
- ``## Rxx.xx New Findings`` tables move to deltas.md when their first
  member archives (generate_audit_dash.py parses them as OPEN, so an
  archived member left behind double-counts and breaks reconcile)
- ``> **Rxx.xx delta (...):**`` header blockquotes move verbatim into
  deltas.md's ``## Release Delta Log`` (release order, deduped, multi-line
  blockquote runs kept together) — including on delta-only runs where the
  findings register is already open-only
- mechanical counts update in both headers; everything else is preserved
  byte-for-byte (including the trailing-newline count)
- exit codes: 0 split, 1 error (duplicate IDs / missing file), 2 no-op
- idempotence: re-running on split files changes nothing

The R07.12 rehearsal (reconstructing the pre-split register from git and
comparing against the hand-split release) lives outside the suite — see
``scripts/`` in the dev environment.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO_ROOT, "audit", "split-audit.py")

_spec = importlib.util.spec_from_file_location("split_audit", SCRIPT)
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


# ─── fixtures ───────────────────────────────────────────────────────────────


def _detail(heading, sev, cat, files, prose, impact, status=None):
    lines = [f"#### {heading}", "", "| Property | Value |", "|----------|-------|",
             f"| **Severity** | {sev} |", f"| **Category** | {cat} |"]
    if files:
        lines.append(f"| **File(s)** | `{files}` |")
    lines += ["", prose, ""]
    if status:
        lines += [f"**Status:** {status}", ""]
    lines += [f"**Impact:** {impact}", "", "---", ""]
    return "\n".join(lines)


def make_audit_md(with_deltas=False):
    """A pre-split audit.md: 4 findings, 2 about to archive (SEC-02 closed,
    ROB-01 wontfix), 1 archiving without a detail section (SEC-03), 1 open
    (SEC-01). Carries an R07.13 New Findings table and a Closures section.
    with_deltas=True adds per-release delta blockquotes to the header
    (newest first, one of them a multi-line blockquote run) — the format
    that accumulated in the real audit.md through R07.12."""
    delta_lines = ([
        "> **R07.13 delta (unit-test fixture release):** DELTA-PROSE-R07.13 "
        "first line with **bold** and `code` — must move verbatim.",
        "> continued delta prose for R07.13 (multi-line blockquote run).",
        "",
        "> **R07.12 delta (feature release):** DELTA-PROSE-R07.12 single-line note.",
        "",
    ] if with_deltas else [])
    return "\n".join([
        "# Improvement & Enhancement Audit",
        "",
        "**AgentKthx v0.7.13 (R07.13 — unit-test fixture)**",
        "",
        "**Repository:** https://github.com/VTSTech/AgentKthx  ",
        "**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-28  ",
        "**Commit:** `abc1234` | **Test Suite:** 42 passed / 0 skipped  ",
        "4 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  ",
        "Severity: 0 High | 3 Medium | 1 Low  ",
        "4 OPEN (CLOSED + WONTFIX archived in deltas.md — generate_audit_dash.py merges both for the dashboard)",
        "",
        *delta_lines,
        "> **Split:** 2 CLOSED/WONTFIX findings moved to `deltas.md`. "
        "`generate_audit_dash.py` reads both `audit.md` (open) and `deltas.md` "
        "(closed/wontfix) and merges them into the full register. "
        "The dashboard shows all 6 findings (4 open + 2 closed/wontfix).",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        "PROSE-MARKER-EXEC-SUMMARY which must survive the split byte-for-byte.",
        "",
        "---",
        "",
        "## Findings Summary",
        "",
        "| ID | Severity | Category | Status | Title |",
        "|----|----------|----------|--------|-------|",
        "| SEC-01 | Medium | Security | OPEN | open sec one |",
        "| SEC-02 | Medium | Security | ✓ CLOSED R07.13 | closed sec two |",
        "| SEC-03 | High | Security | ✓ CLOSED R07.13 | closed sec three no detail |",
        "| ROB-01 | Low | Robustness | ⊘ WONTFIX (R07.13, owner decision) | wontfix rob one |",
        "",
        "---",
        "",
        "## R07.13 New Findings",
        "",
        "| ID | Severity | Category | File(s) | Title |",
        "|----|----------|----------|---------|-------|",
        "| SEC-02 | Medium | Security | `agentkthx/core/x.py` | closed sec two |",
        "| FEAT-09 | Low | New Features | `agentkthx/tools/y.py` | still open feat nine |",
        "",
        "---",
        "",
        "## Detailed Findings",
        "",
        "### Security",
        "",
        _detail("SEC-01: open sec one", "Medium", "Security", "agentkthx/core/a.py",
                "PROSE-SEC-01 open finding body.", "impact one"),
        _detail("SEC-02: closed sec two", "Medium", "Security", "agentkthx/core/x.py",
                "PROSE-SEC-02 closure body.", "impact two"),
        "",
        "### Robustness",
        "",
        _detail("ROB-01: wontfix rob one", "Low", "Robustness", "agentkthx/core/r.py",
                "PROSE-ROB-01 wontfix body.", "impact rob"),
        "",
        "---",
        "",
        "## Priority Matrix",
        "",
        "PROSE-MARKER-PRIORITY-MATRIX must survive too.",
        "",
    ]) + "\n"


def make_deltas_md(with_sec02=False):
    """A pre-split deltas.md: 2 archived findings (SEC-05 High, ROB-02 Low)
    with one closure-timeline section."""
    counts = "3 CLOSED · 1 WONTFIX · 4 total" if with_sec02 else \
        "2 CLOSED · 0 WONTFIX · 2 total"
    sec02_detail = _detail("SEC-02: closed sec two", "Medium", "Security",
                           "agentkthx/core/x.py", "PROSE-SEC-02 closure body.",
                           "impact two") if with_sec02 else ""
    sec02_row = ("| SEC-02 | Medium | Security | ✓ CLOSED R07.13 | closed sec two |"
                 if with_sec02 else "")
    return "\n".join([
        "# Audit Deltas — Closed & Wontfix Archive",
        "",
        "**Project:** AgentKthx  ",
        "**Release:** R07.08  ",
        "**Date:** 2026-09-27  ",
        "**Archived:** 2026-09-27 17:59 UTC+0  ",
        f"**Counts:** {counts}",
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
        "| SEC-05 | **High** | Security | ✓ CLOSED R07.06 | archived sec five |",
        "| ROB-02 | Low | Robustness | ✓ CLOSED R07.07 | archived rob two |",
        sec02_row,
        "",
        "---",
        "",
        "## Detailed Findings (Archived)",
        "",
        "<!-- Closed + WONTFIX detail sections. -->",
        "",
        "### Security",
        "",
        _detail("SEC-05: archived sec five", "High", "Security", "agentkthx/core/old.py",
                "old closure body.", "old impact", status="✓ CLOSED R07.06"),
        sec02_detail,
        "### Robustness",
        "",
        _detail("ROB-02: archived rob two", "Low", "Robustness", "agentkthx/core/old2.py",
                "old rob body.", "old rob impact", status="✓ CLOSED R07.07"),
        "",
        "---",
        "",
        "## Closure Timeline",
        "",
        "<!-- The dashboard's closure-timeline cards parse these sections. -->",
        "",
        "## R07.07 Closures (Old Batch)",
        "",
        "| ID | Severity | Status | Notes |",
        "|----|----------|--------|-------|",
        "| ~~ROB-02~~ | Low | ✓ CLOSED R07.07 | old notes |",
        "",
    ]) + "\n"


def _sha(path):
    with open(path, encoding="utf-8") as f:
        return hashlib.sha256(f.read().encode()).hexdigest()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def files(tmp_path):
    audit = tmp_path / "audit.md"
    deltas = tmp_path / "deltas.md"
    audit.write_text(make_audit_md(), encoding="utf-8")
    deltas.write_text(make_deltas_md(), encoding="utf-8")
    return audit, deltas


# ─── no-op / dry-run / guards ───────────────────────────────────────────────


class TestNoopAndGuards:

    def test_noop_when_already_split(self, tmp_path):
        audit = tmp_path / "audit.md"
        deltas = tmp_path / "deltas.md"
        audit.write_text(make_audit_md().replace(
            "| SEC-02 | Medium | Security | ✓ CLOSED R07.13 |",
            "| SEC-02 | Medium | Security | OPEN |").replace(
            "| SEC-03 | High | Security | ✓ CLOSED R07.13 |",
            "| SEC-03 | High | Security | OPEN |").replace(
            "| ROB-01 | Low | Robustness | ⊘ WONTFIX (R07.13, owner decision) |",
            "| ROB-01 | Low | Robustness | OPEN |"), encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        before = (_sha(audit), _sha(deltas))
        rc = sa.split(str(audit), str(deltas))
        assert rc == 2
        assert (_sha(audit), _sha(deltas)) == before

    def test_dry_run_writes_nothing(self, files, capsys):
        audit, deltas = files
        before = (_sha(audit), _sha(deltas))
        rc = sa.split(str(audit), str(deltas), dry_run=True)
        assert rc == 0
        assert (_sha(audit), _sha(deltas)) == before
        err = capsys.readouterr().err
        assert "DRY RUN" in err and "3 new" in err

    def test_duplicate_id_refused(self, tmp_path):
        audit = tmp_path / "audit.md"
        deltas = tmp_path / "deltas.md"
        audit.write_text(make_audit_md(), encoding="utf-8")
        deltas.write_text(make_deltas_md(with_sec02=True), encoding="utf-8")
        before = (_sha(audit), _sha(deltas))
        rc = sa.split(str(audit), str(deltas))
        assert rc == 1
        assert (_sha(audit), _sha(deltas)) == before

    def test_missing_audit_file(self, tmp_path):
        rc = sa.split(str(tmp_path / "nope.md"), str(tmp_path / "deltas.md"))
        assert rc == 1

    def test_audit_without_summary_table(self, tmp_path):
        audit = tmp_path / "audit.md"
        audit.write_text("# no table here\n", encoding="utf-8")
        rc = sa.split(str(audit), str(tmp_path / "deltas.md"))
        assert rc == 1


# ─── row + section movement ─────────────────────────────────────────────────


class TestMovement:

    def test_rows_move_verbatim(self, files):
        audit, deltas = files
        assert sa.split(str(audit), str(deltas)) == 0
        d, a = _read(deltas), _read(audit)
        assert "| SEC-02 | Medium | Security | ✓ CLOSED R07.13 | closed sec two |" in d
        assert "| SEC-03 | High | Security | ✓ CLOSED R07.13 | closed sec three no detail |" in d
        assert ("| ROB-01 | Low | Robustness | ⊘ WONTFIX (R07.13, owner decision) |"
                " wontfix rob one |") in d
        for row in ("SEC-02", "SEC-03", "ROB-01"):
            assert f"| {row} |" not in a.split("## Detailed Findings")[0]
        assert "| SEC-01 | Medium | Security | OPEN | open sec one |" in a

    def test_row_insertion_positions(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        section = _read(deltas).split("## Findings Summary (Archived)")[1]
        table = section.split("## Detailed Findings")[0]
        order = [ln.split("|")[1].strip() for ln in table.splitlines()
                 if ln.startswith("| SEC") or ln.startswith("| ROB")]
        # canonical order: High first (SEC-03 < SEC-05 by ID), then Medium
        # Security inserted before the Low Robustness block, ROB-01 < ROB-02
        assert order == ["SEC-03", "SEC-05", "SEC-02", "ROB-01", "ROB-02"]

    def test_detail_sections_move_verbatim_with_status(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        d = _read(deltas)
        # SEC-02 moved with File(s) row + inserted Status + verbatim prose
        sec02 = d[d.index("#### SEC-02:"):d.index("### Robustness")]
        assert "| **File(s)** | `agentkthx/core/x.py` |" in sec02
        assert "**Status:** ✓ CLOSED R07.13" in sec02
        assert "PROSE-SEC-02 closure body." in sec02
        assert "**Impact:** impact two" in sec02
        # WONTFIX status text carried verbatim — the v1 flattening bug
        rob = d[d.index("#### ROB-01:"):]
        assert "**Status:** ⊘ WONTFIX (R07.13, owner decision)" in rob
        assert "PROSE-ROB-01 wontfix body." in rob

    def test_missing_detail_section_warns(self, files, capsys):
        audit, deltas = files
        rc = sa.split(str(audit), str(deltas))
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARN" in err and "SEC-03" in err
        # row still moved
        assert "SEC-03" in _read(deltas).split("## Detailed Findings")[0]

    def test_open_section_and_prose_preserved(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        a = _read(audit)
        assert "PROSE-MARKER-EXEC-SUMMARY which must survive the split byte-for-byte." in a
        assert "PROSE-MARKER-PRIORITY-MATRIX must survive too." in a
        assert "#### SEC-01: open sec one" in a
        assert "PROSE-SEC-01 open finding body." in a
        assert "R07.13 — unit-test fixture" in a  # header annotation untouched
        # archived detail sections gone from audit.md
        assert "#### SEC-02:" not in a and "#### ROB-01:" not in a

    def test_emptied_category_heading_removed(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        a = _read(audit)
        assert "### Security" in a          # SEC-01 still open
        assert "### Robustness" not in a    # ROB-01 was its only member


# ─── New Findings tables ────────────────────────────────────────────────────


class TestNewFindingsTables:

    def test_table_moves_when_member_archives(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        a, d = _read(audit), _read(deltas)
        assert "## R07.13 New Findings" not in a
        assert "## R07.13 New Findings" in d
        # still-open member rides along, row intact
        assert ("| FEAT-09 | Low | New Features | `agentkthx/tools/y.py` |"
                " still open feat nine |") in d
        # placed between the detail groups and EOF (after the legacy
        # Closure Timeline in this fixture — the Timeline is retired and
        # New Findings tables anchor before the Release Delta Log when
        # that section exists, else append at EOF)
        assert (d.index("#### ROB-01:") < d.index("## R07.13 New Findings"))
        assert (d.index("## Closure Timeline") < d.index("## R07.13 New Findings"))

    def test_table_stays_when_no_member_archives(self, tmp_path):
        audit = tmp_path / "audit.md"
        deltas = tmp_path / "deltas.md"
        content = make_audit_md().replace(
            "| SEC-02 | Medium | Security | `agentkthx/core/x.py` | closed sec two |",
            "| FEAT-08 | Low | New Features | `agentkthx/tools/z.py` | also open |")
        audit.write_text(content, encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        sa.split(str(audit), str(deltas))
        assert "## R07.13 New Findings" in _read(audit)
        assert "## R07.13 New Findings" not in _read(deltas)

    def test_table_anchors_before_release_delta_log(self, tmp_path):
        # the retired Closure Timeline no longer anchors anything: when the
        # Release Delta Log exists, New Findings tables land before it
        audit = tmp_path / "audit.md"
        deltas = tmp_path / "deltas.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        sa.split(str(audit), str(deltas))
        d = _read(deltas)
        nf = d.index("## R07.13 New Findings")
        log = d.index("## Release Delta Log")
        assert nf < log
        assert "## R07.13 Closures" not in d


# ─── header updates ─────────────────────────────────────────────────────────


class TestHeaderUpdates:

    def test_audit_header_counts(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        a = _read(audit)
        assert "1 Open Findings | 7 Categories" in a
        assert "Severity: 0 High | 1 Medium | 0 Low" in a
        assert "1 OPEN (CLOSED + WONTFIX archived" in a
        assert "> **Split:** 5 CLOSED/WONTFIX findings moved" in a
        assert "all 6 findings (1 open + 5 closed/wontfix)" in a

    def test_deltas_header_counts(self, files):
        audit, deltas = files
        sa.split(str(audit), str(deltas))
        d = _read(deltas)
        assert "**Counts:** 4 CLOSED · 1 WONTFIX · 5 total" in d
        assert "closure batch)" in d and "R07.13" in d.split("**Counts:**")[0]
        assert "**Release:** R07.13" in d


# ─── from-scratch deltas + closure sections + CLI ──────────────────────────


class TestFromScratchAndClosures:

    def test_creates_deltas_when_absent(self, tmp_path):
        audit = tmp_path / "audit.md"
        audit.write_text(make_audit_md(), encoding="utf-8")
        deltas = tmp_path / "audit" / "deltas.md"
        assert sa.split(str(audit), str(deltas)) == 0
        assert deltas.exists()
        d = _read(deltas)
        assert "# Audit Deltas — Closed & Wontfix Archive" in d
        assert "**Counts:** 2 CLOSED · 1 WONTFIX · 3 total" in d
        assert "## Findings Summary (Archived)" in d
        assert "#### SEC-02:" in d and "#### ROB-01:" in d
        assert "**Status:** ⊘ WONTFIX (R07.13, owner decision)" in d

    def test_closure_section_dropped_from_audit(self, tmp_path, capsys):
        # the Closure Timeline is retired: Closures sections found in
        # audit.md are deleted at split time, NOT moved to deltas.md
        audit = tmp_path / "audit.md"
        deltas = tmp_path / "deltas.md"
        content = make_audit_md().replace(
            "## Priority Matrix",
            "## R07.13 Closures (Unit Test)\n\n| ID | Severity | Status | Notes |\n"
            "|----|----------|--------|-------|\n"
            "| ~~SEC-02~~ | Medium | ✓ CLOSED R07.13 | notes prose |\n\n"
            "## Priority Matrix")
        audit.write_text(content, encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        sa.split(str(audit), str(deltas))
        a, d = _read(audit), _read(deltas)
        assert "## R07.13 Closures (Unit Test)" not in a
        assert "## R07.13 Closures (Unit Test)" not in d
        assert "notes prose" not in d
        err = capsys.readouterr().err
        assert "Closures sections DROPPED" in err and "R07.13" in err

    def test_idempotent_second_run(self, files):
        audit, deltas = files
        assert sa.split(str(audit), str(deltas)) == 0
        before = (_sha(audit), _sha(deltas))
        assert sa.split(str(audit), str(deltas)) == 2
        assert (_sha(audit), _sha(deltas)) == before

    def test_cli_exit_codes(self, files):
        audit, deltas = files
        run = lambda *extra: subprocess.run(
            [sys.executable, SCRIPT, "--audit", str(audit), "--deltas", str(deltas),
             *extra], capture_output=True, text=True)
        assert run().returncode == 0
        assert run().returncode == 2                      # already split
        assert run("--dry-run").returncode == 2           # still nothing to do
        assert subprocess.run(
            [sys.executable, SCRIPT, "--audit", str(audit.parent / "gone.md")],
            capture_output=True).returncode == 1

    def test_trailing_newline_count_preserved(self, files):
        # regression: the write path used to append an unconditional extra
        # "\n" on top of the roundtrip join, doubling the file ending
        audit, deltas = files
        ends = lambda p: len(_read(p)) - len(_read(p).rstrip("\n"))
        before = (ends(audit), ends(deltas))
        assert sa.split(str(audit), str(deltas)) == 0
        assert (ends(audit), ends(deltas)) == before


# ─── delta blockquote migration ─────────────────────────────────────────────


class TestDeltaBlockMigration:

    def test_delta_blocks_move_verbatim(self, tmp_path):
        audit, deltas = tmp_path / "audit.md", tmp_path / "deltas.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        assert sa.split(str(audit), str(deltas)) == 0
        a, d = _read(audit), _read(deltas)
        # gone from audit.md — no delta blockquote line remains anywhere
        assert not [ln for ln in a.splitlines() if sa.DELTA_BLOCK_RE.match(ln)]
        assert "DELTA-PROSE-R07.12" not in a and "DELTA-PROSE-R07.13" not in a
        assert "PROSE-MARKER-EXEC-SUMMARY" in a      # rest of the file intact
        assert "> **Split:** 5 CLOSED/WONTFIX" in a   # counts refreshed too
        # deltas.md: Release Delta Log, release order, blocks byte-verbatim
        log = d[d.index("## Release Delta Log"):]
        assert "DELTA-PROSE-R07.12 single-line note." in log
        assert ("> **R07.13 delta (unit-test fixture release):** "
                "DELTA-PROSE-R07.13 first line with **bold** and `code` — "
                "must move verbatim.") in log
        assert "> continued delta prose for R07.13 (multi-line blockquote run)." in log
        assert log.index("R07.12 delta") < log.index("R07.13 delta")
        # the findings split still happened in the same run
        assert "| SEC-02 | Medium | Security | ✓ CLOSED R07.13 |" in d

    def test_delta_only_run_when_findings_open(self, tmp_path, capsys):
        # the user's actual scenario: register already open-only, a page of
        # delta blockquotes still in audit.md → must NOT be a no-op exit
        audit, deltas = tmp_path / "audit.md", tmp_path / "deltas.md"
        content = make_audit_md(with_deltas=True).replace(
            "| SEC-02 | Medium | Security | ✓ CLOSED R07.13 |",
            "| SEC-02 | Medium | Security | OPEN |").replace(
            "| SEC-03 | High | Security | ✓ CLOSED R07.13 |",
            "| SEC-03 | High | Security | OPEN |").replace(
            "| ROB-01 | Low | Robustness | ⊘ WONTFIX (R07.13, owner decision) |",
            "| ROB-01 | Low | Robustness | OPEN |").replace(
            "> **Split:** 2 CLOSED/WONTFIX findings moved",
            "> **Split:** 1 CLOSED/WONTFIX findings moved").replace(
            "The dashboard shows all 6 findings (4 open + 2 closed/wontfix).",
            "The dashboard shows all 5 findings (4 open + 1 closed/wontfix).")
        audit.write_text(content, encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        assert sa.split(str(audit), str(deltas)) == 0
        assert "delta blockquotes moving" in capsys.readouterr().err
        a, d = _read(audit), _read(deltas)
        # deltas moved, findings table untouched
        assert "DELTA-PROSE-R07.12" not in a and "DELTA-PROSE-R07.13" not in a
        for row_id in ("SEC-01", "SEC-02", "SEC-03", "ROB-01"):
            assert f"| {row_id} |" in a
        # stale Split line refreshed from the real register state
        assert "> **Split:** 2 CLOSED/WONTFIX findings moved" in a
        assert "The dashboard shows all 6 findings (4 open + 2 closed/wontfix)." in a
        # deltas.md: log section added; table + counts untouched
        log = d[d.index("## Release Delta Log"):]
        assert "DELTA-PROSE-R07.12" in log and "DELTA-PROSE-R07.13" in log
        assert "**Counts:** 2 CLOSED · 0 WONTFIX · 2 total" in d

    def test_delta_log_dedupes_existing_release(self, tmp_path):
        # deltas.md already logs R07.12 → audit.md's R07.12 block is skipped
        audit, deltas = tmp_path / "audit.md", tmp_path / "deltas.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas.write_text(make_deltas_md() + "## Release Delta Log\n\n"
                          "> **R07.12 delta (feature release):** "
                          "EXISTING-R07.12 hand-curated note.\n", encoding="utf-8")
        sa.split(str(audit), str(deltas))
        a, d = _read(audit), _read(deltas)
        assert "DELTA-PROSE-R07.12" not in d               # deduped, not duplicated
        log = d[d.index("## Release Delta Log"):]
        assert "EXISTING-R07.12 hand-curated note." in log
        assert "DELTA-PROSE-R07.13" in log                 # the new one appended
        assert log.index("EXISTING-R07.12") < log.index("DELTA-PROSE-R07.13")
        assert "DELTA-PROSE-R07.12" not in a and "DELTA-PROSE-R07.13" not in a

    def test_delta_log_inserts_before_newer_existing_block(self, tmp_path):
        audit, deltas = tmp_path / "audit.md", tmp_path / "deltas.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas.write_text(make_deltas_md() + "## Release Delta Log\n\n"
                          "> **R07.14 delta (future release):** "
                          "EXISTING-R07.14 note.\n", encoding="utf-8")
        sa.split(str(audit), str(deltas))
        log = _read(deltas)[_read(deltas).index("## Release Delta Log"):]
        assert (log.index("DELTA-PROSE-R07.12")
                < log.index("DELTA-PROSE-R07.13")
                < log.index("EXISTING-R07.14"))

    def test_idempotent_after_delta_move(self, tmp_path):
        audit, deltas = tmp_path / "audit.md", tmp_path / "deltas.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas.write_text(make_deltas_md(), encoding="utf-8")
        assert sa.split(str(audit), str(deltas)) == 0
        before = (_sha(audit), _sha(deltas))
        assert sa.split(str(audit), str(deltas)) == 2
        assert (_sha(audit), _sha(deltas)) == before

    def test_from_scratch_deltas_includes_delta_log(self, tmp_path):
        audit = tmp_path / "audit.md"
        audit.write_text(make_audit_md(with_deltas=True), encoding="utf-8")
        deltas = tmp_path / "audit" / "deltas.md"
        assert sa.split(str(audit), str(deltas)) == 0
        d = _read(deltas)
        assert "## Release Delta Log" in d
        assert "DELTA-PROSE-R07.12" in d and "DELTA-PROSE-R07.13" in d
        assert "**Counts:** 2 CLOSED · 1 WONTFIX · 3 total" in d
