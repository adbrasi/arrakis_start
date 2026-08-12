import unittest

import start


class LTX25CompletePresetTests(unittest.TestCase):
    EXPECTED_MODELS = [
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/diffusion_models/ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors",
            "dir": "diffusion_models",
            "filename": "ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            "dir": "text_encoders",
            "filename": "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
        },
        {
            "url": "https://huggingface.co/Comfy-Org/gemma-4/resolve/main/text_encoders/gemma4_e2b_it_bf16.safetensors",
            "dir": "text_encoders",
            "filename": "gemma4_e2b_it_bf16.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-video-vae-bf16.safetensors",
            "dir": "vae",
            "filename": "ltx-2.5-video-vae-bf16.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/vae/ltx-2.5-audio-vae-bf16.safetensors",
            "dir": "vae",
            "filename": "ltx-2.5-audio-vae-bf16.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
            "dir": "loras",
            "filename": "ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            "dir": "latent_upscale_models",
            "filename": "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/latent_upscale_models/ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors",
            "dir": "latent_upscale_models",
            "filename": "ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors",
        },
        {
            "url": "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/model_patches/ltx-2.5-duration-head-bf16.safetensors",
            "dir": "model_patches",
            "filename": "ltx-2.5-duration-head-bf16.safetensors",
        },
    ]

    def test_loads_complete_non_duplicated_ltx25_stack(self):
        presets = {
            preset["_filename"]: preset for preset in start.load_presets()
        }
        preset = presets["ltx25-5090-complete.json"]

        self.assertEqual(preset["name"], "LTX 2.5 COMPLETE - RTX 5090")
        self.assertEqual(preset["models"], self.EXPECTED_MODELS)
        self.assertEqual(
            len({(model["dir"], model["filename"]) for model in preset["models"]}),
            len(preset["models"]),
        )
        self.assertTrue(all("?" not in model["url"] for model in preset["models"]))
        self.assertEqual(
            preset["workflows"],
            [
                {"label": "DEV single-stage", "file": "ltx25_dev_single_stage_5090.json"},
                {"label": "DEV two-stage", "file": "ltx25_dev_two_stage_5090.json"},
                {"label": "Distilled LoRA 8 steps", "file": "ltx25_distilled_lora_5090.json"},
            ],
        )
        self.assertEqual(
            preset["nodes"],
            [
                "https://github.com/adbrasi/ComfyUI-LTXVideo",
                "https://github.com/kijai/ComfyUI-KJNodes",
                "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
            ],
        )


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
        "ltx25-5090-complete.json": (True, 55),
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
