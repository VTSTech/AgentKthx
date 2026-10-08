"""R07.20 — the in-chat ``/auth`` picker (API keys + FREE_ONLY flags) and
its persistent env-file home.

Covers six layers:

1. Registry (``agentkthx/cli/auth.py``) — 8 cloud backends × (key, flag)
   pairs, canonical env var names, display-order pairing, key→slug map.
2. Mechanics — ``mask_key`` / ``flag_state`` display helpers, the
   ``set_flag`` / ``toggle_flag`` live rebinding (os.environ +
   ``agentkthx.config`` attribute + plugin module globals),
   ``set_key`` set/clear semantics incl. the short-key warning and the
   live-backend patch (``_api_key`` / ``api_key`` attributes, matched by
   ``_api_key_env_var`` or ``backend_type`` slug), and non-matching
   backends left untouched.
3. ``prompt_secret`` — TTY (getpass) vs piped (stdin.readline) channels,
   blank = keep, ``"-"`` = clear sentinel, EOF = keep.
4. ``agentkthx/env_file.py`` — parse/clobber-protection/atomic upsert
   and removal, ``AGENTKTHX_ENV_FILE`` override, 0600 permissions.
5. Persistence — ``set_key``/``set_flag`` write the env file (set→
   upsert, clear/flag-OFF→line removal) and say so in the outcome.
6. ``agentkthx config`` — ``_backend_auth_rows`` one-line-per-backend
   listing (key state / FREE_ONLY / free fallback, active marker) plus
   cmd_chat wiring pins and the stdlib-only import contract.
"""

import io
import os
import re
import stat
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import agentkthx.config as config_module
from agentkthx.cli import auth as auth_mod
from agentkthx.cli.auth import (
    _KEY_VAR_TO_SLUG,
    AUTH_BACKENDS,
    AuthVar,
    _menu_labels,
    auth_vars,
    flag_state,
    mask_key,
    prompt_secret,
    run_auth_picker,
    set_flag,
    set_key,
    toggle_flag,
)
from agentkthx.env_file import (
    env_file_path,
    load_env_file,
    remove_env_var,
    save_env_var,
)

_ALL_VARS = [v for trio in AUTH_BACKENDS for v in (trio[1], trio[2])]


# ═══════════════════════════════════════════════════════════════════════
# Isolation — snapshot env + config attrs + plugin globals per test
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _auth_env_isolation(tmp_path, monkeypatch):
    """Snapshot + restore env, config attrs, and EVERY agentkthx module
    global bound to a registry name — flips rebind real plugin modules
    (zai.py etc. import their constants at module import time), and those
    globals must not leak between tests. Also redirects the env file to
    a per-test path so /auth persistence never touches the developer's
    real ~/.agentkthx/.env."""
    monkeypatch.setenv("AGENTKTHX_ENV_FILE", str(tmp_path / "auth.env"))
    names = set(_ALL_VARS)
    saved_env = {n: os.environ.get(n) for n in names}
    saved_cfg = {n: getattr(config_module, n) for n in names}
    saved_globals = []
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("agentkthx"):
            continue
        ns = getattr(mod, "__dict__", {})
        hit = {n: ns[n] for n in names if n in ns}
        if hit:
            saved_globals.append((mod, hit))
    yield
    for n, v in saved_env.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v
    for n, v in saved_cfg.items():
        setattr(config_module, n, v)
    for mod, hit in saved_globals:
        for n, v in hit.items():
            setattr(mod, n, v)
    for mod_name in list(sys.modules):
        if mod_name.startswith("agentkthx.tests._fake_plugin"):
            del sys.modules[mod_name]


def _make_fake_plugin(name: str, **globals_) -> types.ModuleType:
    """Register a fake agentkthx.* module carrying imported config globals."""
    mod = types.ModuleType(f"agentkthx.tests.{name}")
    for attr, value in globals_.items():
        setattr(mod, attr, value)
    sys.modules[mod.__name__] = mod
    return mod


class FakeBackend:
    """Minimal stand-in for a cloud backend instance."""

    def __init__(self, slug: str, env_var: str = "", **attrs):
        self.backend_type = types.SimpleNamespace(value=slug)
        if env_var:
            self._api_key_env_var = env_var
        for attr, value in attrs.items():
            setattr(self, attr, value)


class FakeAgent:
    def __init__(self, backend):
        self.backend = backend


# ═══════════════════════════════════════════════════════════════════════
# 1. Registry
# ═══════════════════════════════════════════════════════════════════════


