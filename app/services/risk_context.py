from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class RiskSignalKind(str, Enum):
    EXPLICIT_HIGH = "EXPLICIT_HIGH"
    NEGATED_CURRENT = "NEGATED_CURRENT"
    CONTEXTUAL_MENTION = "CONTEXTUAL_MENTION"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


@dataclass(frozen=True)
class RiskContextResult:
    kind: RiskSignalKind
    matched_terms: tuple[str, ...] = ()
    reason: str = ""
    requires_confirmation: bool = False


RISK_TERMS = (
    "自杀", "自残", "轻生", "结束生命", "结束我的生命", "伤害自己", "伤害我自己",
    "割腕", "跳楼", "服毒", "上吊", "不想活", "活不下去", "想死", "去死",
    "杀人", "杀他", "杀她", "杀了他", "杀了她", "伤害他人", "伤害别人", "伤害同学", "报复他们",
    "suicide", "kill myself", "end my life", "hurt myself", "self harm", "self-harm",
    "take my own life", "suicidal thoughts", "hurt him", "hurt her", "kill him", "kill her",
)

_CONTRAST_SPLIT = re.compile(r"(?:但是|不过|可是|然而|但|\bbut\b|\bhowever\b)", re.IGNORECASE)
_HISTORICAL_MARKERS = re.compile(r"以前|曾经|过去|之前|上周|上个月|去年|那时候|小时候|从前|previously|used to|in the past")
_CURRENT_MARKERS = re.compile(r"现在|目前|此刻|今晚|今天|马上|立刻|等会|正在|仍然|还在|now|tonight|right now|still")
_INFORMATIONAL_CONTEXT = re.compile(
    r"(?:这(?:句|段|个)话|这个词).{0,16}(?:是什么意思|如何定义)|"
    r"(?:自杀|自残|轻生|伤害自己|self[ -]?harm|suicide).{0,12}(?:是什么意思|如何定义)|"
    r"(?:论文|作业|研究|新闻|电影|书里|文章|案例).{0,20}(?:讨论|提到|写道|描述|出现|有人)"
    r".{0,20}(?:自杀|自残|轻生|伤害自己|割腕|self[ -]?harm|suicide)|"
    r"讨论.{0,8}(?:自杀|自残|轻生|伤害自己|self[ -]?harm|suicide)|"
    r"what does .{0,30}(?:suicide|self[ -]?harm) mean",
    re.IGNORECASE,
)
_GENERAL_HYPOTHETICAL = re.compile(
    r"(?:如果|假如|假设|假定|比如)(?:有)?(?:人|别人|一个人|学生|朋友|同学)"
    r"(?:想|要|准备|计划|正在|可能会).{0,12}"
    r"(?:自杀|自残|轻生|伤害自己)|"
    r"(?:if|suppose|assuming) (?:someone|a person|a student).{0,40}(?:suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_THIRD_PARTY = re.compile(
    r"(?:朋友|同学|室友|老师|家人|父母|他|她|他们|别人|有人|新闻里的人|案例中的人)"
    r".{0,12}(?:说|表示|提到|写道|想|要|准备|计划).{0,16}"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|割腕|跳楼|杀人|伤害他人|伤害别人)|"
    r"(?:my friend|my classmate|my roommate|he|she|they|someone).{0,20}"
    r"(?:said|says|wants?|plans?|intends?).{0,24}(?:suicide|kill (?:himself|herself|themselves)|self[ -]?harm)",
    re.IGNORECASE,
)
_THIRD_PARTY_FIRST_PERSON_QUOTE = re.compile(
    r"(?:朋友|同学|室友|老师|家人|父母|他|她|他们).{0,10}(?:对我)?(?:说|表示|告诉我)"
    r"[：:,，]?\s*[“\"']我.{0,12}(?:自杀|自残|轻生|结束生命|伤害自己)|"
    r"(?:my friend|my classmate|my roommate|he|she|they).{0,16}(?:said|told me)\s*[,:]?\s*[\"']i"
    r".{0,20}(?:kill myself|hurt myself|suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_SELF_REPORTED_FIRST_PERSON_QUOTE = re.compile(
    r"我(?:跟|对|告诉).{0,8}(?:朋友|同学|室友|老师|家人|父母|他|她|他们)"
    r".{0,4}(?:说|表示|提到|告诉)[：:,，]?\s*[“\"']我.{0,12}"
    r"(?:自杀|自残|轻生|结束生命|伤害自己)|"
    r"\bi\s+(?:said|told).{0,20}[\"']i.{0,20}(?:kill myself|hurt myself|suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_FIRST_PERSON_RISK = re.compile(
    r"(?:我|本人|自己)(?:现在|真的|已经|一直|也|就|马上|今晚|今天|最近|仍然|还)?"
    r"(?:很|非常)?(?:在)?(?:想|要|准备|打算|计划|决定|就要|马上要|忍不住想)(?:去)?"
    r"(?:自杀|自残|轻生|结束(?:我)?的?生命|伤害(?:我)?自己|割腕|跳楼|服毒|上吊)|"
    r"\bi\s+(?:really\s+)?(?:want|plan|intend|am going|have decided)\s+to\s+"
    r"(?:kill myself|hurt myself|end my life|commit suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_DIRECT_RISK = re.compile(
    r"^(?:现在|今晚|今天|马上|真的|已经)?(?:就)?(?:想|要|准备|打算|计划|决定)(?:去)?"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|割腕|跳楼|服毒|上吊)",
    re.IGNORECASE,
)
_DECLARED_ACTION = re.compile(
    r"(?:我|本人)(?:现在|今晚|今天|马上|立刻)?(?:就|马上就)"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|割腕|跳楼|服毒|上吊)|"
    r"\bi(?:'ll| will)\s+(?:kill myself|hurt myself|end my life|commit suicide)",
    re.IGNORECASE,
)
_FIXED_HIGH = re.compile(
    r"(?<!没有)(?<!不是)(?<!并非)不想(?:再)?活(?:了|下去)?|"
    r"(?:我)?活不下去(?:了)?|(?:我)?想死(?:了)?|生无可恋",
    re.IGNORECASE,
)
_CANNOT_GUARANTEE = re.compile(
    r"(?:我)?(?:不能|无法|不敢)保证(?:自己)?(?:不会|不再|不去)"
    r"(?:自杀|自残|轻生|伤害自己|伤害我自己)|"
    r"\bi\s+(?:cannot|can't) guarantee i (?:will not|won't)\s+"
    r"(?:kill myself|hurt myself|self[ -]?harm)",
    re.IGNORECASE,
)
_LOSS_OF_CONTROL = re.compile(
    r"(?:我)?(?:控制不住|无法控制|怕自己会|担心自己会|忍不住)"
    r".{0,12}(?:自杀|自残|轻生|伤害自己|伤害我自己|割腕|跳楼|服毒|上吊)|"
    r"\bi\s+(?:can't control myself|am afraid i will|fear i might).{0,20}"
    r"(?:kill myself|hurt myself|self[ -]?harm)",
    re.IGNORECASE,
)
_PLAN_OR_IMMEDIACY = re.compile(
    r"(?:今晚|今天|马上|立刻|现在|等会).{0,12}(?:自杀|自残|轻生|结束生命|伤害自己|割腕|跳楼|服毒|上吊)|"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|割腕|跳楼|服毒|上吊).{0,12}"
    r"(?:计划|时间|地点|工具|方法|马上|立刻|今晚|今天|现在)|"
    r"(?:准备|拿好|买好|藏好).{0,8}(?:刀|药|绳|工具).{0,12}"
    r"(?:马上|今晚|现在|等会).{0,8}(?:动手|去做|结束这一切)|"
    r"(?:tonight|today|right now|immediately).{0,24}(?:kill myself|hurt myself|suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_FAREWELL_HIGH = re.compile(
    r"(?:我)?(?:已经)?(?:写好|准备好)(?:了)?(?:遗书|告别信)|"
    r"(?:我)?(?:遗书|告别信)(?:已经)?(?:写好|准备好)|"
    r"这是我最后一次(?:说话|发消息)|再也见不到我"
)
_CURRENT_IDEATION = re.compile(
    r"(?:我|自己)(?:现在|最近|一直|反复|仍然|还)?(?:在)?(?:有|出现|存在|冒出|想着|想)"
    r".{0,8}(?:自杀|自残|轻生|伤害自己).{0,6}(?:想法|念头|冲动)?|"
    r"\bi\s+(?:currently\s+|still\s+)?have\s+(?:suicidal|self[ -]?harm)\s+(?:thoughts?|urges?)",
    re.IGNORECASE,
)
_RECENT_ATTEMPT = re.compile(
    r"(?:我)?(?:刚刚|刚才|已经|刚)?(?:割腕|服毒|上吊|跳楼|伤害了自己)|"
    r"(?:我)?(?:刚刚|刚才|已经|刚)?吞了.{0,8}(?:药|安眠药)|"
    r"(?:我)?站在(?:楼顶|天台|桥边).{0,12}(?:准备|打算|想|要).{0,6}(?:跳下去|跳楼)|"
    r"\bi\s+(?:just\s+)?(?:cut myself|took an overdose|swallowed .{0,10} pills)",
    re.IGNORECASE,
)
_HARM_OTHERS_HIGH = re.compile(
    r"(?:我|本人)(?:现在|已经|真的|就|马上|今晚|今天)?(?:想|要|准备|打算|计划|决定|就要)"
    r".{0,10}(?:杀了|杀死|伤害|报复)(?:他|她|他们|别人|同学|老师|家人|某人)|"
    r"(?:我|本人)(?:现在|已经)?有.{0,6}(?:杀人|伤害他人|伤害别人)(?:的)?(?:想法|念头|冲动|计划|打算)|"
    r"(?:我)?想报复(?:他|她|他们|别人).{0,12}(?:刀|工具).{0,8}(?:准备好|拿好|买好)|"
    r"\bi\s+(?:want|plan|intend|am going)\s+to\s+(?:kill|hurt)\s+(?:him|her|them|someone)",
    re.IGNORECASE,
)
_CLEAR_NEGATION = re.compile(
    r"(?:我|自己)?(?:现在|目前)?(?:没有|没|不会|不想|不打算|不准备|从未|从来没有|不再|并不)"
    r"(?:真的)?(?:想|要|打算|准备|计划)?(?:去)?"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|伤害我自己|割腕|跳楼|服毒|上吊)|"
    r"(?:我|自己)?(?:现在|目前)?没有(?:自杀|自残|轻生|伤害自己)(?:的)?(?:想法|念头|打算|计划)|"
    r"(?:我|自己)?(?:现在|目前)?(?:没有|不会|不想|不打算|不准备|从未|不再)"
    r".{0,8}(?:杀人|杀他|杀她|伤害他人|伤害别人)(?:的)?(?:想法|念头|打算|计划)?|"
    r"\bi\s+(?:do not|don't|will not|won't)\s+(?:want to|plan to|intend to)?\s*"
    r"(?:kill myself|hurt myself|end my life|commit suicide|self[ -]?harm)|"
    r"\bi\s+have no (?:thoughts?|plans?|intention) to\s+(?:kill myself|hurt myself|self[ -]?harm)",
    re.IGNORECASE,
)
_HISTORICAL_RESOLUTION = re.compile(
    r"(?:以前|曾经|过去|之前|上周|上个月|去年|那时候|小时候).{0,30}"
    r"(?:自杀|自残|轻生|结束生命|伤害自己|不想活|想死).{0,30}"
    r"(?:现在|目前|后来|已经).{0,8}(?:没有|没了|不会|不再|过去了|好多了)",
    re.IGNORECASE,
)
_CURRENT_SAFETY_QUESTION = re.compile(
    r"(?:你|您).{0,8}(?:是否|有没有|还有没有|还会不会|会不会).{0,16}"
    r"(?:自杀|自残|轻生|伤害自己|结束生命)|"
    r"(?:do you|are you).{0,24}(?:kill yourself|hurt yourself|suicide|self[ -]?harm)",
    re.IGNORECASE,
)
_AFFIRMATIVE_SAFETY_FOLLOW_UP = re.compile(
    r"上一轮安全确认：.{0,120}(?:自杀|自残|轻生|伤害自己|结束生命).{0,120}"
    r"学生当前确认：\s*(?:是|对|有|还在|仍然|依然|更严重)",
    re.IGNORECASE | re.DOTALL,
)


