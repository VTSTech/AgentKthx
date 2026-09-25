"""
AgentKthx - Ollama Model Registry
Discovers Ollama models by reading manifests and mapping names to GGUF blob paths.

Ollama stores models as:
  ~/.ollama/models/manifests/registry.ollama.ai/<library>/<repo>/<tag>  (JSON manifest)
  ~/.ollama/models/blobs/sha256-<digest>                                (raw GGUF data)

Each manifest's "model" layer (mediaType: application/vnd.ollama.image.model)
points to a blob that IS the complete GGUF file - usable directly by llama-server.

No conversion or copying needed.

Written by VTSTech - https://www.vts-tech.org
"""

from __future__ import annotations

import json
import mmap
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Default Ollama model storage paths
OLLAMA_MODELS_DIR = Path(os.environ.get("OLLAMA_MODELS", os.path.expanduser("~/.ollama/models")))
OLLAMA_MANIFESTS_DIR = OLLAMA_MODELS_DIR / "manifests" / "registry.ollama.ai"
OLLAMA_BLOBS_DIR = OLLAMA_MODELS_DIR / "blobs"

# GGUF magic number: "GGUF" as little-endian uint32
_GGUF_MAGIC = 0x46554747


# TurboQuant requires head_dim >= TURBO_D (128) for KV cache block alignment
_TURBO_D = 128


@dataclass
class OllamaModel:
    """Represents a discovered Ollama model."""
    name: str               # e.g. "qwen2.5:7b"
    repo: str               # e.g. "qwen2.5"
    tag: str                # e.g. "7b"
    blob_path: Path         # absolute path to the GGUF blob
    size_bytes: int         # model layer size from manifest
    weight_quant: str       # detected weight quant (e.g. "Q4_K_M", "Q8_0", "F16")
    manifest_path: Path     # path to the manifest file
    model_digest: str       # sha256 digest of the model blob
    architecture: str = "" # e.g. "qwen2", "gemma3", "llama"
    head_dim: int = 0       # attention head dimension (embed_length / head_count)
    n_heads: int = 0        # number of attention heads
    n_layers: int = 0       # number of transformer layers
    context_length: int = 0 # max context window from model metadata

    @property
    def size_human(self) -> str:
        """Return human-readable size string."""
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 ** 2:
            return f"{self.size_bytes / 1024:.1f} KB"
        elif self.size_bytes < 1024 ** 3:
            return f"{self.size_bytes / (1024 ** 2):.1f} MB"
        else:
            return f"{self.size_bytes / (1024 ** 3):.1f} GB"

    @property
    def exists(self) -> bool:
        """Check if the blob file exists on disk."""
        return self.blob_path.exists()


def _gguf_find_key(mm: mmap.mmap, key: bytes) -> tuple[int, int, int] | None:
    """Find a GGUF key and return (idx, value_type, value_int) for uint32 values.

    For string values, returns (idx, 8, value_start_offset) where
    value_start_offset points to the string data.
    Returns None if key not found.
    """
    idx = mm.find(key)
    if idx < 0:
        return None
    key_len = struct.unpack_from("<Q", mm, idx - 8)[0]
    vtype = struct.unpack_from("<I", mm, idx + key_len)[0]
    return (idx, vtype, idx + key_len + 4)


def _gguf_read_u32(mm: mmap.mmap, key: bytes) -> int | None:
    """Read a uint32 GGUF value by key name."""
    result = _gguf_find_key(mm, key)
    if result is None:
        return None
    _, vtype, val_off = result
    if vtype != 4:  # GGUF_TYPE_UINT32
        return None
    return struct.unpack_from("<I", mm, val_off)[0]


def _gguf_read_str(mm: mmap.mmap, key: bytes) -> str | None:
    """Read a string GGUF value by key name."""
    result = _gguf_find_key(mm, key)
    if result is None:
        return None
    _, vtype, val_off = result
    if vtype != 8:  # GGUF_TYPE_STRING
        return None
    val_len = struct.unpack_from("<Q", mm, val_off)[0]
    return mm[val_off + 8:val_off + 8 + val_len].rstrip(b'\x00').decode("utf-8", errors="replace")


