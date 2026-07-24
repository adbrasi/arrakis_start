# AnimeGen Wan 2.2 Preset Design

## Goal

Add an active Arrakis Start preset named `animegen wan 2.2` for the AnimeGen
Wan 2.2 I2V GGUF model pair.

## Source Preset

Use `presets/wan-base.json.ignore` as the structural source. Preserve its
supporting models, LoRAs, custom nodes, ComfyUI flags, and SageAttention
configuration.

## Changes

Create `presets/animegen-wan-2.2.json` with these intentional differences from
the source preset:

1. Set `name` to `animegen wan 2.2`.
2. Update the description to identify the AnimeGen I2V Q8 GGUF model pair.
3. Replace the two standard Wan 2.2 entries in `diffusion_models` with:
   - `I2V_low_noise_Q8_0.gguf`
   - `I2V_high_noise_Q8_0.gguf`
4. Store both files in `ComfyUI/models/diffusion_models`.
5. Add `https://github.com/city96/ComfyUI-GGUF` to `nodes`.

No existing preset is modified or enabled.

## Validation

- Parse the new file as JSON.
- Verify the preset loader exposes it as an active preset.
- Assert that the two AnimeGen URLs, directories, and filenames are exact.
- Assert that no standard Wan 2.2 diffusion model remains in the new preset.
- Assert that `ComfyUI-GGUF` is present exactly once.
- Run the relevant preset-loading test suite.
