"""
AgentNova — R04.6 Quick Win Tests

Unit tests for the three non-trivial R04.6 changes:
  1. check_compatibility() zero-dep tuple comparison (skills/loader.py)
  2. TurboState schema versioning (turbo.py)

Written by VTSTech — https://www.vts-tech.org
"""

import json
import os
import pytest
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List, Any


# ============================================================================
# check_compatibility() Zero-Dependency Tests
# ============================================================================


class TestCheckCompatibilityZeroDep:
    """Verify that check_compatibility uses tuple comparison, not packaging.version."""

    def _make_skill(self, compatibility: str):
        """Create a Skill with a given compatibility string.
        
        Bypasses __post_init__ validation to directly set _compatibility_parsed,
        which is what check_compatibility reads from.
        """
        from agentkthx.skills.loader import Skill, parse_compatibility
        skill = Skill.__new__(Skill)
        skill.name = "test-skill"
        skill.description = "test"
        skill.instructions = "test"
        skill.path = Path("/tmp")
        skill.license = None
        skill.compatibility = compatibility
        skill.metadata = {}
        skill.allowed_tools = []
        skill._license_valid = True
        skill._license_warning = ""
        # Manually parse compatibility to avoid Skill.__post_init__ re-parsing
        skill._compatibility_parsed = parse_compatibility(compatibility) if compatibility else {
            "python": None, "runtimes": [], "frameworks": [], "raw": ""
        }
        return skill

    def test_python_version_gte_passes(self):
        """Python 3.10 >= required 3.8 should pass."""
        skill = self._make_skill("python>=3.8")
        ok, warnings = skill.check_compatibility(python_version="3.10")
        assert ok is True
        assert len(warnings) == 0

    def test_python_version_gte_fails(self):
        """Python 3.7 >= required 3.8 should fail."""
        skill = self._make_skill("python>=3.8")
        ok, warnings = skill.check_compatibility(python_version="3.7")
        assert ok is False
        assert any("3.7" in w and "3.8" in w for w in warnings)

    def test_python_version_exact_match(self):
        """Python 3.8 >= required 3.8 should pass (boundary)."""
        skill = self._make_skill("python>=3.8")
        ok, warnings = skill.check_compatibility(python_version="3.8")
        assert ok is True
        assert len(warnings) == 0

    def test_python_version_multi_segment(self):
        """Tuple comparison handles multi-segment versions correctly."""
        skill = self._make_skill("python>=3.9.1")
        ok, warnings = skill.check_compatibility(python_version="3.10.0")
        assert ok is True

    def test_python_version_multi_segment_fail(self):
        """Multi-segment version comparison fails correctly."""
        skill = self._make_skill("python>=3.10.5")
        ok, warnings = skill.check_compatibility(python_version="3.10.4")
        assert ok is False
        assert any("3.10.4" in w for w in warnings)

    def test_python_version_gt_operator(self):
        """Python > operator (strict greater than)."""
        skill = self._make_skill("python>3.8")
        # 3.8 is NOT > 3.8
        ok, warnings = skill.check_compatibility(python_version="3.8")
        assert ok is False
        # 3.9 IS > 3.8
        ok, warnings = skill.check_compatibility(python_version="3.9")
        assert ok is True

    def test_python_version_malformed_handled(self):
        """Malformed version strings are handled gracefully (no crash).
        
        Non-numeric segments are filtered by isdigit(), so 'not.a.version'
        becomes empty tuple () which is < any required version — producing
        a warning. The important thing is no crash / no exception.
        """
        skill = self._make_skill("python>=3.8")
        ok, warnings = skill.check_compatibility(python_version="not.a.version")
        # Empty tuple () < (3,8) is True, so the >= check triggers a warning
        assert ok is False
        assert len(warnings) == 1

    def test_no_compatibility_always_passes(self):
        """Skill with no compatibility requirement always passes."""
        skill = self._make_skill(None)
        ok, warnings = skill.check_compatibility(python_version="3.7")
        assert ok is True

    def test_runtime_match(self):
        """Runtime requirement matches correctly.
        
        Directly set _compatibility_parsed since parse_compatibility has
        limited runtime format support.
        """
        skill = self._make_skill(None)
        skill._compatibility_parsed["runtimes"] = [{"name": "ollama"}]
        ok, warnings = skill.check_compatibility(runtime="ollama")
        assert ok is True

    def test_runtime_mismatch(self):
        """Runtime requirement mismatch produces warning.
        
        Directly set _compatibility_parsed since parse_compatibility has
        limited runtime format support.
        """
        skill = self._make_skill(None)
        skill._compatibility_parsed["runtimes"] = [{"name": "ollama"}]
        ok, warnings = skill.check_compatibility(runtime="openai")
        assert ok is False
        assert any("ollama" in w for w in warnings)

    def test_no_runtime_check_when_unspecified(self):
        """No runtime check when no python_version/runtime provided."""
        skill = self._make_skill("python>=3.8, runtime:ollama")
        ok, warnings = skill.check_compatibility()
        assert ok is True
        assert len(warnings) == 0


