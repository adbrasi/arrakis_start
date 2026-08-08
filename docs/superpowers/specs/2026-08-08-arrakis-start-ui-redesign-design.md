# Arrakis Start UI Redesign

**Status:** Approved for implementation planning

**Date:** 2026-08-08

## Objective

Replace the current generic preset-card grid with the approved dense, two-panel interface while preserving the existing installer lifecycle, safe resume behavior, workflow downloads, ComfyUI controls, and per-preset uninstall flow.

The generated file in `Redesign Arrakis Start/Arrakis Start Redesign.dc.html` is a visual reference only. Its generated runtime and hard-coded data will not become part of the production application.

## Design principles

- Keep the existing vanilla HTML, CSS, and JavaScript architecture.
- Keep `/api/status` as the single live-status channel; do not add a WebSocket or parallel progress path.
- Treat server data as authoritative. Do not add client-side favorites, ordering, or persistence.
- Never invent download totals that the backend cannot prove.
- Preserve safe cancellation, resumable downloads, honest installed state, and selective preset removal.

## Page structure

The page remains a single application view centered at a maximum width of 1280 pixels.

### Preset catalog

The left panel uses the approved brutalist presentation:

1. A `FIXADOS` section contains pinned presets in a three-column card grid.
2. A `RECENTES - MODIFICADO` section contains every unpinned preset in compact rows, newest modification first.

Pinned cards show the selection control, preset name, a two-line description, estimated size, model and node counts, installed state, and workflow links. Recent rows expose the same information in a denser arrangement.

The entire selection area of a card or row toggles its checkbox. Workflow links remain independent controls and never toggle selection. Multiple selections form an ordered install queue.

There is no product header, logo, search box, or category filter.

### Control panel

The right panel is 300 pixels wide on desktop, sticky at the top, and constrained to the viewport height. It contains, in order:

1. ComfyUI status and port.
2. Extra-flags input.
3. Selected preset queue, estimated total size, primary install action, cancel action, and progress.
4. Live installation activity.
5. Persistent `REINICIAR`, `DESLIGAR`, and `DELETAR` actions.

The queue is independently scrollable when many presets are selected so the activity and footer actions remain reachable.

## Visual system

The production CSS follows the approved reference palette and square geometry:

- Page background: `#0d0b11`
- Catalog panel: `#131118`
- Control panel: `#0f0d15`
- Section bands: `#18151f`
- Structural borders: `#2b2738`, generally two pixels
- Primary text: `#eae6f2`
- Secondary text: `#87809c` and `#b0a9c4`
- Muted text: `#6b6480`
- Primary accent: `#a284f2`
- Accent hover: `#c4b2f7`
- Online status: `#7fd4d0`
- Installed state: `#7fd4a8`
- Workflow links: `#8bb8f2`
- Destructive controls: `#d178b8`

Controls, panels, cards, and dialogs use square corners. `JetBrains Mono` is the base interface font; `Space Grotesk` is reserved for preset names. System monospace and sans-serif fallbacks keep the interface usable if remote fonts fail.

Inline SVG icons replace emoji or text-symbol icons for restart, power, delete, close, and workflow-download actions.

## Preset metadata and ordering

Active preset JSON files gain these author-maintained fields:

```json
{
  "pinned": true,
  "size_gb": 42
}
```

`pinned` defaults to `false`. `size_gb` is a positive estimated number representing the complete preset payload. All active presets should provide it. The API remains defensive: if it is missing or invalid, it returns `null`, the UI displays an unknown-size marker, and queue totals clearly indicate that the known sum is incomplete.

The initial pinned set follows the approved mockup:

- `Anima 3 Studio`
- `Krea 2 Full`
- `MiniMax H3 5090`

The server computes each preset's modification timestamp from the latest Git commit that touched its JSON file. This survives fresh cloud clones, where filesystem modification times are not meaningful. An uncommitted preset falls back to its filesystem `mtime`.

`load_presets()` orders presets by modification timestamp descending, then by filename for deterministic ties. Filtering that ordered response into pinned and recent collections preserves one authoritative server order without client persistence.

## API contract

`GET /api/presets` preserves every existing field and adds:

