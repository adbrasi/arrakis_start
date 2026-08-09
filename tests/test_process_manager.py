import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import process_manager


class RecordingState:
    def __init__(self, pid):
        self.status = {
            "status": "running",
            "pid": pid,
            "port": 8818,
            "flags": [],
        }

    def get_comfyui_status(self):
        return self.status.copy()

    def set_comfyui_status(self, **values):
        if values.pop("clear_pid", False):
            self.status["pid"] = None
        self.status.update(values)

    def get_comfyui_flags(self):
        return []


class ProcessCleanupTests(unittest.TestCase):
    def test_ensure_stopped_removes_tracked_and_untracked_managed_main_processes(self):
        processes = []
        with tempfile.TemporaryDirectory() as temp_dir:
            comfy_dir = Path(temp_dir) / "ComfyUI"
            comfy_dir.mkdir()
            main_script = comfy_dir / "main.py"
            main_script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

            try:
                processes = [
                    subprocess.Popen(
                        [sys.executable, str(main_script), "--port", str(8818 + index)],
                        start_new_session=True,
                    )
                    for index in range(2)
                ]
                time.sleep(0.1)
                manager = process_manager.ProcessManager(RecordingState(processes[0].pid))
                manager._record_tracked_identity(processes[0].pid)

                with patch.object(process_manager, "COMFY_DIR", comfy_dir), \
                        patch.object(manager, "_try_comfy_stop", return_value=False), \
                        patch.object(manager, "_find_port_owner_pid", return_value=None):
                    stopped = manager.ensure_stopped(port=8818, timeout=1)

                self.assertTrue(stopped)
                self.assertIsNotNone(processes[0].poll())
                self.assertIsNotNone(processes[1].poll())
            finally:
                for process in processes:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
