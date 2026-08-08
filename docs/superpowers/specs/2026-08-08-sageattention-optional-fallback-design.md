# Optional SageAttention Fallback Design

**Date:** 2026-08-08

## Goal

Allow every preset to finish installing and launch ComfyUI when SageAttention is unavailable, provided the standard PyTorch runtime remains functional.

## Runtime Contract

`use_sage_attention` requests an optional acceleration capability. It does not make SageAttention part of preset completeness.

When a preset requests SageAttention, Arrakis still attempts the existing unified installation and import validation. If installation, rebuild, ABI compatibility, or SageAttention import validation fails, Arrakis validates the standard runtime instead:

- A successful `torch` import makes the runtime launchable.
- The persisted runtime stack becomes `standard`.
- `--use-sage-attention` is omitted from the persisted ComfyUI flags.
- Preset installation continues through pip commands, model downloads, custom nodes, and completion tracking.

If the standard PyTorch runtime cannot import, runtime configuration remains fatal and ComfyUI is not launched.

## Post-Installation Revalidation

Preset pip commands and custom-node requirements may replace PyTorch after SageAttention was configured. The final SageAttention revalidation follows the same contract: a broken SageAttention import downgrades the marker to `standard` and remains launchable when `torch` still imports. A broken standard runtime remains fatal.

## Preset State and User Feedback

Preset completeness continues to depend only on its models and custom nodes. A preset whose artifacts are complete is marked installed even when SageAttention fell back to the standard runtime.

The SageAttention failure and fallback are logged as warnings. A successful artifact installation reports normal completion because the optional acceleration is not a missing preset artifact. Existing pending-state behavior remains unchanged for missing models or failed custom nodes.

## Implementation Boundary

Keep one runtime-selection path in `start.py`. Centralize the transition to the standard runtime in a small helper that validates `torch`, updates the state marker, and returns whether ComfyUI is launchable. Use that helper from initial SageAttention configuration and final revalidation.

Do not change preset JSON files, add per-preset exceptions, introduce a second installation path, or persist `--use-sage-attention` when SageAttention cannot import.

## Verification

Focused tests must prove:

1. A failed SageAttention installer falls back to `standard` when `torch` imports.
2. The same failure remains fatal when `torch` cannot import.
3. A SageAttention import lost after pip or node work falls back to a launchable standard runtime.
4. Persisted ComfyUI flags omit `--use-sage-attention` after fallback.
5. Existing successful SageAttention configuration remains unchanged.
