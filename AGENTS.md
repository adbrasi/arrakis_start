# Arrakis Start: agent guide

## What this project does

Arrakis Start provisions ComfyUI on VastAI and RunPod instances. A small web selector lets the user choose presets, install their models and custom nodes, watch progress, and start ComfyUI. The cloud installation normally lives at `/workspace/comfy/arrakis_start`, with the Python environment at `/workspace/comfy/.venv`. The selector uses port 8090; ComfyUI defaults to port 8818.

Start with the file for the task at hand. Inspect the relevant implementation and configuration before editing. Read tests when changing application code; the preset-only rule below takes precedence for data-only preset work. The table below is the shortest route through the codebase.

| Task | Start here | Then check |
| --- | --- | --- |
| Add, remove, or repair a preset | `presets/<name>.json`, related `workflows/` files | `start.py:load_presets`, `server.py:serialize_presets` only if the data format is unclear |
| Model download or resume | `downloader.py` | `hf_xet_worker.py`, `tests/test_downloader.py`, `tests/test_hf_xet_worker.py` |
| Custom nodes or Python packages | `start.py:_install_presets_impl` | `start.py` node/pip helpers, `tests/test_runtime_stack.py` |
| Install/bootstrap | `bootstrap.sh` | `tests/test_bootstrap.py`, `requirements.txt` |
| Selector/API | `server.py` | `web/index.html`, `web/app.js`, `web/styles.css`, `tests/test_web_ui.py` |
| ComfyUI lifecycle | `process_manager.py` | `tests/test_process_manager.py`, `state.py` |
| Status/progress | `progress.py`, `state.py` | `server.py`, `web/app.js` |

The normal path is `bootstrap.sh` -> `start.py --web-only` -> `server.py` API -> `start.py` installer -> `downloader.py` and custom-node installation -> `state.py`. The browser polls status through the API. `process_manager.py` starts and monitors ComfyUI. Read `README.md` for deployment and operator instructions.

## What a preset is

A preset is one installable bundle described by a JSON file in `presets/`. It is **not** a ComfyUI workflow: the preset installs assets; a workflow tells ComfyUI how to connect nodes and use those assets. The user may select several presets together. The installer automatically includes `Base`, which supplies shared nodes and flags. Existing assets are skipped when possible, and installation state is persisted.

Every active `*.json` file is loaded automatically. Files ending in `.json.ignore` and hidden files are ignored. A preset requires no registration in Python or the UI. Its `name` is the user-facing selection and installation key; keep it unique. `load_presets()` sorts by the latest Git commit that touched each preset, using file modification time for uncommitted files. The UI keeps that order within its pinned and recent sections. Set `pinned: true` to put a preset in the top large-card section; a newly committed preset is first among pinned presets until another one is modified later. `size_gb` is a human-maintained estimate of the total download, not a quota.

A typical preset looks like this:

```json
{
  "name": "Example Model",
  "description": "What the user can do with the installed bundle",
  "pinned": false,
  "size_gb": 18,
  "use_sage_attention": false,
  "comfyui_flags": [],
  "models": [
    {
      "url": "https://huggingface.co/owner/repo/resolve/main/model.safetensors",
      "dir": "diffusion_models",
      "filename": "model.safetensors"
    }
  ],
  "nodes": ["https://github.com/owner/ComfyUI-Node"],
  "workflows": [{"label": "Text to image", "file": "example_t2i.json"}]
}
```

`models[]` downloads each `url` to `ComfyUI/models/<dir>/<filename>`. Use the directory expected by the ComfyUI loader, such as `diffusion_models`, `text_encoders`, `vae`, `loras`, or `checkpoints`. Verify the **exact file path** exists at the source, not merely its repository. Avoid two different URLs targeting the same directory and filename: they conflict at the install boundary. Keep an official filename when a workflow refers to it.

