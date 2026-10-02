"""Host-environment probe (R07.19) — OS + shell detection for the system prompt.

Light, stdlib-only, fail-safe. Called ONCE during ``Agent`` construction
(see ``agent_setup.py``) and rendered as a short ``# Host Environment``
section appended to the system prompt, so the model knows which argument
syntax to pass to the ``shell`` tool:

- Windows  → the shell tool runs commands via ``cmd.exe`` (subprocess
  ``shell=True`` semantics) — ``dir`` / ``where`` / ``set`` / ``type``
  work, PowerShell needs ``powershell -Command "..."``.
- Linux    → commands run via ``/bin/sh`` — POSIX syntax, GNU coreutils.
- macOS    → commands run via ``/bin/sh`` — BSD userland differences
  (``sed -i ''``, ``awk``,``readlink -f`` absence) are worth flagging.

"Light" means: no subprocess spawning, no network, no filesystem probes
beyond reading ``/etc/os-release`` (via ``platform.freedesktop_os_release``,
stdlib since 3.10). Everything is ``platform`` / ``sys`` reads — total cost
is microseconds, paid once per session, not per turn.

Design notes:

- **Fail-safe**: ``build_environment_section()`` returns ``""`` on ANY
  probe failure. The env section is decoration — a broken ``/etc/os-release``
  must never take down Agent construction (same best-effort contract as
  the soul loader's ``except Exception`` fallback).
- **Opt-out**: ``AGENTKTHX_NO_ENV_PROBE=1`` skips the probe entirely
  (mirrors ``AGENTKTHX_NO_UPDATE_CHECK=1``).
- **BitNet compact mode**: BitNet's lean prompt is kept under ~500 chars
  (reserved-token-ID crash threshold, see ``_build_default_prompt``), so
  BitNet sessions get a single compact ``Environment: ...`` line instead
  of the full markdown section.
- **Testability**: all platform reads live in small module-level
  ``_read_*`` functions so tests can monkeypatch them individually
  without touching ``platform`` internals.
"""

from __future__ import annotations

import os
import platform
import sys

# Windows 11 is reported as NT 10.0 by every stdlib surface (the version
# fields were frozen at 10.0 for app-compat). The build number is the only
# reliable discriminator: 22000 = first Win11 build (21H2).
_WIN11_BUILD = 22000

# AGENTKTHX_NO_ENV_PROBE=1/true/yes disables the probe (kill switch,
# same truthiness convention as AGENTKTHX_DEBUG).
_ENV_PROBE_OFF = ("1", "true", "yes")

# Cap for the /etc/os-release PRETTY_NAME — the field is attacker-adjacent
# on multi-user boxes (anyone with write access to /etc/os-release can put
# 4KB of junk in the system prompt). 80 chars is generous for a distro name.
_MAX_PRETTY_NAME = 80


# ============================================================================
# Platform readers (one per family — monkeypatch targets for tests)
# ============================================================================


def _read_windows() -> dict:
    """Read Windows version info. Returns the ``probe_environment`` dict.

    ``sys.getwindowsversion()`` gives (major, minor, build, ...). The
    friendly name needs the build: NT 10.0 covers both Windows 10
    (build < 22000) and Windows 11 (build >= 22000).
    """
    winver = sys.getwindowsversion()
    build = int(winver.build)
    major = int(winver.major)
    minor = int(winver.minor)
    name = "Windows 11" if build >= _WIN11_BUILD else "Windows 10"
    # Defensive: other NT majors (5.x = XP/2000 era, 6.x = Vista-8.1) still
    # render sensibly instead of claiming "Windows 10".
    if major != 10:
        name = f"Windows (NT {major}.{minor})"
    return {
        "family": "windows",
        "os": f"{name} (NT {major}.{minor} build {build})",
        "kernel": "",
        "arch": platform.machine(),
    }


def _read_linux() -> dict:
    """Read Linux distro (PRETTY_NAME from /etc/os-release) + kernel version."""
    distro = ""
    try:
        os_release = platform.freedesktop_os_release()
        distro = str(os_release.get("PRETTY_NAME") or os_release.get("NAME") or "")
    except (OSError, KeyError, AttributeError, ValueError):
        # Non-freedistro platforms, stripped containers, or a malformed file —
        # fall back to kernel-only identification.
        distro = ""
    distro = distro[:_MAX_PRETTY_NAME]
    return {
        "family": "linux",
        "os": distro,
        "kernel": platform.release(),
        "arch": platform.machine(),
    }


