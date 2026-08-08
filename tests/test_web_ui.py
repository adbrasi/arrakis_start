import json
import tempfile
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

    def test_base_preset_remains_hidden(self):
        result = server.serialize_presets(
            [{"name": "Base", "models": [], "nodes": []}],
            set(),
        )
        self.assertEqual(result, [])
