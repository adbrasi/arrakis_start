# XET Download Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve XET as the fast Hugging Face path, deduplicate model work, make cancellation terminal, and make Shutdown delete only incomplete model data.

**Architecture:** `DownloadManager` remains the single owner of model transfer state. XET keeps the disk observer for progress only, while HTTP retains disk-stall termination. Queue normalization happens before worker submission, and cancellation carries an explicit `delete_partials` policy from the server through `start.py` to the downloader.

**Tech Stack:** Python 3.12, `unittest`, `huggingface_hub`/`hf_xet`, browser JavaScript, Git.

## Global Constraints

- XET is the first Hugging Face backend and cannot be terminated solely because local staging bytes did not grow.
- HTTP fallback starts only after the XET subprocess exits unsuccessfully.
- Cancel preserves partial model data.
- Shutdown deletes incomplete model data and preserves completed final models.
- Different sources targeting the same destination are configuration errors.
- User-facing logs and UI text are in Portuguese; code identifiers and comments are in English.

---

### Task 1: Make the model queue unique and cancellation terminal

**Files:**
- Modify: `downloader.py:1001-1154`
- Modify: `tests/test_downloader.py`

**Interfaces:**
- Consumes: preset model dictionaries with `url`, `dir`, and `filename`.
- Produces: `DownloadManager._deduplicate_downloads(downloads: List[Dict]) -> Tuple[List[Dict], int]`.
- Produces: `DownloadManager.download_all()` using unique destination work only.

- [ ] **Step 1: Write failing queue and cancellation tests**

Add these behaviors to `tests/test_downloader.py`:

```python
def test_identical_destinations_are_scheduled_once(self):
    manager = self._manager(Path('/tmp/models'))
    item = {
        'url': 'https://huggingface.co/org/repo/resolve/main/model.safetensors',
        'dir': 'loras',
        'filename': 'model.safetensors',
    }

    unique, removed = manager._deduplicate_downloads([item, dict(item)])

    self.assertEqual(unique, [item])
    self.assertEqual(removed, 1)


def test_conflicting_sources_for_one_destination_are_rejected(self):
    manager = self._manager(Path('/tmp/models'))
    first = {'url': 'https://example.com/one', 'dir': 'loras', 'filename': 'same.bin'}
    second = {'url': 'https://example.com/two', 'dir': 'loras', 'filename': 'same.bin'}

    with self.assertRaisesRegex(ValueError, 'same.bin'):
        manager._deduplicate_downloads([first, second])


def test_cancelled_download_is_not_retried_or_recorded_as_failure(self):
    manager = self._manager(Path('/tmp/models'))
    manager._cancelled = False
    manager._failures_lock = threading.Lock()
    manager.failures = []

    def cancelled(*_args):
        manager._cancelled = True
        return False, 'cancelled_by_user', 'cancel'

    with patch.object(manager, '_download_file', side_effect=cancelled) as download:
        result = manager._download_one_with_retry(
            {'url': 'https://example.com/model', 'dir': 'loras', 'filename': 'model.bin'},
            '[1/1]',
        )

    self.assertFalse(result)
    download.assert_called_once()
    self.assertEqual(manager.failures, [])
```

Import `threading` in the test module.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_downloader.DownloadStagingTests.test_identical_destinations_are_scheduled_once \
  tests.test_downloader.DownloadStagingTests.test_conflicting_sources_for_one_destination_are_rejected \
  tests.test_downloader.DownloadStagingTests.test_cancelled_download_is_not_retried_or_recorded_as_failure -v
