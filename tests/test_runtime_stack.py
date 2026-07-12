import os
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


if __name__ == '__main__':
    unittest.main()
