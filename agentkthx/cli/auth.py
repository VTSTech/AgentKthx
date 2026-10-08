"""Interactive ``/auth`` picker — API keys + FREE_ONLY flags (R07.20).

Backs the in-chat ``/auth`` slash command: an arrow-key menu over every
cloud backend's ``*_API_KEY`` and ``*_FREE_ONLY`` environment variable.
Enter on a FREE_ONLY row flips it; Enter on an API_KEY row prompts for a
value (hidden input on a TTY). ``q``/Esc closes the menu.

Design notes:

- **Applied three ways so it always sticks.** A change is written to
  (1) the persisted env file (``~/.agentkthx/.env``, override with
  ``AGENTKTHX_ENV_FILE``) so future CLI invocations pick it up via the
  config-module loader, (2) ``os.environ`` so the running process sees
  it immediately, and (3) every ``agentkthx`` module global that
  already imported the constant, so live code paths react without a
  restart. When the session's current backend matches an edited key,
  its instance attributes are patched too.

- **Shell exports keep precedence.** The env-file loader never clobbers
  a variable that is already set in the environment, so profile exports
  remain the authoritative source; ``/auth`` edits only fill the file
  (a saved value behind an export is shadowed until the export is
  removed — the picker says so via the closing hint).

- **Live rebinding is the whole point.** Plugins import their config
  constants at module import time (``from agentkthx.config import
  ZAI_FREE_ONLY``), so setting ``os.environ`` alone would not touch a
  running session. :func:`_apply_env` therefore walks ``sys.modules``
  for every ``agentkthx.*`` module that has the name in its globals and
  rebinds it — the same mechanism that makes R07.20's "FREE_ONLY filters
  applied at return time" instantly responsive to the flip. Pollinations
  additionally re-reads the env live per call, which this covers too.

- **Live backend patching.** If the picker runs inside a chat session
  whose current backend matches the edited variable (via the backend's
  ``_api_key_env_var`` or its ``backend_type`` slug), the key is also
  written onto the instance's ``_api_key`` / ``api_key`` attributes, so
  the very next request uses the new key without restarting the REPL.

- **Rendering reuses :class:`~agentkthx.cli.picker.ArrowMenu`** (the
  R07.19 ``/models`` switcher component) including its non-TTY numbered
  fallback, so scripted / piped sessions get a plain numbered list
  instead of hanging on raw-mode reads. Every frame is rebuilt from the
  live env between interactions, so state changes are visible
  immediately.

Registry layout (8 cloud backends × key + flag)::

    ZAI           ZAI_API_KEY              set (…Ab3x)
    ZAI           ZAI_FREE_ONLY            [off]
    OpenRouter    OPENROUTER_API_KEY       not set
    ...

Local backends (Ollama / TurboQuant / BitNet) are deliberately absent —
they take no API key and have no FREE_ONLY tier.
"""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass

# ============================================================================
# Registry
# ============================================================================

_TRUTHY = ("1", "true", "yes")

# Canonical API-key env vars (matching what each backend actually resolves):
#   ZAI / OrcaRouter / Mistral / Pollinations — cloud_base ``_api_key_env_var``
#   Gemini  — GEMINI_API_KEY (GOOGLE_API_KEY accepted as fallback)
#   HuggingFace — HF_TOKEN (HUGGING_FACE_HUB_TOKEN / HF_API_KEY accepted)
#   OpenAI / OpenRouter — their documented names.
# The tuple order is the display order of the picker (grouped per backend,
# API key row first, then its FREE_ONLY flag).


@dataclass(frozen=True)
class AuthVar:
    """One editable row in the ``/auth`` menu."""

    name: str
    """Canonical environment variable name (what gets set / rebound)."""

    kind: str
    """``"key"`` (secret string) or ``"flag"`` (FREE_ONLY boolean)."""

    backend: str
    """Human-readable backend label (``"ZAI"``, ``"OpenRouter"``, …)."""

    alt_names: tuple[str, ...] = ()
    """Fallback env names the backend also accepts (display-only hint)."""


