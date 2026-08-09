# Full-Width Control Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the full desktop viewport, widen the control panel responsively, and enlarge the three persistent footer actions.

**Architecture:** Keep the existing two-column CSS Grid and responsive breakpoint. Replace only the shell width and desktop panel track, then strengthen the existing browser smoke with computed-geometry assertions before applying the minimal CSS delta.

**Tech Stack:** HTML, CSS Grid, vanilla JavaScript, Node.js 22, Playwright smoke harness

## Global Constraints

- The application shell uses `100%` of the available viewport width with no fixed maximum width.
- The desktop columns are `minmax(0, 1fr) clamp(360px, 30vw, 480px)`.
- The stacked layout remains active through `920px`.
- Footer buttons use a `68px` minimum height, `11px` labels, and `20px` SVG icons.
- Existing behavior, colors, order, focus, hover, disabled, and destructive treatments remain unchanged.
- No new dependency or JavaScript application behavior is allowed.

---

### Task 1: Full-width shell, responsive control panel, and enlarged footer actions

**Files:**
- Modify: `tests/ui_smoke.cjs:692-735`
- Modify: `web/styles.css:44-52, 183-187, 234-258`

**Interfaces:**
- Consumes: existing `.app-shell`, `.control-panel`, `.control-footer`, `#restart-btn`, `#shutdown-btn`, and `#manage-btn` DOM/CSS contracts
- Produces: computed shell, panel, button, and SVG geometry validated at `375px`, `768px`, `1024px`, and `1440px`

- [ ] **Step 1: Add failing geometry assertions to the responsive smoke**

Inside `verifyResponsiveAccessibility()`, extend the existing `page.evaluate()` result so every viewport captures the shell, panel, footer buttons, and icons:

```javascript
const dimensions = await page.evaluate(() => {
    const shell = document.querySelector(".app-shell").getBoundingClientRect();
    const panel = document.getElementById("control-panel").getBoundingClientRect();
    const footerButtons = [...document.querySelectorAll(".control-footer button")].map(button => {
        const rect = button.getBoundingClientRect();
        const icon = button.querySelector("svg").getBoundingClientRect();
        return {
            height: rect.height,
            fontSize: parseFloat(getComputedStyle(button).fontSize),
            iconWidth: icon.width,
            iconHeight: icon.height,
        };
    });
    return {
        page: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
        shellWidth: shell.width,
        panelWidth: panel.width,
        footerButtons,
    };
});

assert.ok(Math.abs(dimensions.shellWidth - width) <= 1, `shell does not fill ${width}px viewport`);
if (width > 920) {
    const expectedPanelWidth = Math.min(480, Math.max(360, width * 0.30));
    assert.ok(
        Math.abs(dimensions.panelWidth - expectedPanelWidth) <= 1,
        `control panel width is wrong at ${width}px`,
    );
}
for (const button of dimensions.footerButtons) {
    assert.ok(button.height >= 68, `footer button is shorter than 68px at ${width}px`);
    assert.equal(button.fontSize, 11);
    assert.equal(button.iconWidth, 20);
    assert.equal(button.iconHeight, 20);
}
```

- [ ] **Step 2: Run the smoke and verify the new assertions fail**

Run:

```bash
node tests/ui_smoke.cjs
```

Expected: FAIL because the shell is capped at `1280px`, the panel is `300px`, and footer buttons still use `52px`, `9px`, and `16px`.

- [ ] **Step 3: Apply the minimal CSS layout and sizing change**

Change the shell and desktop grid contract:

```css
.app-shell {
    width: 100%;
    min-height: 100vh;
    margin: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) clamp(360px, 30vw, 480px);
    background: var(--catalog-bg);
    border-inline: 2px solid var(--border);
}
```

Change only the approved footer dimensions:

```css
.control-footer button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    min-width: 0;
    min-height: 68px;
    padding: 7px 4px;
    color: var(--text-secondary);
    background: transparent;
    border: 0;
    border-right: 1px solid var(--border);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .03em;
    transition: color 180ms ease, background-color 180ms ease, opacity 180ms ease;
}
.control-footer svg { width: 20px; height: 20px; fill: currentColor; }
```

Remove `.control-footer button` from the mobile `min-height: 44px` selector group and delete the redundant mobile `.control-footer button { min-height: 44px; }` rule so the approved `68px` base value remains effective at every viewport.

- [ ] **Step 4: Run the focused browser smoke**

Run:

```bash
node tests/ui_smoke.cjs
```

Expected: PASS with full-width shell, clamped panel, enlarged footer geometry, no horizontal overflow, and existing action/focus behavior intact.

- [ ] **Step 5: Run syntax and diff checks**

Run:

```bash
node --check tests/ui_smoke.cjs
git diff --check
```

Expected: both commands exit `0`.

- [ ] **Step 6: Commit the implementation**

```bash
git add tests/ui_smoke.cjs web/styles.css
git commit -m "Amplia painel e botões de controle"
```
