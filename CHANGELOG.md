# CHANGELOG

All notable changes to AgentNova will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.5] - 2026-09-18

### Added
- **ZAI Model Accuracy Improvements**
  - Added comprehensive max_tokens documentation to ZAI backend with confirmed model specifications
  - Implemented FREE_ONLY filtering for ZAI backend to show only free models (glm-4.5-flash, glm-4.7-flash)
  - Added intelligent tool support defaults for cloud providers (no expensive API calls needed)

### Changed
- **Cloud Provider Model Listing**
  - Fixed model list display for cloud providers (ZAI, OpenRouter) to remove redundant family column
  - Improved context size formatting with proper "K" suffix display (128K → 128K, not 125K)
  - Fixed cloud provider backend detection using BackendType enum instead of isinstance checks
  - Set cloud model size display to "unknown" (accurate since cloud APIs don't provide this info)

### Fixed
- **ZAI Context Length Accuracy**
  - Updated ZAI catalog context lengths based on official API documentation:
    - glm-4.5, glm-4.5-flash: 128K → 132K (for proper 128K display)
    - glm-4.7, glm-4.7-flash: 128K → 204800 (for proper 200K display)
  - Removed orphaned ZAI backend code from `backends/zai.py` (was duplicated in plugin system)
  - Fixed FREE_ONLY filtering for ZAI backend using `ZAI_FREE_ONLY=true` environment variable

### Documentation
- Added cache refresh endpoint comments to cloud provider backends:
  - OpenRouter: GET /v1/models (1-hour cache timeout)
  - ZAI: GET /api/paas/v4/models (1-hour cache timeout)
- Added comprehensive ZAI documentation with context lengths, max tokens, and pricing information for all GLM models
  - Includes official context sizes: 128K, 200K, and 1M variants
  - Shows per-model pricing and FREE_ONLY behavior

---

## [0.5.4] - Previous Release

[Previous changelog content...]