```

Expected: the first two tests fail because `_deduplicate_downloads` does not exist; the cancellation test fails because `_download_one_with_retry` emits a retry and invokes `_download_file` again.

- [ ] **Step 3: Implement destination deduplication**

Add a helper before `download_all()`:

```python
@staticmethod
def _deduplicate_downloads(downloads: List[Dict]) -> Tuple[List[Dict], int]:
    unique: List[Dict] = []
    seen: Dict[Tuple[str, str], Dict] = {}
    source_only = set()
    removed = 0

    for item in downloads:
        url = str(item.get('url') or '').strip()
        directory = str(item.get('dir') or '').strip().strip('/')
        filename = str(item.get('filename') or '').strip()
        if not filename:
            source_key = (directory, url)
            if source_key in source_only:
                removed += 1
                continue
            source_only.add(source_key)
            unique.append(item)
            continue

        key = (directory, filename)
        previous = seen.get(key)
        if previous is None:
            seen[key] = item
            unique.append(item)
            continue
        previous_url = str(previous.get('url') or '').split('?', 1)[0]
        current_url = url.split('?', 1)[0]
        if previous_url != current_url:
            raise ValueError(
                f"Conflicting model sources for {directory}/{filename}: "
                f"{previous_url} != {current_url}"
            )
        removed += 1

    return unique, removed
```

Call it at the beginning of `download_all()`. Log
`Fila de modelos: {raw} entradas, {unique} destinos únicos ({removed} duplicatas removidas)`
when duplicates exist. On `ValueError`, log the conflict and return `False`
before creating the executor.

- [ ] **Step 4: Make cancellation terminal**

Immediately after `_download_file()` returns:

```python
if self._cancelled or stage == 'cancel' or reason == 'cancelled_by_user':
    return False
```

In `download_all()`, stop waiting on queued work once `_cancelled` becomes true,
cancel futures that have not started, and log a cancellation summary instead of
`Downloaded X/Y files successfully`.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_downloader -v
```

Expected: all downloader tests pass and cancellation emits no retry.

- [ ] **Step 6: Commit Task 1**

```bash
git add downloader.py tests/test_downloader.py
git commit -m "Corrige fila e cancelamento de downloads"
```

---

### Task 2: Keep XET alive through local staging silence

**Files:**
- Modify: `downloader.py:703-768`
- Modify: `downloader.py:1312-1426`
- Modify: `tests/test_downloader.py`
- Modify: `README.md:240-257`

**Interfaces:**
- Consumes: `_run_disk_watchdog()` process and staging paths.
- Produces: `_run_disk_watchdog(..., terminate_on_stall: bool = True, backend_label: str = "HTTP")`.
- Preserves: HTTP fallback disk-stall termination.

- [ ] **Step 1: Write a failing XET liveness test**

Add a fake process and test:

```python
class _ProcessSequence:
    def __init__(self, states):
        self._states = iter(states)

    def poll(self):
        return next(self._states)


def test_xet_observer_does_not_kill_live_process_on_local_disk_silence(self):
    manager = self._manager(Path('/tmp/models'))
    manager._cancelled = False
    manager.aria2_stall_timeout_seconds = 1
    state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0}
    process = _ProcessSequence([None, 0])

    with patch.object(manager, '_tree_bytes', return_value=(0, 0)), \
            patch.object(manager, '_terminate_process') as terminate, \
            patch('downloader.time.sleep'), \
            patch('downloader.time.monotonic', side_effect=[0.0, 2.0]):
        manager._run_disk_watchdog(
            process,
            Path('/tmp/staging'),
            Path('/tmp/final'),
            'model.safetensors',
            1024,
            state,
            terminate_on_stall=False,
            backend_label='XET',
        )

    terminate.assert_not_called()
    self.assertFalse(state['killed'])
```

- [ ] **Step 2: Run the XET test and verify RED**

Run:

```bash
python -m unittest \
  tests.test_downloader.DownloadStagingTests.test_xet_observer_does_not_kill_live_process_on_local_disk_silence -v
```

Expected: `TypeError` because the observer does not yet accept the XET policy.

- [ ] **Step 3: Separate observation from termination**

Extend `_run_disk_watchdog()` with the two keyword arguments. When no bytes
grow and `terminate_on_stall=False`, emit one informational heartbeat:

```python
logger.info(
    f"  ↳ {filename}: {backend_label} ativo; preparando/transferindo "
    "sem crescimento visível no staging local"
)
```