class TestAuthRegistry:
    def test_sixteen_entries_eight_pairs(self):
        """R07.27: Cloudflare added — 21 entries (10 keys + 10 flags + 1
        extra account-ID key entry).

        Historical name retained. The count grew from 8 pairs (16) to 9
        pairs (18) when NVIDIA NIM was added in R07.26, and again to 10
        pairs (20) + 1 extra (Cloudflare's CLOUDFLARE_ACCOUNT_ID) when
        Cloudflare Workers AI was added in R07.27. Cloudflare is unique
        among cloud backends: it requires BOTH an API key AND an account
        ID baked into the URL path.
        """
        entries = auth_vars()
        assert len(entries) == 21
        assert len({e.name for e in entries}) == 21
        kinds = [e.kind for e in entries]
        # 10 API-key rows + 1 extra account-ID row = 11 key-kind entries
        assert kinds.count("key") == 11
        assert kinds.count("flag") == 10

    def test_pairs_grouped_key_first(self):
        entries = auth_vars()
        # R07.27: 21 entries — 10 (key, flag) pairs PLUS one extra
        # CLOUDFLARE_ACCOUNT_ID entry injected between the Cloudflare
        # key row and the Cloudflare flag row. The simple-pair iteration
        # only applies to backends WITHOUT an extra entry; for those,
        # every (key, flag) pair is adjacent. For Cloudflare, the triple
        # (key, account_id, flag) is adjacent. We verify both shapes.
        # Iterate the standard pairs by walking entries and matching each
        # (key, flag) we find adjacent. The extra account-ID entry sits
        # between Cloudflare's key and flag.
        i = 0
        seen_cloudflare_account_id = False
        while i < len(entries):
            e = entries[i]
            if e.kind == "key":
                # The next entry is either the matching flag (standard pair)
                # or the Cloudflare account ID (then the flag after that).
                if (
                    e.backend == "Cloudflare"
                    and i + 2 < len(entries)
                    and entries[i + 1].name == "CLOUDFLARE_ACCOUNT_ID"
                ):
                    # Cloudflare triple: key, account_id, flag
                    assert entries[i + 1].kind == "key"
                    assert entries[i + 1].backend == "Cloudflare"
                    assert entries[i + 2].kind == "flag"
                    assert entries[i + 2].backend == "Cloudflare"
                    seen_cloudflare_account_id = True
                    i += 3
                    continue
                # Standard pair: key, flag
                flag = entries[i + 1]
                assert flag.kind == "flag"
                assert e.backend == flag.backend
                i += 2
            else:
                pytest.fail(f"unexpected entry kind at index {i}: {e!r}")
        assert seen_cloudflare_account_id, (
            "Expected the CLOUDFLARE_ACCOUNT_ID entry to appear between "
            "the Cloudflare API key row and the Cloudflare FREE_ONLY flag row"
        )

    def test_all_eight_cloud_backends_present(self):
        """R07.27: Cloudflare added — 10 cloud backends now."""
        labels = {t[0] for t in AUTH_BACKENDS}
        assert labels == {
            "ZAI",
            "OpenRouter",
            "OrcaRouter",
            "Gemini",
            "HuggingFace",
            "OpenAI",
            "Mistral",
            "Pollinations",
            "NVIDIA",
            "Cloudflare",
        }

    def test_canonical_env_names_match_backend_resolution(self):
        names = {t[1] for t in AUTH_BACKENDS}
        assert names == {
            "ZAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ORCAROUTER_API_KEY",
            "GEMINI_API_KEY",
            "HF_TOKEN",
            "OPENAI_API_KEY",
            "MISTRAL_API_KEY",
            "POLLINATIONS_API_KEY",
            "NVIDIA_API_KEY",
            "CLOUDFLARE_API_KEY",
        }
        flags = {t[2] for t in AUTH_BACKENDS}
        assert flags == {
            "ZAI_FREE_ONLY",
            "OPENROUTER_FREE_ONLY",
            "ORCAROUTER_FREE_ONLY",
            "GEMINI_FREE_ONLY",
            "HF_FREE_ONLY",
            "OPENAI_FREE_ONLY",
            "MISTRAL_FREE_ONLY",
            "POLLINATIONS_FREE_ONLY",
            "NVIDIA_FREE_ONLY",
            "CLOUDFLARE_FREE_ONLY",
        }

    def test_gemini_and_hf_carry_alt_names(self):
        by_name = {e.name: e for e in auth_vars()}
        assert by_name["GEMINI_API_KEY"].alt_names == ("GOOGLE_API_KEY",)
        assert by_name["HF_TOKEN"].alt_names == ("HUGGING_FACE_HUB_TOKEN", "HF_API_KEY")
        assert by_name["ZAI_API_KEY"].alt_names == ()

    def test_slug_map_covers_every_key_var(self):
        assert set(_KEY_VAR_TO_SLUG) == {t[1] for t in AUTH_BACKENDS}
        assert _KEY_VAR_TO_SLUG["ZAI_API_KEY"] == "zai"
        assert _KEY_VAR_TO_SLUG["HF_TOKEN"] == "huggingface"
        assert _KEY_VAR_TO_SLUG["CLOUDFLARE_API_KEY"] == "cloudflare"

    def test_extra_auth_entries_includes_cloudflare_account_id(self):
        """R07.27: Cloudflare needs BOTH an API key AND an account ID.
        The account ID is registered as an extra 'key'-kind AuthVar so
        the /auth picker prompts for it between the Cloudflare API key
        row and the Cloudflare FREE_ONLY flag row."""
        from agentkthx.cli.auth import _EXTRA_AUTH_ENTRIES

        names = {e.name for e in _EXTRA_AUTH_ENTRIES}
        assert "CLOUDFLARE_ACCOUNT_ID" in names
        for e in _EXTRA_AUTH_ENTRIES:
            if e.name == "CLOUDFLARE_ACCOUNT_ID":
                assert e.kind == "key"
                assert e.backend == "Cloudflare"
                assert e.alt_names == ()

    def test_no_local_backends_in_registry(self):
        labels = " ".join(t[0] for t in AUTH_BACKENDS)
        assert "Ollama" not in labels
        assert "TurboQuant" not in labels
        assert "BitNet" not in labels


