# Arrakis Start Quick Polish Design

**Date:** 2026-08-09  
**Status:** Approved  
**Approach:** Small, direct changes without broad component or state refactoring

## Goal

Fix five concrete usability and preset issues while preserving the current Arrakis Start layout and behavior.

## UI interaction affordance

The entire preset card and recent preset row already select a preset when clicked. Their full clickable area must therefore use `cursor: pointer`, along with workflow links and enabled controls. Disabled controls must retain `cursor: not-allowed`.

This is a CSS-only correction. It does not add new click targets or change selection behavior.

## Installed preset management label

The footer action currently labeled `DELETAR` opens the installed-preset manager and does not delete immediately. Rename it to `GERENCIAR` and use a management-style SVG icon. Actual removal remains an explicit action inside the existing dialog.

## Supporting text readability

Keep preset titles unchanged. Increase:

- preset descriptions to 14px;
- metadata, workflow links, panel labels, queue details, progress summaries, activity lines, status port, and other supporting copy to 12px.

Lighten secondary copy using the existing lighter neutral palette so body and supporting text remain visually subordinate to titles while becoming easier to read. Preserve the current dark brutalist visual language and layout.

## Restart with current extra flags

The restart action must read the current `FLAGS EXTRAS` field at click time and send the parsed tokens in the `/api/restart` JSON request as `extra_flags`.

The backend must:

1. accept an optional JSON request body;
2. validate `extra_flags` as an array of strings;
3. pass the provided flags to `ProcessManager.start()` after the existing stop and wait flow;
4. preserve the current restart operation reservation and conflict handling.

An empty field means restart with the preset flags already stored in state. The flags typed into the field are runtime input for that restart and are not persisted.

## `upscale_smooth` preset registration

Add the exact custom-node repository URL `https://github.com/adbrasi/upscale_smooth` once to the `nodes` array of:

- `presets/anima3-studio.json`;
- `presets/ltx23-anime-production.json`;
- `presets/minimax-h3-5090.json`;
- `presets/minimax-h3-6000pro-96gb.json`.

No model size or workflow metadata changes are required.

## Complete ComfyUI process cleanup

Shutdown and restart must not leave managed ComfyUI launchers or `main.py`
processes behind. The current tracked-PID and port-owner checks remain the
first cleanup paths, followed by a strict sweep limited to processes whose
command line belongs to this Arrakis workspace's exact `COMFY_DIR`.

Shutdown must run `ensure_stopped()` even when the state and configured port
appear idle, because an orphaned `main.py` may own neither. Restart must only
start the replacement process after the same cleanup reports success.

## Verification

- UI smoke coverage must prove the complete clickable card/row cursor, the `GERENCIAR` label, the approved typography values, and the restart request payload.
- Backend tests must prove restart flags reach `ProcessManager.start()` and invalid flag payloads are rejected without leaking the restart reservation.
- Preset tests must prove the repository is present exactly once in all four target presets.
- Process-manager tests must prove two managed `main.py` processes are both stopped, including one that is neither tracked nor the configured port owner.
- Run the full project gate once after the complete batch is implemented.
