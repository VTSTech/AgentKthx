"""
R07.05 regression tests for 6 audit findings:
  SEC-07: SQLite DB file permissions (0600) + parent dir (0700)
  ROB-03: threading.Lock on PersistentMemory writes
  ROB-04: Agent.register_tool doesn't clear memory (split from add_tool)
  MAINT-04: args_normal.py deleted (dead code)
  MAINT-05: cli/utils.py dead-code trio deleted (_load_tool_cache etc.)
  MAINT-06: model_config.py deleted (deprecated re-export)
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core.persistent_memory import PersistentMemory, _get_db_path, _DEFAULT_DB_DIR


# ---------------------------------------------------------------------------
# SEC-07: File permissions on SQLite DB + parent dir
# ---------------------------------------------------------------------------

class TestSec07DbFilePermissions:
    """Verify the SQLite DB file and parent directory have restrictive permissions."""

    def test_db_file_chmod_0600(self, tmp_path):
        """The DB file is chmod'd to 0o600 (owner-only read/write) after creation."""
        db_path = str(tmp_path / "test_memory.db")
        pm = PersistentMemory(db_path=db_path, auto_save=True)
        # Trigger connection + table creation
        pm.add("user", "test message")
        # The file should exist now
        assert os.path.exists(db_path)
        # Check permissions — on Linux, stat.st_mode & 0o777 gives the permission bits
        mode = os.stat(db_path).st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_db_dir_chmod_0700_default_path(self, monkeypatch):
        """The default ~/.agentkthx/ directory is created with mode 0o700."""
        # Use a temp dir as the "home" so we don't touch the real ~/.agentkthx
        fake_home = tempfile.mkdtemp()
        monkeypatch.setenv("HOME", fake_home)
        # Re-import to pick up the new HOME (module-level _DEFAULT_DB_DIR is set at import time)
        # Actually, _DEFAULT_DB_DIR is computed at module load. Let's call _get_db_path
        # which uses the module-level _DEFAULT_DB_DIR. We need to patch it.
        import agentkthx.core.persistent_memory as pm_mod
        monkeypatch.setattr(pm_mod, "_DEFAULT_DB_DIR", os.path.join(fake_home, ".agentkthx"))

        path = _get_db_path()
        assert path == os.path.join(fake_home, ".agentkthx", "memory.db")
        # Directory should exist with 0o700
        assert os.path.isdir(os.path.join(fake_home, ".agentkthx"))
        mode = os.stat(os.path.join(fake_home, ".agentkthx")).st_mode & 0o777
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    def test_db_file_not_world_readable(self, tmp_path):
        """The DB file must NOT be world-readable (no 0o004 bit)."""
        db_path = str(tmp_path / "test_memory.db")
        pm = PersistentMemory(db_path=db_path, auto_save=True)
        pm.add("user", "secret API key: sk-abc123")
        mode = os.stat(db_path).st_mode
        # No world-read (0o004), no group-read (0o040), no world-write (0o002)
        assert not (mode & 0o004), "DB file is world-readable (SEC-07 violation)"
        assert not (mode & 0o040), "DB file is group-readable (SEC-07 violation)"
        assert not (mode & 0o002), "DB file is world-writable"


# ---------------------------------------------------------------------------
# ROB-03: threading.Lock on PersistentMemory writes
# ---------------------------------------------------------------------------

class TestRob03ThreadSafeWrites:
    """Verify PersistentMemory writes are thread-safe via _write_lock."""

    def test_write_lock_exists(self, tmp_path):
        """PersistentMemory has a _write_lock attribute (threading.Lock)."""
        pm = PersistentMemory(db_path=str(tmp_path / "test.db"))
        assert hasattr(pm, "_write_lock")
        assert isinstance(pm._write_lock, type(threading.Lock()))

    def test_concurrent_writes_no_error(self, tmp_path):
        """Concurrent writes from multiple threads don't raise OperationalError.

        Before ROB-03, concurrent execute() calls from multiple threads
        could trip sqlite3.OperationalError: database is locked. The
        _write_lock serializes writes so this doesn't happen.
        """
        db_path = str(tmp_path / "concurrent.db")
        # Use a large max_messages so the sliding window doesn't prune
        # (the in-memory list prunes at ~max_messages, but the DB keeps all)
        from agentkthx.core.memory import MemoryConfig
        pm = PersistentMemory(
            db_path=db_path,
            auto_save=True,
            config=MemoryConfig(max_messages=1000),
        )
        errors = []

        def writer(thread_id):
            try:
                for i in range(20):
                    pm.add("user", f"thread-{thread_id}-msg-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent writes raised errors: {errors}"
        # All 80 messages should be in memory (max_messages=1000 prevents pruning)
        assert len(pm._messages) == 80, (
            f"Expected 80 messages, got {len(pm._messages)}"
        )
        # Verify all 80 also made it to the DB
        pm.save()
        import sqlite3
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (pm._session_id,),
        ).fetchone()[0]
        conn.close()
        assert count == 80, f"DB has {count} messages, expected 80"

    def test_write_lock_is_reentrant_safe(self, tmp_path):
        """The write lock is a regular Lock (not RLock) — verify _touch_session
        doesn't deadlock when called from within _write_message's lock.

        _write_message acquires the lock, calls commit(), returns, releases.
        Then add() calls _touch_session which acquires the lock again.
        These are sequential, not nested, so no deadlock.
        """
        db_path = str(tmp_path / "reentrant.db")
        pm = PersistentMemory(db_path=db_path, auto_save=True)
        # This should not deadlock
        pm.add("user", "message 1")
        pm.add("user", "message 2")
        pm.add_tool_result("call_1", "calculator", "42")
        assert len(pm._messages) >= 3


