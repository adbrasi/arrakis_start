import io
import json
import unittest
from contextlib import redirect_stdout

from hf_xet_worker import (
    COMPAT_PREFIX,
    EVENT_PREFIX,
    ProgressReporter,
    download_with_compat_fallback,
)


class ProgressReporterTests(unittest.TestCase):
    def test_close_emits_machine_readable_progress(self):
        output = io.StringIO()

        with redirect_stdout(output):
            reporter = ProgressReporter(
                desc='model.safetensors: reconstructing file',
                total=100,
                initial=0,
            )
            reporter.update(40)
            reporter.close()

        events = [
            json.loads(line.removeprefix(EVENT_PREFIX))
            for line in output.getvalue().splitlines()
            if line.startswith(EVENT_PREFIX)
        ]
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[-1]['phase'], 'model.safetensors: reconstructing file')
        self.assertEqual(events[-1]['current'], 40)
        self.assertEqual(events[-1]['total'], 100)
        self.assertGreaterEqual(events[-1]['speed'], 0)

    def test_callback_api_breakage_falls_back_to_xet_without_progress(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            if 'tqdm_class' in kwargs:
                raise TypeError("unexpected keyword argument 'tqdm_class'")
            return '/tmp/model.safetensors'

        output = io.StringIO()
        with redirect_stdout(output):
            downloaded = download_with_compat_fallback(
                fake_download,
                repo_id='owner/repo',
                filename='model.safetensors',
                revision='main',
                local_dir='/tmp/staging',
            )

        self.assertEqual(downloaded, '/tmp/model.safetensors')
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0]['tqdm_class'], ProgressReporter)
        self.assertNotIn('tqdm_class', calls[1])
        self.assertIn(COMPAT_PREFIX, output.getvalue())

    def test_real_download_error_does_not_hide_behind_compat_fallback(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            raise RuntimeError('network unavailable')

        with self.assertRaisesRegex(RuntimeError, 'network unavailable'):
            download_with_compat_fallback(
                fake_download,
                repo_id='owner/repo',
                filename='model.safetensors',
                revision='main',
                local_dir='/tmp/staging',
            )

        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