# ═══════════════════════════════════════════════════════════════════════
# 2. Display helpers
# ═══════════════════════════════════════════════════════════════════════


class TestDisplayHelpers:
    def test_mask_key_never_leaks(self):
        assert mask_key("") == "not set"
        assert mask_key("short") == "set (***)"
        assert mask_key("12345678") == "set (***)"
        long_key = "sk-proj-abcdefghij1234567890"
        masked = mask_key(long_key)
        assert masked == f"set (***{long_key[-4:]})"
        assert "sk-proj" not in masked

    def test_flag_state_truthiness(self, monkeypatch):
        monkeypatch.setenv("ZAI_FREE_ONLY", "1")
        assert flag_state("ZAI_FREE_ONLY") == "ON"
        monkeypatch.setenv("ZAI_FREE_ONLY", "true")
        assert flag_state("ZAI_FREE_ONLY") == "ON"
        monkeypatch.setenv("ZAI_FREE_ONLY", "")
        assert flag_state("ZAI_FREE_ONLY") == "off"
        monkeypatch.delenv("ZAI_FREE_ONLY", raising=False)
        assert flag_state("ZAI_FREE_ONLY") == "off"

    def test_menu_labels_reflect_live_state(self, monkeypatch):
        monkeypatch.setenv("ZAI_API_KEY", "sk-proj-abcdefghij1234567890")
        monkeypatch.setenv("ZAI_FREE_ONLY", "1")
        monkeypatch.setenv("GOOGLE_API_KEY", "google-fallback-key-123456")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        labels = _menu_labels(auth_vars())
        assert "ZAI_API_KEY" in labels[0]
        assert "set (***7890)" in labels[0]
        assert "[ON]" in labels[1]
        assert "not set" in labels[6]  # GEMINI_API_KEY row
        assert "via GOOGLE_API_KEY" in labels[6]
        assert "[off]" in labels[7]  # GEMINI_FREE_ONLY row

    def test_label_columns_aligned(self):
        labels = _menu_labels(auth_vars())
        for label in labels:
            # backend label column is 12 wide (longest: "Pollinations")
            assert label.index(" ") <= 12


# ═══════════════════════════════════════════════════════════════════════
# 3. Flag flip mechanics (env + config + plugin global rebinding)
# ═══════════════════════════════════════════════════════════════════════


