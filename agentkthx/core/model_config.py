"""
AgentKthx — Model Configuration (DEPRECATED)

This module is deprecated. All model family configuration has been merged into
model_family_config.py. This module re-exports ModelFamilyConfig and
get_model_config from there for backward compatibility.

Please import from model_family_config instead:
    from agentkthx.core.model_family_config import ModelFamilyConfig, get_model_config

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import warnings

# Re-export from the unified module for backward compatibility
from .model_family_config import ModelFamilyConfig, get_model_config

# Legacy MODEL_CONFIGS dict — maps to FAMILY_CONFIGS entries
from .model_family_config import FAMILY_CONFIGS as MODEL_CONFIGS


# Emit deprecation warning on import
warnings.warn(
    "model_config.py is deprecated. Use model_family_config.py instead. "
    "Import ModelFamilyConfig and get_model_config from agentkthx.core.model_family_config.",
    DeprecationWarning,
    stacklevel=2,
)