Do not set `stall_state['killed']` and do not call `_terminate_process()`.
Retain the current warning and timeout behavior when
`terminate_on_stall=True`.

- [ ] **Step 4: Apply the XET policy only to the primary backend**

Change the watchdog call in `_download_hf_direct()` to:

```python
kwargs={
    'terminate_on_stall': False,
    'backend_label': 'XET',
},
```

Keep `_download_hf_via_python()` on the default terminating HTTP policy.
Remove the unreachable `hf_cli_stall_timeout_*` outcome from the primary XET
path. A non-zero XET process exit remains the condition that enables HTTP
fallback.

- [ ] **Step 5: Update documentation**

Document that XET may prepare chunks without continuously growing the local
staging file, while HTTP still uses the disk-stall timeout. State that
`DOWNLOAD_OVERALL_STALL_SECONDS` is the batch hard stop.

- [ ] **Step 6: Run downloader tests and commit Task 2**

Run:

```bash
python -m unittest tests.test_downloader -v
```

Expected: all downloader tests pass.

Commit:

```bash
git add downloader.py tests/test_downloader.py README.md
git commit -m "Preserva o caminho rápido XET"
```

---

### Task 3: Give Shutdown destructive partial cleanup

**Files:**
- Modify: `downloader.py:975-987`
- Modify: `start.py:267-285`
- Modify: `server.py:398-432`
- Modify: `web/app.js:507-525`
- Modify: `tests/test_downloader.py`
- Modify: `tests/test_runtime_stack.py`
- Modify: `README.md:209-234`

**Interfaces:**
- Produces: `DownloadManager.cleanup_partials() -> Dict[str, int]`.
- Produces: `DownloadManager.cancel(delete_partials: bool = False) -> None`.
- Produces: `cancel_active_install(delete_partials: bool = False) -> bool`.
- Consumes: the Shutdown handler passes `delete_partials=True`; Cancel uses the default.

- [ ] **Step 1: Write failing cleanup tests**

Add to `tests/test_downloader.py`:

```python
def test_cleanup_partials_preserves_completed_models(self):
    with tempfile.TemporaryDirectory() as temp_dir:
        models = Path(temp_dir) / 'models'
        manager = self._manager(models)
        final = models / 'loras' / 'complete.safetensors'
        partial = models / 'loras' / 'incomplete.safetensors.arrakis.part'
        final.parent.mkdir(parents=True)
        final.write_bytes(b'complete')
        partial.write_bytes(b'partial')
        partial.with_name(f'{partial.name}.aria2').write_bytes(b'control')
        (manager.hf_partial_root / 'job').mkdir(parents=True)
        (manager.hf_partial_root / 'job' / 'chunk').write_bytes(b'xet')

        result = manager.cleanup_partials()

        self.assertTrue(final.exists())
        self.assertFalse(partial.exists())
        self.assertFalse(manager.hf_partial_root.exists())
        self.assertEqual(result['partial_payloads'], 2)


def test_normal_cancel_preserves_partials(self):
    manager = self._manager(Path('/tmp/models'))
    manager._cancelled = False
    manager._active_procs = set()
    manager._process_lock = threading.Lock()

    with patch.object(manager, 'cleanup_partials') as cleanup:
        manager.cancel()

    cleanup.assert_not_called()


def test_shutdown_cancel_deletes_partials(self):
    manager = self._manager(Path('/tmp/models'))
    manager._cancelled = False
    manager._active_procs = set()
    manager._process_lock = threading.Lock()

    with patch.object(manager, 'cleanup_partials') as cleanup:
        manager.cancel(delete_partials=True)

    cleanup.assert_called_once_with()
```

- [ ] **Step 2: Write a failing shutdown policy test**

Add to `tests/test_runtime_stack.py`:

