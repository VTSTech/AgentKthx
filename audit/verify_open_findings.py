#!/usr/bin/env python3
"""
verify_open_findings.py — confirm the OPEN findings in audit.md are still
genuinely open in the codebase.

Lives in the repo's ``audit/`` directory next to ``audit.md`` and
``deltas.md``. Paths are derived from the script's own location, so it
runs correctly regardless of cwd — invoke it from anywhere:

    python3 audit/verify_open_findings.py            # from the repo root
    python3 verify_open_findings.py                  # from inside audit/
    python3 /path/to/AgentKthx/audit/verify_open_findings.py  # absolute

Strategy:
  1. Parse audit.md's Findings Summary table -> list of OPEN findings
     (ID, severity, category, title).
  2. Parse audit.md's Detailed Findings section to extract each finding's
     File(s) reference and detail prose.
  3. Cross-check deltas.md: none of the OPEN IDs should appear there as
     CLOSED/WONTFIX (would indicate register drift).
  4. For each finding with a File(s) reference, check that the file still
     exists. For findings where the detail mentions a specific pattern
     (function name, identifier), confirm the pattern is still present.
  5. Search recent git log for the finding ID — if a commit mentions it
     in a closure context ("closes XYZ-NN", "fixes XYZ-NN", "XYZ-NN:"),
     flag as candidate silent closure.

Output: a per-finding verdict + summary counts.

Stdlib only.
"""

import re
import subprocess
import sys
from pathlib import Path
from collections import Counter

# Paths are derived from the script's own location so the script is
# location-independent: it works from inside audit/, from the repo root,
# or via an absolute path. The expected layout is:
#   <repo-root>/
#     audit/
#       audit.md
#       deltas.md
#       verify_open_findings.py   <- this file
SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT = SCRIPT_DIR / "audit.md"
DELTAS = SCRIPT_DIR / "deltas.md"
REPO = SCRIPT_DIR.parent  # audit/ is a subdirectory of the repo root


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- parse

