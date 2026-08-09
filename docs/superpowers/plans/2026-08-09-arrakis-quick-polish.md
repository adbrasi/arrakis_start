# Arrakis Start Quick Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved small UI fixes, restart ComfyUI with the currently typed extra flags, and register `upscale_smooth` in the four requested presets.

**Architecture:** Keep the current HTML/CSS/vanilla-JavaScript UI and threaded Python HTTP handler intact. Make local changes at the existing interaction, request, and preset-array boundaries; do not introduce new components, persistent state, or alternate restart paths.

**Tech Stack:** HTML, CSS, vanilla JavaScript, Python 3 `http.server`, `unittest`, Playwright browser smoke tests, JSON presets.

## Global Constraints

- Use the approved fast, simple approach; do not perform a broad refactor.
- Keep preset title typography unchanged.
- Set descriptions to 14px and supporting copy to 12px.
- Rename the footer action to `GERENCIAR`; removal remains explicit inside the dialog.
- Restart flags are runtime-only and must not be persisted.
- Add `https://github.com/adbrasi/upscale_smooth` exactly once to each target preset.
- Preserve the existing restart operation reservation and conflict handling.
- Shutdown and restart must leave no launcher or `main.py` process owned by this exact `COMFY_DIR`.
- Preserve the untracked `Redesign Arrakis Start/` directory.

---

### Task 1: Register `upscale_smooth` in target presets

**Files:**
- Modify: `tests/test_presets.py`
- Modify: `presets/anima3-studio.json`
- Modify: `presets/ltx23-anime-production.json`
- Modify: `presets/minimax-h3-5090.json`
- Modify: `presets/minimax-h3-6000pro-96gb.json`

**Interfaces:**
- Consumes: `start.load_presets() -> list[dict]`
- Produces: each target preset `nodes` array containing the exact repository URL once

- [ ] **Step 1: Write the failing preset test**

Add this test class to `tests/test_presets.py`:

```python
class UpscaleSmoothPresetTests(unittest.TestCase):
    TARGET_PRESETS = {
        "anima3-studio.json",
        "ltx23-anime-production.json",
        "minimax-h3-5090.json",
        "minimax-h3-6000pro-96gb.json",
    }
    NODE_URL = "https://github.com/adbrasi/upscale_smooth"

    def test_target_presets_include_upscale_smooth_exactly_once(self):
        presets = {preset["_filename"]: preset for preset in start.load_presets()}

        for filename in self.TARGET_PRESETS:
            with self.subTest(filename=filename):
                self.assertEqual(presets[filename]["nodes"].count(self.NODE_URL), 1)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_presets.UpscaleSmoothPresetTests -v
```

Expected: FAIL because all four counts are currently zero.

- [ ] **Step 3: Add the exact node URL to the four JSON arrays**

Append this value once to each target preset's `nodes` array without reordering or changing unrelated metadata:

```json
"https://github.com/adbrasi/upscale_smooth"
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
python -m unittest tests.test_presets.UpscaleSmoothPresetTests -v
```

Expected: PASS for all four subtests.

- [ ] **Step 5: Commit the preset checkpoint**

```bash
git add tests/test_presets.py presets/anima3-studio.json presets/ltx23-anime-production.json presets/minimax-h3-5090.json presets/minimax-h3-6000pro-96gb.json
git commit -m "feat: adiciona upscale smooth aos presets de vídeo"
```

---

### Task 2: Fix clickable affordance, management copy, typography, and restart request

**Files:**
- Modify: `tests/ui_smoke.cjs`
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`

**Interfaces:**
- Consumes: `#extra-flags-input` current text value
- Produces: `readExtraFlags() -> string[]`
- Produces: `POST /api/restart` JSON body `{ "extra_flags": string[] }`

- [ ] **Step 1: Extend the browser smoke assertions**

In `verifyCatalogInteraction`, assert the card and recent row expose their actual click behavior:

```javascript
assert.equal(await card.evaluate(element => getComputedStyle(element).cursor), "pointer");
assert.equal(
    await page.locator(".recent-row").first().evaluate(element => getComputedStyle(element).cursor),
    "pointer",
);
```

In the desktop flow, assert the new management copy and approved typography:

```javascript
assert.match(await desktop.locator("#manage-btn").textContent(), /GERENCIAR/);
assert.equal(
    await desktop.locator(".preset-description").first().evaluate(
        element => getComputedStyle(element).fontSize,
    ),
    "14px",
);
for (const selector of [".preset-meta", ".control-label", ".activity-line"]) {
    assert.equal(
        await desktop.locator(selector).first().evaluate(element => getComputedStyle(element).fontSize),
        "12px",
    );
}
```

