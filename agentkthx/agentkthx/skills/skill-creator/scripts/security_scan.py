#!/usr/bin/env python3
"""
Security Scanner - Scans a skill for security issues.

Usage:
    python security_scan.py path/to/skill/

Checks:
    - Hardcoded API keys, tokens, secrets
    - .env files (should not be included)
    - SQL injection patterns
    - Command injection patterns
    - Sensitive file patterns
"""

import re
import sys
from pathlib import Path


# Patterns that indicate security issues
SECRET_PATTERNS = [
    (r'api[_-]?key\s*=\s*["\'][^"\']{10,}["\']', "hardcoded API key"),
    (r'secret[_-]?key\s*=\s*["\'][^"\']{10,}["\']', "hardcoded secret key"),
    (r'access[_-]?token\s*=\s*["\'][^"\']{10,}["\']', "hardcoded access token"),
    (r'auth[_-]?token\s*=\s*["\'][^"\']{10,}["\']', "hardcoded auth token"),
    (r'password\s*=\s*["\'][^"\']{4,}["\']', "hardcoded password"),
    (r'Bearer\s+[A-Za-z0-9_-]{20,}', "Bearer token in code"),
    (r'sk-[A-Za-z0-9]{20,}', "OpenAI-style API key"),
    (r'xox[baprs]-[A-Za-z0-9-]{10,}', "Slack token"),
    (r'ghp_[A-Za-z0-9]{36,}', "GitHub personal access token"),
    (r'gho_[A-Za-z0-9]{36,}', "GitHub OAuth token"),
    (r'AKIA[0-9A-Z]{16}', "AWS access key ID"),
    (r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*=\s*["\'][^"\']+["\']', "AWS secret key"),
]

INJECTION_PATTERNS = [
    (r'eval\s*\(\s*[^"\']+\+', "possible eval injection"),
    (r'exec\s*\(\s*[^"\']+\+', "possible exec injection"),
    (r'subprocess\..*\+.*input', "possible command injection"),
    (r'os\.system\s*\(\s*[^"\']+\+', "possible command injection"),
    (r'cursor\.execute\s*\(\s*[^"\']?\s*%\s*\(', "possible SQL injection (old style)"),
]

SENSITIVE_FILES = [
    '.env',
    '.env.local',
    '.env.production',
    'credentials.json',
    'secrets.json',
    'private.key',
    'id_rsa',
]


def scan_file(file_path: Path) -> list[str]:
    """Scan a single file for security issues."""
    issues = []
    
    try:
        content = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return issues  # Skip binary files
    
    # Check for secret patterns
    for pattern, desc in SECRET_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append(f"{desc}")
    
    # Check for injection patterns (only in Python files)
    if file_path.suffix == '.py':
        for pattern, desc in INJECTION_PATTERNS:
            if re.search(pattern, content):
                issues.append(f"{desc}")
    
    return issues


def scan_skill(skill_path: Path) -> tuple[list[str], list[str]]:
    """Scan a skill directory for security issues.
    Returns (errors, warnings)."""
    errors = []
    warnings = []
    
    # Check for sensitive files
    for sensitive in SENSITIVE_FILES:
        if (skill_path / sensitive).exists():
            errors.append(f"Sensitive file found: {sensitive}")
    
    # Scan all files
    for file_path in skill_path.rglob('*'):
        if not file_path.is_file():
            continue
        
        # Skip hidden files and common non-code files
        if file_path.name.startswith('.') and file_path.suffix not in ('.md', '.py'):
            continue
        
        issues = scan_file(file_path)
        for issue in issues:
            rel_path = file_path.relative_to(skill_path)
            warnings.append(f"{rel_path}: {issue}")
    
    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python security_scan.py path/to/skill/")
        sys.exit(1)
    
    skill_path = Path(sys.argv[1])
    if not skill_path.is_dir():
        print(f"Error: {skill_path} is not a directory")
        sys.exit(1)
    
    print(f"Security scan: {skill_path.name}")
    print("-" * 40)
    
    errors, warnings = scan_skill(skill_path)
    
    if errors:
        print("SECURITY ERRORS (must fix):")
        for err in errors:
            print(f"  ✗ {err}")
    
    if warnings:
        print("\nSECURITY WARNINGS (review):")
        for warn in warnings:
            print(f"  ⚠ {warn}")
    
    if not errors and not warnings:
        print("✓ No security issues found")
        sys.exit(0)
    elif errors:
        print("\nSECURITY SCAN FAILED")
        sys.exit(1)
    else:
        print("\nSECURITY SCAN PASSED (with warnings)")
        sys.exit(0)


if __name__ == "__main__":
    main()