```python
def test_shutdown_policy_is_forwarded_to_active_downloader(self):
    self.assertTrue(start.reserve_install_slot())
    downloader = Mock()

    with patch.object(start, '_active_downloader', downloader):
        self.assertTrue(start.cancel_active_install(delete_partials=True))

downloader.cancel.assert_called_once_with(delete_partials=True)
```

Add `Mock` to the existing `from unittest.mock import ...` import.
Update the existing normal cancellation assertion to require
`downloader.cancel(delete_partials=False)` when a downloader is present.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_downloader.DownloadStagingTests.test_cleanup_partials_preserves_completed_models \
  tests.test_downloader.DownloadStagingTests.test_normal_cancel_preserves_partials \
  tests.test_downloader.DownloadStagingTests.test_shutdown_cancel_deletes_partials \
  tests.test_runtime_stack.InstallCoordinatorTests.test_shutdown_policy_is_forwarded_to_active_downloader -v
```

Expected: failures for missing cleanup method and unsupported
`delete_partials` arguments.

- [ ] **Step 4: Implement precise partial cleanup**

`cleanup_partials()` must:

1. remove the exact `hf_partial_root` tree;
2. remove `*.arrakis.part` and matching `.aria2` files below `models_dir`;
3. remove legacy final/control pairs only when `<final>.aria2` exists;
4. never remove a final model without the legacy `.aria2` marker;
5. return removed payload count and bytes for logging.

Call cleanup only after `cancel()` has synchronously terminated every tracked
download subprocess.

- [ ] **Step 5: Thread the policy through start and server**

Change `cancel_active_install()` to accept `delete_partials=False` and pass the
value to the active downloader. In `_handle_shutdown.do_shutdown()`, call:

```python
from start import cancel_active_install
cancel_active_install(delete_partials=True)
```

before stopping ComfyUI and sending SIGTERM. The SIGTERM handler keeps calling
the default resume-preserving cancellation, which becomes a no-op after the
explicit shutdown cancellation.

- [ ] **Step 6: Make the UI warning explicit**

Change the confirmation to:

```javascript
if (!confirm(
    'Desligar o Arrakis Start e o ComfyUI? Downloads incompletos de modelos serão apagados.'
)) return;
```

Do not change the Cancel button text or behavior.

- [ ] **Step 7: Update cancellation documentation**

Document the exact distinction between Cancel and Shutdown and list what
Shutdown removes. Remove the old statement that `SIGTERM` and the UI shutdown
button always share resume-preserving semantics.

- [ ] **Step 8: Run focused tests and commit Task 3**

Run:

```bash
python -m unittest tests.test_downloader tests.test_runtime_stack -v
```

Expected: all focused tests pass.

Commit:

```bash
git add downloader.py start.py server.py web/app.js README.md \
  tests/test_downloader.py tests/test_runtime_stack.py
git commit -m "Limpa downloads incompletos ao desligar"
```

---

### Task 4: Verify the complete lifecycle

**Files:**
- Verify only: all modified files.

**Interfaces:**
- Consumes: the completed behavior from Tasks 1-3.
- Produces: a clean tested branch ready for a pod run.

- [ ] **Step 1: Run formatting and syntax checks**

Run:

```bash
git diff --check
python -m py_compile downloader.py start.py server.py
```

Expected: exit code 0 with no output.

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 3: Inspect the final diff and history**

Run:

```bash
git status --short --branch
git diff HEAD~3 --stat
git log -4 --oneline
```

Expected: clean working tree; one spec commit and three implementation
checkpoints on `main`.

- [ ] **Step 4: Provide the pod acceptance check**

The handoff must instruct the user to update/restart Arrakis, select the same
presets, and verify these log invariants:

```text
Fila de modelos: 58 entradas, 47 destinos únicos (11 duplicatas removidas)
HuggingFace [XET]
XET ativo; preparando/transferindo sem crescimento visível no staging local
```

The run must not contain:

```text
encerrando (SIGINT) e caindo para fallback
cancelled_by_user], retrying
Downloaded 1/58 files successfully
```

Do not push until the user explicitly requests it.
