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
        self.assertEqual(
            preset["workflow"],
            "padrao_ltx2_arrakisV1-3.json",
        )
        self.assertEqual(
            models[
                (
                    "checkpoints",
                    "PinkCherry_FineTune_fp8scaled_v1_7-alpha.safetensors",
                )
            ],
            "https://huggingface.co/SexGod1979/PinkCherry_NSFW_LTX23/resolve/main/v1.7-alpha/PinkCherry_FineTune_fp8scaled_v1_7-alpha.safetensors?download=true",
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


if __name__ == "__main__":
    unittest.main()