class TestFlagFlip:
    def test_set_flag_updates_env_config_and_plugin_global(self):
        fake = _make_fake_plugin(_fake_plugin1, ZAI_FREE_ONLY=False)
        entry = AuthVar("ZAI_FREE_ONLY", "flag", "ZAI")
        outcome = set_flag(entry, True)
        assert outcome.startswith("ZAI_FREE_ONLY -> ON")
        assert os.environ["ZAI_FREE_ONLY"] == "1"
        assert config_module.ZAI_FREE_ONLY is True
        assert fake.ZAI_FREE_ONLY is True

    def test_set_flag_off_writes_zero(self):
        os.environ["ZAI_FREE_ONLY"] = "1"
        config_module.ZAI_FREE_ONLY = True
        fake = _make_fake_plugin(_fake_plugin2, ZAI_FREE_ONLY=True)
        assert set_flag(AuthVar("ZAI_FREE_ONLY", "flag", "ZAI"), False).startswith(
            "ZAI_FREE_ONLY -> OFF"
        )
        assert os.environ["ZAI_FREE_ONLY"] == "0"
        assert config_module.ZAI_FREE_ONLY is False
        assert fake.ZAI_FREE_ONLY is False

    def test_toggle_flips_current_state(self, monkeypatch):
        monkeypatch.delenv("MISTRAL_FREE_ONLY", raising=False)
        entry = AuthVar("MISTRAL_FREE_ONLY", "flag", "Mistral")
        assert toggle_flag(entry).startswith("MISTRAL_FREE_ONLY -> ON")
        assert toggle_flag(entry).startswith("MISTRAL_FREE_ONLY -> OFF")
        assert toggle_flag(entry).startswith("MISTRAL_FREE_ONLY -> ON")

    def test_toggle_respects_preset_truthy_values(self, monkeypatch):
        monkeypatch.setenv("OPENAI_FREE_ONLY", "yes")
        assert toggle_flag(AuthVar("OPENAI_FREE_ONLY", "flag", "OpenAI")).startswith(
            "OPENAI_FREE_ONLY -> OFF"
        )

    def test_toggle_does_not_rebind_unrelated_globals(self):
        fake = _make_fake_plugin(_fake_plugin3, UNRELATED=True)
        toggle_flag(AuthVar("HF_FREE_ONLY", "flag", "HuggingFace"))
        assert fake.UNRELATED is True  # untouched
        assert not hasattr(fake, "HF_FREE_ONLY")  # absent before → not injected


# ═══════════════════════════════════════════════════════════════════════
# 4. Key set/clear mechanics
# ═══════════════════════════════════════════════════════════════════════


class TestKeySet:
    ENTRY = AuthVar("ZAI_API_KEY", "key", "ZAI")

    def test_set_key_updates_env_config_and_plugins(self):
        fake = _make_fake_plugin(_fake_plugin4, ZAI_API_KEY="")
        key = "sk-zai-real-key-1234567890abcdef"
        outcome = set_key(self.ENTRY, key)
        assert mask_key(key) in outcome
        assert os.environ["ZAI_API_KEY"] == key
        assert config_module.ZAI_API_KEY == key
        assert fake.ZAI_API_KEY == key

    def test_set_key_masks_value_in_outcome(self):
        key = "sk-proj-supersecretvalue-1234567890"
        outcome = set_key(self.ENTRY, key)
        assert key not in outcome
        assert key[-4:] in outcome

    def test_clear_key_pops_env_and_rebinds_empty(self):
        os.environ["ZAI_API_KEY"] = "sk-zai-real-key-1234567890abcdef"
        config_module.ZAI_API_KEY = "sk-zai-real-key-1234567890abcdef"
        fake = _make_fake_plugin(_fake_plugin5, ZAI_API_KEY="old")
        outcome = set_key(self.ENTRY, "")
        assert outcome.startswith("ZAI_API_KEY cleared")
        assert "ZAI_API_KEY" not in os.environ
        assert config_module.ZAI_API_KEY == ""
        assert fake.ZAI_API_KEY == ""

    def test_short_key_warning(self):
        outcome = set_key(self.ENTRY, "abc123")
        assert "looks short" in outcome
        assert os.environ["ZAI_API_KEY"] == "abc123"

    def test_long_key_no_warning(self):
        assert "looks short" not in set_key(self.ENTRY, "a" * 24)

    def test_live_backend_patched_by_declared_env_var(self):
        backend = FakeBackend("zai", env_var="ZAI_API_KEY", _api_key="old", api_key="old")
        agent = FakeAgent(backend)
        outcome = set_key(self.ENTRY, "sk-zai-real-key-1234567890abcdef", agent)
        assert "live backend patched" in outcome
        assert backend._api_key == "sk-zai-real-key-1234567890abcdef"
        assert backend.api_key == "sk-zai-real-key-1234567890abcdef"

    def test_live_backend_patched_by_slug_when_no_declared_var(self):
        # Gemini / OpenAI / OpenRouter / HF resolve keys in custom __init__
        backend = FakeBackend("gemini", api_key="old")
        outcome = set_key(
            AuthVar("GEMINI_API_KEY", "key", "Gemini"),
            "g-key-12345678901234567890",
            FakeAgent(backend),
        )
        assert "live backend patched" in outcome
        assert backend.api_key == "g-key-12345678901234567890"

    def test_non_matching_backend_untouched(self):
        backend = FakeBackend("ollama", _api_key="local", api_key="local")
        outcome = set_key(self.ENTRY, "sk-zai-real-key-1234567890abcdef", FakeAgent(backend))
        assert "live backend patched" not in outcome
        assert backend._api_key == "local"

    def test_no_agent_is_fine(self):
        outcome = set_key(self.ENTRY, "sk-zai-real-key-1234567890abcdef", None)
        assert "live backend patched" not in outcome
        assert os.environ["ZAI_API_KEY"] == "sk-zai-real-key-1234567890abcdef"

    def test_flag_kind_never_patches_backend(self):
        backend = FakeBackend("zai", _api_key="keep")
        outcome = set_key(AuthVar("ZAI_FREE_ONLY", "flag", "ZAI"), "1", FakeAgent(backend))
        assert "live backend patched" not in outcome
        assert backend._api_key == "keep"


