# Optional SageAttention Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep SageAttention-enabled presets installable and launchable through the standard PyTorch runtime when the optional SageAttention capability is unavailable.

**Architecture:** Add one fallback helper in `start.py` that validates `torch` before persisting the existing `standard` runtime marker. Route both initial SageAttention setup failures and post-install SageAttention invalidation through that helper, leaving preset completeness and ComfyUI flag persistence on their existing single paths.

**Tech Stack:** Python 3, `unittest`, `unittest.mock`, Arrakis runtime state manager

## Global Constraints

- `use_sage_attention` requests an optional acceleration capability; it does not make SageAttention part of preset completeness.
- Persist `standard` and omit `--use-sage-attention` whenever SageAttention is unavailable but `torch` imports.
- Keep runtime configuration fatal when the standard PyTorch runtime cannot import.
- Do not change preset JSON files, create per-preset exceptions, or introduce a second installation path.
- User-facing logs remain warnings for optional SageAttention loss and errors only for a broken standard runtime.

---

### Task 1: Standard-runtime fallback primitive

**Files:**
- Modify: `start.py:1211-1271`
- Test: `tests/test_runtime_stack.py:15-84`

**Interfaces:**
- Consumes: `state.get_runtime_stack() -> str`, `state.set_runtime_stack(stack: str)`, `_verify_python_import(package_name: str, python_bin: Optional[str]) -> bool`, `_comfy_python() -> str`
- Produces: `_fallback_to_standard_runtime(state, reason: str) -> bool`

- [ ] **Step 1: Write failing helper tests**

Add these tests to `SageAttentionInstallerTests`:

```python
    @patch('start._verify_python_import', return_value=True)
    def test_sage_failure_uses_launchable_standard_runtime(self, verify_import):
        state = Mock()

        result = start._fallback_to_standard_runtime(state, 'wheel unavailable')

        self.assertTrue(result)
        verify_import.assert_called_once_with(
            'torch', python_bin=start._comfy_python()
        )
        state.set_runtime_stack.assert_called_once_with('standard')

    @patch('start._verify_python_import', return_value=False)
    def test_sage_failure_remains_fatal_when_torch_is_broken(self, verify_import):
        state = Mock()

        result = start._fallback_to_standard_runtime(state, 'wheel unavailable')

        self.assertFalse(result)
        verify_import.assert_called_once_with(
            'torch', python_bin=start._comfy_python()
        )
        state.set_runtime_stack.assert_not_called()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_runtime_stack.SageAttentionInstallerTests.test_sage_failure_uses_launchable_standard_runtime \
  tests.test_runtime_stack.SageAttentionInstallerTests.test_sage_failure_remains_fatal_when_torch_is_broken -v
```

Expected: both tests error with `AttributeError: module 'start' has no attribute '_fallback_to_standard_runtime'`.

- [ ] **Step 3: Implement the fallback helper**

Insert immediately before `configure_runtime_stack`:

```python
def _fallback_to_standard_runtime(state, reason: str) -> bool:
    """Use the standard runtime when optional SageAttention is unavailable."""
    logger.warning(f"{reason}. Falling back to the standard PyTorch runtime.")
    comfy_python = _comfy_python()
    if not _verify_python_import('torch', python_bin=comfy_python):
        logger.error(
            "SageAttention is unavailable and the standard PyTorch runtime "
            "cannot be imported."
        )
        return False
    state.set_runtime_stack('standard')
    logger.warning(
        "SageAttention unavailable; continuing without --use-sage-attention."
    )
    return True
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2.

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit the fallback primitive**

```bash
git add start.py tests/test_runtime_stack.py
git commit -m "Adiciona fallback validado para runtime padrão"
```

---

### Task 2: Route SageAttention failures through the fallback

**Files:**
- Modify: `start.py:1211-1271`
- Modify: `start.py:1441-1448`
- Modify: `start.py:1746-1762`
- Test: `tests/test_runtime_stack.py:15-120`

**Interfaces:**
- Consumes: `_fallback_to_standard_runtime(state, reason: str) -> bool`, `_run_sageattention_installer(...) -> Tuple[bool, List[str]]`, `_rebuild_sageattention_for_current_torch(...) -> Tuple[bool, List[str]]`, `_can_import(package_name: str, python_bin: Optional[str]) -> bool`
- Produces: `configure_runtime_stack(use_sage_attention: bool) -> bool` where `True` means some launchable runtime is active; `_revalidate_sageattention_runtime(state) -> bool` with the same launchability contract

- [ ] **Step 1: Write failing configuration and revalidation tests**

Add these tests to `SageAttentionInstallerTests`:

```python
    @patch('start.get_state_manager')
    @patch('start._detect_runtime_stack', return_value='unknown')
    @patch('start._verify_python_import', return_value=True)
    @patch('start._run_sageattention_installer', return_value=(False, ['ABI mismatch']))
    def test_failed_installer_does_not_block_preset_or_persist_sage_flag(
        self,
        installer,
        verify_import,
        detect_stack,
        get_state_manager,
    ):
        state = get_state_manager.return_value
        runtime = {'value': 'unknown'}
        state.get_runtime_stack.side_effect = lambda: runtime['value']
        state.set_runtime_stack.side_effect = (
            lambda value: runtime.__setitem__('value', value)
        )
        state.get_installed_presets.return_value = ['Video Preset']

        result = start.configure_runtime_stack(use_sage_attention=True)
        start._persist_comfyui_flags(
            state,
            {'Video Preset': {'comfyui_flags': []}},
        )

        self.assertTrue(result)
        self.assertEqual(runtime['value'], 'standard')
        state.set_comfyui_flags.assert_called_once_with([])

    @patch('start.get_state_manager')
    @patch('start._detect_runtime_stack', return_value='unknown')
    @patch('start._verify_python_import', return_value=False)
    @patch('start._run_sageattention_installer', return_value=(False, ['ABI mismatch']))
    def test_failed_installer_blocks_when_standard_runtime_is_broken(
        self,
        installer,
        verify_import,
        detect_stack,
        get_state_manager,
    ):
        state = get_state_manager.return_value
        state.get_runtime_stack.return_value = 'unknown'

        result = start.configure_runtime_stack(use_sage_attention=True)

        self.assertFalse(result)
        state.set_runtime_stack.assert_not_called()

    @patch('start._verify_python_import', return_value=True)
    @patch('start._can_import', return_value=False)
    def test_post_install_sage_loss_falls_back_without_blocking(
        self,
        can_import,
        verify_import,
    ):
        state = Mock()
        state.get_runtime_stack.return_value = 'sageattention'

        result = start._revalidate_sageattention_runtime(state)

        self.assertTrue(result)
        can_import.assert_called_once_with(
            'sageattention', python_bin=start._comfy_python()
        )
        state.set_runtime_stack.assert_called_once_with('standard')
