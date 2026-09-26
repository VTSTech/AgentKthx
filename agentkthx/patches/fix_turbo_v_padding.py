#!/usr/bin/env python3
"""
Fix for llama-cpp-turboquant zero-padding bug in build_attn.

Bug: When turbo KV cache compression is enabled, models with head_dim not
aligned to 128 (e.g., head_dim=64 for Qwen2, Llama3) need zero-padding to 128.
The KV cache allocation and copy paths handle this correctly, but build_attn()
reads the padded V head dimension from v->ne[0] — which for v_trans=true
(Flash Attention) contains n_kv (token count), NOT the padded head dimension.
This causes ggml_reshape_3d to fail with:
    GGML_ASSERT(ggml_nelements(a) == ne0*ne1*ne2)

Fix: Replace `v->ne[0]` with a direct computation of the padded head dimension:
    ((orig_v_head + 127) / 128) * 128
This is robust regardless of whether v_trans is true or false.

Affected file: src/llama-graph.cpp
Affected sites: 3 locations in build_attn() where padded_v_head is derived from v->ne[0]

Usage: python3 fix_turbo_v_padding.py [--dry-run]
"""

import sys
import os

def apply_fix(filepath, dry_run=False):
    with open(filepath, 'r') as f:
        content = f.read()

    # The exact pattern we're looking for:
    #   const int64_t padded_v_head = v->ne[0];
    # This appears 3 times in build_attn, each preceded by orig_v_head assignment.
    # We replace it with a direct computation.

    old_pattern = 'const int64_t padded_v_head = v->ne[0];'
    new_pattern = 'const int64_t padded_v_head = ((orig_v_head + 127) / 128) * 128;'

    count = content.count(old_pattern)

    if count == 0:
        print(f"ERROR: Pattern not found in {filepath}")
        print(f"  The code may have already been patched or the file structure changed.")
        return False

    if count != 3:
        print(f"WARNING: Found {count} occurrences (expected 3). Applying fix to all.")

    if dry_run:
        print(f"DRY RUN: Would replace {count} occurrence(s) of:")
        print(f"  OLD: {old_pattern}")
        print(f"  NEW: {new_pattern}")
        return True

    new_content = content.replace(old_pattern, new_pattern)

    with open(filepath, 'w') as f:
        f.write(new_content)

    print(f"SUCCESS: Fixed {count} occurrence(s) in {filepath}")
    return True

if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv

    # Default path for Colab
    filepath = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') \
               else 'src/llama-graph.cpp'

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    ok = apply_fix(filepath, dry_run)
    sys.exit(0 if ok else 1)