# ═══════════════════════════════════════════════════════════════════════
# 5. prompt_secret
# ═══════════════════════════════════════════════════════════════════════


class TestPromptSecret:
    def test_tty_path_uses_getpass(self, monkeypatch):
        fake_stdin = io.StringIO("")
        fake_stdin.isatty = lambda: True  # type: ignore[method-assign]
        monkeypatch.setattr(auth_mod.getpass, "getpass", lambda p: "sk-hidden-key-1234567890")
        assert prompt_secret("ZAI_API_KEY", stdin=fake_stdin) == "sk-hidden-key-1234567890"

    def test_piped_path_reads_stdin_and_prompts(self):
        stdin = io.StringIO("sk-piped-key-1234567890\n")
        out = io.StringIO()
        assert prompt_secret("ZAI_API_KEY", stdin=stdin, stdout=out) == "sk-piped-key-1234567890"
        assert "Enter ZAI_API_KEY" in out.getvalue()

    def test_blank_means_keep(self):
        assert prompt_secret("ZAI_API_KEY", stdin=io.StringIO("\n"), stdout=io.StringIO()) is None

    def test_eof_means_keep(self):
        assert prompt_secret("ZAI_API_KEY", stdin=io.StringIO(""), stdout=io.StringIO()) is None

    def test_dash_is_clear_sentinel(self):
        assert prompt_secret("ZAI_API_KEY", stdin=io.StringIO("-\n"), stdout=io.StringIO()) == "-"

    def test_whitespace_stripped(self):
        assert (
            prompt_secret(
                "ZAI_API_KEY",
                stdin=io.StringIO("  sk-x-1234567890123456  \n"),
                stdout=io.StringIO(),
            )
            == "sk-x-1234567890123456"
        )


# ═══════════════════════════════════════════════════════════════════════
# 6. run_auth_picker end-to-end (non-TTY numbered fallback)
# ═══════════════════════════════════════════════════════════════════════


class TestRunAuthPicker:
    def _run(self, lines: str, agent=None) -> str:
        stdin = io.StringIO(lines)
        out = io.StringIO()
        with redirect_stdout(out):
            run_auth_picker(agent, stdin=stdin, stdout=out)
        return out.getvalue()

    def test_flip_flag_then_set_key_then_close(self):
        output = self._run("2\n1\nsk-zai-real-key-1234567890abcdef\n\n")
        # Row 2 = ZAI_FREE_ONLY (flipped ON), row 1 = ZAI_API_KEY (set), blank closes
        assert os.environ["ZAI_FREE_ONLY"] == "1"
        assert config_module.ZAI_FREE_ONLY is True
        assert os.environ["ZAI_API_KEY"] == "sk-zai-real-key-1234567890abcdef"
        assert "ZAI_FREE_ONLY -> ON" in output
        assert "ZAI_API_KEY set" in output
        assert "Auth menu closed" in output

    def test_blank_first_line_closes_immediately(self):
        output = self._run("\n")
        assert "Auth menu closed" in output
        assert "->" not in output

    def test_out_of_range_then_cancel(self):
        output = self._run("99\n\n")
        assert "Out of range" in output
        assert "Auth menu closed" in output

    def test_set_then_clear_key_via_dash(self):
        output = self._run("1\nsk-zai-real-key-1234567890abcdef\n1\n-\n\n")
        assert "ZAI_API_KEY" not in os.environ
        assert config_module.ZAI_API_KEY == ""
        assert "ZAI_API_KEY cleared" in output

    def test_blank_at_key_prompt_keeps_current(self):
        output = self._run("1\n\n\n")
        assert "ZAI_API_KEY unchanged" in output
        assert "ZAI_API_KEY" not in os.environ

    def test_menu_reflects_flip_on_re_render(self):
        output = self._run("2\n\n")
        # After the flip the menu re-rendered with [ON] before closing
        assert "[ON]" in output

    def test_key_rows_mask_values_in_menu(self):
        os.environ["MISTRAL_API_KEY"] = "sk-mistral-key-1234567890abcd"
        output = self._run("\n")
        assert "sk-mistral-key-1234567890abcd" not in output
        assert "***abcd" in output

    def test_live_agent_flows_through(self):
        backend = FakeBackend("zai", env_var="ZAI_API_KEY", _api_key="old")
        agent = FakeAgent(backend)
        output = self._run("1\nsk-zai-real-key-1234567890abcdef\n\n", agent=agent)
        assert backend._api_key == "sk-zai-real-key-1234567890abcdef"
        assert "live backend patched" in output

    def test_close_reminder_names_env_file(self):
        output = self._run("\n")
        assert "Saved to" in output
        assert str(env_file_path()) in output
        assert "shell exports still take precedence" in output