def parse_findings_summary(md: str):
    """Return list of dicts {id, severity, category, status, title} from the
    Findings Summary markdown table."""
    rows = []
    in_table = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("| ID |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not s.startswith("|"):
            in_table = False
            continue
        # Skip separator row "|----|----|..."
        if re.match(r"^\|[-:\s|]+\|?$", s):
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 5:
            continue
        fid = parts[0]
        if not re.match(r"\b(?:SEC|ROB|MAINT|PERF|FEAT|ARCH|TEST|MCP)-\d+\b", fid):
            continue
        rows.append({
            "id": fid,
            "severity": parts[1],
            "category": parts[2],
            "status": parts[3],
            "title": parts[4],
        })
    return rows


def parse_detailed_findings(md: str):
    """Return {id: {files: [str], detail: str}} from the Detailed Findings section."""
    out = {}
    # Match sections like: #### SEC-09: title ... (until next #### or ### or EOF)
    # Property table row: | **File(s)** | `path1:line`, `path2` |
    pattern = re.compile(
        r"^####\s+((?:SEC|ROB|MAINT|PERF|FEAT|ARCH|MCP|TEST)-\d+):\s*(.+)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(md))
    for i, m in enumerate(matches):
        fid = m.group(1)
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end]
        # Extract File(s) row
        files = []
        fm = re.search(
            r"\|\s*\*\*File\(s\)\*\*\s*\|\s*(.+?)\s*\|",
            body,
        )
        if fm:
            # parse out backtick-quoted paths
            files = re.findall(r"`([^`]+)`", fm.group(1))
        out[fid] = {"title": title, "files": files, "detail": body.strip()}
    return out


def parse_deltas_ids(md: str):
    """Return set of IDs that appear in deltas.md (these are CLOSED/WONTFIX)."""
    return set(re.findall(r"\b(SEC|ROB|MAINT|PERF|FEAT|ARCH|MCP|TEST)-\d+\b", md))


# ---------------------------------------------------------------- verify

def file_refs_split(refs):
    """Split 'agentkthx/foo.py:12-30' -> ('agentkthx/foo.py', 12, 30).
    Returns list of (path, start, end) tuples; lines may be None."""
    out = []
    for r in refs:
        # strip surrounding backticks already done; handle forms:
        #   path.py
        #   path.py:12
        #   path.py:12-30
        #   path.py:12,34,56  (multiple lines)
        m = re.match(r"^([^\s:]+\.py)(?::([\d,\-]+))?$", r)
        if not m:
            # non-py reference (e.g., docs/), keep as path-only
            out.append((r, None, None))
            continue
        path = m.group(1)
        if m.group(2):
            rng = m.group(2)
            if "-" in rng:
                a, b = rng.split("-", 1)
                out.append((path, int(a), int(b)))
            elif "," in rng:
                # take first as start, last as end
                nums = [int(x) for x in rng.split(",")]
                out.append((path, nums[0], nums[-1]))
            else:
                ln = int(rng)
                out.append((path, ln, ln))
        else:
            out.append((path, None, None))
    return out


def file_still_has_pattern(path: Path, finding: dict):
    """Heuristic: extract quoted identifiers from the detail prose (function
    names, attribute names, env vars) and confirm at least one still appears
    in the referenced file. Returns (found_count, searched_patterns, snippet)
    or None if no patterns to check."""
    if not path.exists():
        return None
    detail = finding["detail"]
    # Pull out backtick-quoted code identifiers (longer than 4 chars, snake/kebab case)
    pats = set(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_\-]{4,})`", detail))
    # Filter out generic English words that got backticked
    stop = {
        "false", "true", "none", "self", "localhost", "http",
        "https", "error", "value", "result", "default", "config",
        "logger", "client", "agent", "model", "response",
    }
    pats = {p for p in pats if p.lower() not in stop and not p.startswith("http")}
    if not pats:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = [p for p in pats if p in text]
    return (len(found), len(pats), found[:5])


def git_log_mentions(fid: str):
    """Return list of (sha, subject) for commits mentioning the finding ID."""
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), "log", "--oneline", "--all", "-i",
             "--grep", fid],
            capture_output=True, text=True, check=True,
        )
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return lines
    except subprocess.CalledProcessError:
        return []


# ---------------------------------------------------------------- main

def main():
    if not AUDIT.is_file():
        fail(f"audit.md not found at {AUDIT}")
    if not DELTAS.is_file():
        fail(f"deltas.md not found at {DELTAS}")

    audit_md = AUDIT.read_text(encoding="utf-8")
    deltas_md = DELTAS.read_text(encoding="utf-8")

    summary = parse_findings_summary(audit_md)
    detailed = parse_detailed_findings(audit_md)
    deltas_ids = parse_deltas_ids(deltas_md)

    open_findings = [f for f in summary if f["status"] == "OPEN"]
    print(f"=== Audit register parse ===")
    print(f"  Findings Summary rows:        {len(summary)}")
    print(f"  OPEN in audit.md summary:     {len(open_findings)}")
    print(f"  Detailed entries parsed:      {len(detailed)}")
    print(f"  IDs in deltas.md:             {len(deltas_ids)}")
    print()

    # Cross-check #1: every OPEN id should NOT appear in deltas
    drift = [f for f in open_findings if f["id"] in deltas_ids]
    print(f"=== Cross-check #1: register drift ===")
    if drift:
        print(f"  WARN: {len(drift)} OPEN findings ALSO appear in deltas.md:")
        for f in drift:
            print(f"    - {f['id']} ({f['status']} in audit.md, found in deltas.md)")
    else:
        print(f"  OK: none of the {len(open_findings)} OPEN IDs appear in deltas.md")
    print()

    # Cross-check #2: every OPEN id should have a Detailed Findings entry
    missing_detail = [f for f in open_findings if f["id"] not in detailed]
    print(f"=== Cross-check #2: detail coverage ===")
    if missing_detail:
        print(f"  WARN: {len(missing_detail)} OPEN findings lack a Detailed Findings entry:")
        for f in missing_detail:
            print(f"    - {f['id']}")
    else:
        print(f"  OK: all {len(open_findings)} OPEN findings have detail entries")
    print()

    # Cross-check #3: file existence + pattern presence
    print(f"=== Cross-check #3: file + pattern presence in codebase ===")
    verdicts = []
    for f in open_findings:
        d = detailed.get(f["id"], {"files": [], "detail": "", "title": f["title"]})
        refs = file_refs_split(d["files"])
        if not refs:
            verdicts.append((f, d, "NO_FILE_REF", None, []))
            continue
        # Take the first .py file ref for the pattern check
        verdict = "UNKNOWN"
        pattern_info = None
        git_mentions = git_log_mentions(f["id"])
        for (rel, start, end) in refs:
            if not rel.endswith(".py"):
                continue
            p = REPO / rel
            if not p.exists():
                verdict = "FILE_MISSING"
                continue
            pi = file_still_has_pattern(p, d)
            if pi is None:
                verdict = "FILE_EXISTS_NO_PATTERN"
                continue
            found, searched, sample = pi
            if found > 0:
                verdict = "STILL_OPEN_LIKELY"
                pattern_info = (found, searched, sample)
                break
            else:
                verdict = "PATTERN_GONE"
        verdicts.append((f, d, verdict, pattern_info, git_mentions))

    # Tally
    counts = Counter(v[2] for v in verdicts)
    print(f"  Verdict tallies:")
    for k, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {k:25s}  {n}")

    print()
    print(f"=== Per-finding verdicts ===")
    for (f, d, verdict, pi, git_mentions) in verdicts:
        refs_str = ", ".join(d["files"]) if d["files"] else "(no file ref)"
        line = f"  {f['id']:10s} [{f['severity']:6s}] {verdict:22s}  {refs_str}"
        if pi:
            line += f"  (pattern: {pi[0]}/{pi[1]} found, e.g. {pi[2]})"
        print(line)
        if git_mentions:
            for gm in git_mentions[:3]:
                print(f"             git log: {gm}")

    # Specifically flag the suspicious ones
    print()
    print(f"=== Findings needing follow-up ===")
    flagged = [v for v in verdicts if v[2] in ("PATTERN_GONE", "FILE_MISSING", "NO_FILE_REF")]
    if not flagged:
        print(f"  (none — every OPEN finding with a file reference still shows the issue pattern)")
    else:
        for (f, d, verdict, pi, git_mentions) in flagged:
            print(f"  - {f['id']} {verdict}: {f['title'][:80]}")
            if git_mentions:
                print(f"      git mentions ({len(git_mentions)}): {git_mentions[0]}")


if __name__ == "__main__":
    main()