```

Update `test_runtime_rebuilds_when_prebuilt_wheel_cannot_import` so the optional probes expect `triton`, the first SageAttention probe, and the post-rebuild SageAttention probe through `_can_import`; `_verify_python_import` should only expect the required `torch` probe:

```python
        self.assertEqual(
            can_import.call_args_list,
            [
                call('triton', python_bin=start._comfy_python()),
                call('sageattention', python_bin=start._comfy_python()),
                call('sageattention', python_bin=start._comfy_python()),
            ],
        )
        verify_import.assert_called_once_with(
            'torch', python_bin=start._comfy_python()
        )
```

Set that test's `_can_import` decorator to `side_effect=[True, False, True]`.

- [ ] **Step 2: Run the configuration tests and verify RED**

Run:

```bash
python -m unittest tests.test_runtime_stack.SageAttentionInstallerTests -v
```

Expected: the failed-installer and post-install-loss tests fail because both paths currently return `False`; the successful rebuild test also exposes the old optional-import probe contract.

- [ ] **Step 3: Implement optional capability routing**

In `configure_runtime_stack`:

1. On `_run_sageattention_installer` failure, log the last lines with `logger.warning` and return `_fallback_to_standard_runtime(state, 'SageAttention installer failed after retries')`.
2. After the installer succeeds, validate required `torch` with `_verify_python_import`; return `False` if it fails.
3. Probe optional `triton` with `_can_import`; on failure return `_fallback_to_standard_runtime(state, 'Triton required by SageAttention is unavailable')`.
4. Keep the existing prebuilt SageAttention probe and rebuild attempt.
5. On rebuild failure, log the last lines with `logger.warning` and return `_fallback_to_standard_runtime(state, 'SageAttention source rebuild failed')`.
6. Replace final noisy SageAttention verification with `_can_import`; on failure return `_fallback_to_standard_runtime(state, 'SageAttention cannot be imported after installation')`.
7. Preserve the successful `state.set_runtime_stack('sageattention')` path unchanged.

Replace `_revalidate_sageattention_runtime`'s error/downgrade block with:

```python
    return _fallback_to_standard_runtime(
        state,
        "SageAttention stopped importing after preset pip or node requirements",
    )
```

Update `_install_presets_impl`'s docstring to state that SageAttention loss falls back to the standard runtime and only a broken launchable runtime remains fatal.

- [ ] **Step 4: Run the focused runtime test module and verify GREEN**

Run:

```bash
python -m unittest tests.test_runtime_stack -v
```

Expected: all tests pass with `OK`.

- [ ] **Step 5: Run the full project gate once**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests pass with `OK` and no traceback outside intentionally captured failure probes.

- [ ] **Step 6: Commit the completed behavior change**

```bash
git add start.py tests/test_runtime_stack.py
git commit -m "Permite presets sem SageAttention instalado"
```

- [ ] **Step 7: Inspect the final checkpoint without pushing**

Run:

```bash
git status --short --branch
git log -3 --oneline --decorate
```

Expected: only the pre-existing untracked `Redesign Arrakis Start/` remains; the branch is ahead of `origin/main`; nothing is pushed.
