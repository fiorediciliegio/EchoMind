import unittest

from app.services.risk_context import (
    RiskSignalKind,
    classify_risk_context,
    has_risk_mention,
    is_explicit_high_risk,
)


class RiskContextTests(unittest.TestCase):
    def test_explicit_current_danger_is_hard_high(self):
        cases = [
            "我今晚准备自杀",
            "我已经计划好了，今晚结束生命",
            "我不能保证自己不会伤害自己",
            "我无法保证今晚不自残",
            "我不想活了",
            "我想死",
            "我现在有自杀的念头",
            "我一直在想自残",
            "我控制不住自己，可能会伤害自己",
            "我担心自己马上会自残",
            "如果今晚还是这样，我就自杀",
            "如果有人继续欺负我，我就自杀",
            "论文写不完我就自杀",
            "看完这部电影我就结束生命",
            "我刚刚割腕了",
            "我已经吞了一瓶安眠药",
            "我站在楼顶，准备跳下去",
            "我已经写好遗书",
            "我要杀了他",
            "我准备拿刀伤害同学",
            "我有伤害他人的计划",
            "我想报复他们，刀已经准备好了",
            "I want to kill myself tonight",
            "I can't guarantee I won't hurt myself",
            "I have suicidal thoughts",
            "I am going to hurt him",
        ]

        for text in cases:
            with self.subTest(text=text):
                result = classify_risk_context(text)
                self.assertEqual(result.kind, RiskSignalKind.EXPLICIT_HIGH)
                self.assertFalse(result.requires_confirmation)
                self.assertTrue(is_explicit_high_risk(text))

    def test_clear_current_negation_is_not_hard_high(self):
        cases = [
            "我不想伤害自己",
            "我没有自杀的想法",
            "我不会自残",
            "我现在没有轻生的计划",
            "我没有伤害别人的打算",
            "我不会杀他",
            "I do not want to kill myself",
            "I have no plan to hurt myself",
        ]

        for text in cases:
            with self.subTest(text=text):
                result = classify_risk_context(text)
                self.assertEqual(result.kind, RiskSignalKind.NEGATED_CURRENT)
                self.assertFalse(is_explicit_high_risk(text))

    def test_explicit_danger_wins_over_negation_or_contrast(self):
        cases = [
            "我不会伤害自己，但是我已经准备好了刀，今晚就动手",
            "我不想这么做，但我控制不住自己，可能会自残",
            "我没有自杀计划，不过我现在已经决定结束生命",
        ]

        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(classify_risk_context(text).kind, RiskSignalKind.EXPLICIT_HIGH)

    def test_contextual_mentions_are_not_student_hard_high(self):
        cases = [
            "我以前想过自杀，但现在没有了",
            "朋友说他想自杀",
            "朋友对我说“我想自杀”",
            "朋友说他要杀人",
            "新闻里有人自杀了",
            "电影里有人割腕",
            "如果有人想自杀，应该怎么办？",
            "“我想自杀”这句话是什么意思？",
            "论文里讨论了 self-harm",
        ]

        for text in cases:
            with self.subTest(text=text):
                result = classify_risk_context(text)
                self.assertEqual(result.kind, RiskSignalKind.CONTEXTUAL_MENTION)
                self.assertFalse(is_explicit_high_risk(text))

    def test_first_person_risk_inside_reported_speech_remains_high(self):
        for text in ("我跟朋友说我想自杀", "我对朋友说“我想自杀”"):
            with self.subTest(text=text):
                self.assertEqual(classify_risk_context(text).kind, RiskSignalKind.EXPLICIT_HIGH)

    def test_unresolved_risk_mention_requires_confirmation(self):
        cases = ["可能会伤害自己", "最近总出现轻生这个念头", "自杀"]

        for text in cases:
            with self.subTest(text=text):
                result = classify_risk_context(text)
                self.assertEqual(result.kind, RiskSignalKind.AMBIGUOUS)
                self.assertTrue(result.requires_confirmation)

    def test_no_risk_language_returns_none(self):
        result = classify_risk_context("帮我看看 Python 作业")

        self.assertEqual(result.kind, RiskSignalKind.NONE)
        self.assertFalse(result.requires_confirmation)
        self.assertFalse(has_risk_mention("帮我看看 Python 作业"))

    def test_risk_mention_is_broader_than_explicit_high(self):
        self.assertTrue(has_risk_mention("你现在是否还有伤害自己的想法？"))
        self.assertFalse(is_explicit_high_risk("你现在是否还有伤害自己的想法？"))


if __name__ == "__main__":
    unittest.main()