def _read_mac() -> dict:
    """Read macOS version (platform.mac_ver) + Darwin kernel version."""
    mac_version = platform.mac_ver()[0]  # e.g. "15.2" ("" on exotic builds)
    return {
        "family": "macos",
        "os": f"macOS {mac_version}" if mac_version else "macOS (version unknown)",
        "kernel": f"Darwin {platform.release()}",
        "arch": platform.machine(),
    }


def _read_unknown() -> dict:
    """Fallback for platforms that are neither Windows / Linux / Darwin."""
    return {
        "family": "unknown",
        "os": f"Unknown ({platform.system()} {platform.release()})".strip(),
        "kernel": "",
        "arch": platform.machine(),
    }


def probe_environment() -> dict:
    """Probe the host OS once. Returns a dict:

    ``{"family": "windows"|"linux"|"macos"|"unknown", "os": str,
       "kernel": str, "arch": str}``

    Raises nothing — callers get a valid (possibly "unknown") dict.
    """
    system = platform.system()
    if system == "Windows":
        return _read_windows()
    if system == "Linux":
        return _read_linux()
    if system == "Darwin":
        return _read_mac()
    return _read_unknown()


# ============================================================================
# Rendering
# ============================================================================


def _shell_note(family: str) -> str:
    """One-line shell-syntax guidance per platform family.

    The point of the whole section: the model must know that the shell
    tool's ``shell=True`` subprocess means cmd.exe semantics on Windows
    but POSIX sh on Linux/macOS, and pick arguments accordingly.
    """
    if family == "windows":
        return (
            "The shell tool runs commands via cmd.exe — use cmd.exe syntax "
            "(dir, where, set, type). For PowerShell, invoke it explicitly: "
            'powershell -Command "..."'
        )
    if family == "macos":
        return (
            "The shell tool runs commands via /bin/sh — use POSIX syntax; "
            "BSD userland (sed/awk/readlink flags differ from GNU; "
            "sed -i needs an empty-string suffix: sed -i '' s/a/b/ file)"
        )
    # linux + unknown: POSIX sh, GNU-flavoured on the vast majority of distros
    return (
        "The shell tool runs commands via /bin/sh — use POSIX syntax; "
        "GNU coreutils and pipes are available"
    )


def _format_os_line(env: dict) -> str:
    """Compose the ``- OS: ...`` bullet from a probe dict."""
    parts = [env["os"]]
    if env.get("kernel"):
        parts.append(f"kernel {env['kernel']}")
    if env.get("arch"):
        parts.append(env["arch"])
    return "- OS: " + ", ".join(p for p in parts if p)


def build_environment_section(is_bitnet: bool = False, debug: bool = False) -> str:
    """Build the system-prompt environment section. Returns "" when disabled.

    Args:
        is_bitnet: BitNet sessions get a single compact line (their lean
            prompt must stay under ~500 chars — see _build_default_prompt).
        debug: When True, prints ``[Env] ...`` progress lines (house style).

    Kill switch: ``AGENTKTHX_NO_ENV_PROBE=1`` (or true/yes) returns "".
    Any probe/render failure returns "" — best-effort decoration.
    """
    if os.environ.get("AGENTKTHX_NO_ENV_PROBE", "").lower() in _ENV_PROBE_OFF:
        if debug:
            print("[Env] Probe disabled (AGENTKTHX_NO_ENV_PROBE)")
        return ""

    try:
        env = probe_environment()
        if is_bitnet:
            # Compact single line — no markdown (BitNet's degraded tokenizer
            # crashes on some markdown glyphs at scale; keep it plain).
            os_desc = ", ".join(p for p in (env["os"], env.get("arch", "")) if p)
            section = (
                f"Environment: {os_desc}; shell tool runs "
                f"{'cmd.exe syntax' if env['family'] == 'windows' else '/bin/sh POSIX syntax'}."
            )
        else:
            lines = ["# Host Environment", _format_os_line(env)]
            lines.append(f"- Shell tool: {_shell_note(env['family'])}")
            section = "\n".join(lines)
        if debug:
            print(f"[Env] Detected: {env['os']} ({env['family']})")
        return section
    except Exception as e:  # pragma: no cover — defensive, probe never crashes setup
        if debug:
            print(f"[Env] Probe failed, skipping environment section: {e}")
        return ""