Update the restart assertion in `verifyLifecycleRequests`:

```javascript
assertActionRequest(restartRequest, "/api/restart", {
    extra_flags: ["--disable-xformers", "--preview-method", "auto"],
});
```

- [ ] **Step 2: Run the browser smoke and verify RED**

Run:

```bash
node tests/ui_smoke.cjs
```

Expected: FAIL on the current cursor, label, typography, or missing restart body.

- [ ] **Step 3: Apply the minimal HTML and CSS changes**

In `web/index.html`, replace the trash SVG with a management/list SVG and change only the visible footer copy:

```html
GERENCIAR
```

In `web/styles.css`:

- change `--text-secondary` to `#aaa3bc`;
- add `cursor: pointer` to `.preset-card`, `.recent-row`, and `.workflow-link`;
- keep disabled inputs/buttons at `cursor: not-allowed`;
- set `.preset-description` to `14px`;
- set supporting selectors to `12px`: `.section-hint`, `.preset-meta`, `.workflow-link`, `.catalog-empty`, `.status-port`, `.control-label`, `.flags-block input`, `.queue-row`, `.queue-total`, `.progress-summary`, `.activity-line`, `.toast`, and `.manage-empty`.

Do not alter `.preset-name`.

- [ ] **Step 4: Send current flags in the existing restart request**

In `web/app.js`, add one local parser and reuse it from install and restart:

```javascript
function readExtraFlags() {
    return document.getElementById("extra-flags-input")
        .value
        .trim()
        .split(/\s+/)
        .filter(Boolean);
}
```

Replace the install-local parsing with `readExtraFlags()`. Add the restart request body:

```javascript
body: JSON.stringify({ extra_flags: readExtraFlags() }),
```

- [ ] **Step 5: Run the browser smoke and verify GREEN**

Run:

```bash
node tests/ui_smoke.cjs
UI_INSTALLING=1 node tests/ui_smoke.cjs
```

Expected: both normal and installing-state smoke runs PASS without console errors.

- [ ] **Step 6: Commit the UI checkpoint**

```bash
git add tests/ui_smoke.cjs web/index.html web/styles.css web/app.js
git commit -m "fix: melhora interações e leitura da interface"
```

---

### Task 3: Apply current flags in the restart backend

**Files:**
- Modify: `tests/test_web_ui.py`
- Modify: `server.py`

**Interfaces:**
- Consumes: optional `/api/restart` JSON object with `extra_flags: list[str]`
- Calls: `ProcessManager.start(flags: Optional[list[str]]) -> bool`
- Preserves: `reserve_restart_slot()` / `finish_restart_reservation()` lifecycle

- [ ] **Step 1: Add restart request support to the test helper**

Replace `post_restart()` in `UninstallEndpointTests` with:

```python
def post_restart(self, extra_flags=None):
    connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
    body = None if extra_flags is None else json.dumps({"extra_flags": extra_flags})
    headers = {} if body is None else {"Content-Type": "application/json"}
    try:
        connection.request("POST", "/api/restart", body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode())
    finally:
        connection.close()
```

- [ ] **Step 2: Write failing backend tests**

Add one test proving flags reach the asynchronous start call:

```python
def test_restart_passes_current_extra_flags_to_start(self):
    restarted = threading.Event()
    process_manager = Mock()
    process_manager.ensure_stopped.return_value = True
    process_manager.start.side_effect = lambda **_kwargs: restarted.set() or True
    flags = ["--disable-xformers", "--preview-method", "auto"]

    with patch.object(server, "_state_manager", object()), \
            patch("process_manager.ProcessManager", return_value=process_manager), \
            patch("time.sleep"):
        status, _ = self.post_restart(flags)
        self.assertEqual(status, 202)
        self.assertTrue(restarted.wait(timeout=2))

    process_manager.start.assert_called_once_with(flags=flags)
```

Add a validation test that sends `extra_flags` as a string, expects HTTP 400, asserts `ProcessManager` was not constructed, and proves `start.reserve_install_slot()` succeeds immediately afterward.

```python
def test_restart_rejects_non_list_extra_flags(self):
    with patch("process_manager.ProcessManager") as process_manager:
        status, payload = self.post_restart("--disable-xformers")

    self.assertEqual(status, 400)
    self.assertEqual(payload, {"error": "extra_flags deve ser uma lista de strings"})
    process_manager.assert_not_called()
    self.assertTrue(start.reserve_install_slot())
    start.finish_install_reservation("failed")
```

