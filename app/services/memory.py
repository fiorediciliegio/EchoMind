
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from importlib import import_module
from typing import Protocol

from app.core.config import Settings
from app.models.entities import ChatMessage
from app.schemas.dtos import AiMessage
from app.services.privacy import PrivacySanitizer


logger = logging.getLogger(__name__)


CONCERN_TOPICS = {
    "high_risk": ("自杀", "自残", "不想活", "结束生命", "伤害自己", "轻生", "suicide", "kill myself", "self harm"),
    "sleep": ("失眠", "睡不着", "睡眠", "熬夜", "insomnia"),
    "anxiety": ("焦虑", "恐慌", "惊恐", "紧张", "anxious", "panic"),
    "low_mood": ("抑郁", "低落", "难过", "崩溃", "痛苦", "无助", "depress", "hopeless"),
    "stress": ("压力", "压得喘不过气", "stress"),
}

RESOLUTION_WORDS = ("已经好了", "已经恢复", "不再", "没有了", "缓解了", "好多了", "解决了")


class MemoryCompactionSettings(Protocol):
    memory_compaction_enabled: bool
    memory_compaction_recent_messages: int
    memory_summary_max_chars: int


class RedisShortTermMemoryStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.privacy = PrivacySanitizer()
        self.client = self._connect()

    def load_recent(self, session_public_id: str) -> list[AiMessage]:
        if self.client is None:
            return []
        try:
            return self._read(session_public_id, self.settings.redis_memory_max_messages)
        except Exception as exc:
            logger.warning("Redis memory read unavailable: %s", exc)
            return []

    def messages_from_rows(self, rows: list[ChatMessage]) -> list[AiMessage]:
        return [self._message_from_row(row) for row in rows]

    def append(self, session_public_id: str, role: str, content: str) -> None:
        if self.client is None:
            return
        key = self._key(session_public_id)
        payload = self._serialize(role, content)
        try:
            self.client.rpush(key, payload)
            self.client.ltrim(key, -self.settings.redis_memory_max_messages, -1)
            self.client.expire(key, self.settings.redis_memory_ttl_seconds)
        except Exception as exc:
            logger.warning("Redis memory append unavailable: %s", exc)

    def replace(self, session_public_id: str, messages: list[AiMessage]) -> None:
        if self.client is None:
            return
        key = self._key(session_public_id)
        pipe = self.client.pipeline()
        pipe.delete(key)
        if messages:
            pipe.rpush(
                key,
                *[
                    self._serialize(message.role, message.content, message.createdAt)
                    for message in messages
                ],
            )
            pipe.ltrim(key, -self.settings.redis_memory_max_messages, -1)
            pipe.expire(key, self.settings.redis_memory_ttl_seconds)
        try:
            pipe.execute()
        except Exception as exc:
            logger.warning("Redis memory replace unavailable: %s", exc)

    def _read(self, session_public_id: str, limit: int) -> list[AiMessage]:
        raw_items = self.client.lrange(self._key(session_public_id), -limit, -1)
        messages = []
        for raw in raw_items:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            role = str(data.get("role", "")).lower()
            content = str(data.get("content", ""))
            if role and content:
                messages.append(
                    AiMessage(
                        role=role,
                        content=self.privacy.sanitize(content),
                        createdAt=_parse_datetime(data.get("createdAt")),
                    )
                )
        return messages

    def _connect(self):
        try:
            redis_module = import_module("redis")
        except ModuleNotFoundError as exc:
            raise RuntimeError("请先安装 requirements.txt 中的 redis 依赖") from exc
        client = redis_module.Redis.from_url(
            self.settings.redis_url,
            decode_responses=True,
            socket_timeout=self.settings.redis_socket_timeout_seconds,
            socket_connect_timeout=self.settings.redis_socket_timeout_seconds,
        )
        try:
            client.ping()
        except Exception as exc:
            logger.warning("Redis memory disabled: %s", exc)
            return None
        return client

    def _message_from_row(self, row: ChatMessage) -> AiMessage:
        return AiMessage(
            role=row.role.lower(),
            content=self.privacy.sanitize(row.content),
            createdAt=row.created_at,
        )

    def _serialize(self, role: str, content: str, created_at: datetime | None = None) -> str:
        return json.dumps(
            {
                "role": role.lower(),
                "content": self.privacy.sanitize(content),
                "createdAt": (created_at or datetime.now(timezone.utc)).isoformat(),
            },
            ensure_ascii=False,
        )

    def _key(self, session_public_id: str) -> str:
        return f"echomind:short-term-memory:{session_public_id}"


class EphemeralMemoryStore:
    def __init__(self, history: list[AiMessage]):
        self.history = history

    def load_recent(self, _session_public_id: str) -> list[AiMessage]:
        return list(self.history)

    def append(self, _session_public_id: str, _role: str, _content: str) -> None:
        return None

    def replace(self, _session_public_id: str, _messages: list[AiMessage]) -> None:
        return None