def classify_risk_context(text: str) -> RiskContextResult:
    normalized = _normalize(text)
    matched_terms = _matched_terms(normalized)

    if matched_terms and (_INFORMATIONAL_CONTEXT.search(normalized) or _GENERAL_HYPOTHETICAL.search(normalized)):
        return RiskContextResult(
            RiskSignalKind.CONTEXTUAL_MENTION,
            matched_terms,
            "risk language appears in an informational or general hypothetical context",
        )

    if _THIRD_PARTY_FIRST_PERSON_QUOTE.search(normalized) and not _SELF_REPORTED_FIRST_PERSON_QUOTE.search(normalized):
        return RiskContextResult(
            RiskSignalKind.CONTEXTUAL_MENTION,
            matched_terms,
            "first-person risk language is quoted from another speaker",
        )

    if _contains_explicit_high(normalized):
        return RiskContextResult(
            RiskSignalKind.EXPLICIT_HIGH,
            matched_terms,
            "explicit current intent, plan, means, timing, or loss of control detected",
        )

    if not matched_terms:
        return RiskContextResult(RiskSignalKind.NONE, reason="no risk language detected")

    if _HISTORICAL_RESOLUTION.search(normalized):
        return RiskContextResult(
            RiskSignalKind.CONTEXTUAL_MENTION,
            matched_terms,
            "historical first-person risk is explicitly described as not current",
        )

    if _CLEAR_NEGATION.search(normalized):
        return RiskContextResult(
            RiskSignalKind.NEGATED_CURRENT,
            matched_terms,
            "current risk behavior is explicitly negated",
        )

    if _THIRD_PARTY.search(normalized):
        return RiskContextResult(
            RiskSignalKind.CONTEXTUAL_MENTION,
            matched_terms,
            "risk language refers to another person",
        )

    if _HISTORICAL_MARKERS.search(normalized):
        return RiskContextResult(
            RiskSignalKind.CONTEXTUAL_MENTION,
            matched_terms,
            "risk language is historical and needs current-state confirmation",
            requires_confirmation=True,
        )

    return RiskContextResult(
        RiskSignalKind.AMBIGUOUS,
        matched_terms,
        "risk language is present but current intent is unresolved",
        requires_confirmation=True,
    )