def _detect_weight_quant(blob_path: Path) -> str:
    """Read GGUF header to detect weight quantization.

    Uses mmap to directly search for the "general.file_type" key in the
    binary file — no sequential KV parsing needed. This is fast, handles
    arbitrarily large files, and doesn't break on unknown value types.

    The key is found by byte search, then we read the uint32 value_type
    and value that follow the key data in the GGUF binary layout:
        [key_len: u64][key_data: key_len bytes][value_type: u32][value: varies]
    """
    try:
        with open(blob_path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                # Quick rejection: not a GGUF file
                if mm[:4] != b"GGUF":
                    return _filename_heuristic(blob_path)

                # Search for the key bytes
                idx = mm.find(b"general.file_type")
                if idx < 0:
                    return _filename_heuristic(blob_path)

                # Read the key_len that precedes the key (8 bytes before key start)
                key_len = struct.unpack_from("<Q", mm, idx - 8)[0]
                # value_type starts right after the key data
                vtype = struct.unpack_from("<I", mm, idx + key_len)[0]
                # value starts 4 bytes after value_type
                file_type = struct.unpack_from("<I", mm, idx + key_len + 4)[0]

                if vtype != 4:  # Must be GGUF_TYPE_UINT32
                    return _filename_heuristic(blob_path)

                return _gguf_file_type_to_name(file_type)
            finally:
                mm.close()
    except (OSError, struct.error):
        pass

    return _filename_heuristic(blob_path)


# GGUF file_type constants — official ggml_ftype enum from ggml.h
# https://github.com/ggerganov/llama.cpp/blob/master/ggml/include/ggml.h
_GGUF_FILE_TYPES = {
    0:  "F32",
    1:  "F16",
    2:  "Q4_0",
    3:  "Q4_1",
    # 4: Q4_2 (removed)
    # 5: Q4_3 (removed)
    6:  "Q5_0",
    7:  "Q5_1",
    8:  "Q8_0",
    9:  "Q8_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    21: "Q2_K_S",
    22: "IQ3_XS",
    23: "Q3_K_XS",  # aka Q3_K_XS
    24: "IQ1_S",
    25: "IQ4_NL",
    26: "IQ3_S",
    27: "IQ2_S",
    28: "IQ4_XS",
    29: "I8",
    30: "I16",
    31: "I32",
    32: "I64",
    33: "F64",
    34: "IQ1_M",
    35: "BF16",
    # TQ types (TheTom's turboquant fork)
    36: "TQ4_1S",
    37: "TQ3_1S",
}


def _gguf_file_type_to_name(file_type: int) -> str:
    """Convert GGUF file_type constant to human-readable name."""
    return _GGUF_FILE_TYPES.get(file_type, f"UNKNOWN({file_type})")


def _filename_heuristic(blob_path: Path) -> str:
    """Fallback: detect quant from filename."""
    name = blob_path.stem.lower().replace("-", "").replace("_", "")
    # Sort by specificity (longer matches first)
    candidates = [
        ("iq4xs", "IQ4_XS"), ("iq4nl", "IQ4_NL"), ("iq4_nl", "IQ4_NL"),
        ("iq3xxs", "IQ3_XXS"), ("iq3s", "IQ3_S"), ("iq3_s", "IQ3_S"),
        ("iq2xxs", "IQ2_XXS"), ("iq2xs", "IQ2_XS"), ("iq2_s", "IQ2_S"),
        ("iq1m", "IQ1_M"), ("iq1_s", "IQ1_S"),
        ("q4km", "Q4_K_M"), ("q4_ks", "Q4_K_S"), ("q4_k_m", "Q4_K_M"),
        ("q5km", "Q5_K_M"), ("q5ks", "Q5_K_S"), ("q5_k_m", "Q5_K_M"),
        ("q6k", "Q6_K"), ("q6_k", "Q6_K"),
        ("q3km", "Q3_K_M"), ("q3ks", "Q3_K_S"), ("q3kl", "Q3_K_L"),
        ("q8_0", "Q8_0"), ("q8_1", "Q8_1"),
        ("q4_0", "Q4_0"), ("q4_1", "Q4_1"),
        ("q5_0", "Q5_0"), ("q5_1", "Q5_1"),
        ("q2_k", "Q2_K"), ("q3_k", "Q3_K"),
        ("f16", "F16"), ("bf16", "BF16"), ("f32", "F32"),
        ("tq41s", "TQ4_1S"), ("tq4_1s", "TQ4_1S"),
        ("tq31s", "TQ3_1S"), ("tq3_1s", "TQ3_1S"),
    ]
    for pattern, label in candidates:
        if pattern in name:
            return label
    return "UNKNOWN"


def _resolve_blob_path(digest: str) -> Path:
    """Convert a manifest digest to a blob file path.

    Manifest uses "sha256:<hex>" format.
    Blob file is stored as "sha256-<hex>" (colon replaced with dash).
    """
    return OLLAMA_BLOBS_DIR / digest.replace(":", "-")


def _parse_ollama_name(model_name: str) -> tuple[str, str]:
    """Parse an Ollama model name into (repo, tag).

    Examples:
        "qwen2.5:7b"           -> ("qwen2.5", "7b")
        "llama3.1:8b"          -> ("llama3.1", "8b")
        "mistral:7b-instruct"  -> ("mistral", "7b-instruct")
        "library/qwen2.5:7b"   -> ("qwen2.5", "7b")
    """
    if "/" in model_name:
        # Handle "library/qwen2.5:7b" format
        parts = model_name.split("/")
        model_name = parts[-1] if parts[-1] else parts[-2]

    if ":" in model_name:
        repo, tag = model_name.split(":", 1)
        return repo, tag

    # No tag - use "latest"
    return model_name, "latest"


def discover_models(
    ollama_dir: Optional[Path] = None,
    only_existing: bool = True,
) -> list[OllamaModel]:
    """Discover all Ollama models and map them to GGUF blob paths.

    Args:
        ollama_dir: Override Ollama models directory (default: ~/.ollama/models)
        only_existing: Only return models whose blob files exist on disk

    Returns:
        List of OllamaModel objects sorted by name
    """
    global OLLAMA_MANIFESTS_DIR, OLLAMA_BLOBS_DIR

    if ollama_dir:
        OLLAMA_MANIFESTS_DIR = ollama_dir / "manifests" / "registry.ollama.ai"
        OLLAMA_BLOBS_DIR = ollama_dir / "blobs"

    if not OLLAMA_MANIFESTS_DIR.exists():
        return []

    models: list[OllamaModel] = []

    # Walk the manifests directory
    # Structure: registry.ollama.ai/<library>/<repo>/<tag>
    for library_dir in sorted(OLLAMA_MANIFESTS_DIR.iterdir()):
        if not library_dir.is_dir():
            continue
        for repo_dir in sorted(library_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            for tag_file in sorted(repo_dir.iterdir()):
                if not tag_file.is_file():
                    continue

                # The tag file is the manifest JSON
                try:
                    manifest = json.loads(tag_file.read_text())
                except (json.JSONDecodeError, OSError):
                    continue

                repo = repo_dir.name
                tag = tag_file.name
                name = f"{repo}:{tag}"

                # Find the model layer (GGUF blob)
                model_layer = None
                for layer in manifest.get("layers", []):
                    if layer.get("mediaType") == "application/vnd.ollama.image.model":
                        model_layer = layer
                        break

                if not model_layer:
                    continue

                digest = model_layer.get("digest", "")
                size = model_layer.get("size", 0)
                blob_path = _resolve_blob_path(digest)

                # Detect weight quantization and model metadata from GGUF header
                architecture = ""
                head_dim = 0
                n_heads = 0
                n_layers = 0
                context_length = 0

                if blob_path.exists():
                    weight_quant = _detect_weight_quant(blob_path)
                    # Read architecture-specific metadata
                    try:
                        with open(blob_path, "rb") as bf:
                            bm = mmap.mmap(bf.fileno(), 0, access=mmap.ACCESS_READ)
                            try:
                                if bm[:4] == b"GGUF":
                                    arch = _gguf_read_str(bm, b"general.architecture")
                                    if arch:
                                        architecture = arch
                                        n_heads = _gguf_read_u32(bm, arch.encode() + b".attention.head_count") or 0
                                        embed = _gguf_read_u32(bm, arch.encode() + b".embedding_length") or 0
                                        head_dim = int(embed / n_heads) if n_heads > 0 and embed > 0 else 0
                                        n_layers = _gguf_read_u32(bm, arch.encode() + b".block_count") or 0
                                        context_length = _gguf_read_u32(bm, arch.encode() + b".context_length") or 0
                            finally:
                                bm.close()
                    except (OSError, struct.error):
                        pass
                else:
                    weight_quant = "UNKNOWN"

                model = OllamaModel(
                    name=name,
                    repo=repo,
                    tag=tag,
                    blob_path=blob_path,
                    size_bytes=size,
                    weight_quant=weight_quant,
                    manifest_path=tag_file,
                    model_digest=digest,
                    architecture=architecture,
                    head_dim=head_dim,
                    n_heads=n_heads,
                    n_layers=n_layers,
                    context_length=context_length,
                )

                if only_existing and not model.exists:
                    continue

                models.append(model)

    return models


def find_model(
    model_name: str,
    ollama_dir: Optional[Path] = None,
) -> Optional[OllamaModel]:
    """Find a specific Ollama model by name.

    Args:
        model_name: Ollama model name (e.g. "qwen2.5:7b")
        ollama_dir: Override Ollama models directory

    Returns:
        OllamaModel if found, None otherwise
    """
    repo, tag = _parse_ollama_name(model_name)
    all_models = discover_models(ollama_dir=ollama_dir, only_existing=False)

    for model in all_models:
        if model.repo == repo and model.tag == tag:
            return model

    # Fuzzy match on just the repo part
    for model in all_models:
        if model.repo == repo:
            return model

    # Fuzzy match on full name
    for model in all_models:
        if model_name in model.name or model.name in model_name:
            return model

    return None


def recommended_turbo_config(weight_quant: str) -> dict[str, str]:
    """Get recommended TurboQuant config based on weight quantization.

    Based on TheTom's turboquant_plus findings:
    - Q8_0/F16/BF16 weights -> symmetric turbo (best compression)
    - Q4_K_M and lower -> asymmetric (keep K at q8_0, compress V only)

    R06.57: Changed the asymmetric path to use turbo4/turbo4 instead of
    q8_0/turbo4. The server has its own auto-asymmetric upgrade logic:
    when it detects a high GQA ratio (n_head/n_head_kv > 4), it upgrades
    the K cache from turbo4 to q8_0 internally. Sending q8_0 directly
    crashes on qwen35 architecture (rope.dimension_sections bug), but
    turbo4 works because the server's auto-upgrade handles it correctly.
    Disable the server's auto-upgrade with TURBO_AUTO_ASYMMETRIC=0.

    Returns:
        Dict with 'cache_type_k' and 'cache_type_v' keys
    """
    wq = weight_quant.upper().replace("-", "_").replace(" ", "")

    # High-quality weights: symmetric is safe (uncompressed or minimal loss)
    high_quality = {"F32", "F16", "BF16", "Q8_0", "Q8_1", "I8", "I16", "I32", "I64", "F64"}

    if wq in high_quality:
        return {
            "cache_type_k": "turbo3",
            "cache_type_v": "turbo3",
            "mode": "symmetric",
            "reason": f"{weight_quant} weights: symmetric turbo3/turbo3 safe",
        }

    # TurboQuant weight types: these are already compressed, go easy on KV
    if wq.startswith("TQ"):
        return {
            "cache_type_k": "turbo4",
            "cache_type_v": "turbo4",
            "mode": "symmetric",
            "reason": f"{weight_quant} weights: turbo4/turbo4 (server auto-upgrades K to q8_0 for high GQA ratios)",
        }

    # Lower bit weights: use turbo4/turbo4 and let the server's
    # auto-asymmetric logic handle GQA ratio protection.
    # R06.57: was q8_0/turbo4, but q8_0 crashes on qwen35 architecture.
    return {
        "cache_type_k": "turbo4",
        "cache_type_v": "turbo4",
        "mode": "symmetric",
        "reason": f"{weight_quant} weights: turbo4/turbo4 (server auto-upgrades K to q8_0 for high GQA ratios)",
    }


__all__ = [
    "OllamaModel",
    "OLLAMA_MODELS_DIR",
    "OLLAMA_MANIFESTS_DIR",
    "OLLAMA_BLOBS_DIR",
    "discover_models",
    "find_model",
    "recommended_turbo_config",
    "_parse_ollama_name",
    "_resolve_blob_path",
]
