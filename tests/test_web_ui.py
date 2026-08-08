import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
import start


class PresetModificationTests(unittest.TestCase):
    def test_git_history_keeps_latest_touch_for_each_preset(self):
        history = "\n".join([
            "200",
            "presets/newer.json",
            "presets/shared.json",
            "",
            "100",
            "presets/shared.json",
            "presets/older.json",
        ])
        completed = Mock(returncode=0, stdout=history)

        with patch("start.subprocess.run", return_value=completed):
            timestamps = start.preset_modified_timestamps()

        self.assertEqual(timestamps, {
            "newer.json": 200.0,
            "shared.json": 200.0,
            "older.json": 100.0,
        })

    def test_load_presets_orders_by_modified_time_then_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            preset_dir = Path(directory)
            for filename, name in [
                ("bravo.json", "Bravo"),
                ("alpha.json", "Alpha"),
                ("newest.json", "Newest"),
            ]:
                (preset_dir / filename).write_text(
                    json.dumps({"name": name, "models": [], "nodes": []}),
                    encoding="utf-8",
                )

            with patch.object(start, "PRESETS_DIR", preset_dir), patch(
                "start.preset_modified_timestamps",
                return_value={
                    "alpha.json": 100.0,
                    "bravo.json": 100.0,
                    "newest.json": 200.0,
                },
            ):
                presets = start.load_presets()

        self.assertEqual(
            [preset["_filename"] for preset in presets],
            ["newest.json", "alpha.json", "bravo.json"],
        )
        self.assertEqual(
            [preset["_modified_at"] for preset in presets],
            [200.0, 100.0, 100.0],
        )

    def test_untracked_preset_uses_filesystem_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            preset_dir = Path(directory)
            preset_file = preset_dir / "local.json"
            preset_file.write_text(
                json.dumps({"name": "Local", "models": [], "nodes": []}),
                encoding="utf-8",
            )
            expected = preset_file.stat().st_mtime

            with patch.object(start, "PRESETS_DIR", preset_dir), patch(
                "start.preset_modified_timestamps", return_value={}
            ):
                preset = start.load_presets()[0]

        self.assertEqual(preset["_modified_at"], expected)


class PresetSerializationTests(unittest.TestCase):
    def test_serializes_ui_metadata_and_preserves_workflows(self):
        presets = [{
            "name": "Pinned",
            "description": "Dense preset",
            "models": [{"filename": "model.safetensors"}],
            "nodes": ["https://example.com/node"],
            "pinned": True,
            "size_gb": 42,
            "_modified_at": 1786147200.9,
            "workflow": "preset.json",
        }]

        result = server.serialize_presets(presets, {"Pinned"})

        self.assertEqual(result, [{
            "name": "Pinned",
            "description": "Dense preset",
            "models_count": 1,
            "nodes_count": 1,
            "installed": True,
            "workflows": [{
                "label": "Workflow",
                "url": "/api/workflows/preset.json",
                "local": True,
                "file": "preset.json",
            }],
            "pinned": True,
            "size_gb": 42,
            "modified_at": 1786147200,
        }])

    def test_invalid_optional_metadata_becomes_safe_defaults(self):
        presets = [{
            "name": "Unsafe metadata",
            "models": [],
            "nodes": [],
            "pinned": "true",
            "size_gb": "42",
            "_modified_at": "yesterday",
        }]

        result = server.serialize_presets(presets, set())[0]

        self.assertIs(result["pinned"], False)
        self.assertIsNone(result["size_gb"])
        self.assertEqual(result["modified_at"], 0)

    def test_preserves_workflow_list_and_remote_urls(self):
        presets = [
            {
                "name": "Workflow list",
                "models": [],
                "nodes": [],
                "workflows": [
                    {"label": "Local", "file": "local.json"},
                    {"label": "Remote", "url": "https://example.com/list.json"},
                ],
            },
            {
                "name": "Legacy remote",
                "models": [],
                "nodes": [],
                "workflow_url": "https://example.com/legacy.json",
            },
        ]

        result = server.serialize_presets(presets, set())

        self.assertEqual(result[0]["workflows"], [
            {
                "label": "Local",
                "url": "/api/workflows/local.json",
                "local": True,
                "file": "local.json",
            },
            {
                "label": "Remote",
                "url": "https://example.com/list.json",
                "local": False,
                "file": "",
            },
        ])
        self.assertEqual(result[1]["workflows"], [{
            "label": "Workflow",
            "url": "https://example.com/legacy.json",
            "local": False,
            "file": "",
        }])

    def test_non_finite_or_boolean_metadata_uses_safe_public_defaults(self):
        invalid_values = [True, float("nan"), float("inf"), float("-inf")]
        presets = [
            {
                "name": f"Invalid {index}",
                "models": [],
                "nodes": [],
                "size_gb": value,
                "_modified_at": value,
            }
            for index, value in enumerate(invalid_values)
        ]

        result = server.serialize_presets(presets, set())

        self.assertEqual(
            [{"size_gb": preset["size_gb"], "modified_at": preset["modified_at"]}
             for preset in result],
            [{"size_gb": None, "modified_at": 0}] * 4,
        )

    def test_base_preset_remains_hidden(self):
        result = server.serialize_presets(
            [{"name": "Base", "models": [], "nodes": []}],
            set(),
        )
        self.assertEqual(result, [])


class UninstallEndpointTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.PresetHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def post_uninstall(self, preset):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        body = json.dumps({"preset": preset})
        try:
            connection.request(
                "POST",
                "/api/uninstall",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode())
        finally:
            connection.close()

    def test_active_reserved_install_blocks_uninstall_without_downloader(self):
        self.assertTrue(start.reserve_install_slot())
        try:
            with patch("start.get_active_downloader", return_value=None), patch(
                "start.uninstall_preset", return_value={"success": True}
            ) as uninstall_preset:
                status, payload = self.post_uninstall("Pinned")
        finally:
            start.finish_install_reservation()

        self.assertEqual(status, 409)
        self.assertEqual(payload, {
            "success": False,
            "error": "Instalação em andamento — aguarde a conclusão antes de remover.",
        })
        uninstall_preset.assert_not_called()

    def test_uninstall_without_installation_preserves_success_response(self):
        expected = {"success": True, "preset": "Pinned", "deleted": []}
        with patch("start.uninstall_preset", return_value=expected) as uninstall_preset:
            status, payload = self.post_uninstall("Pinned")

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        uninstall_preset.assert_called_once_with("Pinned")

    def test_uninstall_blocks_install_reservation_until_removal_finishes(self):
        uninstall_entered = threading.Event()
        allow_uninstall = threading.Event()
        request_result = {}

        def blocking_uninstall(_preset_name):
            uninstall_entered.set()
            self.assertTrue(allow_uninstall.wait(timeout=2))
            return {"success": True, "preset": "Pinned", "deleted": []}

        def request_uninstall():
            request_result["response"] = self.post_uninstall("Pinned")

        with patch("start.uninstall_preset", side_effect=blocking_uninstall):
            request_thread = threading.Thread(target=request_uninstall)
            request_thread.start()
            self.assertTrue(uninstall_entered.wait(timeout=2))

            self.assertFalse(start.get_install_status()["installing"])
            self.assertFalse(start.cancel_active_install())
            unexpectedly_reserved = start.reserve_install_slot()
            if unexpectedly_reserved:
                start.finish_install_reservation("failed")

            allow_uninstall.set()
            request_thread.join(timeout=2)

        self.assertFalse(unexpectedly_reserved)
        self.assertFalse(request_thread.is_alive())
        self.assertEqual(request_result["response"], (
            200,
            {"success": True, "preset": "Pinned", "deleted": []},
        ))
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation("completed")

    def test_uninstall_exception_releases_operation_reservation(self):
        with patch("start.uninstall_preset", side_effect=RuntimeError("boom")):
            status, payload = self.post_uninstall("Pinned")

        self.assertEqual(status, 500)
        self.assertEqual(payload, {"error": "Erro interno do servidor"})
        self.assertTrue(start.reserve_install_slot())
        start.finish_install_reservation("failed")
