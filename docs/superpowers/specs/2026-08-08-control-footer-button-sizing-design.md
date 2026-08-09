# Arrakis Start Control Footer Button Sizing

## Goal

Make the persistent `REINICIAR`, `DESLIGAR`, and `DELETAR` actions easier to
see and operate without changing their hierarchy, behavior, colors, or order.

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

## Behavior

No JavaScript or endpoint behavior changes. Restart, shutdown, and installed
preset management retain their current event handlers and lifecycle guards.

## Verification

The browser smoke must assert the computed minimum dimensions of all three
footer buttons and retain the existing responsive overflow and interaction
checks at the four supported viewport widths.
