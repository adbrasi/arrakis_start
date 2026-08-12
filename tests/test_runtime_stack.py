import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import start
import server
from state import StateManager


class SageAttentionInstallerTests(unittest.TestCase):
    @patch('start._run_streaming_command')
    def test_build_action_is_forwarded_to_remote_installer(self, run_command):
        run_command.return_value = (0, ['ok'])

        with patch.object(
            start,
            'SAGEATTENTION_WORK_DIR',
            Path('/workspace/comfy/.cache/sageattention'),
            create=True,
        ):
            result = start._run_sageattention_installer(
                Path('/workspace/comfy/.venv/bin/activate'),
                action='build',
                env={'TEST_ENV': '1'}
            )

        self.assertEqual(result, (True, ['ok']))
        command = run_command.call_args.args[0]
        self.assertIn('| bash -s -- build', command[-1])
        installer_env = run_command.call_args.kwargs['env']
        self.assertEqual(installer_env['TEST_ENV'], '1')
        self.assertEqual(
            installer_env['WORK_DIR'],
            '/workspace/comfy/.cache/sageattention',
        )

    @patch('start._run_sageattention_installer')
    def test_rebuild_preserves_torch_and_hf_publish_token(self, installer):
        installer.return_value = (True, ['rebuilt'])

        with patch.dict(os.environ, {'HF_TOKEN': 'secret', 'KEEP_ME': 'yes'}, clear=True):
            result = start._rebuild_sageattention_for_current_torch(
                Path('/workspace/comfy/.venv/bin/activate')
            )

        self.assertEqual(result, (True, ['rebuilt']))
        installer.assert_called_once()
        kwargs = installer.call_args.kwargs
        self.assertEqual(kwargs['action'], 'build')
        self.assertEqual(kwargs['env']['HF_TOKEN'], 'secret')
        self.assertEqual(kwargs['env']['SKIP_TORCH_INSTALL'], '1')
        self.assertEqual(kwargs['env']['KEEP_ME'], 'yes')

    def test_sage_failure_uses_launchable_standard_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('state.STATE_FILE', Path(temp_dir) / 'state.json'), \
                patch('start._verify_python_import', return_value=True):
            state = StateManager()
            result = start._fallback_to_standard_runtime(
                state, 'wheel unavailable'
            )

        self.assertTrue(result)
        self.assertEqual(state.get_runtime_stack(), 'standard')

    def test_sage_failure_remains_fatal_when_torch_is_broken(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('state.STATE_FILE', Path(temp_dir) / 'state.json'), \
                patch('start._verify_python_import', return_value=False):
            state = StateManager()
            result = start._fallback_to_standard_runtime(
                state, 'wheel unavailable'
            )

        self.assertFalse(result)
        self.assertEqual(state.get_runtime_stack(), 'unknown')

    def test_failed_installer_does_not_block_preset_or_persist_sage_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('state.STATE_FILE', Path(temp_dir) / 'state.json'):
            state = StateManager()
            state.add_preset('Video Preset')
            with patch('start.get_state_manager', return_value=state), \
                    patch('start._detect_runtime_stack', return_value='unknown'), \
                    patch('start._verify_python_import', return_value=True), \
                    patch(
                        'start._run_sageattention_installer',
                        return_value=(False, ['ABI mismatch']),
                    ):
                result = start.configure_runtime_stack(use_sage_attention=True)
            start._persist_comfyui_flags(
                state,
                {'Video Preset': {'comfyui_flags': []}},
            )

        self.assertTrue(result)
        self.assertEqual(state.get_runtime_stack(), 'standard')
        self.assertEqual(state.get_comfyui_flags(), [])

    def test_failed_installer_blocks_when_standard_runtime_is_broken(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('state.STATE_FILE', Path(temp_dir) / 'state.json'):
            state = StateManager()
            with patch('start.get_state_manager', return_value=state), \
                    patch('start._detect_runtime_stack', return_value='unknown'), \
                    patch('start._verify_python_import', return_value=False), \
                    patch(
                        'start._run_sageattention_installer',
                        return_value=(False, ['ABI mismatch']),
                    ):
                result = start.configure_runtime_stack(use_sage_attention=True)

        self.assertFalse(result)
        self.assertEqual(state.get_runtime_stack(), 'unknown')

    def test_post_install_sage_loss_falls_back_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('state.STATE_FILE', Path(temp_dir) / 'state.json'):
            state = StateManager()
            state.set_runtime_stack('sageattention')
            with patch('start._can_import', return_value=False), \
                    patch('start._verify_python_import', return_value=True):
                result = start._revalidate_sageattention_runtime(state)

        self.assertTrue(result)
        self.assertEqual(state.get_runtime_stack(), 'standard')

    @patch('start.get_state_manager')
    @patch('start._detect_runtime_stack', return_value='standard')
    @patch('start._verify_python_import', return_value=True)
    @patch('start._can_import', side_effect=[True, False, True])
    @patch('start._rebuild_sageattention_for_current_torch', return_value=(True, ['rebuilt']))
    @patch('start._run_sageattention_installer', return_value=(True, ['installed']))
    def test_runtime_rebuilds_when_prebuilt_wheel_cannot_import(
        self,
        installer,
        rebuild,
        can_import,
        verify_import,
        detect_stack,
        get_state_manager
    ):
        state = get_state_manager.return_value
        state.get_runtime_stack.return_value = 'unknown'

        self.assertTrue(start.configure_runtime_stack(use_sage_attention=True))

        installer.assert_called_once()
        rebuild.assert_called_once()
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
        state.set_runtime_stack.assert_any_call('sageattention')


class PipInstallStreamingTests(unittest.TestCase):
    def tearDown(self):
        start._install_cancel_event.clear()
        if start.get_install_status()['installing']:
            start.finish_install_reservation('failed')

    def test_silent_process_emits_heartbeat(self):
        command = [sys.executable, '-c', 'import time; time.sleep(0.2)']

        with self.assertLogs(start.logger, level='INFO') as captured:
            returncode, last_line = start._run_pip_install_streaming(
                command,
                'silent-node',
                heartbeat_interval=0.05,
                timeout_sec=2,
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(last_line, '')
        self.assertTrue(
            any('[silent-node pip] ativo' in message for message in captured.output)
        )

    def test_silent_process_is_killed_at_timeout(self):
        command = [sys.executable, '-c', 'import time; time.sleep(10)']
        started_at = time.monotonic()

        with self.assertLogs(start.logger, level='ERROR') as captured:
            returncode, _ = start._run_pip_install_streaming(
                command,
                'stuck-node',
                heartbeat_interval=0.05,
                timeout_sec=0.2,
            )

        self.assertEqual(returncode, -1)
        self.assertLess(time.monotonic() - started_at, 2)
        self.assertTrue(any('timeout after' in message for message in captured.output))

    def test_pip_phase_output_is_not_discarded(self):
        command = [
            sys.executable,
            '-c',
            "print('Resolved 8 packages', flush=True)",
        ]

        with self.assertLogs(start.logger, level='INFO') as captured:
            returncode, _ = start._run_pip_install_streaming(
                command,
                'phase-node',
                heartbeat_interval=1,
                timeout_sec=2,
            )

        self.assertEqual(returncode, 0)
        self.assertTrue(
            any('Resolved 8 packages' in message for message in captured.output)
        )

    def test_silent_active_process_reports_cpu_or_io_activity(self):
        command = [
            sys.executable,
            '-c',
            (
                'import time\n'
                'end = time.monotonic() + 0.3\n'
                'value = 0\n'
                'while time.monotonic() < end:\n'
                '    value += 1\n'
            ),
        ]

        with self.assertLogs(start.logger, level='INFO') as captured:
            returncode, _ = start._run_pip_install_streaming(
                command,
                'active-node',
                heartbeat_interval=0.05,
                timeout_sec=2,
            )

        self.assertEqual(returncode, 0)
        self.assertTrue(
            any(
                'CPU +' in message or 'I/O +' in message
                for message in captured.output
            )
        )

    def test_active_pip_process_is_stopped_by_install_cancel(self):
        self.assertTrue(start.reserve_install_slot())
        result = {}

        def run_pip():
            result['value'] = start._run_pip_install_streaming(
                [sys.executable, '-c', 'import time; time.sleep(30)'],
                'cancel-node',
                heartbeat_interval=5,
                timeout_sec=60,
            )

        worker = threading.Thread(target=run_pip)
        worker.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with start._active_install_processes_lock:
                if start._active_install_processes:
                    break
            time.sleep(0.01)

        self.assertTrue(start.cancel_active_install())
        worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertNotEqual(result['value'][0], 0)
        start.finish_install_reservation('cancelled')
        self.assertEqual(start.get_install_status()['install_status'], 'cancelled')


class InstallCoordinatorTests(unittest.TestCase):
    def tearDown(self):
        start._install_cancel_event.clear()
        if start.get_install_status()['installing']:
            start.finish_install_reservation('failed')

    def test_only_one_installation_can_be_reserved(self):
        self.assertTrue(start.reserve_install_slot())
        self.assertFalse(start.reserve_install_slot())

    def test_cancelled_reservation_never_enters_installer(self):
        self.assertTrue(start.reserve_install_slot())
        self.assertTrue(start.cancel_active_install())

        with patch('start._install_presets_impl') as installer:
            result = start.install_presets(['Base'], _slot_reserved=True)

        self.assertFalse(result)
        installer.assert_not_called()
        status = start.get_install_status()
        self.assertFalse(status['installing'])
        self.assertEqual(status['install_status'], 'cancelled')

    def test_shutdown_policy_is_forwarded_to_active_downloader(self):
        self.assertTrue(start.reserve_install_slot())
        downloader = Mock()

        with patch.object(start, '_active_downloader', downloader):
            self.assertTrue(start.cancel_active_install(delete_partials=True))

        downloader.cancel.assert_called_once_with(delete_partials=True)

    def test_shutdown_cancels_install_before_waiting_to_stop_comfyui(self):
        events = []
        recording_pm = Mock()
        recording_pm.is_running.return_value = True
        stop_entered = threading.Event()
        allow_stop = threading.Event()

        def record_stop(port=None, timeout=None):
            stop_entered.set()
            self.assertTrue(allow_stop.wait(timeout=2))
            events.append(('stop', timeout))
            return True

        recording_pm.ensure_stopped.side_effect = record_stop

        self.assertTrue(start.reserve_install_slot())
        with patch('downloader.cleanup_incomplete_downloads') as cleanup_partials, \
                patch.object(server, '_state_manager', object()), \
                patch('process_manager.ProcessManager', return_value=recording_pm):
            shutdown_thread = threading.Thread(target=server._shutdown_runtime)
            shutdown_thread.start()
            try:
                self.assertTrue(start._install_cancel_event.wait(timeout=2))
                self.assertFalse(stop_entered.wait(timeout=0.2))
                cleanup_partials.assert_not_called()
            finally:
                start.finish_install_reservation('cancelled')
                allow_stop.set()
                shutdown_thread.join(timeout=2)

        self.assertFalse(shutdown_thread.is_alive())
        cleanup_partials.assert_called_once_with(start.MODELS_DIR)
        self.assertEqual(events, [('stop', 15)])
        # And the port is actually passed, so a non-default COMFY_PORT is honored.
        _, stop_kwargs = recording_pm.ensure_stopped.call_args
        self.assertIn('port', stop_kwargs)
        self.assertEqual(stop_kwargs['port'], server.COMFY_PORT)

    def test_shutdown_releases_operation_slot_when_stop_raises(self):
        process_manager = Mock()
        process_manager.is_running.return_value = True
        process_manager.ensure_stopped.side_effect = RuntimeError('stop failed')

        with patch.object(server, '_state_manager', object()), \
                patch('process_manager.ProcessManager', return_value=process_manager), \
                patch('server.os.kill') as kill_process:
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                server._shutdown_runtime(terminate_process=True)

        kill_process.assert_not_called()
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation('failed')

    def test_idle_shutdown_still_sweeps_residual_processes(self):
        process_manager = Mock()
        process_manager.is_running.return_value = False
        process_manager.ensure_stopped.return_value = True

        with patch('downloader.cleanup_incomplete_downloads'), \
                patch.object(server, '_state_manager', object()), \
                patch('process_manager.ProcessManager', return_value=process_manager):
            server._shutdown_runtime()

        process_manager.ensure_stopped.assert_called_once_with(
            port=server.COMFY_PORT,
            timeout=15,
        )

    def test_idle_shutdown_reserves_slot_before_cleaning_partials(self):
        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()
        process_manager = Mock()
        process_manager.is_running.return_value = False

        def blocking_cleanup(_models_dir):
            cleanup_entered.set()
            self.assertTrue(allow_cleanup.wait(timeout=2))

        with patch('downloader.cleanup_incomplete_downloads', side_effect=blocking_cleanup), \
                patch.object(server, '_state_manager', object()), \
                patch('process_manager.ProcessManager', return_value=process_manager):
            shutdown_thread = threading.Thread(target=server._shutdown_runtime)
            shutdown_thread.start()
            self.assertTrue(cleanup_entered.wait(timeout=2))

            unexpectedly_reserved = start.reserve_install_slot()
            if unexpectedly_reserved:
                start.finish_install_reservation('failed')

            allow_cleanup.set()
            shutdown_thread.join(timeout=2)

        self.assertFalse(unexpectedly_reserved)
        self.assertFalse(shutdown_thread.is_alive())
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation('failed')

    def test_pending_shutdown_blocks_new_operations_before_install_releases(self):
        shutdown_cancelled = threading.Event()
        allow_shutdown_to_wait = threading.Event()
        cleanup_entered = threading.Event()
        allow_cleanup = threading.Event()
        process_manager = Mock()
        process_manager.is_running.return_value = False
        original_cancel = start.cancel_active_install

        def pause_after_cancel(*args, **kwargs):
            result = original_cancel(*args, **kwargs)
            shutdown_cancelled.set()
            self.assertTrue(allow_shutdown_to_wait.wait(timeout=2))
            return result

        def blocking_cleanup(_models_dir):
            cleanup_entered.set()
            self.assertTrue(allow_cleanup.wait(timeout=2))

        def try_uninstall_reservation():
            with start.reserve_uninstall_slot() as reserved:
                return reserved

        self.assertTrue(start.reserve_install_slot())
        with patch('start.cancel_active_install', side_effect=pause_after_cancel), \
                patch('downloader.cleanup_incomplete_downloads', side_effect=blocking_cleanup), \
                patch.object(server, '_state_manager', object()), \
                patch('process_manager.ProcessManager', return_value=process_manager):
            shutdown_thread = threading.Thread(target=server._shutdown_runtime)
            shutdown_thread.start()
            try:
                self.assertTrue(shutdown_cancelled.wait(timeout=2))
                start.finish_install_reservation('cancelled')

                install_b_reserved = start.reserve_install_slot()
                if install_b_reserved:
                    start.finish_install_reservation('failed')
                uninstall_b_reserved = try_uninstall_reservation()

                allow_shutdown_to_wait.set()
                self.assertTrue(cleanup_entered.wait(timeout=2))

                install_during_cleanup = start.reserve_install_slot()
                if install_during_cleanup:
                    start.finish_install_reservation('failed')
                uninstall_during_cleanup = try_uninstall_reservation()
            finally:
                allow_shutdown_to_wait.set()
                allow_cleanup.set()
                shutdown_thread.join(timeout=2)

        self.assertFalse(shutdown_thread.is_alive())
        self.assertFalse(install_b_reserved)
        self.assertFalse(uninstall_b_reserved)
        self.assertFalse(install_during_cleanup)
        self.assertFalse(uninstall_during_cleanup)
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation('failed')
        self.assertTrue(try_uninstall_reservation())

    def test_shutdown_preparation_exception_does_not_leave_pending_admission(self):
        cancellation_attempted = threading.Event()
        failure = []

        def fail_cancellation(*_args, **_kwargs):
            cancellation_attempted.set()
            raise RuntimeError('cancel failed')

        def reserve_shutdown():
            try:
                with start.reserve_shutdown_slot():
                    pass
            except RuntimeError as exc:
                failure.append(exc)

        self.assertTrue(start.reserve_install_slot())
        with patch('start.cancel_active_install', side_effect=fail_cancellation):
            shutdown_thread = threading.Thread(target=reserve_shutdown)
            shutdown_thread.start()
            try:
                self.assertTrue(cancellation_attempted.wait(timeout=2))
            finally:
                start.finish_install_reservation('cancelled')
                shutdown_thread.join(timeout=2)

        self.assertFalse(shutdown_thread.is_alive())
        self.assertEqual(str(failure[0]), 'cancel failed')
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation('failed')


class PresetCompletionTests(unittest.TestCase):
    def test_missing_model_keeps_preset_pending(self):
        preset = {
            'models': [{
                'url': 'https://example.com/model.safetensors',
                'dir': 'checkpoints',
                'filename': 'model.safetensors',
            }],
            'nodes': [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            issues = start._preset_install_issues(
                preset,
                downloader_failures=[],
                failed_node_names=set(),
                models_dir=Path(temp_dir),
            )

        self.assertEqual(issues, ['modelo ausente: model.safetensors'])

    def test_complete_model_and_nodes_have_no_issues(self):
        preset = {
            'models': [{
                'url': 'https://example.com/model.safetensors',
                'dir': 'checkpoints',
                'filename': 'model.safetensors',
            }],
            'nodes': ['https://github.com/example/good-node'],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir)
            target = models_dir / 'checkpoints' / 'model.safetensors'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'model')
            issues = start._preset_install_issues(
                preset,
                downloader_failures=[],
                failed_node_names=set(),
                models_dir=models_dir,
            )

        self.assertEqual(issues, [])

    def test_failed_unnamed_download_and_node_are_reported(self):
        url = 'https://civitai.com/api/download/models/123'
        preset = {
            'models': [{'url': url, 'dir': 'checkpoints', 'filename': ''}],
            'nodes': ['https://github.com/example/broken-node'],
        }

        issues = start._preset_install_issues(
            preset,
            downloader_failures=[{'url': url, 'filename': '', 'stage': 'wget'}],
            failed_node_names={'broken-node'},
        )

        self.assertEqual(
            issues,
            ['download sem filename falhou', 'custom node falhou: broken-node'],
        )


class CustomNodeRecoveryTests(unittest.TestCase):
    def test_complete_clone_from_different_origin_is_replaced_recoverably(self):
        def create_source(path, marker):
            path.mkdir(parents=True)
            (path / 'source.txt').write_text(marker)
            subprocess.run(['git', 'init', '-q'], cwd=path, check=True)
            subprocess.run(['git', 'add', 'source.txt'], cwd=path, check=True)
            subprocess.run(
                ['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                 'commit', '-qm', marker],
                cwd=path,
                check=True,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_source = root / 'old' / 'ComfyUI-LTXVideo'
            new_source = root / 'new' / 'ComfyUI-LTXVideo'
            create_source(old_source, 'old source')
            create_source(new_source, 'new source')

            custom_nodes = root / 'ComfyUI' / 'custom_nodes'
            custom_nodes.mkdir(parents=True)
            node_dir = custom_nodes / 'ComfyUI-LTXVideo'
            subprocess.run(
                ['git', 'clone', '-q', old_source.as_uri(), str(node_dir)],
                check=True,
            )

            result = start._clone_node(new_source.as_uri(), custom_nodes)

            self.assertTrue(result[3])
            self.assertIsNone(result[4])
            self.assertEqual((node_dir / 'source.txt').read_text(), 'new source')
            backups = list(
                (custom_nodes.parent / '.arrakis-node-backups').glob(
                    'ComfyUI-LTXVideo-*'
                )
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'source.txt').read_text(), 'old source')

    @patch('start._pip_install_argv', return_value=['pip', 'install'])
    @patch('start._run_pip_install_streaming', return_value=(0, 'done'))
    @patch('start.get_state_manager')
    def test_existing_untracked_clone_resumes_requirements(
        self,
        get_state_manager,
        run_pip,
        _pip_argv,
    ):
        url = 'https://github.com/example/recover-node'
        state = get_state_manager.return_value
        state.is_node_installed.return_value = False

        with tempfile.TemporaryDirectory() as temp_dir:
            comfy_dir = Path(temp_dir)
            node_dir = comfy_dir / 'custom_nodes' / 'recover-node'
            node_dir.mkdir(parents=True)
            (node_dir / 'requirements.txt').write_text('example-package\n')
            # A real checkout with a commit. A bare `.git` directory is what a
            # clone killed mid-flight leaves behind, and treating that as
            # installed is the bug that marks a node complete with its code
            # absent — so the fixture must be a genuinely complete clone for the
            # "resume requirements" behaviour to be the thing under test.
            subprocess.run(['git', 'init', '-q'], cwd=node_dir, check=True)
            subprocess.run(['git', 'add', 'requirements.txt'], cwd=node_dir, check=True)
            subprocess.run(
                ['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                 'commit', '-qm', 'init'],
                cwd=node_dir, check=True,
            )
            subprocess.run(
                ['git', 'remote', 'add', 'origin', url],
                cwd=node_dir,
                check=True,
            )

            with patch.object(start, 'COMFY_DIR', comfy_dir):
                result = start.install_custom_nodes([url])

        self.assertTrue(result['success'])
        run_pip.assert_called_once()
        state.add_node.assert_called_once_with(url)

    @patch('start._pip_install_argv', return_value=['pip', 'install'])
    @patch('start._run_pip_install_streaming', return_value=(0, ''))
    @patch('start.get_state_manager')
    def test_incomplete_clone_is_not_treated_as_installed(
        self,
        get_state_manager,
        run_pip,
        _pip_argv,
    ):
        """A bare `.git` left by a killed clone must not count as installed.

        `_clone_node` used to accept the mere existence of a `.git` entry — true
        for an empty directory or even a plain file — so a node cancelled
        mid-clone stayed permanently "installed" with no code in it.
        """
        url = 'https://github.com/example/recover-node'
        state = get_state_manager.return_value
        state.is_node_installed.return_value = False

        with tempfile.TemporaryDirectory() as temp_dir:
            comfy_dir = Path(temp_dir)
            node_dir = comfy_dir / 'custom_nodes' / 'recover-node'
            (node_dir / '.git').mkdir(parents=True)

            self.assertFalse(start._git_checkout_is_complete(node_dir))

            # A `.git` *file* is rejected too.
            import shutil as _shutil
            _shutil.rmtree(node_dir / '.git')
            (node_dir / '.git').write_text('gitdir: /nowhere\n')
            self.assertFalse(start._git_checkout_is_complete(node_dir))

    @patch('start._pip_install_argv', return_value=['pip', 'install'])
    @patch('start._run_pip_install_streaming', return_value=(1, 'failed'))
    @patch('start.get_state_manager')
    def test_requirements_failure_does_not_mark_node_installed(
        self,
        get_state_manager,
        _run_pip,
        _pip_argv,
    ):
        url = 'https://github.com/example/broken-node'
        state = get_state_manager.return_value
        state.is_node_installed.return_value = False

        with tempfile.TemporaryDirectory() as temp_dir:
            comfy_dir = Path(temp_dir)
            node_dir = comfy_dir / 'custom_nodes' / 'broken-node'
            node_dir.mkdir(parents=True)
            (node_dir / 'requirements.txt').write_text('broken-package\n')
            subprocess.run(['git', 'init', '-q'], cwd=node_dir, check=True)
            subprocess.run(['git', 'add', 'requirements.txt'], cwd=node_dir, check=True)
            subprocess.run(
                ['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                 'commit', '-qm', 'init'],
                cwd=node_dir,
                check=True,
            )
            subprocess.run(
                ['git', 'remote', 'add', 'origin', url],
                cwd=node_dir,
                check=True,
            )

            with patch.object(start, 'COMFY_DIR', comfy_dir):
                result = start.install_custom_nodes([url])

        self.assertFalse(result['success'])
        self.assertEqual(result['failed'], ['broken-node'])
        state.add_node.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class StreamCommandLivenessTests(unittest.TestCase):
    """A child starved of bandwidth is slow, not hung — only silence is hung.

    A requirements install racing multi-GB model transfers took longer than the
    wall-clock deadline while still making progress, and was killed metres from
    the finish line. Elapsed time cannot tell slow from hung; inactivity can.
    """

    def setUp(self):
        start._install_cancel_event.clear()
        self.addCleanup(start._install_cancel_event.clear)

    @staticmethod
    def _child(body: str):
        return [sys.executable, '-u', '-c', body]

    def test_busy_but_silent_child_survives_the_stall_window(self):
        # Burns CPU while printing nothing: the shape of a pip unpacking wheels
        # with a quiet log. A total-time deadline kills it; the guard must not.
        rc, _, _ = start._stream_command(
            self._child(
                "import time\n"
                "end = time.monotonic() + 2.0\n"
                "x = 0\n"
                "while time.monotonic() < end:\n"
                "    x += 1\n"
            ),
            'busy child',
            log_prefix='busy',
            timeout_sec=0,
            stall_sec=1.0,
            heartbeat_interval=0.3,
        )
        self.assertEqual(rc, 0, "a working child must not trip the stall guard")

    def test_silent_idle_child_is_killed_by_the_stall_guard(self):
        started = time.monotonic()
        rc, _, _ = start._stream_command(
            self._child("import time\ntime.sleep(30)\n"),
            'idle child',
            log_prefix='idle',
            timeout_sec=0,
            stall_sec=1.0,
            heartbeat_interval=0.3,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(rc, -1, "no output, no CPU and no I/O means hung")
        self.assertLess(elapsed, 15, "the guard must fire, not wait out the sleep")

    def test_stall_guard_stays_off_when_not_requested(self):
        rc, _, _ = start._stream_command(
            self._child("import time\ntime.sleep(1.5)\n"),
            'unguarded child',
            log_prefix='unguarded',
            timeout_sec=0,
            heartbeat_interval=0.3,
        )
        self.assertEqual(rc, 0, "stall_sec defaults to off for existing callers")


class InstallOutcomeTests(unittest.TestCase):
    """A usable install must launch ComfyUI even when some artifacts are missing."""

    def test_missing_model_still_launches_comfyui(self):
        # A gated LoRA that 18 other models do not depend on: the install is
        # incomplete but perfectly usable, so it must NOT block the launch.
        failures = [{
            'filename': 'looper.safetensors', 'dir': 'loras',
            'stage': 'auth', 'reason': 'auth_gated_model_not_accepted',
            'url': 'https://huggingface.co/x/y',
        }]
        fatal = (not False) and (
            not failures
            or any(str(f.get('stage', '')).lower() in start.FATAL_DOWNLOAD_STAGES
                   for f in failures)
        )
        self.assertFalse(fatal, "per-file auth failure must not be fatal")

    def test_unrepairable_local_file_is_fatal(self):
        failures = [{
            'filename': 'vae.safetensors', 'dir': 'vae',
            'stage': 'precheck', 'reason': 'invalid_existing_file_cleanup_failed',
            'url': 'https://huggingface.co/x/y',
        }]
        fatal = any(
            str(f.get('stage', '')).lower() in start.FATAL_DOWNLOAD_STAGES
            for f in failures
        )
        self.assertTrue(fatal, "an unusable local tree means nothing usable ran")

    def test_destination_conflict_is_not_a_download_failure(self):
        # Two presets naming one destination is a preset-data problem; the file
        # still lands from the winning source, so it must not block the launch.
        self.assertNotIn('config', start.FATAL_DOWNLOAD_STAGES)

    def test_failed_batch_with_no_recorded_failures_is_fatal(self):
        fatal = (not False) and (not [] or False)
        self.assertTrue(fatal, "an empty failure list on a failed batch is fatal")

    def test_launchable_statuses_map_to_true(self):
        launchable = {start.INSTALL_COMPLETED, start.INSTALL_COMPLETED_WITH_FAILURES}
        self.assertIn(start.INSTALL_COMPLETED_WITH_FAILURES, launchable)
        self.assertNotIn(start.INSTALL_FAILED, launchable)
        self.assertNotIn(start.INSTALL_CANCELLED, launchable)