```json
{
  "pinned": true,
  "size_gb": 42,
  "modified_at": 1786147200
}
```

`modified_at` is a Unix timestamp in seconds. The browser formats it as a compact `DD/MM` label using the user's locale.

No new endpoint is required. Installation status and progress continue to come from `GET /api/status`.

## Installation progress and activity

The global progress bar uses the backend's proven `progress.done` and `progress.total` model counts. It does not divide downloaded bytes by the selected presets' estimated `size_gb` values.

The activity panel renders the existing progress registry:

- Current lane messages from `progress.stages`
- Active filenames, backend, bytes, total when known, and speed
- Recent completed or failed transfers from `progress.recent`

This is the canonical browser-facing activity stream. It avoids exposing arbitrary process logs or secrets and avoids introducing a second logging system.

During installation, the primary action shows an installing state and is disabled. The cancel action becomes available and keeps the existing confirmation that completed files are preserved and partial downloads can be resumed.

## Runtime actions

- `REINICIAR` keeps the existing `/api/restart` behavior and is unavailable during installation.
- `DESLIGAR` keeps the existing `/api/shutdown` behavior and destructive confirmation.
- `DELETAR` opens the installed-preset manager. It does not duplicate shutdown.

The installed-preset manager lists installed presets with one remove action per row. Removal keeps the existing `/api/uninstall` contract, confirmation copy, shared-model protection, custom-node preservation, result summary, and server-side installation conflict guard. The manager exposes an explicit empty state when nothing is installed.

The existing floating delete button and rounded legacy popup are removed completely. The manager is rebuilt as a square-corner dialog using the redesign's palette, typography, borders, spacing, focus states, and responsive rules. Only the proven uninstall behavior is reused; no legacy manager markup or visual classes remain.

## State and error handling

The redesign preserves the current status priority:

1. An active installation overrides ComfyUI process state.
2. Terminal installation states distinguish `cancelled`, `failed`, `start_failed`, and `completed_with_failures`.
3. A failed status request displays an unreachable state instead of claiming ComfyUI is stopped.

Network and server failures remain visible through toasts near the control panel. Status and toast containers use live-region semantics so important changes are announced without moving keyboard focus.

Selections survive DOM rerenders within the current page session but are not persisted across reloads. Presets removed or renamed by the server are pruned from the selection queue.

## Responsive behavior

- Above 920 pixels, the catalog and 300-pixel control panel remain side by side.
- At 920 pixels and below, the control panel becomes a normal full-width block after the catalog and no longer uses viewport-sticky positioning.
- The pinned grid changes from three columns to two, then one below 640 pixels.
- Recent rows become compact stacked cards below 640 pixels so no horizontal scrolling is required.
- Action targets are at least 44 pixels high on touch layouts.

## Accessibility and motion

- Native buttons, inputs, labels, and links retain their semantic roles.
- Selection controls are keyboard reachable and have visible focus treatment.
- Icon-only controls receive explicit accessible names.
- Status, installed state, selection, and failures are never communicated by color alone.
- Animations are limited to short opacity or transform transitions and are disabled by `prefers-reduced-motion: reduce`.
- Text and interactive states must retain at least WCAG AA contrast against their production backgrounds.

## Verification strategy

Focused backend tests will prove:

- Latest-modification timestamps use Git history and fall back to `mtime` for uncommitted files.
- Preset ordering is deterministic.
- `/api/presets` exposes valid `pinned`, `size_gb`, and `modified_at` values while handling invalid optional metadata safely.

Frontend verification will prove:

- JavaScript syntax remains valid.
- Preset selection, queue totals, workflow links, install/cancel states, progress rendering, restart, shutdown, and preset removal remain wired to their current endpoints.
- The layout has no horizontal overflow at 375, 768, 1024, and 1440 pixels.
- Keyboard focus, dialog access, reduced motion, and empty/error states remain usable.

The finished feature receives one full project gate at the implementation boundary, followed by a local visual/runtime smoke before handoff.

## Out of scope

- Importing `support.js` or the generated Design Canvas runtime
- React, Tailwind, or another frontend framework
- Search, categories, filters, or client-side favorites
- WebSockets or a second progress/logging channel
- Changes to download, resume, install, uninstall, restart, or shutdown semantics