# ---------------------------------------------------------------------------
# ROB-04: Agent.register_tool doesn't clear memory
# ---------------------------------------------------------------------------

class TestRob04RegisterToolPreservesMemory:
    """Verify register_tool adds a tool WITHOUT clearing conversation memory."""

    def test_register_tool_exists(self):
        """Agent has a register_tool method (the new, non-destructive API)."""
        from agentkthx.agent import Agent
        assert hasattr(Agent, "register_tool")
        assert callable(getattr(Agent, "register_tool"))

    def test_rebuild_system_prompt_exists(self):
        """Agent has a rebuild_system_prompt method (explicit clear+rebuild)."""
        from agentkthx.agent import Agent
        assert hasattr(Agent, "rebuild_system_prompt")
        assert callable(getattr(Agent, "rebuild_system_prompt"))

    def test_register_tool_preserves_memory(self, monkeypatch):
        """register_tool adds a tool but does NOT clear conversation history.

        This is the key fix for ROB-04: the old add_tool() destroyed
        all conversation history when called mid-session.
        """
        # Mock the backend to avoid needing a real LLM
        from unittest.mock import MagicMock
        from agentkthx.agent import Agent
        from agentkthx.core.models import Tool, ToolParam
        from agentkthx.tools import make_builtin_registry

        mock_backend = MagicMock()
        mock_backend.backend_type = MagicMock()
        mock_backend.backend_type.value = "test"
        mock_backend.api_mode = MagicMock()
        mock_backend.api_mode.value = "openai"
        mock_backend.is_cloud = False
        mock_backend.config = MagicMock(timeout=120)

        tools = make_builtin_registry().subset(["calculator"])
        agent = Agent(model="test-model", tools=tools, backend=mock_backend)

        # Add some conversation history
        agent.memory.add("user", "What is 2+2?")
        agent.memory.add("assistant", "4")
        assert len(agent.memory) >= 3  # system + user + assistant

        # Create a new tool and register it
        new_tool = Tool(
            name="test_echo",
            description="Echo back the input",
            params=[ToolParam(name="text", type="string", description="Text to echo")],
            handler=lambda text="": text,
        )

        # Register the tool — should NOT clear memory
        agent.register_tool(new_tool)

        # Memory should still have the conversation
        assert len(agent.memory) >= 3, (
            "register_tool cleared conversation memory (ROB-04 regression). "
            f"Memory has {len(agent.memory)} messages, expected >= 3."
        )
        # The new tool should be in the registry
        assert "test_echo" in agent.tools.names()

    def test_add_tool_still_clears_for_backward_compat(self, monkeypatch):
        """add_tool (deprecated) still clears memory for backward compatibility.

        Existing code that relied on the clear-on-add behavior continues
        to work. A future release will make add_tool an alias for register_tool.
        """
        from unittest.mock import MagicMock
        from agentkthx.agent import Agent
        from agentkthx.core.models import Tool, ToolParam
        from agentkthx.tools import make_builtin_registry

        mock_backend = MagicMock()
        mock_backend.backend_type = MagicMock()
        mock_backend.backend_type.value = "test"
        mock_backend.api_mode = MagicMock()
        mock_backend.api_mode.value = "openai"
        mock_backend.is_cloud = False
        mock_backend.config = MagicMock(timeout=120)

        tools = make_builtin_registry().subset(["calculator"])
        agent = Agent(model="test-model", tools=tools, backend=mock_backend)

        agent.memory.add("user", "What is 2+2?")
        agent.memory.add("assistant", "4")
        assert len(agent.memory) >= 3

        new_tool = Tool(
            name="test_echo",
            description="Echo",
            params=[ToolParam(name="text", type="string")],
            handler=lambda text="": text,
        )

        # add_tool (deprecated) — should clear memory (backward compat)
        agent.add_tool(new_tool)

        # Memory should have been cleared + system prompt re-added
        # So only 1 message (the system prompt) should remain
        assert len(agent.memory) == 1, (
            f"add_tool should have cleared memory for backward compat, "
            f"but {len(agent.memory)} messages remain"
        )


