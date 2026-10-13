import unittest

from app.core.config import Settings


class ConfigDefaultTests(unittest.TestCase):
    def test_ollama_defaults_use_generic_local_model(self):
        settings = Settings(_env_file=None)

        self.assertEqual(settings.ollama_model, "local-ollama-model:latest")
        self.assertEqual(settings.local_model_name, "local-ollama-model:latest")
        self.assertEqual(settings.local_model_dir, "models/local-ollama-model")
        self.assertEqual(settings.local_model_file, "model.gguf")

    def test_bm25_index_defaults_to_persistent_local_storage(self):
        settings = Settings(_env_file=None)

        self.assertTrue(settings.knowledge_bm25_enabled)
        self.assertFalse(settings.knowledge_bm25_required)
        self.assertEqual(settings.knowledge_bm25_index_dir, "data/bm25")
        self.assertFalse(settings.knowledge_bm25_mmap)


if __name__ == "__main__":
    unittest.main()