# ═══════════════════════════════════════════════════════════════════════
# 7. cmd_chat wiring pins + import contract
# ═══════════════════════════════════════════════════════════════════════

CHAT_PATH = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
AUTH_PATH = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "auth.py"


class TestWiringPins:
    @pytest.fixture(scope="class")
    def chat_source(self):
        return CHAT_PATH.read_text(encoding="utf-8")

    def test_auth_branch_dispatches_picker(self, chat_source):
        assert 'if user_input == "/auth":' in chat_source
        assert "run_auth_picker(agent)" in chat_source

    def test_auth_branch_runs_before_help(self, chat_source):
        auth_pos = chat_source.index('if user_input == "/auth":')
        help_pos = chat_source.index('if user_input == "/help":')
        assert auth_pos < help_pos

    def test_help_lists_auth(self, chat_source):
        assert "/auth" in chat_source
        assert "FREE_ONLY" in chat_source.split("/auth")[1][:200]

    def test_auth_module_stdlib_only(self):
        """No third-party imports in the auth module (zero-dep contract)."""
        import ast

        tree = ast.parse(AUTH_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        third_party = imported - {
            "__future__",
            "dataclasses",
            "getpass",
            "os",
            "sys",
            "agentkthx",  # relative re-exports resolve inside the package
        }
        assert not third_party, f"unexpected imports: {third_party}"

    def test_registry_backed_by_config_constants(self):
        """Every registry var must exist as an agentkthx.config attribute."""
        for name in _ALL_VARS:
            assert hasattr(config_module, name), f"agentkthx.config.{name} missing"


# fake-plugin module-name constants (used above; keep at bottom for readability)
_fake_plugin1 = "agentkthx.tests._fake_plugin1"
_fake_plugin2 = "agentkthx.tests._fake_plugin2"
_fake_plugin3 = "agentkthx.tests._fake_plugin3"
_fake_plugin4 = "agentkthx.tests._fake_plugin4"
_fake_plugin5 = "agentkthx.tests._fake_plugin5"


# ═══════════════════════════════════════════════════════════════════════
# 8. agentkthx/env_file.py — parse / upsert / remove / clobber-protection
# ═══════════════════════════════════════════════════════════════════════


class TestEnvFile:
    def test_path_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_ENV_FILE", str(tmp_path / "custom.env"))
        assert env_file_path() == tmp_path / "custom.env"

    def test_load_missing_file_is_noop(self, tmp_path):
        applied = load_env_file(tmp_path / "nope.env")
        assert applied == {}

    def test_load_parses_comments_quotes_blanks(self, tmp_path):
        f = tmp_path / "env"
        f.write_text(
            "# comment\n\n"
            "ZAI_API_KEY=sk-zai-real-key-1234567890abcdef\n"
            'OPENAI_API_KEY="sk-openai-quoted-key-1234567890"\n'
            "MISTRAL_API_KEY='sk-mistral-quoted-1234567890'\n"
            "invalid line without equals\n"
            "=novalue\n",
            encoding="utf-8",
        )
        applied = load_env_file(f)
        assert applied["ZAI_API_KEY"] == "sk-zai-real-key-1234567890abcdef"
        assert applied["OPENAI_API_KEY"] == "sk-openai-quoted-key-1234567890"
        assert applied["MISTRAL_API_KEY"] == "sk-mistral-quoted-1234567890"
        assert len(applied) == 3

    def test_load_never_clobbers_live_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZAI_API_KEY", "from-shell")
        f = tmp_path / "env"
        f.write_text("ZAI_API_KEY=sk-zai-real-key-1234567890abcdef\n", encoding="utf-8")
        applied = load_env_file(f)
        assert "ZAI_API_KEY" not in applied
        assert os.environ["ZAI_API_KEY"] == "from-shell"

    def test_save_upserts_and_removes(self, tmp_path):
        f = tmp_path / "env"
        save_env_var("ZAI_API_KEY", "first-key-1234567890", path=f)
        assert f.read_text().strip() == "ZAI_API_KEY=first-key-1234567890"
        save_env_var("ZAI_API_KEY", "second-key-1234567890", path=f)
        assert f.read_text().strip() == "ZAI_API_KEY=second-key-1234567890"
        assert remove_env_var("ZAI_API_KEY", path=f) is True
        assert f.read_text() == ""
        assert remove_env_var("ZAI_API_KEY", path=f) is False

    def test_save_creates_parents_and_is_0600(self, tmp_path):
        f = tmp_path / "deep" / "dir" / "env"
        save_env_var("ZAI_API_KEY", "x" * 24, path=f)
        assert f.exists()
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode == 0o600

    def test_save_keeps_other_lines(self, tmp_path):
        f = tmp_path / "env"
        f.write_text("# my keys\nHF_TOKEN=a-token-value-1234567890\n", encoding="utf-8")
        save_env_var("ZAI_API_KEY", "sk-zai-real-key-1234567890abcdef", path=f)
        text = f.read_text()
        assert "# my keys" in text
        assert "HF_TOKEN=a-token-value-1234567890" in text
        assert "ZAI_API_KEY=sk-zai-real-key-1234567890abcdef" in text


# ═══════════════════════════════════════════════════════════════════════
# 9. Persistence — set_key / set_flag write the env file
# ═══════════════════════════════════════════════════════════════════════


class TestAuthPersistence:
    def test_set_key_persists_to_env_file(self):
        set_key(AuthVar("ZAI_API_KEY", "key", "ZAI"), "sk-zai-real-key-1234567890abcdef")
        content = env_file_path().read_text()
        assert "ZAI_API_KEY=sk-zai-real-key-1234567890abcdef" in content
        # the raw key is in the FILE (that's the point) but never in stdout

    def test_clear_key_removes_env_file_line(self):
        save_env_var("ZAI_API_KEY", "sk-zai-real-key-1234567890abcdef")
        outcome = set_key(AuthVar("ZAI_API_KEY", "key", "ZAI"), "")
        assert "cleared" in outcome
        assert "ZAI_API_KEY" not in env_file_path().read_text()

    def test_flag_on_persists_one_flag_off_removes(self):
        entry = AuthVar("MISTRAL_FREE_ONLY", "flag", "Mistral")
        set_flag(entry, True)
        assert "MISTRAL_FREE_ONLY=1" in env_file_path().read_text()
        set_flag(entry, False)
        assert "MISTRAL_FREE_ONLY" not in env_file_path().read_text()

    def test_outcome_reports_env_file_path(self):
        outcome = set_key(AuthVar("ZAI_API_KEY", "key", "ZAI"), "sk-zai-real-key-1234567890abcdef")
        assert "[saved to" in outcome
        assert str(env_file_path()) in outcome

    def test_unwritable_env_file_degrades_to_warning(self, monkeypatch, tmp_path):
        # Make the env-file PARENT a regular file → mkdir must fail
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setenv("AGENTKTHX_ENV_FILE", str(blocker / "nested.env"))
        outcome = set_key(AuthVar("ZAI_API_KEY", "key", "ZAI"), "sk-zai-real-key-1234567890abcdef")
        assert "env file not written" in outcome
        assert os.environ["ZAI_API_KEY"] == "sk-zai-real-key-1234567890abcdef"  # live anyway

    def test_config_constant_loads_env_file_at_import(self, tmp_path, monkeypatch):
        """The contract that makes /auth persistence work across processes."""
        f = tmp_path / "env"
        f.write_text("AGENTKTHX_TEST_MARKER=sk-zai-real-key-1234567890abcdef\n", encoding="utf-8")
        monkeypatch.delenv("AGENTKTHX_TEST_MARKER", raising=False)
        load_env_file(f)
        assert os.environ["AGENTKTHX_TEST_MARKER"] == "sk-zai-real-key-1234567890abcdef"


# ═══════════════════════════════════════════════════════════════════════
# 10. agentkthx config — one-line-per-backend Cloud Backends listing
# ═══════════════════════════════════════════════════════════════════════


class TestConfigBackendRows:
    def _rows(self, active=""):
        from agentkthx.cli.commands.config import _backend_auth_rows

        return _backend_auth_rows(active)

    @pytest.fixture(scope="class")
    def rows_ansi(self):
        from agentkthx.cli.commands.config import _backend_auth_rows

        return _backend_auth_rows("")

    def _strip(self, s):
        import re

        return re.compile(r"\x1b\[[0-9;]*m").sub("", s)

    def test_eight_rows_all_cloud_backends(self, rows_ansi):
        """R07.27: Cloudflare added — 10 rows now. Historical name retained."""
        labels = [r[1] for r in rows_ansi]
        assert labels == [
            "ZAI",
            "OpenRouter",
            "OrcaRouter",
            "Gemini",
            "HuggingFace",
            "OpenAI",
            "Mistral",
            "Pollinations",
            "NVIDIA",
            "Cloudflare",
        ]

    def test_key_display_masked_or_not_set(self, rows_ansi):
        """Every key is either Not Set or masked as Set (***<last4>).

        Two valid mask shapes:
          - ``Set (***<4chars>)`` — long-key path (key > 8 chars; last 4 shown)
          - ``Set (***)``         — short-key path (key ≤ 8 chars; no last4)

        The short-key path matters because some prior tests (e.g.
        test_api_resilience.py:381) set ``OPENROUTER_API_KEY=test-key``
        without monkeypatch cleanup, leaking 8-char values into the env.
        The mask itself is correct in both cases — only the test's
        tail-length assertion was too strict for the short-key case.
        """
        for marker, label, key_s, free_s, fb in rows_ansi:
            plain = self._strip(key_s)
            assert plain == "Not Set" or plain.startswith("Set (***")
            if plain.startswith("Set (***"):
                # Two valid shapes:
                #   "Set (***abcd)" — long key, last 4 chars shown (len 13)
                #   "Set (***)"      — short key (≤ 8 chars), no last4 (len 9)
                if plain == "Set (***)":
                    # Short-key path — no last4 to verify. Already confirmed
                    # by the startswith check above; nothing else to assert.
                    continue
                # Long-key path: verify exactly 4 chars of last-4 tail.
                tail = plain[len("Set (***") : -1]
                assert len(tail) == 4, (
                    f"{label}: masked display {plain!r} — expected 4-char "
                    f"tail, got {tail!r} (len {len(tail)})"
                )

    def test_masked_key_shows_last4_only(self, monkeypatch):
        monkeypatch.setenv("ZAI_API_KEY", "sk-zai-test-key-1234567890abcd")
        monkeypatch.setattr(config_module, "ZAI_API_KEY", "sk-zai-test-key-1234567890abcd")
        rows = self._rows()
        zai_row = next(r for r in rows if r[1] == "ZAI")
        assert self._strip(zai_row[2]) == "Set (***abcd)"

    def test_free_only_display(self, rows_ansi):
        free_states = [self._strip(r[3]) for r in rows_ansi]
        assert all(s in ("ON", "off") for s in free_states)

    def test_free_only_on_reflected(self, monkeypatch):
        monkeypatch.setenv("OPENAI_FREE_ONLY", "1")
        monkeypatch.setattr(config_module, "OPENAI_FREE_ONLY", True)
        rows = self._rows()
        openai_row = next(r for r in rows if r[1] == "OpenAI")
        assert self._strip(openai_row[3]) == "ON"

    def test_fallback_models(self, rows_ansi):
        fallbacks = {r[1]: self._strip(r[4]) for r in rows_ansi}
        assert fallbacks["ZAI"] == "glm-4.5-flash"
        assert fallbacks["OpenRouter"] == "—"
        assert fallbacks["Gemini"] == "—"
        assert fallbacks["OrcaRouter"] == "orcarouter/free"
        assert fallbacks["OpenAI"] == "gpt-4o-mini"
        assert fallbacks["Pollinations"] == "z-ai/glm-5.3-flash"

    def test_active_backend_marker(self):
        rows = self._rows("zai")
        zai_row = next(r for r in rows if r[1] == "ZAI")
        other_row = next(r for r in rows if r[1] == "OpenAI")
        assert zai_row[0] == " *"
        assert other_row[0] == "  "

    def test_hf_slug_maps_to_huggingface_row(self):
        rows = self._rows("hf")
        hf_row = next(r for r in rows if r[1] == "HuggingFace")
        assert hf_row[0] == " *"

    def test_local_backend_active_marks_nothing(self):
        rows = self._rows("ollama")
        assert all(r[0] == "  " for r in rows)

    def test_summary_renders_cloud_backends_block(self):
        """cmd_config default view carries the new listing end-to-end."""
        import argparse

        from agentkthx.cli.commands.config import cmd_config

        out = io.StringIO()
        with redirect_stdout(out):
            cmd_config(argparse.Namespace(full=False, urls=False))
        text = self._strip(out.getvalue())
        assert "Cloud Backends" in text
        for label in (
            "ZAI",
            "OpenRouter",
            "OrcaRouter",
            "Gemini",
            "HuggingFace",
            "OpenAI",
            "Mistral",
            "Pollinations",
        ):
            assert label in text
        assert "fallback" in text
        # the old verbose sections are gone (env-ref "ZAI API URL" may remain)
        assert not re.search(r"^  ZAI API$", text, re.M)
        assert not re.search(r"^  OpenRouter API$", text, re.M)

    def test_env_reference_documents_env_file(self):
        import argparse

        from agentkthx.cli.commands.config import cmd_config

        out = io.StringIO()
        with redirect_stdout(out):
            cmd_config(argparse.Namespace(full=False, urls=False))
        assert "AGENTKTHX_ENV_FILE" in self._strip(out.getvalue())