AUTH_BACKENDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("ZAI", "ZAI_API_KEY", "ZAI_FREE_ONLY", ()),
    ("OpenRouter", "OPENROUTER_API_KEY", "OPENROUTER_FREE_ONLY", ()),
    ("OrcaRouter", "ORCAROUTER_API_KEY", "ORCAROUTER_FREE_ONLY", ()),
    ("Gemini", "GEMINI_API_KEY", "GEMINI_FREE_ONLY", ("GOOGLE_API_KEY",)),
    (
        "HuggingFace",
        "HF_TOKEN",
        "HF_FREE_ONLY",
        ("HUGGING_FACE_HUB_TOKEN", "HF_API_KEY"),
    ),
    ("OpenAI", "OPENAI_API_KEY", "OPENAI_FREE_ONLY", ()),
    ("Mistral", "MISTRAL_API_KEY", "MISTRAL_FREE_ONLY", ()),
    ("Pollinations", "POLLINATIONS_API_KEY", "POLLINATIONS_FREE_ONLY", ()),
    ("NVIDIA", "NVIDIA_API_KEY", "NVIDIA_FREE_ONLY", ()),
    ("Cloudflare", "CLOUDFLARE_API_KEY", "CLOUDFLARE_FREE_ONLY", ()),
)

# Canonical API-key env var -> BackendType.slug, used to patch the LIVE
# session backend even when the backend class doesn't define
# ``_api_key_env_var`` (Gemini / OpenAI / OpenRouter / HF roll their own
# __init__ key resolution).
_KEY_VAR_TO_SLUG: dict[str, str] = {
    "ZAI_API_KEY": "zai",
    "OPENROUTER_API_KEY": "openrouter",
    "ORCAROUTER_API_KEY": "orcarouter",
    "GEMINI_API_KEY": "gemini",
    "HF_TOKEN": "huggingface",
    "OPENAI_API_KEY": "openai",
    "MISTRAL_API_KEY": "mistral",
    "POLLINATIONS_API_KEY": "pollinations",
    "NVIDIA_API_KEY": "nvidia",
    "CLOUDFLARE_API_KEY": "cloudflare",
}

# Cloudflare is unique among AgentKthx cloud backends: it requires BOTH an
# API key AND a 32-hex-char account ID (baked into the URL path, NOT
# derivable from the Bearer token). The account ID is treated as a
# non-secret "key" entry in the picker (masked for parity, but the value
# is not actually secret — it appears in the dashboard URL bar). It is
# appended to the standard (key, flag) pair so the Cloudflare block in
# the picker reads:
#   Cloudflare   CLOUDFLARE_API_KEY       set (***xyz)
#   Cloudflare   CLOUDFLARE_ACCOUNT_ID    set (***abc)
#   Cloudflare   CLOUDFLARE_FREE_ONLY     [off]
# Live-patching the account ID is NOT supported — the CloudflareBackend
# resolves account_id once at __init__ time (it's baked into the base
# URL), so changing it mid-session requires a backend restart. The
# env-file + config module + os.environ are still updated so the next
# CLI invocation picks it up.
_EXTRA_AUTH_ENTRIES: tuple[AuthVar, ...] = (
    AuthVar("CLOUDFLARE_ACCOUNT_ID", "key", "Cloudflare", ()),
)


def auth_vars() -> list[AuthVar]:
    """Flat registry in display order - (key, flag) pairs per backend.

    Cloudflare gets a third entry (``CLOUDFLARE_ACCOUNT_ID``) injected
    after its API-key row, before its FREE_ONLY flag - see the
    ``_EXTRA_AUTH_ENTRIES`` constant for the rationale (Cloudflare
    requires both an API key AND an account ID, unlike every other
    cloud backend which derives everything from the API key alone).
    """
    entries: list[AuthVar] = []
    for backend_label, key_var, flag_var, alt_names in AUTH_BACKENDS:
        entries.append(AuthVar(key_var, "key", backend_label, alt_names))
        # Inject any extra (non-flag) entries that belong between the key
        # and the flag for this backend (Cloudflare's account ID).
        for extra in _EXTRA_AUTH_ENTRIES:
            if extra.backend == backend_label:
                entries.append(extra)
        entries.append(AuthVar(flag_var, "flag", backend_label))
    return entries


# ============================================================================
# State helpers
# ============================================================================