class EphemeralPrivateMemory:
    def load(self, _agent_name: str, _session_public_id: str) -> list[AiMessage]:
        return []

    def append(self, _agent_name: str, _session_public_id: str, _content: str) -> None:
        return None

    def _key(self, _agent_name: str, _session_public_id: str) -> str:
        return "private:memory-disabled"


def compact_history_for_prompt(
    history: list[AiMessage],
    settings: MemoryCompactionSettings,
    current_input: str = "",
) -> tuple[list[AiMessage], str]:
    """Return bounded prompt history plus a student-safe memory brief.

    The summary is deterministic and avoids diagnostic labels. It is intended
    for prompt context and auditability, not for student-facing display.
    """

    sanitized = [
        AiMessage(
            role=item.role,
            content=PrivacySanitizer().sanitize(item.content),
            createdAt=item.createdAt,
        )
        for item in history
    ]
    if not sanitized:
        return [], "无相关历史记忆。"

    recent_count = max(2, int(getattr(settings, "memory_compaction_recent_messages", 8)))
    max_chars = max(120, int(getattr(settings, "memory_summary_max_chars", 500)))
    brief = summarize_history_for_memory(sanitized, current_input, max_chars)
    prompt_history = [
        AiMessage(
            role=item.role,
            content=f"[{relative_time_label(item.createdAt)}] {item.content}",
            createdAt=item.createdAt,
        )
        for item in sanitized
    ]

    if not getattr(settings, "memory_compaction_enabled", True) or len(sanitized) <= recent_count:
        return prompt_history, brief

    recent = prompt_history[-recent_count:]
    summary_message = AiMessage(
        role="system",
        content=(
            "历史摘要（仅供 EchoMind 内部上下文使用；不要向学生展示；"
            "不要据此输出诊断、风险等级或后台标签）：\n" + brief
        ),
    )
    return [summary_message, *recent], brief


def summarize_history_for_memory(
    history: list[AiMessage],
    current_input: str = "",
    max_chars: int = 500,
    reference_time: datetime | None = None,
) -> str:
    privacy = PrivacySanitizer()
    user_points = []
    assistant_points = []
    for message in history:
        content = " ".join(privacy.sanitize(message.content).split())
        if not content:
            continue
        point = f"[{relative_time_label(message.createdAt, reference_time)}] {_clip(content, 80)}"
        if message.role == "user":
            user_points.append(point)
        elif message.role == "assistant":
            assistant_points.append(point)

    parts = []
    if user_points:
        parts.append("学生历史记录：" + "；".join(user_points[-4:]))
    if assistant_points:
        parts.append("历史支持记录：" + "；".join(assistant_points[-3:]))
    if current_input:
        parts.append("本轮输入关注：" + _clip(privacy.sanitize(current_input), 80))
    if not parts:
        return "无相关历史记忆。"
    return _clip("\n".join(parts), max_chars)


def relative_time_label(created_at: datetime | None, reference_time: datetime | None = None) -> str:
    if created_at is None:
        return "时间未知"
    current = _as_utc(reference_time or datetime.now(timezone.utc))
    created = _as_utc(created_at)
    days = max(0, (current.date() - created.date()).days)
    if days == 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 7:
        return f"{days}天前"
    if days < 30:
        return f"{days // 7}周前"
    return f"{max(1, days // 30)}个月前"


def detect_historical_concerns(
    history: list[AiMessage],
    current_input: str,
    stale_after_days: int = 7,
    reference_time: datetime | None = None,
) -> list[dict[str, object]]:
    current = _as_utc(reference_time or datetime.now(timezone.utc))
    current_lower = (current_input or "").lower()
    concerns: list[dict[str, object]] = []
    for topic, keywords in CONCERN_TOPICS.items():
        if _contains_any(current_lower, keywords):
            continue
        matching = [
            (index, message)
            for index, message in enumerate(history)
            if message.role == "user" and _contains_any(message.content.lower(), keywords)
        ]
        if not matching:
            continue
        latest_index, latest = matching[-1]
        if latest.createdAt is None or _contains_any(latest.content, RESOLUTION_WORDS):
            continue
        later_user_messages = [message for message in history[latest_index + 1:] if message.role == "user"]
        if any(_contains_any(message.content, RESOLUTION_WORDS) for message in later_user_messages):
            continue
        age_days = max(0, (current - _as_utc(latest.createdAt)).days)
        if age_days <= max(0, stale_after_days):
            continue
        concerns.append(
            {
                "topic": topic,
                "summary": _clip(latest.content, 80),
                "relativeTime": relative_time_label(latest.createdAt, current),
                "needsConfirmation": True,
                "requiresSafetyCheck": topic == "high_risk",
                "createdAt": _as_utc(latest.createdAt).isoformat(),
            }
        )
    concerns.sort(key=lambda item: str(item["createdAt"]), reverse=True)
    return concerns


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(word in lowered for word in words)


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clip(text: str, limit: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."
