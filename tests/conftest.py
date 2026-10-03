"""
Shared pytest fixtures for the AgentKthx test suite.

R07.20: the persistent model-catalog cache (``agentkthx/model_cache.py``)
writes a JSON file under the shared cache dir. Every test gets an
isolated cache file (and a clean TTL) via this autouse fixture so cache
state can never leak between tests — or from a developer's real
``~/.cache/agentkthx`` into the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_model_cache(tmp_path, monkeypatch):
    """Point the persistent model-catalog cache at a per-test file."""
    monkeypatch.setenv("AGENTKTHX_MODEL_CACHE", str(tmp_path / "model_catalog.json"))
    monkeypatch.delenv("AGENTKTHX_MODEL_CACHE_TTL", raising=False)
    monkeypatch.delenv("AGENTKTHX_MODEL_SEED", raising=False)
    yield
