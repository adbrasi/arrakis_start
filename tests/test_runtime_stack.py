import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch

import start


class SageAttentionInstallerTests(unittest.TestCase):
    @patch('start._run_streaming_command')
    def test_build_action_is_forwarded_to_remote_installer(self, run_command):
        run_command.return_value = (0, ['ok'])

        result = start._run_sageattention_installer(
            Path('/workspace/comfy/.venv/bin/activate'),
            action='build',
            env={'TEST_ENV': '1'}
        )

        self.assertEqual(result, (True, ['ok']))
        command = run_command.call_args.args[0]
        self.assertIn('| bash -s -- build', command[-1])
        self.assertEqual(run_command.call_args.kwargs['env'], {'TEST_ENV': '1'})

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

    @patch('start.get_state_manager')
    @patch('start._detect_runtime_stack', return_value='standard')
    @patch('start._verify_python_import', return_value=True)
    @patch('start._can_import', side_effect=[False])
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
        can_import.assert_called_once_with(
            'sageattention',
            python_bin=start._comfy_python()
        )
        self.assertEqual(
            verify_import.call_args_list,
            [
                call('torch', python_bin=start._comfy_python()),
                call('triton', python_bin=start._comfy_python()),
                call('sageattention', python_bin=start._comfy_python()),
            ]
        )
        state.set_runtime_stack.assert_any_call('sageattention')


class PipInstallStreamingTests(unittest.TestCase):
    def tearDown(self):
        start._install_cancel_event.clear()
        if start._install_lock.locked():
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
            any('still working' in message for message in captured.output)
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
        if start._install_lock.locked():
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
            (node_dir / '.git').mkdir(parents=True)
            (node_dir / 'requirements.txt').write_text('example-package\n')

            with patch.object(start, 'COMFY_DIR', comfy_dir):
                result = start.install_custom_nodes([url])

        self.assertTrue(result['success'])
        run_pip.assert_called_once()
        state.add_node.assert_called_once_with(url)

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
            (node_dir / '.git').mkdir(parents=True)
            (node_dir / 'requirements.txt').write_text('broken-package\n')

            with patch.object(start, 'COMFY_DIR', comfy_dir):
                result = start.install_custom_nodes([url])

        self.assertFalse(result['success'])
        self.assertEqual(result['failed'], ['broken-node'])
        state.add_node.assert_not_called()


if __name__ == '__main__':
    unittest.main()
