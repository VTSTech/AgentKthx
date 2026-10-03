"""Persistent env-var file for ``/auth`` (R07.20).

The ``/auth`` picker originally applied API keys and FREE_ONLY flags to
``os.environ`` + the imported module globals — correct for the running
session, but per-instance only: quitting the REPL dropped everything.
R07.20 extends it with a durable home: ``~/.agentkthx/.env`` (override
with ``AGENTKTHX_ENV_FILE``), a stdlib ``KEY=VALUE`` file that
:mod:`agentkthx.config` loads at import time BEFORE its constants read
the environment, so anything ``/auth`` saves is live for every future
CLI invocation — no shell exports required.

Semantics:

- **Shell exports always win.** ``load_env_file()`` never clobbers a
  variable that is already present in ``os.environ`` — the file only
  fills gaps, so a user's profile exports keep precedence over stale
  file content.
- **File format is deliberately minimal**: ``KEY=VALUE`` per line,
  ``#`` comments, blank lines ignored, optional single/double quotes
  stripped, no interpolation/expansion (secrets go in verbatim).
- **Atomic writes** (temp file + ``os.replace``) under a module lock,
  created with ``0600`` permissions — the file holds API keys.
- ``save_env_var`` upserts one line; ``remove_env_var`` deletes it.
  ``/auth`` maps: set key → upsert, clear key → remove, FREE_ONLY ON →
  upsert ``1``, FREE_ONLY OFF → remove (absent env = off).
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.Lock()

_DEFAULT_PATH = Path.home() / ".agentkthx" / ".env"


def env_file_path() -> Path:
    """Resolve the env-file location (``AGENTKTHX_ENV_FILE`` overrides)."""
    override = os.environ.get("AGENTKTHX_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PATH


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """Parse the env file into ``os.environ`` without clobbering live values.

    Returns the ``{name: value}`` mapping actually applied (i.e. only
    entries that were NOT already set in the environment). Missing files
    are a no-op; malformed lines are skipped, never fatal.
    """
    file_path = env_file_path() if path is None else Path(path).expanduser()
    try:
        raw = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    applied: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = _strip_quotes(value.strip())
        if not name or not name.replace("_", "A").isalnum():
            continue
        if name in os.environ:
            continue  # shell exports take precedence
        os.environ[name] = value
        applied[name] = value
    return applied


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def save_env_var(name: str, value: str, path: str | Path | None = None) -> Path:
    """Upsert ``name=value`` in the env file (atomic, ``0600``).

    Creates the parent directory when needed. Returns the file path.
    Raises ``OSError`` on unwritable locations — callers decide whether
    that is fatal (``/auth`` degrades to a warning).
    """
    file_path = env_file_path() if path is None else Path(path).expanduser()
    with _LOCK:
        lines = _read_lines(file_path)
        entry = f"{name}={value}"
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == name:
                lines[i] = entry
                break
        else:
            lines.append(entry)
        _write_lines(file_path, lines)
    return file_path


def remove_env_var(name: str, path: str | Path | None = None) -> bool:
    """Delete ``name``'s line from the env file. True if it was present."""
    file_path = env_file_path() if path is None else Path(path).expanduser()
    with _LOCK:
        lines = _read_lines(file_path)
        kept = [ln for ln in lines if ln.split("=", 1)[0].strip() != name]
        if len(kept) == len(lines):
            return False
        _write_lines(file_path, kept)
    return True


def _read_lines(file_path: Path) -> list[str]:
    try:
        return file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _write_lines(file_path: Path, lines: list[str]) -> None:
    parent = file_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, file_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
