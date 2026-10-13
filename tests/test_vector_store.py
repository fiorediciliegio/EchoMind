import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.vector_store import ChromaKnowledgeStore


class FakeEmbeddingResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}


class FakeCollection:
    def count(self):
        return 0


class FakeClient:
    def __init__(self, path):
        self.path = path

    def get_or_create_collection(self, **kwargs):
        self.collection_kwargs = kwargs
        return FakeCollection()


class FakeChromaModule:
    PersistentClient = FakeClient


class VectorStoreProviderTests(unittest.TestCase):
    def test_qwen_provider_uses_dashscope_embedding_settings(self):
        settings = SimpleNamespace(
            ai_provider="qwen",
            openai_api_key="",
            openai_base_url="https://api.openai.com/v1",
            openai_embedding_model="text-embedding-3-small",
            qwen_api_key="test-qwen-key",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            qwen_embedding_model="text-embedding-v4",
            knowledge_vector_enabled=True,
            knowledge_vector_required=False,
            chroma_persist_dir="data/chroma",
            chroma_collection_name="echomind_knowledge",
            embedding_timeout_seconds=30,
            project_root=Path("."),
        )

        with patch.dict("sys.modules", {"chromadb": FakeChromaModule()}):
            store = ChromaKnowledgeStore(settings)

        with patch("app.services.vector_store.httpx.post", return_value=FakeEmbeddingResponse()) as post:
            embeddings = store.embed_texts(["hello"])

        self.assertEqual(embeddings, [[0.1, 0.2, 0.3]])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-qwen-key")
        self.assertEqual(kwargs["json"]["model"], "text-embedding-v4")


if __name__ == "__main__":
    unittest.main()
