import unittest

import start


class AnimeGenWanPresetTests(unittest.TestCase):
    def test_loads_animegen_wan_2_2_with_exact_gguf_pair(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }
        preset = presets["animegen-wan-2.2.json"]
        diffusion_models = [
            model
            for model in preset["models"]
            if model["dir"] == "diffusion_models"
        ]

        self.assertEqual(preset["name"], "animegen wan 2.2")
        self.assertEqual(
            diffusion_models,
            [
                {
                    "url": "https://huggingface.co/NullpoLab/AnimeGen-I2V-GGUF/resolve/main/I2V_low_noise_Q8_0.gguf?download=true",
                    "dir": "diffusion_models",
                    "filename": "I2V_low_noise_Q8_0.gguf",
                },
                {
                    "url": "https://huggingface.co/NullpoLab/AnimeGen-I2V-GGUF/resolve/main/I2V_high_noise_Q8_0.gguf?download=true",
                    "dir": "diffusion_models",
                    "filename": "I2V_high_noise_Q8_0.gguf",
                },
            ],
        )
        self.assertEqual(
            preset["nodes"].count("https://github.com/city96/ComfyUI-GGUF"),
            1,
        )
        self.assertTrue(preset["use_sage_attention"])


class PinkCherryPresetTests(unittest.TestCase):
    def test_loads_pinkcherry_with_author_recommended_dependencies(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }
        self.assertIn("ltx23-gerador_nsfw-pinkcherry.json", presets)
        preset = presets["ltx23-gerador_nsfw-pinkcherry.json"]
        models = {
            (model["dir"], model["filename"]): model["url"]
            for model in preset["models"]
        }

        self.assertEqual(
            preset["name"],
            "LTX 2.3 GERADOR NSFW - PINKCHERRY",
        )
        workflow_files = [wf["file"] for wf in preset["workflows"]]
        self.assertIn("padrao_ltx2_arrakisV1-3.json", workflow_files)
        self.assertIn("pinkcherry_v18_oficial.json", workflow_files)
        self.assertEqual(
            models[
                (
                    "checkpoints",
                    "PinkCherry_FineTune_fp8scaled_v1_8_LTX23.safetensors",
                )
            ],
            "https://huggingface.co/SexGod1979/PinkCherry_NSFW_LTX23/resolve/main/v1.8/PinkCherry_FineTune_fp8scaled_v1_8_LTX23.safetensors",
        )
        self.assertEqual(
            models[
                (
                    "loras",
                    "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
                )
            ],
            "https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
        )
        self.assertIn(
            ("text_encoders", "gemma-3-12b-it-heretic-v2.safetensors"),
            models,
        )
        self.assertIn(
            ("text_encoders", "ltx-2.3_text_projection_bf16.safetensors"),
            models,
        )
        self.assertIn(
            ("vae", "LTX23_video_vae_bf16.safetensors"),
            models,
        )
        self.assertNotIn(
            ("checkpoints", "10Eros_v1-fp8mixed_learned.safetensors"),
            models,
        )
        self.assertNotIn(
            ("text_encoders", "gemma_3_12B_it_fpmixed.safetensors"),
            models,
        )
        self.assertNotIn(
            (
                "text_encoders",
                "ltx-2-19b-embeddings_connector_dev_bf16.safetensors",
            ),
            models,
        )

        required_nodes = {
            "https://github.com/kijai/ComfyUI-KJNodes",
            "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
            "https://github.com/yolain/ComfyUI-Easy-Use",
            "https://github.com/rgthree/rgthree-comfy",
        }
        self.assertTrue(required_nodes.issubset(preset["nodes"]))
        self.assertTrue(preset["use_sage_attention"])


class UpscaleSmoothPresetTests(unittest.TestCase):
    TARGET_PRESETS = {
        "anima3-studio.json",
        "ltx23-anime-production.json",
        "minimax-h3-5090.json",
        "minimax-h3-6000pro-96gb.json",
    }
    NODE_URL = "https://github.com/adbrasi/upscale_smooth"

    def test_target_presets_include_upscale_smooth_exactly_once(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }

        for filename in self.TARGET_PRESETS:
            with self.subTest(filename=filename):
                self.assertEqual(
                    presets[filename]["nodes"].count(self.NODE_URL),
                    1,
                )


class PresetUiMetadataTests(unittest.TestCase):
    EXPECTED = {
        "anima3-studio.json": (True, 42),
        "anima3.json": (False, 8),
        "animegen-wan-2.2.json": (False, 91),
        "base.json": (False, 1),
        "flux2-klein-4b-base-full.json": (False, 18),
        "flux2-klein-9b-base.json": (False, 24),
        "gerar_imagens_validacao.json": (False, 9),
        "ideogram4.json": (False, 21),
        "krea2-full.json": (True, 39),
        "krea2.json": (False, 28),
        "ltx-gerador_nsfw.json": (False, 37),
        "ltx-lip-sync-gemma-q4.json": (False, 54),
        "ltx-wan-helper.json": (False, 37),
        "ltx23-anime-production.json": (False, 58),
        "ltx23-gerador_nsfw-10eros.json": (False, 58),
        "ltx23-gerador_nsfw-pinkcherry.json": (False, 39),
        "ltx23-gerador_nsfw-sulphur.json": (False, 39),
        "ltx23-gerador_nsfw.json": (False, 39),
        "ltx23-production-base.json": (False, 58),
        "minimax-h3-5090.json": (True, 91),
        "minimax-h3-6000pro-96gb.json": (False, 190),
        "qwen-image.json": (False, 15),
        "seedvr_tester.json": (False, 2),
        "video-scail-test.json": (False, 33),
    }

    def test_every_active_preset_has_approved_ui_metadata(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }

        self.assertEqual(set(presets), set(self.EXPECTED))
        for filename, (pinned, size_gb) in self.EXPECTED.items():
            with self.subTest(filename=filename):
                self.assertIs(presets[filename]["pinned"], pinned)
                self.assertEqual(presets[filename]["size_gb"], size_gb)


if __name__ == "__main__":
    unittest.main()