def is_explicit_high_risk(text: str) -> bool:
    return classify_risk_context(text).kind is RiskSignalKind.EXPLICIT_HIGH


def has_risk_mention(text: str) -> bool:
    return bool(_matched_terms(_normalize(text)))


def _contains_explicit_high(text: str) -> bool:
    if (
        _CANNOT_GUARANTEE.search(text)
        or _LOSS_OF_CONTROL.search(text)
        or _FAREWELL_HIGH.search(text)
        or _CURRENT_IDEATION.search(text)
        or _RECENT_ATTEMPT.search(text)
        or _HARM_OTHERS_HIGH.search(text)
        or _AFFIRMATIVE_SAFETY_FOLLOW_UP.search(text)
    ):
        return True

    for clause in _CONTRAST_SPLIT.split(text):
        clause = clause.strip(" ，。！？；,.!?;:\"'“”‘’")
        if not clause:
            continue
        if _CURRENT_SAFETY_QUESTION.search(clause) or _CLEAR_NEGATION.search(clause):
            continue
        if _HISTORICAL_MARKERS.search(clause) and not _CURRENT_MARKERS.search(clause):
            continue
        if _THIRD_PARTY.search(clause) and not _FIRST_PERSON_RISK.search(clause):
            continue
        if any(
            pattern.search(clause)
            for pattern in (_FIRST_PERSON_RISK, _DIRECT_RISK, _DECLARED_ACTION, _FIXED_HIGH, _PLAN_OR_IMMEDIACY)
        ):
            return True
    return False


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _matched_terms(text: str) -> tuple[str, ...]:
    return tuple(term for term in RISK_TERMS if term in text)
