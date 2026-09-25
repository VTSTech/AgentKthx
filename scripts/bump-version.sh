#!/usr/bin/env bash
# bump-version.sh — bump the AgentKthx version across all declaration sites.
#
# Usage:
#   scripts/bump-version.sh R06.58            # by release tag
#   scripts/bump-version.sh 0.6.58            # by semver (auto-derives R06.58)
#   scripts/bump-version.sh R06.58 --dry-run  # preview without writing
#   scripts/bump-version.sh --current         # print current version, exit
#
# What gets bumped (4 sites in 3 files):
#   pyproject.toml            version = "0.6.57"
#   agentkthx/__init__.py:2   ⚛️ AgentKthx R06.57
#   agentkthx/__init__.py:34  __version__ = "0.6.57"  # R06.57
#   README.md:1              # ⚛️ AgentKthx R06.57
#
# What does NOT get bumped (intentionally):
#   - Historical changelog markers in code comments like "R06.57: was 24h"
#     (these reference the version that introduced a change, not the current
#     version — touching them would rewrite history).
#   - docs/CHANGELOG.md (add entries manually — they're prose, not regex-able)
#   - cli.py banner (imports __version__ dynamically — no edit needed)
#
# Exit codes:
#   0  success
#   1  bad invocation (missing arg, bad format)
#   2  no changes needed (already at target version)
#
# Written by VTSTech — https://www.vts-tech.org

set -euo pipefail

# ─── colors ──────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
    C_RED=$'\033[31m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
    C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_RED=''; C_DIM=''; C_RESET=''
fi

# ─── locate repo root (parent of scripts/) ───────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Files that contain version declarations
INIT_PY="agentkthx/__init__.py"
PYPROJECT="pyproject.toml"
README="README.md"

# ─── helpers ─────────────────────────────────────────────────────────────

die() {
    printf '%s%s%s\n' "$C_RED" "$1" "$C_RESET" >&2
    exit 1
}

info() {
    printf '%s%s%s\n' "$C_CYAN" "$1" "$C_RESET"
}

ok() {
    printf '%s%s%s\n' "$C_GREEN" "$1" "$C_RESET"
}

warn() {
    printf '%s%s%s\n' "$C_YELLOW" "$1" "$C_RESET"
}

dim() {
    printf '%s%s%s\n' "$C_DIM" "$1" "$C_RESET"
}

# Extract the current version from __init__.py
# Reads: __version__ = "0.6.57"  # R06.57
# Returns: "0.6.57"
get_current_version() {
    local ver
    ver=$(grep -E '^__version__\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"' "$INIT_PY" \
        | head -1 \
        | sed -E 's/.*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/')
    if [[ -z "$ver" ]]; then
        die "could not find __version__ in $INIT_PY"
    fi
    echo "$ver"
}

# Convert "0.6.57" → "R06.57"
# Convention: R{minor:02d}.{patch}  — major is always 0 and not shown,
# minor is the second number (06), patch is the third (57).
semver_to_release() {
    local semver="$1"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$semver"
    printf 'R%02d.%s\n' "$minor" "$patch"
}