- [ ] **Step 3: Run the focused backend tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_web_ui.UninstallEndpointTests.test_restart_passes_current_extra_flags_to_start \
  tests.test_web_ui.UninstallEndpointTests.test_restart_rejects_non_list_extra_flags \
  -v
```

Expected: FAIL because the handler currently ignores the request body.

- [ ] **Step 4: Parse and validate the optional body before reserving restart**

At the start of `PresetHandler._handle_restart`:

```python
try:
    content_length = int(self.headers.get("Content-Length", "0"))
except ValueError:
    self._send_json_error(400, "Content-Length inválido")
    return
if content_length < 0:
    self._send_json_error(400, "Content-Length inválido")
    return
if content_length > 1024 * 1024:
    self._send_json_error(413, "Request body too large")
    return
try:
    data = json.loads(self.rfile.read(content_length).decode()) if content_length else {}
except (UnicodeDecodeError, json.JSONDecodeError):
    self._send_json_error(400, "JSON inválido")
    return
if not isinstance(data, dict):
    self._send_json_error(400, "O corpo deve ser um objeto JSON")
    return
extra_flags = data.get("extra_flags", [])
if not isinstance(extra_flags, list) or not all(isinstance(flag, str) for flag in extra_flags):
    self._send_json_error(400, "extra_flags deve ser uma lista de strings")
    return
```

In the existing restart worker, replace `pm.start()` with:

```python
started = pm.start(flags=extra_flags) if extra_flags else pm.start()
```

- [ ] **Step 5: Run the restart tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_web_ui.UninstallEndpointTests -v
```

Expected: all restart, uninstall, and shutdown serialization tests PASS.

- [ ] **Step 6: Commit the backend checkpoint**

```bash
git add tests/test_web_ui.py server.py
git commit -m "fix: aplica flags atuais ao reiniciar ComfyUI"
```

---

### Task 4: Stop every managed ComfyUI process

**Files:**
- Create: `tests/test_process_manager.py`
- Modify: `tests/test_runtime_stack.py`
- Modify: `process_manager.py`
- Modify: `server.py`

**Interfaces:**
- Produces: `ProcessManager._managed_comfy_server_pids() -> list[int]`
- Preserves: strict command-line ownership under the exact configured `COMFY_DIR`

- [ ] **Step 1: Reproduce the orphaned process failure**

Create two real temporary `ComfyUI/main.py` processes. Track only the first,
mock the configured port as unused, call `ensure_stopped()`, and assert both
processes exit. Also assert `_shutdown_runtime()` calls `ensure_stopped()` when
`is_running()` reports false.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python -m unittest tests.test_process_manager tests.test_runtime_stack.InstallCoordinatorTests.test_idle_shutdown_still_sweeps_residual_processes -v
```

- [ ] **Step 3: Implement the strict residual-process sweep**

Identify only the exact configured ComfyUI launcher and `COMFY_DIR/main.py`,
terminate every matching PID after the tracked/port paths, rescan to prove none
remain, and call `ensure_stopped()` unconditionally during shutdown.

- [ ] **Step 4: Run focused lifecycle tests and verify GREEN**

```bash
python -m unittest tests.test_process_manager tests.test_runtime_stack.InstallCoordinatorTests -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_process_manager.py tests/test_runtime_stack.py process_manager.py server.py
git commit -m "fix: encerra todos os processos gerenciados do ComfyUI"
```

---

### Task 5: Run the finished batch gate

**Files:**
- Verify only: all files modified in Tasks 1 through 3

**Interfaces:**
- Consumes: the three committed checkpoints
- Produces: one merge/test-ready local branch with no pushed remote changes

- [ ] **Step 1: Validate syntax, data, tests, and whitespace once**

Run:

```bash
python -m json.tool presets/anima3-studio.json >/dev/null
python -m json.tool presets/ltx23-anime-production.json >/dev/null
python -m json.tool presets/minimax-h3-5090.json >/dev/null
python -m json.tool presets/minimax-h3-6000pro-96gb.json >/dev/null
python -m py_compile server.py start.py process_manager.py
python -m unittest discover -s tests -v
node tests/ui_smoke.cjs
UI_INSTALLING=1 node tests/ui_smoke.cjs
git diff --check HEAD~3..HEAD
```

Expected: all commands exit zero, both browser states pass, and the working tree contains only the pre-existing untracked redesign directory.

- [ ] **Step 2: Report the local test command**

Give the user the shortest command that exercises the updated checkout without pushing:

```bash
curl -L https://raw.githubusercontent.com/adbrasi/arrakis_start/main/bootstrap.sh | bash
```

State clearly that this command uses the remote `main` only after a later explicit push; before push, local testing must run from the implemented local checkout.