# ---------------------------------------------------------------------------
# MAINT-04: args_normal.py deleted
# ---------------------------------------------------------------------------

class TestMaint04ArgsNormalDeleted:
    """Verify args_normal.py is deleted and no longer importable."""

    def test_args_normal_module_deleted(self):
        """The args_normal module no longer exists."""
        import importlib
        try:
            importlib.import_module("agentkthx.core.args_normal")
            assert False, "agentkthx.core.args_normal should have been deleted (MAINT-04)"
        except ModuleNotFoundError:
            pass  # expected

    def test_args_normal_file_deleted(self):
        """The args_normal.py file no longer exists on disk."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "core" / "args_normal.py"
        assert not path.exists(), f"{path} should have been deleted (MAINT-04)"

    def test_core_init_no_longer_imports_args_normal(self):
        """core/__init__.py no longer imports from args_normal."""
        import agentkthx.core as core_mod
        # The 4 symbols that were re-exported from args_normal should no longer
        # be accessible via the core package
        for symbol in ("normalize_args_full", "fix_calculator_args",
                       "synthesize_missing_args", "generate_helpful_error_message"):
            assert not hasattr(core_mod, symbol), (
                f"core module still exports {symbol!r} — args_normal import "
                f"not fully cleaned up (MAINT-04)"
            )

    def test_core_init_still_exports_normalize_args(self):
        """core.normalize_args (from helpers.py) is still exported — the
        production code path is unaffected by the args_normal deletion."""
        import agentkthx.core as core_mod
        assert hasattr(core_mod, "normalize_args")
        assert callable(core_mod.normalize_args)


# ---------------------------------------------------------------------------
# MAINT-05: cli/utils.py dead-code trio deleted
# ---------------------------------------------------------------------------

class TestMaint05CliUtilsDeadCodeDeleted:
    """Verify _load_tool_cache, _save_tool_cache, _get_cloud_model_size are gone."""

    def test_dead_functions_removed_from_utils(self):
        """The 3 dead functions are no longer defined in cli/utils.py."""
        from agentkthx.cli import utils
        for name in ("_load_tool_cache", "_save_tool_cache", "_get_cloud_model_size"):
            assert not hasattr(utils, name), (
                f"cli/utils.py still defines {name!r} (MAINT-05 not complete)"
            )

    def test_dead_functions_not_exported_from_cli_init(self):
        """The 3 dead functions are no longer in cli.__all__."""
        import agentkthx.cli as cli_mod
        for name in ("_load_tool_cache", "_save_tool_cache", "_get_cloud_model_size"):
            assert name not in getattr(cli_mod, "__all__", []), (
                f"cli.__all__ still contains {name!r} (MAINT-05 not complete)"
            )

    def test_cli_utils_live_functions_still_present(self):
        """The live functions in cli/utils.py are still present."""
        from agentkthx.cli import utils
        # These should still exist
        assert hasattr(utils, "resolve_model_pattern")
        assert hasattr(utils, "_get_cache_dir")
        assert hasattr(utils, "_tool_status")
        assert hasattr(utils, "_is_externally_managed_error")


# ---------------------------------------------------------------------------
# MAINT-06: model_config.py deleted
# ---------------------------------------------------------------------------

class TestMaint06ModelConfigDeleted:
    """Verify model_config.py is deleted and no longer importable."""

    def test_model_config_module_deleted(self):
        """The model_config module no longer exists."""
        import importlib
        try:
            importlib.import_module("agentkthx.core.model_config")
            assert False, "agentkthx.core.model_config should have been deleted (MAINT-06)"
        except ModuleNotFoundError:
            pass  # expected

    def test_model_config_file_deleted(self):
        """The model_config.py file no longer exists on disk."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "core" / "model_config.py"
        assert not path.exists(), f"{path} should have been deleted (MAINT-06)"

    def test_model_family_config_still_importable(self):
        """The canonical model_family_config module is unaffected."""
        from agentkthx.core.model_family_config import (
            ModelFamilyConfig, get_model_config, FAMILY_CONFIGS
        )
        assert ModelFamilyConfig is not None
        assert callable(get_model_config)
        assert isinstance(FAMILY_CONFIGS, dict)