# Convert "R06.57" → "0.6.57"
# Inverse of semver_to_release: minor and patch come from the two halves
# of the release tag, major is always 0.
release_to_semver() {
    local release="$1"
    local rest="${release#R}"   # strip leading R → "06.57"
    local minor="${rest%%.*}"   # "06"
    local patch="${rest#*.}"    # "57"
    # minor may have a leading zero ("06") — strip it for the semver form
    minor=$((10#$minor))
    echo "0.${minor}.${patch}"
}

# Validate a release tag looks like R06.58
is_release_tag() {
    [[ "$1" =~ ^R[0-9]{2}\.[0-9]+$ ]]
}

# Validate a semver looks like 0.6.58
is_semver() {
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# ─── --current ───────────────────────────────────────────────────────────
if [[ "${1:-}" == "--current" ]]; then
    ver=$(get_current_version)
    rel=$(semver_to_release "$ver")
    printf '%s (%s)\n' "$rel" "$ver"
    exit 0
fi

# ─── parse args ──────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    cat >&2 <<EOF
Usage: $0 <version> [--dry-run]
       $0 --current

Examples:
  $0 R06.58              # bump to release R06.58 (semver 0.6.58)
  $0 0.6.58              # bump to semver 0.6.58 (release R06.58)
  $0 R06.58 --dry-run    # preview changes without writing
  $0 --current           # print current version

Bumps version in 4 sites across 3 files:
  - $PYPROJECT
  - $INIT_PY  (header comment + __version__ line)
  - $README
EOF
    exit 1
fi

TARGET="$1"
DRY_RUN=false
if [[ "${2:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Normalize: accept either R06.58 or 0.6.58
if is_release_tag "$TARGET"; then
    TARGET_RELEASE="$TARGET"
    TARGET_SEMVER=$(release_to_semver "$TARGET")
elif is_semver "$TARGET"; then
    TARGET_SEMVER="$TARGET"
    TARGET_RELEASE=$(semver_to_release "$TARGET")
else
    die "bad version format: '$TARGET' — expected R06.58 or 0.6.58"
fi

# Sanity: the release tag and semver must round-trip cleanly
if [[ "$TARGET_RELEASE" != "$(semver_to_release "$TARGET_SEMVER")" ]]; then
    die "internal: release/semver mismatch ($TARGET_RELEASE vs $TARGET_SEMVER)"
fi

# ─── check current vs target ────────────────────────────────────────────
CURRENT_SEMVER=$(get_current_version)
CURRENT_RELEASE=$(semver_to_release "$CURRENT_SEMVER")

if [[ "$CURRENT_SEMVER" == "$TARGET_SEMVER" ]]; then
    warn "already at $TARGET_RELEASE ($TARGET_SEMVER) — nothing to do"
    exit 2
fi

info "Bumping: $CURRENT_RELEASE ($CURRENT_SEMVER) → $TARGET_RELEASE ($TARGET_SEMVER)"
if $DRY_RUN; then
    dim "(dry-run — no files will be written)"
fi
echo

# ─── verify all 4 sites exist and contain the expected current strings ───
# This catches the case where someone manually edited a file and the regex
# would silently no-op. Better to fail loudly than to bump 3/4 sites.

declare -a SITES=(
    "$PYPROJECT|^version = \"$CURRENT_SEMVER\"$"
    "$INIT_PY|^⚛️ AgentKthx $CURRENT_RELEASE$"
    "$INIT_PY|^__version__ = \"$CURRENT_SEMVER\"  # $CURRENT_RELEASE$"
    "$README|^# ⚛️ AgentKthx $CURRENT_RELEASE$"
)

errors=0
for site in "${SITES[@]}"; do
    file="${site%%|*}"
    pattern="${site#*|}"
    if [[ ! -f "$file" ]]; then
        printf '  %s✗%s %s — file missing\n' "$C_RED" "$C_RESET" "$file" >&2
        errors=$((errors + 1))
        continue
    fi
    if ! grep -qE "$pattern" "$file"; then
        printf '  %s✗%s %s — pattern not found: %s\n' \
            "$C_RED" "$C_RESET" "$file" "$pattern" >&2
        errors=$((errors + 1))
    fi
done

if [[ $errors -gt 0 ]]; then
    echo >&2
    die "$errors site(s) missing or out of pattern — fix manually or rerun with correct CURRENT version"
fi

# ─── apply bumps ─────────────────────────────────────────────────────────
# Use perl for in-place edits because macOS sed -i requires a backup suffix
# and Linux sed -i doesn't, and perl -pi -e is portable across both.

apply_bump() {
    local file="$1"
    local pattern="$2"
    local replacement="$3"

    if $DRY_RUN; then
        # Show what would change
        local matches
        matches=$(grep -nE "$pattern" "$file" || true)
        if [[ -n "$matches" ]]; then
            while IFS= read -r line; do
                local lineno="${line%%:*}"
                local oldtext="${line#*:}"
                local newtext
                newtext=$(printf '%s' "$oldtext" | perl -pe "s/$pattern/$replacement/")
                printf '  %s%s:%s%s\n' "$C_DIM" "$file" "$lineno" "$C_RESET"
                printf '  %s- %s%s\n' "$C_RED" "$oldtext" "$C_RESET"
                printf '  %s+ %s%s\n' "$C_GREEN" "$newtext" "$C_RESET"
            done <<< "$matches"
        fi
    else
        perl -pi -e "s/$pattern/$replacement/" "$file"
        printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$file"
    fi
}

echo "Applying bumps:"
apply_bump "$PYPROJECT" \
    "^version = \"$CURRENT_SEMVER\"$" \
    "version = \"$TARGET_SEMVER\""
apply_bump "$INIT_PY" \
    "^(⚛️ AgentKthx )$CURRENT_RELEASE\$" \
    "\${1}$TARGET_RELEASE"
apply_bump "$INIT_PY" \
    "^(__version__ = \")$CURRENT_SEMVER(\"  # )$CURRENT_RELEASE\$" \
    "\${1}$TARGET_SEMVER\${2}$TARGET_RELEASE"
apply_bump "$README" \
    "^(# ⚛️ AgentKthx )$CURRENT_RELEASE\$" \
    "\${1}$TARGET_RELEASE"

echo

# ─── verify result ───────────────────────────────────────────────────────
if $DRY_RUN; then
    warn "dry-run complete — no files written"
    exit 0
fi

# Re-read the version from __init__.py to confirm it took
NEW_SEMVER=$(get_current_version)
NEW_RELEASE=$(semver_to_release "$NEW_SEMVER")
if [[ "$NEW_SEMVER" != "$TARGET_SEMVER" ]]; then
    die "verification failed: __version__ is '$NEW_SEMVER', expected '$TARGET_SEMVER'"
fi

ok "bumped to $NEW_RELEASE ($NEW_SEMVER)"

echo
dim "Next steps:"
dim "  git diff --stat"
dim "  git add -A && git commit -m \"bump: R06.57 → $NEW_RELEASE\""
dim "  git tag $NEW_RELEASE"