`nodes[]` contains Git repository URLs for ComfyUI custom nodes. Add a node only when the preset/workflow needs it; `Base` already supplies ComfyUI-Manager and ComfyUI-PiD. Node repositories may install their own requirements. Optional `pip_commands[]` supports extra dependencies and conditions; inspect existing presets and `start.py` before adding one. `comfyui_flags[]` adds launch flags. `use_sage_attention: true` requests both installation and the ComfyUI flag, while `install_sage_attention: true` makes the wheel available without that launch flag. Treat either as a runtime decision, not a default for every model.

`workflows[]` is a list of `{label, file}` or `{label, url}`. A `file` must exist in `workflows/` and is downloaded through `/api/workflows/<file>`; a `url` links externally. The older single `workflow`/`workflow_url` fields also work. Prefer a workflow whose loader filenames match the preset exactly. Some upstream templates use subgraphs or newer core nodes, so a JSON match alone does not prove a given ComfyUI installation can execute it.

## Model precision preference

For new presets, first inspect the model publisher's **official ComfyUI-compatible repository** and its recommended workflow. Prefer a supported `int8_convrot` or FP8 checkpoint over BF16 when it gives a smaller practical install. Pick the actual published format and use its exact name; never label INT8 as FP8. Keep a BF16 VAE or other component when that is what the official workflow uses. Do not substitute a community or Diffusers-format FP8 file merely because it is smaller: establish ComfyUI loader compatibility first. If the official repository has no FP8, use its supported INT8 variant and state that clearly.

For Qwen Image 2.1 specifically, `presets/qwen-image-2.1.json` supports image editing. It installs the INT8 ConvRot diffusion model, the INT8 ConvRot Qwen3-VL encoder, and the **I2I** INT8 ConvRot prompt-enhancer encoder from `Comfy-Org/Qwen-Image-2.1`. It also installs the official BF16 VAE and madebyollin's BF16 texture-fix VAE. `workflows/qwen_image_2.1_edit.json` is based on the official Comfy-Org image-editing template and selects the texture-fix VAE by default. The latter is a community fine-tune intended to reduce fine checkerboard artifacts; keep the official VAE for comparison. The official Comfy-Org repository had BF16 and INT8 ConvRot diffusion models when this preset was added; recheck the live file tree before making future precision claims.

The [Qwen Image 2.1 research license](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) limits use of its model materials to non-commercial purposes unless a separate commercial license is obtained. Surface this constraint when a preset may be used in a commercial project.

## Verification and operational boundaries

**Preset-only changes:** When the request only adds, removes, or edits files in `presets/` and their related workflow JSON files, do not run unit, integration, end-to-end, UI smoke, or full-suite tests. Do not create or edit test files for these changes. Do not run a test suite just because an existing metadata test enumerates presets or was already failing. This is the user's explicit preference; keep preset maintenance fast.

For preset-only work, inspect the JSON for valid syntax, confirm that model URLs point to the exact published files, and check that referenced local workflows exist and use the intended model filenames. These are data checks, not a reason to start ComfyUI, download model weights, or run the test suite. Report any part that could not be checked without implying runtime execution.

When application code changes, run focused checks for the edited area and then the relevant repository suite from the root:

```bash
PYTHONPATH=. python3 -m unittest tests.test_presets tests.test_web_ui
PYTHONPATH=. python3 -m unittest discover -s tests
```

Run the UI smoke check (`node tests/ui_smoke.cjs`) when selector behavior changes. No linter is configured. If a code change's test suite fails on a stale preset expectation, distinguish that pre-existing failure from the new change. Data checks and test suites do not prove model inference, GPU memory fit, native runtime compatibility, or the ability to execute a workflow on a live pod.

On a cloud pod, use `/workspace/comfy/.venv/bin/python` for runtime checks. The SageAttention path must be verified with that interpreter and, for actual acceleration, a ComfyUI run; a successful installer return alone is insufficient. Model URLs and custom-node branches can change after a preset is committed. Preserve user models, download partials, and unrelated working-tree changes while debugging. Do not print tokens or credentials in logs.

The checkout's root `AGENTS.md` is the repository agent entry point. Edit it when architecture or preset conventions change; keep detailed operational instructions in `README.md` and code-specific facts near their implementation.
