"""ASCII banner + pip-style update-check notice.

Extracted verbatim from cli.py in R07.00 Phase 8. Owns the process-wide
_LAST_UPDATE_CHECK state (written by main() via _run_update_check, read by
_print_update_notice)."""

from __future__ import annotations

from .. import __version__
from ..colors import is_color_enabled, dim




# ============================================================================
# ASCII Banner
# ============================================================================

BANNER_ATOM_BRAILLE = """
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m  \x1b[95;1mAgentKthx\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀\x1b[0m  \x1b[2mAutonomous Agents with Local LLMs\x1b[0m
\x1b[96m⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀\x1b[0m
\x1b[96m⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄\x1b[0m  \x1b[2mStatus:\x1b[0m \x1b[33mAlpha\x1b[0m
\x1b[96m⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇\x1b[0m  \x1b[2mhttps://kthx.vts-tech.org\x1b[0m
\x1b[96m⢸⣿⣿⣿⣧⠀⠀⠀⢸⣟⣠⣾⠟⠋⠀⠙⠻⣶⣄⣿⡄⠀⠀⠀⠀⢀⣾⠃\x1b[0m
\x1b[96m⠘⢿⣿⣿⣏⠀⠀⢀⣼⡿⠋⣠⣶⣿⣿⣿⣦⡌⠙⣿⣧⡀⠀⠀⣠⣾⠋⠀\x1b[0m
\x1b[96m⠀⠀⠀⠈⢻⣦⣴⠟⣹⡇⢰⣿⣿⣿⣿⣿⣿⣿⡄⢸⡟⠻⣦⣴⠟⠁⠀⠀\x1b[0m
\x1b[96m⠀⠀⠀⢀⣴⡟⢿⣦⣿⡗⠸⣿⣿⣿⣿⣿⣿⣿⠃⢸⣇⣴⡿⢿⣦⡀⠀⠀\x1b[0m
\x1b[96m⠀⠀⢠⣾⠋⠀⠀⠙⢿⣧⣄⠙⢿⣿⣿⣿⠿⠃⣠⣿⡟⠁⠀⠀⠙⣷⡄⠀\x1b[0m
\x1b[96m⠀⢠⣿⠁⠀⠀⠀⠀⢸⣯⠛⢷⣦⣀⠀⣠⣴⡿⠋⣿⠃⠀⠀⠀⠀⠘⣿⡄\x1b[0m
\x1b[96m⠀⣾⡇⠀⠀⠀⠀⠀⠈⣿⠀⢀⣩⣿⣿⣿⣅⡀⢠⣿⠀⠀⠀⠀⠀⠀⢸⡇\x1b[0m
\x1b[96m⠀⠹⣷⣄⣀⣀⣀⣠⣤⣿⡿⠟⠋⠁⠀⠈⠙⠻⣿⣷⣤⣄⣀⣀⣀⣠⣾⠇\x1b[0m
\x1b[96m⠀⠀⠈⠉⠛⠛⠛⠉⠉⠘⣿⡀⠀⠀⠀⢀⣴⣶⣿⣄⠈⠉⠙⠛⠛⠋⠁⠀\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣷⡀⠀⠀⢸⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠿⣦⣤⣶⠟⠛⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m
"""



BANNER_ATOM_PLAIN = """
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀  AgentKthx
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀  Autonomous Agents with Local LLMs
⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀
⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄  Status: Alpha
⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇  https://kthx.vts-tech.org
⢸⣿⣿⣿⣧⠀⠀⠀⢸⣟⣠⣾⠟⠋⠀⠙⠻⣶⣄⣿⡄⠀⠀⠀⠀⢀⣾⠃
⠘⢿⣿⣿⣏⠀⠀⢀⣼⡿⠋⣠⣶⣿⣿⣿⣦⡌⠙⣿⣧⡀⠀⠀⣠⣾⠋⠀
⠀⠀⠀⠈⢻⣦⣴⠟⣹⡇⢰⣿⣿⣿⣿⣿⣿⣿⡄⢸⡟⠻⣦⣴⠟⠁⠀⠀
⠀⠀⠀⢀⣴⡟⢿⣦⣿⡗⠸⣿⣿⣿⣿⣿⣿⣿⠃⢸⣇⣴⡿⢿⣦⡀⠀⠀
⠀⠀⢠⣾⠋⠀⠀⠙⢿⣧⣄⠙⢿⣿⣿⣿⠿⠃⣠⣿⡟⠁⠀⠀⠙⣷⡄⠀
⠀⢠⣿⠁⠀⠀⠀⠀⢸⣯⠛⢷⣦⣀⠀⣠⣴⡿⠋⣿⠃⠀⠀⠀⠀⠘⣿⡄
⠀⣾⡇⠀⠀⠀⠀⠀⠈⣿⠀⢀⣩⣿⣿⣿⣅⡀⢠⣿⠀⠀⠀⠀⠀⠀⢸⡇
⠀⠹⣷⣄⣀⣀⣀⣠⣤⣿⡿⠟⠋⠁⠀⠈⠙⠻⣿⣷⣤⣄⣀⣀⣀⣠⣾⠇
⠀⠀⠈⠉⠛⠛⠛⠉⠉⠘⣿⡀⠀⠀⠀⢀⣴⣶⣿⣄⠈⠉⠙⠛⠛⠋⠁⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣷⡀⠀⠀⢸⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠿⣦⣤⣶⠟⠛⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀
"""




def print_banner() -> None:
    """Print the AgentKthx ASCII banner."""
    from .. import __version__, __status__
    # Convert 0.3.3 to R03.3 format for display
    parts = __version__.split('.')
    display_version = f"R{int(parts[1]):02d}.{parts[2]}" if len(parts) >= 2 else __version__    
    version_str = f"{display_version} [{__status__}]"
    if is_color_enabled():
        # Replace ANSI-colored "Status: Alpha" with version
        banner = BANNER_ATOM_BRAILLE.replace("\x1b[2mStatus:\x1b[0m \x1b[33mAlpha\x1b[0m", f"\x1b[2m{version_str}\x1b[0m")
        print(banner)
    else:
        banner = BANNER_ATOM_PLAIN.replace("Status: Alpha", version_str)
        print(banner)




# ============================================================================
# Update Check (pip-style "new release available" notice)
# ============================================================================

# Result of the update check for this process, stashed by main() so the
# notice can be printed under the chat banner (cmd_chat) and after
# non-interactive commands (post-run) without hitting the network twice.
# (R07.00: the check itself is always live — the on-disk cache is gone;
# this per-process stash only dedupes fetches within a single run.)
_LAST_UPDATE_CHECK = None




def _run_update_check(timeout: float = 1.0) -> None:
    """Run the live update check once per process; stash the result. Never raises."""
    global _LAST_UPDATE_CHECK
    try:
        from ..update_check import check_for_update
        _LAST_UPDATE_CHECK = check_for_update(timeout=timeout)
    except Exception:
        _LAST_UPDATE_CHECK = None




def _print_update_notice() -> None:
    """Print the pip-style 'new release' notice if a newer version is on PyPI."""
    result = _LAST_UPDATE_CHECK
    if not result:
        return
    try:
        from ..update_check import format_notice
        text = format_notice(result, current=__version__)
    except Exception:
        return
    if text:
        for line in text.splitlines():
            print(dim(line))
