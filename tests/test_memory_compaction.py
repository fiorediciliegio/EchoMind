import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agents.autonomous import (
    follow_up_history_for_prompt,
    historical_follow_up_instruction,
    risk_input_for_follow_up,
)
from app.schemas.dtos import AiMessage
from app.services.risk_context import RiskSignalKind, classify_risk_context
from app.services.privacy import PrivacySanitizer
from app.services.memory import (
    compact_history_for_prompt,
    detect_historical_concerns,
    RedisShortTermMemoryStore,
    relative_time_label,
    summarize_history_for_memory,
)


def settings(**overrides):
    values = {
        "memory_compaction_enabled": True,
        "memory_compaction_recent_messages": 4,
        "memory_summary_max_chars": 220,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MemoryCompactionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)

    def test_compaction_keeps_recent_messages_and_adds_internal_summary(self):
        history = [AiMessage(role="user" if i % 2 == 0 else "assistant", content=f"第{i}条消息 13800138000") for i in range(10)]

        compacted, brief = compact_history_for_prompt(history, settings(), "我最近还是睡不着")

        self.assertEqual(compacted[0].role, "system")
        self.assertIn("历史摘要", compacted[0].content)
        self.assertEqual(len(compacted), 5)
        self.assertTrue(compacted[-1].content.startswith("[时间未知] 第9条消息"))
        self.assertNotIn("13800138000", brief)
        self.assertIn("[已脱敏]", brief)

    def test_compaction_can_be_disabled(self):
        history = [AiMessage(role="user", content="我最近压力很大")]

        compacted, brief = compact_history_for_prompt(history, settings(memory_compaction_enabled=False), "")

        self.assertEqual(compacted[0].role, history[0].role)
        self.assertEqual(compacted[0].content, "[时间未知] 我最近压力很大")
        self.assertIn("学生历史记录", brief)

    def test_summary_is_bounded(self):
        history = [AiMessage(role="user", content="压力" * 200)]

        brief = summarize_history_for_memory(history, max_chars=80)

        self.assertLessEqual(len(brief), 80)

    def test_message_timestamp_is_internal_only(self):
        message = AiMessage(role="user", content="我上周睡不着", createdAt=self.now - timedelta(days=8))

        self.assertEqual(message.createdAt, self.now - timedelta(days=8))
        self.assertEqual(message.model_dump(), {"role": "user", "content": "我上周睡不着"})

    def test_redis_serialization_preserves_database_timestamp_during_backfill(self):
        store = RedisShortTermMemoryStore.__new__(RedisShortTermMemoryStore)
        store.privacy = PrivacySanitizer()
        created_at = self.now - timedelta(days=8)

        payload = json.loads(store._serialize("user", "我上周睡不着", created_at))

        self.assertEqual(payload["createdAt"], created_at.isoformat())

    def test_relative_time_and_summary_distinguish_old_history(self):
        history = [AiMessage(role="user", content="我连续几天睡不着", createdAt=self.now - timedelta(days=8))]

        brief = summarize_history_for_memory(history, max_chars=220, reference_time=self.now)

        self.assertEqual(relative_time_label(history[0].createdAt, self.now), "1周前")
        self.assertIn("[1周前]", brief)
        self.assertNotIn("学生近期关注", brief)

    def test_old_unconfirmed_sleep_problem_requires_confirmation(self):
        history = [AiMessage(role="user", content="我最近连续失眠", createdAt=self.now - timedelta(days=8))]

        concerns = detect_historical_concerns(
            history,
            current_input="最近学习压力有点大",
            stale_after_days=7,
            reference_time=self.now,
        )

        self.assertEqual(len(concerns), 1)
        self.assertEqual(concerns[0]["topic"], "sleep")
        self.assertEqual(concerns[0]["relativeTime"], "1周前")
        self.assertTrue(concerns[0]["needsConfirmation"])

    def test_current_input_that_confirms_or_resolves_problem_suppresses_follow_up(self):
        history = [AiMessage(role="user", content="我最近连续失眠", createdAt=self.now - timedelta(days=8))]

        ongoing = detect_historical_concerns(
            history,
            current_input="我最近还是失眠",
            stale_after_days=7,
            reference_time=self.now,
        )
        resolved = detect_historical_concerns(
            history,
            current_input="我的失眠已经好了",
            stale_after_days=7,
            reference_time=self.now,
        )

        self.assertEqual(ongoing, [])
        self.assertEqual(resolved, [])

    def test_later_resolution_without_repeating_topic_clears_old_problem(self):
        history = [
            AiMessage(role="user", content="我最近连续失眠", createdAt=self.now - timedelta(days=9)),
            AiMessage(role="assistant", content="最近睡眠有什么变化吗", createdAt=self.now - timedelta(days=8)),
            AiMessage(role="user", content="已经好了", createdAt=self.now - timedelta(days=8)),
        ]

        concerns = detect_historical_concerns(history, "最近学习压力有点大", 7, self.now)

        self.assertEqual(concerns, [])

    def test_problem_must_be_older_than_threshold_not_equal_to_it(self):
        history = [AiMessage(role="user", content="我最近连续失眠", createdAt=self.now - timedelta(days=7))]

        concerns = detect_historical_concerns(history, "最近学习压力有点大", 7, self.now)

        self.assertEqual(concerns, [])

    def test_follow_up_instruction_requires_confirmation_without_asserting_current_state(self):
        instruction = historical_follow_up_instruction(
            [
                {
                    "topic": "sleep",
                    "summary": "我最近连续失眠",
                    "relativeTime": "1周前",
                    "needsConfirmation": True,
                    "requiresSafetyCheck": False,
                }
            ]
        )

        self.assertIn("不得把旧问题表述为当前事实", instruction)
        self.assertIn("1周前", instruction)
        self.assertIn("必须", instruction)
        self.assertIn("是否仍在持续", instruction)

    def test_high_risk_history_instruction_asks_about_current_safety_without_escalating_by_itself(self):
        instruction = historical_follow_up_instruction(
            [
                {
                    "topic": "high_risk",
                    "summary": "过去曾表达不想活",
                    "relativeTime": "2周前",
                    "needsConfirmation": True,
                    "requiresSafetyCheck": True,
                }
            ]
        )

        self.assertIn("确认学生当前是否安全", instruction)
        self.assertIn("不能仅凭旧记录触发本轮预警", instruction)

    def test_elliptical_follow_up_uses_recent_history_but_clear_task_does_not(self):
        history = [AiMessage(role="assistant", content="你现在是否还有伤害自己的想法？")]

        self.assertEqual(follow_up_history_for_prompt("是的，还在", history), history)
        self.assertEqual(follow_up_history_for_prompt("帮我看看 Python 作业", history), [])

    def test_affirmative_reply_to_high_risk_question_becomes_explicit_risk_input(self):
        history = [AiMessage(role="assistant", content="你现在是否还有伤害自己的想法？")]

        risk_input = risk_input_for_follow_up("是的，还在，而且更严重了", history)
        negative_input = risk_input_for_follow_up("没有了", history)

        self.assertIn("伤害自己", risk_input)
        self.assertIn("是的，还在", risk_input)
        self.assertEqual(classify_risk_context(risk_input).kind, RiskSignalKind.EXPLICIT_HIGH)
        self.assertEqual(negative_input, "没有了")


if __name__ == "__main__":
    unittest.main()
