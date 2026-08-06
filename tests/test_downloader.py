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
        """Build a manager without __init__ (no network/tool probing).

        Only the collaborators the tests exercise are populated. Anything a
        method under test reads must be set here, otherwise the test fails with
        an AttributeError that hides the behaviour being checked.
        """
        manager = DownloadManager.__new__(DownloadManager)
        manager.models_dir = models_dir
        manager.hf_partial_root = models_dir.parent / '.arrakis-hf-partials'
        manager.hf_token = ''
        manager.civitai_token = ''
        manager.failures = []
        manager.config_conflicts = []
        manager.attempt_logs = []
        manager._failures_lock = threading.Lock()
        manager._process_lock = threading.Lock()
        manager._active_procs = set()
        manager._cancelled = False
        manager._inflight = 0
        manager._inflight_cv = threading.Condition()
        # XET liveness policy (see DownloadManager.__init__).
        manager.xet_no_progress_seconds = 240
        manager.xet_rate_grace_seconds = 600
        manager.xet_min_rate_bps = 100 * 1024
        # An importable huggingface_hub is what enables the HF backends.
        manager.has_hf_hub = True
        manager.has_hf_xet = True
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

        unique, removed, conflicts = manager._deduplicate_downloads([item, dict(item)])

        self.assertEqual(unique, [item])
        self.assertEqual(removed, 1)
        self.assertEqual(conflicts, 0)

    def test_unnamed_entry_collides_with_named_entry_on_same_destination(self):
        """An empty `filename` must not bypass the destination check.

        Two entries landing on the same final path used to be scheduled in
        parallel: on the aria2c path they share one `.arrakis.part` and corrupt
        it, and on the HF path both move onto the same target and both report
        success.
        """
        manager = self._manager(Path('/tmp/models'))
        named = {
            'url': 'https://huggingface.co/org-a/repo/resolve/main/vae.safetensors',
            'dir': 'vae',
            'filename': 'vae.safetensors',
        }
        unnamed = {
            'url': 'https://huggingface.co/org-b/repo/resolve/main/vae.safetensors',
            'dir': 'vae',
            'filename': '',
        }

        unique, removed, conflicts = manager._deduplicate_downloads([named, unnamed])

        self.assertEqual(unique, [named])
        self.assertEqual(conflicts, 1)

    def test_conflicting_sources_are_reported_without_cancelling_the_batch(self):
        """A destination conflict is a config error, not a batch killer.

        Raising here aborted every queued download and recorded no failure, so
        the installer reported success having downloaded nothing.
        """
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
        unrelated = {
            'url': 'https://example.com/other',
            'dir': 'loras',
            'filename': 'other.bin',
        }

        unique, removed, conflicts = manager._deduplicate_downloads(
            [first, second, unrelated]
        )

        # The first source wins, the unrelated download survives.
        self.assertEqual(unique, [first, unrelated])
        self.assertEqual(conflicts, 1)
        self.assertEqual(removed, 0)
        # The loser is visible in its own report rather than silently dropped,
        # and stays OUT of the download failures: the destination did get a file
        # from the winning source, so no preset is missing an artifact.
        self.assertEqual(manager.failures, [])
        self.assertEqual(len(manager.config_conflicts), 1)
        self.assertEqual(manager.config_conflicts[0]['filename'], 'same.bin')
        self.assertEqual(manager.config_conflicts[0]['dir'], 'loras')
        self.assertEqual(
            manager.config_conflicts[0]['kept_url'], 'https://example.com/one'
        )
        self.assertEqual(
            manager.config_conflicts[0]['dropped_url'], 'https://example.com/two'
        )

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

    def _run_xet_watchdog(self, manager, clock, tree_bytes, state):
        """Drive the observer over a scripted clock until the process exits."""
        # One extra poll than clock samples so the loop always terminates.
        process = _ProcessSequence([None] * (len(clock) - 1) + [0])
        with patch.object(manager, '_tree_bytes', side_effect=tree_bytes), \
                patch.object(manager, '_terminate_process') as terminate, \
                patch('downloader.time.sleep'), \
                patch('downloader.time.monotonic', side_effect=clock):
            manager._run_disk_watchdog(
                process,
                Path('/tmp/staging'),
                Path('/tmp/final'),
                'model.safetensors',
                None,
                state,
                terminate_on_stall=False,
                backend_label='XET',
            )
        return terminate

    def test_xet_slow_but_growing_warmup_is_never_killed(self):
        """A healthy XET transfer starts at tens of KB/s before it accelerates.

        Delivered bytes keep growing the whole time, so neither the
        no-progress rule nor the long-window rate floor may fire — the floor is
        only allowed to catch a transfer that stops moving.
        """
        manager = self._manager(Path('/tmp/models'))
        manager.aria2_stall_timeout_seconds = 120
        state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0,
                 'event_bytes': 0}

        # 50 KB/s reported by the backend, disk silent (XET stages elsewhere).
        clock = [0.0]
        tree = [(0, 0)]
        for tick in range(1, 60):
            clock.append(tick * 5.0)
            tree.append((0, 0))
        clock.append(300.0)
        tree.append((0, 0))

        # The reader thread would feed this; emulate steady 50 KB/s growth.
        class _Growing(dict):
            def get(self, key, default=None):
                if key == 'event_bytes':
                    self['_n'] = self.get('_n', 0) + 1
                    return int(self['_n'] * 5 * 50 * 1024)
                return super().get(key, default)

        state = _Growing(state)
        terminate = self._run_xet_watchdog(manager, clock, tree, state)

        terminate.assert_not_called()
        self.assertFalse(state['killed'])

    def test_xet_is_killed_when_delivered_bytes_stop_growing(self):
        """The failing production case: a burst, then total silence.

        Neither the disk nor the event stream moves, so the transfer is dead and
        must be abandoned so the HTTP fallback can run — instead of occupying a
        worker until the batch backstop fires 30 minutes later.
        """
        manager = self._manager(Path('/tmp/models'))
        manager.aria2_stall_timeout_seconds = 120
        manager.xet_no_progress_seconds = 240
        state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0,
                 # 188 KB arrived early and then nothing ever again.
                 'event_bytes': 188 * 1024}

        # The first observation of those 188 KB legitimately counts as growth, so
        # the idle window only starts there: 100s -> 400s is 300s of silence.
        clock = [0.0, 100.0, 200.0, 300.0, 400.0]
        tree = [(0, 0)] * 5
        terminate = self._run_xet_watchdog(manager, clock, tree, state)

        terminate.assert_called_once()
        self.assertTrue(state['killed'])

    def test_xet_rate_floor_does_not_fire_before_the_grace_window(self):
        """A crawling transfer is tolerated until the grace window elapses."""
        manager = self._manager(Path('/tmp/models'))
        manager.aria2_stall_timeout_seconds = 120
        manager.xet_no_progress_seconds = 0        # isolate the rate floor
        manager.xet_rate_grace_seconds = 600
        manager.xet_min_rate_bps = 100 * 1024

        state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0,
                 'event_bytes': 1}
        # Only 500s elapse — inside the grace window.
        terminate = self._run_xet_watchdog(
            manager, [0.0, 250.0, 500.0], [(0, 0)] * 3, state
        )
        terminate.assert_not_called()
        self.assertFalse(state['killed'])

    def test_xet_rate_floor_fires_after_the_grace_window(self):
        manager = self._manager(Path('/tmp/models'))
        manager.aria2_stall_timeout_seconds = 120
        manager.xet_no_progress_seconds = 0        # isolate the rate floor
        manager.xet_rate_grace_seconds = 600
        manager.xet_min_rate_bps = 100 * 1024

        # ~31 KB/s sustained past the grace window: the observed production rate.
        state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0,
                 'event_bytes': int(31 * 1024 * 700)}
        terminate = self._run_xet_watchdog(
            manager, [0.0, 350.0, 700.0], [(0, 0)] * 3, state
        )
        terminate.assert_called_once()
        self.assertTrue(state['killed'])

    def test_http_retains_disk_stall_termination(self):
        """The HTTP path must keep killing on disk silence (its valid signal)."""
        manager = self._manager(Path('/tmp/models'))
        manager.aria2_stall_timeout_seconds = 60
        state = {'last_progress': 0.0, 'killed': False, 'last_bytes': 0,
                 'event_bytes': 0}
        process = _ProcessSequence([None, 0])

        with patch.object(manager, '_tree_bytes', return_value=(0, 0)), \
                patch.object(manager, '_terminate_process') as terminate, \
                patch('downloader.time.sleep'), \
                patch('downloader.time.monotonic', side_effect=[0.0, 120.0]):
            manager._run_disk_watchdog(
                process,
                Path('/tmp/staging'),
                Path('/tmp/final'),
                'model.safetensors',
                1024,
                state,
                terminate_on_stall=True,
                backend_label='HTTP',
            )

        terminate.assert_called_once()
        self.assertTrue(state['killed'])

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
