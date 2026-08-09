# Arrakis Start Full-Width Control Panel and Footer Sizing

## Goal

Use the available desktop viewport instead of capping the application at
`1280px`. Give the right control panel enough responsive width to improve log
readability and make the persistent `REINICIAR`, `DESLIGAR`, and `DELETAR`
actions easier to see and operate without changing their hierarchy, behavior,
colors, or order.

## Desktop Layout Contract

- The application shell uses `100%` of the available viewport width and has no
  fixed maximum width.
- The right control panel width is `clamp(360px, 30vw, 480px)`.
- The preset catalog receives all remaining width through `minmax(0, 1fr)`.
- The right panel keeps its current sticky, full-viewport-height behavior.
- Activity logs use the additional panel width naturally, reducing line wraps
  without truncation or horizontal scrolling.

## Visual Contract

- All three footer buttons keep equal-width columns.
- Button minimum height increases from `52px` to `68px`.
- Label size increases from `9px` to `11px`.
- SVG icons increase from `16px` to `20px`.
- Existing spacing, square geometry, focus treatment, hover treatment, disabled
  treatment, and destructive color remain unchanged.

## Responsive Contract

- The `68px` minimum height applies at every supported viewport.
- The three buttons remain in one row at `375px`, `768px`, `1024px`, and
  `1440px` without horizontal overflow.
- Every button remains larger than the existing `44px` minimum touch target.
- At widths up to `920px`, the control panel keeps the existing stacked layout,
  fills the page width, and no longer uses the desktop clamp.

## Behavior

No JavaScript or endpoint behavior changes. Restart, shutdown, and installed
preset management retain their current event handlers and lifecycle guards.

## Verification

The browser smoke must assert:

- the computed minimum dimensions of all three footer buttons;
- full shell width and the clamped desktop control-panel width;
- absence of horizontal overflow at the four supported viewport widths;
- existing restart, shutdown, delete-manager, focus, and responsive behavior.
