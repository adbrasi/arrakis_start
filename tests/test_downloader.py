import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader import DownloadManager


class _ProcessSequence:
    def __init__(self, states):
        self._states = iter(states)

    def poll(self):
        return next(self._states)


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
        first = {
            'url': 'https://example.com/one',
            'dir': 'loras',
            'filename': 'same.bin',
        }
        second = {
            'url': 'https://example.com/two',
            'dir': 'loras',
            'filename': 'same.bin',
        }

        with self.assertRaisesRegex(ValueError, 'same.bin'):
            manager._deduplicate_downloads([first, second])

    def test_cancelled_download_is_not_retried_or_recorded_as_failure(self):
        manager = self._manager(Path('/tmp/models'))
        manager._cancelled = False
        manager._failures_lock = threading.Lock()
        manager.failures = []
        manager.progress_callback = None

        def cancelled(*_args):
            manager._cancelled = True
            return False, 'cancelled_by_user', 'cancel'

        item = {
            'url': 'https://example.com/model',
            'dir': 'loras',
            'filename': 'model.bin',
        }
        with self.assertLogs('downloader', level='INFO') as captured, \
                patch.object(manager, '_download_file', side_effect=cancelled) as download:
            result = manager._download_one_with_retry(item, '[1/1]')

        self.assertFalse(result)
        download.assert_called_once()
        self.assertEqual(manager.failures, [])
        self.assertFalse(any('retrying' in line for line in captured.output))

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

    def test_parses_structured_xet_reconstruction_progress(self):
        manager = self._manager(Path('/tmp/models'))
        line = (
            'ARRAKIS_XET_PROGRESS '
            '{"phase":"model.safetensors: reconstructing file",'
            '"current":52428800,"total":104857600,"speed":10485760}'
        )

        progress = manager._parse_hf_xet_progress(line)

        self.assertEqual(progress['kind'], 'reconstruction')
        self.assertEqual(progress['current'], 52_428_800)
        self.assertEqual(progress['total'], 104_857_600)
        self.assertEqual(progress['percent'], 50.0)
        self.assertEqual(progress['speed'], 10_485_760)

    def test_cleanup_partials_preserves_completed_models(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            final = models_dir / 'loras' / 'complete.safetensors'
            partial = models_dir / 'loras' / 'incomplete.safetensors.arrakis.part'
            final.parent.mkdir(parents=True)
            final.write_bytes(b'complete')
            partial.write_bytes(b'partial')
            partial.with_name(f'{partial.name}.aria2').write_bytes(b'control')
            (manager.hf_partial_root / 'job').mkdir(parents=True)
            (manager.hf_partial_root / 'job' / 'chunk').write_bytes(b'xet')

            result = manager.cleanup_partials()

            self.assertTrue(final.exists())
            self.assertFalse(partial.exists())
            self.assertFalse(partial.with_name(f'{partial.name}.aria2').exists())
            self.assertFalse(manager.hf_partial_root.exists())
            self.assertEqual(result['partial_payloads'], 2)

    def test_cleanup_partials_removes_legacy_incomplete_pair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            models_dir = Path(temp_dir) / 'models'
            manager = self._manager(models_dir)
            legacy = models_dir / 'checkpoints' / 'legacy.safetensors'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'incomplete')
            legacy.with_name(f'{legacy.name}.aria2').write_bytes(b'control')

            manager.cleanup_partials()

            self.assertFalse(legacy.exists())
            self.assertFalse(legacy.with_name(f'{legacy.name}.aria2').exists())

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
