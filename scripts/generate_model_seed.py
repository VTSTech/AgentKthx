"""
⚛️ AgentKthx — Static model-catalog seed extractor (R07.20, one-time tool).

R07.20 moved the per-plugin static catalogs (ZAI_MODELS, OPENAI_MODELS,
OPENROUTER_MODELS, GEMINI_MODELS, MISTRAL_MODELS, POLLINATIONS_MODELS,
HF_MODELS) OUT of the Python sources and into the packaged seed file
``agentkthx/data/model_seed.json``. The seed is the INITIAL DEFAULT content
of the persistent model-catalog cache (``agentkthx/model_cache.py``): a
backend whose cache entry is missing gets its catalog seeded from this
file, and the offline fallback list is built from the same seed.

This script was the one-time extractor used for the R07.20 migration: it
imported the pre-migration plugin modules and dumped their catalog dicts
verbatim (keys, order, and values) into the seed JSON. It is kept in the
tree for provenance and for emergency re-derivation from a pre-R07.20
checkout:

    git checkout <pre-R07.20-commit> -- agentkthx/plugins/ scripts/
    python scripts/generate_model_seed.py

After R07.20 the seed JSON is the source of truth for the static
catalogs — edit it directly (or refresh a backend's cache from the live
API) instead of re-running this script.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT_PATH = Path(__file__).resolve().parents[1] / "agentkthx" / "data" / "model_seed.json"

# (backend key in the seed file, import path, catalog attribute name)
CATALOGS: list[tuple[str, str, str]] = [
    ("zai", "agentkthx.plugins.zai.zai", "ZAI_MODELS"),
    ("openai", "agentkthx.plugins.openai.openai", "OPENAI_MODELS"),
    ("openrouter", "agentkthx.plugins.openrouter.openrouter", "OPENROUTER_MODELS"),
    ("gemini", "agentkthx.plugins.gemini.gemini", "GEMINI_MODELS"),
    ("mistral", "agentkthx.plugins.mistral.mistral", "MISTRAL_MODELS"),
    ("pollinations", "agentkthx.plugins.pollinations.pollinations", "POLLINATIONS_MODELS"),
    ("huggingface", "agentkthx.plugins.huggingface.huggingface", "HF_MODELS"),
]


def main() -> int:
    seed: dict = {
        "_meta": {
            "release": "R07.20",
            "generated": date.today().isoformat(),
            "note": (
                "Static model catalogs extracted from the plugin modules by "
                "scripts/generate_model_seed.py. Loaded by "
                "agentkthx.model_cache.load_seed_catalog() and used as the "
                "initial defaults of the persistent model-catalog cache "
                "(and the offline fallback lists). Backend keys must not "
                "start with '_' — that prefix is reserved for metadata."
            ),
        }
    }
    for key, module_name, attr in CATALOGS:
        module = __import__(module_name, fromlist=[attr])
        catalog = getattr(module, attr)
        if not isinstance(catalog, dict) or not catalog:
            print(f"ERROR: {module_name}.{attr} is not a non-empty dict", file=sys.stderr)
            return 1
        seed[key] = catalog
        print(f"  {key:<13} {len(catalog):>4} models from {module_name}.{attr}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
