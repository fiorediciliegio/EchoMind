import unittest
import json

from app.core.enums import RiskLevel
from app.services.assessment import PsychologicalAssessmentService
from app.services.risk_context import RiskSignalKind
from app.services.memory import RedisShortTermMemoryStore
from app.services.privacy import PrivacySanitizer


class ExplodingAi:
    def complete(self, messages):
        raise AssertionError("high risk hard guard should not call the model")


class StaticAi:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


class PrivacyAndAssessmentTests(unittest.TestCase):
    def test_privacy_sanitizer_masks_common_identifiers(self):
        text = PrivacySanitizer().sanitize("电话 13800138000 邮箱 a@example.com 身份证 110101199003071234")

        self.assertNotIn("13800138000", text)
        self.assertNotIn("a@example.com", text)
        self.assertNotIn("110101199003071234", text)
        self.assertEqual(text.count("[已脱敏]"), 3)

    def test_redis_memory_serializes_sanitized_content(self):
        store = RedisShortTermMemoryStore.__new__(RedisShortTermMemoryStore)
        store.privacy = PrivacySanitizer()

        payload = json.loads(store._serialize("user", "电话 13800138000 邮箱 a@example.com"))

        self.assertNotIn("13800138000", payload["content"])
        self.assertNotIn("a@example.com", payload["content"])
        self.assertEqual(payload["content"].count("[已脱敏]"), 2)

    def test_high_risk_signal_uses_hard_guard_before_model(self):
        result = PsychologicalAssessmentService(ExplodingAi()).assess("我不想活了，想结束生命")

        self.assertEqual(result.risk, RiskLevel.HIGH)
        self.assertGreaterEqual(result.confidence, 0.9)
        self.assertEqual(result.risk_signal_kind, RiskSignalKind.EXPLICIT_HIGH)

    def test_clear_negation_reaches_semantic_assessment_without_hard_high(self):
        ai = StaticAi({
            "emotion": "NORMAL",
            "emotionScore": 0.0,
            "risk": "LOW",
            "confidence": 0.91,
            "summary": "明确否认当前自伤意图",
        })

        result = PsychologicalAssessmentService(ai).assess("我不想伤害自己")

        self.assertEqual(ai.calls, 1)
        self.assertEqual(result.risk, RiskLevel.LOW)
        self.assertEqual(result.risk_signal_kind, RiskSignalKind.NEGATED_CURRENT)
        self.assertFalse(result.requires_safety_confirmation)

    def test_ambiguous_mention_requires_confirmation_when_model_does_not_escalate(self):
        ai = StaticAi({
            "emotion": "ANXIETY",
            "emotionScore": 2.0,
            "risk": "LOW",
            "confidence": 0.65,
            "summary": "语义不明确",
        })

        result = PsychologicalAssessmentService(ai).assess("可能会伤害自己")

        self.assertEqual(result.risk_signal_kind, RiskSignalKind.AMBIGUOUS)
        self.assertTrue(result.requires_safety_confirmation)


if __name__ == "__main__":
    unittest.main()
