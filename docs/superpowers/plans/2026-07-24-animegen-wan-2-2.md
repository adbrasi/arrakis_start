# AnimeGen Wan 2.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an active `animegen wan 2.2` preset using the AnimeGen Wan 2.2 I2V Q8 GGUF model pair.

**Architecture:** Clone the declarative structure of the disabled Wan base preset into a new active preset. Change only its identity and diffusion-model pair, then add the GGUF loader node; production Python remains unchanged.

**Tech Stack:** JSON preset files, Python 3 `unittest`, Arrakis Start preset loader

## Global Constraints

- Preserve supporting models, LoRAs, nodes, flags, and SageAttention configuration from `presets/wan-base.json.ignore`.
- Store both AnimeGen GGUF files under `diffusion_models`.
- Do not modify or enable an existing preset.
- Add `https://github.com/city96/ComfyUI-GGUF` exactly once.

---

### Task 1: Add and validate the AnimeGen preset

**Files:**
- Create: `presets/animegen-wan-2.2.json`
- Create: `tests/test_presets.py`

**Interfaces:**
- Consumes: `start.load_presets() -> List[Dict]`
- Produces: active preset with `_filename == "animegen-wan-2.2.json"`

- [ ] **Step 1: Write the failing integration test**

```python
import unittest

import start


class AnimeGenWanPresetTests(unittest.TestCase):
    def test_loads_animegen_wan_2_2_with_exact_gguf_pair(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }
        preset = presets["animegen-wan-2.2.json"]
        diffusion_models = [
            model for model in preset["models"]
            if model["dir"] == "diffusion_models"
        ]

        self.assertEqual(preset["name"], "animegen wan 2.2")
        self.assertEqual(diffusion_models, [
            {
                "url": "https://huggingface.co/NullpoLab/AnimeGen-I2V-GGUF/resolve/main/I2V_low_noise_Q8_0.gguf?download=true",
                "dir": "diffusion_models",
                "filename": "I2V_low_noise_Q8_0.gguf",
            },
            {
                "url": "https://huggingface.co/NullpoLab/AnimeGen-I2V-GGUF/resolve/main/I2V_high_noise_Q8_0.gguf?download=true",
                "dir": "diffusion_models",
                "filename": "I2V_high_noise_Q8_0.gguf",
            },
        ])
        self.assertEqual(
            preset["nodes"].count("https://github.com/city96/ComfyUI-GGUF"),
            1,
        )
        self.assertTrue(preset["use_sage_attention"])
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.test_presets.AnimeGenWanPresetTests -v`

Expected: `ERROR` with missing key `animegen-wan-2.2.json`, because the active preset does not exist.

- [ ] **Step 3: Add the minimal preset**

Create `presets/animegen-wan-2.2.json` from
`presets/wan-base.json.ignore`, set:

```json
{
  "name": "animegen wan 2.2",
  "description": "AnimeGen WAN 2.2 I2V preset with low-noise and high-noise Q8 GGUF diffusion models, WanVideo tooling, and optional SageAttention stack"
}
```

Replace the source's two `diffusion_models` entries with the exact pair from
Step 1. Append this node once:

```json
"https://github.com/city96/ComfyUI-GGUF"
```

- [ ] **Step 4: Run focused and regression tests**

Run: `python -m unittest tests.test_presets.AnimeGenWanPresetTests -v`

Expected: `OK`.

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Validate formatting and commit**

Run: `python -m json.tool presets/animegen-wan-2.2.json >/dev/null`

Run: `git diff --check`

```bash
git add presets/animegen-wan-2.2.json tests/test_presets.py
git commit -m "Adiciona preset AnimeGen Wan 2.2"
```