def _env_truthy(name: str) -> bool:
    """Config-module env-flag semantics: ``1`` / ``true`` / ``yes``."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def flag_state(name: str) -> str:
    """Human-readable FREE_ONLY state — ``"ON"`` or ``"off"``."""
    return "ON" if _env_truthy(name) else "off"


def mask_key(value: str) -> str:
    """Mask an API key for display — never print more than the last 4 chars.

    Asterisk style (``set (***b3x4)``), matching the ``agentkthx config``
    per-backend listing.
    """
    if not value:
        return "not set"
    if len(value) <= 8:
        return "set (***)"
    return f"set (***{value[-4:]})"


def _menu_labels(entries: list[AuthVar]) -> list[str]:
    """Build one picker row per entry, reflecting the CURRENT env state."""
    labels: list[str] = []
    for e in entries:
        if e.kind == "flag":
            mark = "[ON]" if _env_truthy(e.name) else "[off]"
            labels.append(f"{e.backend:<12} {e.name:<26} {mark}")
        else:
            current = os.environ.get(e.name, "")
            note = ""
            if not current and any(os.environ.get(a, "") for a in e.alt_names):
                note = f"  (via {'/'.join(e.alt_names)})"
            labels.append(f"{e.backend:<12} {e.name:<26} {mask_key(current)}{note}")
    return labels


# ============================================================================
# Application — env + config module + every imported plugin global
# ============================================================================


def _apply_env(name: str, env_value: str, typed_value) -> list[str]:
    """Set ``os.environ[name]`` and rebind every agentkthx module global.

    Returns the list of module names whose global ``name`` was rebound
    (``agentkthx.config`` excluded from the report — it is always
    rebound first when it defines the attribute). Unknown names are set
    in the environment but rebind nowhere.
    """
    if env_value:
        os.environ[name] = env_value
    else:
        os.environ.pop(name, None)  # empty = unset, never a stray ""

    import agentkthx.config as _config

    rebound: list[str] = []
    if hasattr(_config, name):
        setattr(_config, name, typed_value)

    this_mod = sys.modules.get(__name__)
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("agentkthx") or mod is None:
            continue
        if mod is _config or mod is this_mod:
            continue
        ns = getattr(mod, "__dict__", None)
        if ns and name in ns:
            setattr(mod, name, typed_value)
            rebound.append(mod_name)
    return rebound


def set_flag(entry: AuthVar, enabled: bool) -> str:
    """Set a FREE_ONLY flag to an explicit state (env ``"1"`` / unset).

    Rebinds the boolean into ``agentkthx.config`` and every plugin module
    that imported the constant, so R07.20's return-time filters react on
    the very next ``list_models()`` / ``generate()`` call. ON is also
    persisted to the env file; OFF removes the line (absent env = off).
    """
    _apply_env(entry.name, "1" if enabled else "0", bool(enabled))
    saved = _persist(entry.name, "1" if enabled else None)
    state = "ON" if enabled else "OFF"
    return f"{entry.name} -> {state}{saved}"


def toggle_flag(entry: AuthVar) -> str:
    """Flip a FREE_ONLY flag (the Enter action on a flag row)."""
    return set_flag(entry, not _env_truthy(entry.name))


def _persist(name: str, value: str | None) -> str:
    """Write/clear one env-file line; returns the outcome suffix.

    ``value=None`` removes the line (flag OFF, key clear). Permission or
    filesystem failures degrade to a warning suffix — the in-session
    change already applied, only persistence is lost.
    """
    from ..colors import dim

    try:
        from .. import env_file

        if value is None:
            env_file.remove_env_var(name)
        else:
            env_file.save_env_var(name, value)
        return f"  {dim('[saved to ' + str(env_file.env_file_path()) + ']')}"
    except OSError as exc:
        return f"  {dim(f'[env file not written: {exc}]')}"


def _patch_live_backend(agent, entry: AuthVar, typed_value) -> bool:
    """Write a new API key onto the running session's backend, if it matches.

    Matches by the backend's ``_api_key_env_var`` (cloud_base subclasses
    that declare it) or by ``backend_type`` slug (the plugins that resolve
    their key in a custom ``__init__``). Updates both ``_api_key``
    (cloud_base) and ``api_key`` (custom plugins) when present.
    """
    backend = getattr(agent, "backend", None)
    if backend is None or entry.kind != "key":
        return False

    by_decl = getattr(backend, "_api_key_env_var", "") == entry.name
    slug = str(getattr(getattr(backend, "backend_type", None), "value", ""))
    by_type = slug == _KEY_VAR_TO_SLUG.get(entry.name, "")
    if not (by_decl or by_type):
        return False

    patched = False
    for attr in ("_api_key", "api_key"):
        if hasattr(backend, attr):
            setattr(backend, attr, typed_value)
            patched = True
    return patched


def set_key(entry: AuthVar, value: str, agent=None) -> str:
    """Set (or clear with ``""``) an API key and propagate it everywhere.

    Applied to the env file (upsert, or line removal on clear),
    ``os.environ`` (pop on clear), and rebound as the raw string into
    ``agentkthx.config`` + the plugin module globals. If ``agent`` is
    attached to a matching backend, the instance's key attributes are
    patched too.
    """
    if value:
        _apply_env(entry.name, value, value)
        outcome = f"{entry.name} set ({mask_key(value)})"
    else:
        _apply_env(entry.name, "", "")
        outcome = f"{entry.name} cleared"
    outcome += _persist(entry.name, value or None)

    if value and len(value) < 20:
        outcome += "  (looks short — the provider may reject it)"

    if _patch_live_backend(agent, entry, value):
        outcome += " — live backend patched"
    return outcome


# ============================================================================
# Secret prompt
# ============================================================================


def prompt_secret(name: str, stdin=None, stdout=None) -> str | None:
    """Ask for an API key value.

    Hidden input (``getpass``) on a real TTY; otherwise the prompt is
    printed to ``stdout`` and one line is read from ``stdin`` — the same
    channel pair ArrowMenu's numbered fallback uses, so piped sessions
    and tests stay consistent. Returns the new value, the sentinel
    ``"-"`` meaning *clear the key*, or ``None`` meaning *keep current*
    (blank answer / EOF / Ctrl+C).
    """
    prompt = f"  Enter {name} (Enter=keep, '-'=clear): "
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        if hasattr(stdin, "isatty") and stdin.isatty():
            raw = getpass.getpass(prompt)
        else:
            stdout.write(prompt)
            stdout.flush()
            raw = (stdin.readline() or "").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return raw or None


# ============================================================================
# Picker loop
# ============================================================================


def run_auth_picker(agent=None, *, stdin=None, stdout=None) -> None:
    """Run the interactive ``/auth`` menu until cancelled.

    Each Enter applies an action and re-renders the menu (fresh labels —
    flips and key sets are immediately visible); ``q``/Esc/Ctrl+C
    closes. Non-TTY stdin gets ArrowMenu's numbered-list fallback, one
    selection per line, EOF/blank closing the menu.
    """
    from ..colors import cyan, dim, green, yellow
    from .picker import ArrowMenu

    entries = auth_vars()
    print(f"{cyan('Auth')} — API keys & FREE_ONLY flags (Enter: set/toggle \u00b7 q/Esc: exit)")
    print(
        dim("Changes apply to THIS session immediately; export the vars in your shell to persist.")
    )

    cursor = 0
    while True:
        labels = _menu_labels(entries)
        menu = ArrowMenu(
            labels,
            title="Auth — pick a variable",
            cursor=cursor,
            visible=len(labels) if len(labels) < 12 else 12,
        )
        idx = menu.run(stdin=stdin, stdout=stdout)
        if idx is None:
            break
        entry = entries[idx]
        cursor = idx

        if entry.kind == "flag":
            print(green(toggle_flag(entry)))
            continue

        new_value = prompt_secret(entry.name, stdin=stdin, stdout=stdout)
        if new_value is None:
            print(dim(f"{entry.name} unchanged."))
            continue
        print(green(set_key(entry, "" if new_value == "-" else new_value, agent)))

    print(dim("Auth menu closed."))
    from ..env_file import env_file_path as _env_file_path

    print(
        yellow(
            f"Saved to {_env_file_path()} — loaded on every startup; "
            f"shell exports still take precedence."
        )
    )
    print(dim(f"Session already updated — {cyan('/status')} or a new CLI run reflects the change."))
