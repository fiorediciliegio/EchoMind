import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.schemas.dtos import AiMessage
from app.services.ai import AiClient


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "qwen response"}}]}


class AiProviderTests(unittest.TestCase):
    def test_qwen_complete_uses_dashscope_openai_compatible_settings(self):
        settings = SimpleNamespace(
            ai_provider="qwen",
            ai_temperature=0.35,
            ai_max_tokens=512,
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            qwen_api_key="test-qwen-key",
            qwen_model="qwen-plus",
        )

        with patch("app.services.ai.httpx.post", return_value=FakeResponse()) as post:
            result = AiClient(settings).complete([AiMessage(role="user", content="hello")])

        self.assertEqual(result, "qwen response")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-qwen-key")
        self.assertEqual(kwargs["json"]["model"], "qwen-plus")


if __name__ == "__main__":
    unittest.main()