# ============================================================================
# TurboState Schema Versioning Tests
# ============================================================================


class TestTurboStateVersioning:
    """Verify TurboState _version field, to_dict, and load version gating."""

    def test_turbo_state_has_version_field(self):
        """TurboState should have a _version field defaulting to 1."""
        from agentkthx.turbo import TurboState, _TURBO_STATE_VERSION
        state = TurboState()
        assert state._version == 1
        assert _TURBO_STATE_VERSION == 1

    def test_to_dict_includes_version(self):
        """to_dict() must include _version key."""
        from agentkthx.turbo import TurboState, _TURBO_STATE_VERSION
        state = TurboState(pid=1234, model_name="test-model")
        d = state.to_dict()
        assert "_version" in d
        assert d["_version"] == _TURBO_STATE_VERSION

    def test_to_dict_all_fields_present(self):
        """to_dict() includes all expected state fields."""
        from agentkthx.turbo import TurboState
        state = TurboState(
            pid=1234, model_name="qwen2.5:7b", blob_path="/models/qwen.gguf",
            port=8764, ctx=8192, cache_type_k="turbo3", cache_type_v="turbo3",
            turbo_mode="turboquant", host="localhost", server_path="llama-server",
            flash_attn=True, sparsity=0.5, started_at=1000.0,
        )
        d = state.to_dict()
        assert d["pid"] == 1234
        assert d["model_name"] == "qwen2.5:7b"
        assert d["cache_type_k"] == "turbo3"
        assert d["flash_attn"] is True
        assert d["sparsity"] == 0.5

    def test_from_dict_roundtrip(self):
        """from_dict(to_dict()) produces equivalent state."""
        from agentkthx.turbo import TurboState
        original = TurboState(pid=999, model_name="test", port=8080, ctx=4096)
        restored = TurboState.from_dict(original.to_dict())
        # _version is excluded from from_dict (it's a dataclass field filter)
        assert restored.pid == original.pid
        assert restored.model_name == original.model_name
        assert restored.port == original.port
        assert restored.ctx == original.ctx

    def test_from_dict_ignores_extra_keys(self):
        """from_dict() silently ignores unknown keys (forward-compatible)."""
        from agentkthx.turbo import TurboState
        data = {"pid": 1, "model_name": "m", "future_field": "should_be_ignored"}
        state = TurboState.from_dict(data)
        assert state.pid == 1
        assert state.model_name == "m"
        assert not hasattr(state, "future_field")

    def test_load_rejects_future_version(self, tmp_path, monkeypatch):
        """load() returns None when state file has version > current."""
        from agentkthx.turbo import TurboState, _TURBO_STATE_VERSION
        # Simulate a future state file with version 99
        future_data = {"_version": 99, "pid": 1, "model_name": "future-model"}
        state_file = tmp_path / "turbo.state"
        state_file.write_text(json.dumps(future_data))

        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", state_file)
        result = TurboState.load()
        assert result is None

    def test_load_accepts_current_version(self, tmp_path, monkeypatch):
        """load() succeeds when state file has current version."""
        from agentkthx.turbo import TurboState, _TURBO_STATE_VERSION
        state = TurboState(pid=42, model_name="test", port=8764, ctx=8192)
        state_dict = state.to_dict()
        state_file = tmp_path / "turbo.state"
        state_file.write_text(json.dumps(state_dict))

        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", state_file)
        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_PID_FILE", tmp_path / "turbo.pid")
        result = TurboState.load()
        assert result is not None
        assert result.pid == 42
        assert result.model_name == "test"

    def test_load_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        """load() returns None when state file doesn't exist."""
        from agentkthx.turbo import TurboState
        state_file = tmp_path / "nonexistent.state"
        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", state_file)
        result = TurboState.load()
        assert result is None

    def test_load_handles_version_zero(self, tmp_path, monkeypatch):
        """State file with no _version key (version 0) should load successfully."""
        from agentkthx.turbo import TurboState
        # Old state file without _version
        old_data = {"pid": 10, "model_name": "old-model"}
        state_file = tmp_path / "turbo.state"
        state_file.write_text(json.dumps(old_data))

        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", state_file)
        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_PID_FILE", tmp_path / "turbo.pid")
        result = TurboState.load()
        assert result is not None
        assert result.pid == 10

    def test_load_handles_corrupt_json(self, tmp_path, monkeypatch):
        """load() returns None for corrupted JSON (graceful degradation)."""
        from agentkthx.turbo import TurboState
        state_file = tmp_path / "turbo.state"
        state_file.write_text("NOT VALID JSON {{{")

        monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", state_file)
        result = TurboState.load()
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
