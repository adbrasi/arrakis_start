# LTX 2.5 RTX 5090 Complete Preset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one Arrakis preset containing the non-duplicated LTX 2.5 model stack and three ready RTX 5090 workflows.

**Architecture:** Register nine role-distinct model files in one preset. Vendor the current official LTX 2.5 single-stage and two-stage workflows, adapt their loader selections to the chosen development INT8 checkpoint, and use the distilled LoRA wherever the distilled schedule is selected. Keep the LTXVideo fork synchronized with upstream while retaining its Kornia compatibility patch.

**Tech Stack:** Arrakis JSON presets, ComfyUI workflow JSON, ComfyUI-LTXVideo, KJNodes, Python `unittest`, GitHub CLI

## Global Constraints

- Store the user-selected development transformer URL without `?download=true`.
- Do not download a separate distilled transformer or convolutional video VAE.
- Keep the nine model destinations and filenames from the approved design exact.
- Preserve `Redesign Arrakis Start/` untouched and unstaged.
- Publish directly to `main` only after the finished gate passes.

---

### Task 1: Synchronize the LTXVideo fork

**Files:**
- Modify in `adbrasi/ComfyUI-LTXVideo`: upstream LTX 2.5 files merged into `master`
- Verify in fork: `pyramid_blending.py`

**Interfaces:**
- Consumes: `Lightricks/ComfyUI-LTXVideo@master`
- Produces: `https://github.com/adbrasi/ComfyUI-LTXVideo` with LTX 2.5 examples and Kornia-safe `F.pad`

- [ ] **Step 1:** Fetch and merge current upstream into a temporary clone of the fork.
- [ ] **Step 2:** Resolve `pyramid_blending.py` by retaining `torch.nn.functional as F` and `F.pad` at both call sites.
- [ ] **Step 3:** Verify Python syntax and absence of the removed Kornia `pad` import.
- [ ] **Step 4:** Commit and push `master`, then verify the remote SHA.

### Task 2: Specify the preset contract with a failing test

**Files:**
- Modify: `tests/test_presets.py`
- Create: `presets/ltx25-5090-complete.json`

**Interfaces:**
- Consumes: `start.load_presets() -> List[Dict]`
- Produces: active preset `_filename == "ltx25-5090-complete.json"`

- [ ] **Step 1:** Add `LTX25CompletePresetTests` asserting the exact nine models, three nodes, three workflows, query-free URLs, and no duplicate `(dir, filename)` destinations.
- [ ] **Step 2:** Run `python -m unittest tests.test_presets.LTX25CompletePresetTests -v` and confirm it fails because the preset is absent.
- [ ] **Step 3:** Add the minimal preset JSON with the asserted contract.
- [ ] **Step 4:** Run the focused test and confirm it passes.

### Task 3: Add the three workflows

**Files:**
- Create: `workflows/ltx25_dev_single_stage_5090.json`
- Create: `workflows/ltx25_dev_two_stage_5090.json`
- Create: `workflows/ltx25_distilled_lora_5090.json`

**Interfaces:**
- Consumes: the nine preset model filenames
- Produces: loadable ComfyUI workflow JSON for guided dev, two-stage dev/refine, and distilled-LoRA fast modes

- [ ] **Step 1:** Vendor the current official LTX 2.5 single-stage and two-stage workflow graphs.
- [ ] **Step 2:** Select the development INT8 transformer, INT8 generation encoder, Gemma enhancer, diffusion video VAE, audio VAE, and spatial upscaler in every applicable loader.
- [ ] **Step 3:** Apply `ltx-2.5-22b-distilled-lora-450-bf16.safetensors` before distilled sampling; leave the guided dev stage unadapted.
- [ ] **Step 4:** Register duration-head and temporal-upscaler model selections where their controls are exposed, and add KJNodes sampling preview before sampler execution.
- [ ] **Step 5:** Parse all three files and assert their model/sampler contracts.

### Task 4: Finished gate and publication

**Files:**
- Verify all changed Arrakis files

**Interfaces:**
- Produces: `origin/main` equal to local `HEAD`

- [ ] **Step 1:** Run JSON parsing and exact preset/workflow assertions.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -v` once.
- [ ] **Step 3:** Run `git diff --check` and confirm unrelated files remain unstaged.
- [ ] **Step 4:** Commit the implementation in Portuguese, push `main`, and verify local/remote SHA equality.
