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


if __name__ == "__main__":
    unittest.main()
