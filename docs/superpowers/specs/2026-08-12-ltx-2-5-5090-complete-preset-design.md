# LTX 2.5 RTX 5090 Complete Preset Design

## Goal

Add one active Arrakis Start preset that installs the minimum shared LTX 2.5 stack needed to test guided development sampling, two-stage refinement, and fast distilled-LoRA sampling on an RTX 5090.

## Preset Contract

Create `presets/ltx25-5090-complete.json` with the display name `LTX 2.5 COMPLETE - RTX 5090`.

The preset owns all shared assets once. It contains three workflows rather than splitting the same model stack across three presets:

1. `ltx25_dev_single_stage_5090.json`: development transformer, guided full-model sampling, T2V/I2V, and diffusion-VAE decode.
2. `ltx25_dev_two_stage_5090.json`: development transformer for guided stage 1, spatial latent upscaling, distilled LoRA refinement for stage 2, and diffusion-VAE decode.
3. `ltx25_distilled_lora_5090.json`: the same development transformer with the distilled LoRA applied for fast 8-step T2V/I2V sampling.

## Models

Store the following exact official assets without URL query strings:

- `diffusion_models/ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors`
- `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors`
- `vae/ltx-2.5-video-vae-bf16.safetensors`
- `vae/ltx-2.5-audio-vae-bf16.safetensors`
- `loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors`
- `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors`

These six files have distinct runtime roles: base transformer, text encoder, video decoder, audio decoder, fast-sampling adapter, and spatial latent upscaler. Every workflow reuses them; the preset contains no alternative checkpoint or fallback model.

The diffusion VAE is the only video VAE. The convolutional VAE is not installed. The separate distilled transformer is not installed because the development transformer plus distilled LoRA supplies the requested fast mode. The temporal upscaler and duration head are not used by these workflows and are therefore not installed. The optional Gemma prompt-enhancer checkpoint is also omitted; prompts go directly through the LTX 2.5 text encoder.

The Hugging Face repository is gated. Arrakis records the public resolve URLs, while installation relies on the user's existing authenticated Hugging Face environment.

## Custom Nodes

Install these nodes once:

- `adbrasi/ComfyUI-LTXVideo`, synchronized with the official LTX 2.5 update while retaining the existing Kornia 0.8.3 compatibility fix.
- `kijai/ComfyUI-KJNodes` for `LTX2SamplingPreviewOverride` and existing 5090-oriented helpers.
- `Kosinkadink/ComfyUI-VideoHelperSuite` for video-combine compatibility with existing Arrakis workflows.

The new workflows keep ComfyUI's native `VIDEO` data flow and `SaveVideo` for synchronized audio/video output. VideoHelperSuite is installed for interoperability; converting the native `VIDEO` object to a legacy image/audio branch solely to use `VHS_VideoCombine` would add an unnecessary parallel path.

No TAELTX 2.5 model is registered because none is published. The KJNodes sampling-preview override is used without pretending that TAELTX 2.3 is compatible with LTX 2.5.

## Runtime and Data Flow

The preset requires current ComfyUI LTX 2.5 core support. Model loading uses the single ComfyUI INT8 ConvRot development checkpoint, with normal ComfyUI offload behavior for the RTX 5090's 32 GB VRAM.

The dev single-stage workflow runs full guided sampling before decoding with tiled `ltx-2.5-video-vae-bf16`. The dev two-stage workflow performs guided low-resolution generation, 2x latent spatial upscaling, distilled-LoRA refinement, then tiled diffusion-VAE decode. The fast workflow applies the distilled LoRA to the development transformer and uses the official distilled 8-step sigma schedule.

The KJNodes preview override sits on the sampling model path. Final output always comes from the real LTX 2.5 video VAE, never from the approximate sampler preview.

## Failure Handling

- A gated-model HTTP 401/403 remains an authentication error; the preset must not substitute another checkpoint.
- A missing current ComfyUI LTX 2.5 implementation is reported as a version/runtime requirement, not patched at launch.
- An out-of-memory failure is surfaced with the workflow's tile/offload controls; no second VAE or checkpoint is downloaded as an automatic fallback.
- Existing unrelated files, including `Redesign Arrakis Start/`, remain untouched and unstaged.

## Verification

Before publication:

1. Parse the preset and all three workflows as JSON.
2. Assert every exact URL, destination directory, filename, custom-node URL, and workflow filename occurs once in the preset.
3. Assert workflow model selections and sampler families match dev single-stage, dev two-stage, and distilled modes respectively.
4. Run the preset-focused tests, the project test suite once at the finished boundary, and `git diff --check`.
5. Commit meaningful checkpoints, push `main`, and confirm local `HEAD` equals `origin/main`.
