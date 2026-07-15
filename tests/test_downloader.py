import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader import DownloadManager


class DownloadStagingTests(unittest.TestCase):
    def _manager(self, models_dir: Path) -> DownloadManager:
        manager = DownloadManager.__new__(DownloadManager)
        manager.models_dir = models_dir
        manager.hf_partial_root = models_dir.parent / '.arrakis-hf-partials'
        return manager

    def test_hf_work_dirs_are_isolated_and_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            dest_dir = models_dir / 'loras'

            first = manager._hf_work_dir(
                dest_dir, 'first.safetensors', 'org/repo', 'main', 'first.safetensors'
            )
            first_again = manager._hf_work_dir(
                dest_dir, 'first.safetensors', 'org/repo', 'main', 'first.safetensors'
            )
            second = manager._hf_work_dir(
                dest_dir, 'second.safetensors', 'org/repo', 'main', 'second.safetensors'
            )

            self.assertEqual(first, first_again)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, manager.hf_partial_root)

    def test_completed_partial_is_promoted_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            dest = models_dir / 'checkpoints' / 'model.safetensors'
            partial = manager._partial_path(dest)
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b'complete-model')
            partial.with_name(f'{partial.name}.aria2').write_bytes(b'control')

            ok, reason = manager._promote_partial(partial, dest)

            self.assertTrue(ok, reason)
            self.assertEqual(dest.read_bytes(), b'complete-model')
            self.assertFalse(partial.exists())
            self.assertFalse(partial.with_name(f'{partial.name}.aria2').exists())

    def test_legacy_aria2_partial_is_not_left_as_final_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            dest = models_dir / 'diffusion_models' / 'model.safetensors'
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b'partial-model')
            legacy_control = dest.with_name(f'{dest.name}.aria2')
            legacy_control.write_bytes(b'control')

            detected = manager._migrate_legacy_aria2_partial(dest)
            partial = manager._partial_path(dest)

            self.assertTrue(detected)
            self.assertFalse(dest.exists())
            self.assertEqual(partial.read_bytes(), b'partial-model')
            self.assertTrue(partial.with_name(f'{partial.name}.aria2').exists())

    def test_cancel_does_not_start_hf_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            manager._cancelled = False
            manager.hf_cli_path = '/fake/hf'
            manager._failures_lock = __import__('threading').Lock()
            manager.attempt_logs = []

            def cancel_primary(*_args):
                manager._cancelled = True
                return False, 'interrupted'

            with patch.object(manager, '_download_hf_direct', side_effect=cancel_primary), \
                    patch.object(manager, '_download_hf_via_python') as fallback:
                ok, reason, stage = manager._download_file(
                    'https://huggingface.co/org/repo/resolve/main/model.safetensors',
                    'checkpoints',
                    'model.safetensors',
                )

            self.assertFalse(ok)
            self.assertEqual((reason, stage), ('cancelled_by_user', 'cancel'))
            fallback.assert_not_called()

    def test_deterministic_404_is_not_retried(self):
        manager = self._manager(Path('/tmp/models'))

        self.assertFalse(
            manager._is_retryable_failure(
                'civitai-resolve',
                'civitai_resolve_http_404',
            )
        )


if __name__ == '__main__':
    unittest.main()
