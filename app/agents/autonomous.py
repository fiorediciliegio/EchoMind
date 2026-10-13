from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.agents.events import (
    AgentArtifact,
    AgentEvent,
    AgentEventType,
    AgentMessage,
    AgentTask,
    AgentTurnResult,
    CollaborationBlackboard,
    TaskPriority,
)
from app.agents.registry import AgentCapability, AgentDecision, AgentProfile
from app.core.config import Settings
from app.core.enums import IntentType, RiskLevel
from app.schemas.dtos import AiMessage
from app.services.agent_models import AgentModelRegistry
from app.services.ai import AiClient, PromptTemplates, has_consult_signal
from app.services.assessment import PsychologicalAssessmentService
from app.services.risk_context import classify_risk_context, has_risk_mention, is_explicit_high_risk

if TYPE_CHECKING:
    from app.models.entities import ChatSession, UserAccount
    from app.services.knowledge import KnowledgeService, SearchResult
    from app.services.memory import RedisShortTermMemoryStore


GENERAL_TASK_WORDS = [
    "java", "python", "javascript", "代码", "编程", "程序", "算法", "数据库", "spring", "maven",
    "前端", "后端", "项目", "接口", "bug", "报错", "作业", "论文", "翻译", "总结", "解释",
    "怎么写", "如何", "是什么", "为什么", "给我", "帮我", "推荐", "查询", "天气", "路线",
]

FOLLOW_UP_PREFIXES = ("是", "对", "有", "还在", "仍然", "依然", "没有", "没了", "好多了", "更严重")
NEGATIVE_FOLLOW_UP_PREFIXES = ("没有", "没了", "不是", "不再", "已经好了", "好多了")


@dataclass
class AgentRuntimeServices:
    db: Session
    settings: Settings
    user: UserAccount
    session: ChatSession
    ai: AiClient
    model_registry: AgentModelRegistry
    memory: RedisShortTermMemoryStore
    private_memory: "AgentPrivateMemory"
    knowledge: KnowledgeService
    allow_db_history: bool = True


class AgentPrivateMemory:
    """Per-agent memory facade backed by isolated Redis keys."""

    def __init__(self, settings: Settings):
        from app.services.memory import RedisShortTermMemoryStore

        self.store = RedisShortTermMemoryStore(settings)

    def load(self, agent_name: str, session_public_id: str) -> list[AiMessage]:
        return self.store.load_recent(self._key(agent_name, session_public_id))

    def append(self, agent_name: str, session_public_id: str, content: str) -> None:
        self.store.append(self._key(agent_name, session_public_id), "system", content)

    def _key(self, agent_name: str, session_public_id: str) -> str:
        return f"agent:{agent_name}:{session_public_id}"


class BaseAutonomousAgent:
    profile: AgentProfile

    def __init__(self, services: AgentRuntimeServices):
        self.services = services

    @property
    def name(self) -> str:
        return self.profile.name

    def client(self) -> AiClient:
        return self.services.model_registry.client_for(self.name)

    def private_memory(self) -> list[AiMessage]:
        return self.services.private_memory.load(self.name, self.services.session.public_id)

    def remember(self, content: str) -> None:
        self.services.private_memory.append(self.name, self.services.session.public_id, content)

    def _artifact(
        self,
        kind: str,
        payload: dict[str, Any],
        task: AgentTask,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> AgentArtifact:
        return AgentArtifact(
            id=f"{self.name}:{kind}:{uuid.uuid4().hex[:10]}",
            owner=self.name,
            kind=kind,
            payload=payload,
            confidence=confidence,
            task_id=task.id,
            metadata=metadata or {},
        )


class UnderstandingAgent(BaseAutonomousAgent):
    profile = AgentProfile(
        name="UnderstandingAgent",
        capabilities=frozenset({AgentCapability.UNDERSTANDING}),
        system_prompt=(
            "你是 UnderstandingAgent。你只负责理解用户当前请求，输出意图、主题、置信度和理由，"
            "不生成最终回复，不做风险处置。"
        ),
        memory_policy="private_intent_history",
        model_profile="understanding",
        tool_permissions=frozenset({"llm.intent"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        if board.latest_artifact("intent"):
            return AgentDecision(False, reason="intent artifact already exists")
        if self._is_directed(task, board):
            return AgentDecision(True, 0.82, "open user-turn task needs understanding")
        return AgentDecision(False, reason="task does not need understanding")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        intent = self._classify(board.model_input or board.user_input, board)
        confidence = 0.92 if intent == IntentType.RISK else 0.78
        payload = {
            "intent": intent.value,
            "topic": self._topic(board.model_input or board.user_input),
            "reason": "high risk hard signal" if intent == IntentType.RISK else "autonomous intent proposal",
            "privateMemoryKey": self.services.private_memory._key(self.name, self.services.session.public_id),
        }
        self.remember(f"intent={intent.value}; topic={payload['topic']}")
        return AgentTurnResult(
            artifacts=(self._artifact("intent", payload, task, confidence),),
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="*",
                    task_id=task.id,
                    kind="PROPOSAL",
                    content=f"我判断本轮意图是 {intent.value}",
                ),
            ),
        )

    def _is_directed(self, task: AgentTask, board: CollaborationBlackboard) -> bool:
        if AgentCapability.UNDERSTANDING.value in task.required_capabilities:
            return True
        return bool(board.user_input and task.metadata.get("kind") in {"root", "understanding"})

    def _classify(self, text: str, board: CollaborationBlackboard) -> IntentType:
        lowered = text.lower()
        risk_context = classify_risk_context(lowered)
        if is_explicit_high_risk(lowered):
            return IntentType.RISK
        if not has_consult_signal(lowered) and any(word in lowered for word in GENERAL_TASK_WORDS):
            return IntentType.CHAT
        try:
            memory_context = "\n".join(item.content for item in self.private_memory()[-6:])
            recent_history = follow_up_history_for_prompt(
                text,
                self.services.memory.load_recent(self.services.session.public_id),
            )
            messages = [
                *PromptTemplates.intent_prompt(recent_history, text, risk_context),
                AiMessage(role="system", content=f"{self.profile.system_prompt}\n私有记忆：\n{memory_context or '无'}"),
            ]
            label = self.client().complete(messages).upper()
            if "RISK" in label:
                return IntentType.RISK
            if "CONSULT" in label:
                return IntentType.CONSULT
            if "CHAT" in label:
                return IntentType.CHAT
        except Exception:
            pass
        return IntentType.CONSULT if has_consult_signal(lowered) else IntentType.CHAT

    def _topic(self, text: str) -> str:
        lowered = text.lower()
        if is_explicit_high_risk(lowered):
            return "safety"
        if has_consult_signal(lowered):
            return "mental_health_support"
        if any(word in lowered for word in GENERAL_TASK_WORDS):
            return "general_task"
        return "conversation"


class SafetyAgent(BaseAutonomousAgent):
    profile = AgentProfile(
        name="SafetyAgent",
        capabilities=frozenset({AgentCapability.SAFETY}),
        system_prompt=(
            "你是 SafetyAgent。你独立评估风险，并审查候选回复是否安全。"
            "你可以发布 SAFETY_OVERRIDE；你不生成最终回复。"
        ),
        memory_policy="private_safety_ledger",
        model_profile="safety",
        tool_permissions=frozenset({"llm.risk", "rules.high_risk", "response.review"}),
    )

    @staticmethod
    def review_generated_text(text: str) -> tuple[bool, str]:
        """Fail closed on high-risk drafts before any text reaches the client."""
        reply = text.strip()
        if not reply:
            return False, "empty response"
        if any(label in reply for label in ("候选回复", "风险等级", "报告ID", "emotionScore")):
            return False, "internal label leaked"
        if not any(term in reply for term in ("安全", "危险", "伤害", "紧急")):
            return False, "missing immediate safety check"
        if not any(term in reply for term in ("联系", "告诉", "求助", "拨打", "身边")):
            return False, "missing human support step"
        if not any(term in reply for term in ("心理中心", "可信任", "信任", "急救", "紧急", "医院", "专业")):
            return False, "missing escalation option"
        return True, "minimum high-risk guidance present"

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        latest_response = board.latest_artifact("response_proposal")
        latest_review = board.latest_artifact("safety_review")
        if latest_response and (latest_review is None or latest_review.metadata.get("responseArtifactId") != latest_response.id):
            return AgentDecision(True, 0.95, "candidate response needs safety critique")
        if not board.latest_artifact("risk") and board.user_input:
            confidence = 0.98 if is_explicit_high_risk(board.user_input) else 0.84
            return AgentDecision(True, confidence, "user input needs independent risk assessment")
        if AgentCapability.SAFETY.value in task.required_capabilities:
            return AgentDecision(True, 0.8, "task explicitly asks for safety")
        return AgentDecision(False, reason="no safety work needed")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        response = board.latest_artifact("response_proposal")
        review = board.latest_artifact("safety_review")
        if response and (review is None or review.metadata.get("responseArtifactId") != response.id):
            return self._review_response(task, board, response)
        return self._assess_risk(task, board)

    def _assess_risk(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        current_input = board.model_input or board.user_input
        follow_up_history = follow_up_history_for_prompt(
            current_input,
            self.services.memory.load_recent(self.services.session.public_id),
        )
        assessment_input = risk_input_for_follow_up(current_input, follow_up_history)
        assessment_history = follow_up_history or _context_history(board)
        assessment = PsychologicalAssessmentService(self.client()).assess(assessment_input, assessment_history)
        payload = {
            "risk": assessment.risk.value,
            "emotion": assessment.emotion.value,
            "emotionScore": assessment.emotion_score,
            "confidence": assessment.confidence,
            "summary": assessment.summary,
            "assessment": assessment,
            "riskSignalKind": assessment.risk_signal_kind.value,
            "riskSignalReason": assessment.risk_signal_reason,
            "requiresSafetyConfirmation": assessment.requires_safety_confirmation,
            "privateMemoryKey": self.services.private_memory._key(self.name, self.services.session.public_id),
        }
        events: tuple[AgentEvent, ...] = ()
        if assessment.risk == RiskLevel.HIGH:
            events = (
                AgentEvent(
                    type=AgentEventType.SAFETY_OVERRIDE,
                    actor=self.name,
                    task_id=task.id,
                    message="RiskGuardian hard/LLM assessment raised this turn to HIGH",
                    metadata={
                        "risk": RiskLevel.HIGH.value,
                        "source": "hard_rule" if assessment.risk_signal_kind.value == "EXPLICIT_HIGH" else "model_assessment",
                        "riskSignalKind": assessment.risk_signal_kind.value,
                    },
                ),
            )
        self.remember(f"risk={assessment.risk.value}; summary={assessment.summary}")
        return AgentTurnResult(
            artifacts=(self._artifact("risk", payload, task, assessment.confidence),),
            events=events,
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="CoordinatorAgent",
                    task_id=task.id,
                    kind="SAFETY_ASSESSMENT",
                    content=f"risk={assessment.risk.value}",
                ),
            ),
        )

    def _review_response(self, task: AgentTask, board: CollaborationBlackboard, response: AgentArtifact) -> AgentTurnResult:
        risk = _risk_level(board)
        messages = response.payload.get("messages", [])
        combined = "\n".join(getattr(message, "content", str(message)) for message in messages)
        approved = True
        reason = "response proposal satisfies current safety constraints"
        if risk == RiskLevel.HIGH and not any(word in combined for word in ["高风险处理规则", "当前安全", "可信任的人", "紧急"]):
            approved = False
            reason = "high-risk response proposal lacks immediate safety guidance"
        payload = {
            "approved": approved,
            "reason": reason,
            "responseArtifactId": response.id,
            "risk": risk.value,
            "privateMemoryKey": self.services.private_memory._key(self.name, self.services.session.public_id),
        }
        kind = "safety_review" if approved else "critique"
        events = ()
        follow_up_tasks = ()
        if not approved:
            events = (
                AgentEvent(
                    type=AgentEventType.REVISION_REQUESTED,
                    actor=self.name,
                    task_id=task.id,
                    artifact_id=response.id,
                    message=reason,
                ),
            )
            follow_up_tasks = (
                AgentTask(
                    id=f"task:revise-response:{uuid.uuid4().hex[:8]}",
                    title="Revise unsafe response proposal",
                    description=reason,
                    priority=TaskPriority.CRITICAL,
                    required_capabilities=frozenset({AgentCapability.RESPONSE.value}),
                    created_by=self.name,
                    metadata={"kind": "response", "revisionOf": response.id},
                ),
            )
        self.remember(f"review approved={approved}; reason={reason}")
        return AgentTurnResult(
            artifacts=(self._artifact(kind, payload, task, 0.95, {"responseArtifactId": response.id}),),
            tasks=follow_up_tasks,
            events=events,
        )


class ContextAgent(BaseAutonomousAgent):
    profile = AgentProfile(
        name="ContextAgent",
        capabilities=frozenset({AgentCapability.CONTEXT}),
        system_prompt=(
            "你是 ContextAgent。你只负责为本轮协作提供上下文，包括私有记忆、会话摘要、RAG 证据和 skill 约束。"
            "你不判断最终答案是否可采纳。"
        ),
        memory_policy="private_context_memory",
        model_profile="context",
        tool_permissions=frozenset({"redis.memory", "mysql.messages", "rag.retrieve", "skills.read"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        if board.latest_artifact("context"):
            return AgentDecision(False, reason="context artifact already exists")
        risk = _risk_level(board)
        intent = _intent(board)
        if AgentCapability.CONTEXT.value in task.required_capabilities:
            return AgentDecision(True, 0.86, "task explicitly asks for context")
        if risk in {RiskLevel.MEDIUM, RiskLevel.HIGH} or intent in {IntentType.CONSULT, IntentType.RISK}:
            return AgentDecision(True, 0.82, "support path needs memory, RAG, and skill context")
        return AgentDecision(False, reason="context not necessary for current artifacts")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        from app.services.memory import compact_history_for_prompt, detect_historical_concerns
        from app.services.skills import EchoMindSkillLibrary

        history = self._load_history()
        compacted_history, deterministic_brief = compact_history_for_prompt(history, self.services.settings, board.model_input)
        memory_brief = self._summarize_memory(history, board.model_input, deterministic_brief)
        model_history = self._bounded_model_history([*compacted_history, AiMessage(role="user", content=board.model_input)])
        historical_concerns = detect_historical_concerns(
            history,
            board.model_input,
            getattr(self.services.settings, "memory_stale_concern_days", 7),
        )
        intent = _intent(board)
        risk = _risk_level(board)

        retrieved: list["SearchResult"] = []
        query = ""
        skill_context = ""
        if intent != IntentType.CHAT or risk != RiskLevel.LOW:
            query = self._rewrite_query(memory_brief, board.model_input)
            retrieved = self.services.knowledge.retrieve(query, self.services.settings.knowledge_top_k)
            skill_context = EchoMindSkillLibrary.response_skill_context(intent, risk, board.user_input)
        payload = {
            "memoryBrief": memory_brief,
            "modelHistory": model_history,
            "historicalConcerns": historical_concerns,
            "knowledgeQuery": query,
            "retrievedKnowledge": retrieved,
            "skillContext": skill_context,
            "privateMemoryKey": self.services.private_memory._key(self.name, self.services.session.public_id),
        }
        self.remember(f"context intent={intent.value}; risk={risk.value}; retrieved={len(retrieved)}")
        return AgentTurnResult(
            artifacts=(self._artifact("context", payload, task, 0.88),),
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="ResponseAgent",
                    task_id=task.id,
                    kind="CONTEXT_READY",
                    content=f"context ready; retrieved={len(retrieved)}",
                ),
            ),
        )

    def _load_history(self) -> list[AiMessage]:
        from app.models.entities import ChatMessage

        history = self.services.memory.load_recent(self.services.session.public_id)
        if history:
            return history
        if not self.services.allow_db_history:
            return []
        rows = (
            self.services.db.query(ChatMessage)
            .filter(ChatMessage.session_id == self.services.session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(self.services.settings.redis_memory_max_messages)
            .all()
        )
        rows.reverse()
        history = self.services.memory.messages_from_rows(rows)
        if history:
            self.services.memory.replace(self.services.session.public_id, history)
        return history

    def _rewrite_query(self, memory_brief: str, model_input: str) -> str:
        try:
            query = self.client().complete([
                AiMessage(
                    role="system",
                    content=(
                        f"{self.profile.system_prompt}\n"
                        "把学生输入改写成适合检索校园心理知识库的中文查询词，只输出查询词。"
                        "以当前输入为主；未经本轮确认的旧问题不能作为本轮查询主题。"
                    ),
                ),
                AiMessage(role="user", content=f"记忆摘要：\n{memory_brief}\n\n当前输入：\n{model_input}"),
            ]).strip()
            return (query or model_input)[:60]
        except Exception:
            return model_input[:60]

    def _summarize_memory(self, history: list[AiMessage], current_input: str, fallback: str) -> str:
        max_chars = max(120, self.services.settings.memory_summary_max_chars)
        if not history:
            return "无相关历史记忆。"
        try:
            summary = self.client().complete([
                AiMessage(
                    role="system",
                    content=(
                        f"{self.profile.system_prompt}\n只输出 1-3 条中文记忆要点，不输出风险等级或诊断。"
                        "保留相对时间；没有本轮确认时，不得声称旧问题仍在持续。"
                    ),
                ),
                AiMessage(role="user", content=f"当前输入：\n{current_input}\n\n带时间的历史摘要：\n{fallback}"),
            ]).strip()
            return summary[:max_chars] or fallback
        except Exception:
            return fallback or "无相关历史记忆。"

    def _bounded_model_history(self, history: list[AiMessage]) -> list[AiMessage]:
        limit = max(2, self.services.settings.chat_history_limit * 2)
        if len(history) <= limit:
            return history
        if history[0].role == "system":
            return [history[0], *history[-(limit - 1):]]
        return history[-limit:]


class ResponseAgent(BaseAutonomousAgent):
    profile = AgentProfile(
        name="ResponseAgent",
        capabilities=frozenset({AgentCapability.RESPONSE}),
        system_prompt=(
            "你负责生成学生将直接看到的最终回复。只输出面向学生的自然对话内容，"
            "不要加标题、标签、元说明，也不要描述任何内部角色、黑板、审查或采纳流程。"
        ),
        memory_policy="private_response_strategy",
        model_profile="response",
        tool_permissions=frozenset({"llm.response_plan"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        if board.latest_artifact("response_proposal") and "revisionOf" not in task.metadata:
            return AgentDecision(False, reason="response proposal already exists")
        if not board.latest_artifact("intent") or not board.latest_artifact("risk"):
            return AgentDecision(False, reason="response needs intent and risk artifacts")
        intent = _intent(board)
        risk = _risk_level(board)
        if intent == IntentType.CHAT and risk == RiskLevel.LOW:
            return AgentDecision(True, 0.78, "normal chat response can be proposed")
        if board.latest_artifact("context") or risk == RiskLevel.HIGH:
            return AgentDecision(True, 0.84, "support response has enough artifacts")
        if AgentCapability.RESPONSE.value in task.required_capabilities:
            return AgentDecision(True, 0.65, "explicit response task")
        return AgentDecision(False, reason="waiting for context")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        intent = _intent(board)
        risk = _risk_level(board)
        context = board.latest_artifact("context")
        context_payload = context.payload if context else {}
        risk_artifact = board.latest_artifact("risk")
        risk_payload = risk_artifact.payload if risk_artifact else {}
        confirmation_instruction = ""
        if risk_payload.get("requiresSafetyConfirmation") is True:
            confirmation_instruction = (
                "\n当前风险语义尚不明确。必须询问学生当前是否有伤害自己的想法、计划、工具或失控风险；"
                "每次只问一个简洁、直接的问题，不得把尚未确认的内容说成事实。"
            )
        model_history = context_payload.get("modelHistory") or [AiMessage(role="user", content=board.model_input)]
        memory_brief = context_payload.get("memoryBrief") or "无相关历史记忆。"
        historical_concerns = context_payload.get("historicalConcerns") or []
        knowledge = context_payload.get("retrievedKnowledge") or []
        skill_context = context_payload.get("skillContext") or ""
        knowledge_context = "\n\n".join(f"- [{item.source}] {item.content}" for item in knowledge)
        if intent == IntentType.CHAT and risk == RiskLevel.LOW:
            messages = [
                PromptTemplates.answer_system_prompt(IntentType.CHAT, RiskLevel.LOW, "", self.services.user.display_name),
                AiMessage(
                    role="system",
                    content=(
                        f"{self.profile.system_prompt}\n"
                        f"对话场景：普通聊天。请直接回应学生当前输入。\n"
                        f"私有记忆：\n{_format_private_memory(self.private_memory())}\n"
                        f"记忆摘要：\n{memory_brief}"
                        f"{confirmation_instruction}"
                    ),
                ),
                *model_history,
            ]
            mode = "normal_chat"
        else:
            messages = [
                PromptTemplates.answer_system_prompt(
                    intent if intent != IntentType.CHAT else IntentType.CONSULT,
                    risk,
                    knowledge_context,
                    self.services.user.display_name,
                    skill_context,
                ),
                AiMessage(
                    role="system",
                    content=(
                        f"{self.profile.system_prompt}\n"
                        f"对话场景：心理支持。请直接回应学生当前输入，语气温和、具体、有边界。\n"
                        f"私有记忆：\n{_format_private_memory(self.private_memory())}\n"
                        f"记忆摘要：\n{memory_brief}\n"
                        f"{historical_follow_up_instruction(historical_concerns)}"
                        f"{confirmation_instruction}"
                    ),
                ),
                *model_history,
            ]
            mode = "support"
        payload = {
            "messages": messages,
            "mode": mode,
            "intent": intent.value,
            "risk": risk.value,
            "responseAgent": self.name,
            "privateMemoryKey": self.services.private_memory._key(self.name, self.services.session.public_id),
        }
        self.remember(f"response mode={mode}; intent={intent.value}; risk={risk.value}")
        return AgentTurnResult(
            artifacts=(self._artifact("response_proposal", payload, task, 0.86),),
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="SafetyAgent",
                    task_id=task.id,
                    kind="REVIEW_REQUEST",
                    content="请审查候选回复方案。",
                ),
            ),
        )


class CoordinatorAgent(BaseAutonomousAgent):
    profile = AgentProfile(
        name="CoordinatorAgent",
        capabilities=frozenset({AgentCapability.COORDINATION}),
        system_prompt=(
            "你是 CoordinatorAgent。你不规定固定 Agent 顺序；你只维护任务板、预算、安全门槛、冲突仲裁和最终采纳。"
        ),
        memory_policy="private_coordination_trace",
        model_profile="coordinator",
        tool_permissions=frozenset({"taskboard.write", "blackboard.accept"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        return AgentDecision(False, reason="CoordinatorAgent is driven by the event loop, not by fixed workflow slots")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        return AgentTurnResult(close_task=False)

    def root_task(self, board: CollaborationBlackboard) -> AgentTask:
        return AgentTask(
            id="task:root",
            title="Resolve user turn",
            description=board.user_input,
            priority=TaskPriority.CRITICAL if is_explicit_high_risk(board.user_input) else TaskPriority.NORMAL,
            created_by=self.name,
            metadata={"kind": "root"},
        )

    def remember_acceptance(self, artifact_id: str, reason: str) -> None:
        self.remember(f"accepted={artifact_id}; reason={reason}")


def _intent(board: CollaborationBlackboard) -> IntentType:
    artifact = board.latest_artifact("intent")
    if artifact:
        try:
            return IntentType(str(artifact.payload.get("intent", IntentType.CHAT.value)).upper())
        except ValueError:
            return IntentType.CHAT
    if is_explicit_high_risk(board.user_input):
        return IntentType.RISK
    if has_consult_signal(board.user_input):
        return IntentType.CONSULT
    return IntentType.CHAT


def _risk_level(board: CollaborationBlackboard) -> RiskLevel:
    highest = RiskLevel.LOW
    order = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}
    for artifact in board.artifacts_by_kind("risk"):
        try:
            risk = RiskLevel(str(artifact.payload.get("risk", RiskLevel.LOW.value)).upper())
        except ValueError:
            risk = RiskLevel.LOW
        if order[risk] > order[highest]:
            highest = risk
    if any(event.type == AgentEventType.SAFETY_OVERRIDE for event in board.events):
        return RiskLevel.HIGH
    return highest


def _context_history(board: CollaborationBlackboard) -> list[AiMessage]:
    context = board.latest_artifact("context")
    if not context:
        return [AiMessage(role="user", content=board.model_input or board.user_input)]
    return context.payload.get("modelHistory") or [AiMessage(role="user", content=board.model_input or board.user_input)]


def _format_private_memory(items: list[AiMessage]) -> str:
    if not items:
        return "无"
    return "\n".join(f"- {item.content}" for item in items[-5:])


def historical_follow_up_instruction(concerns: list[dict[str, Any]]) -> str:
    pending = [item for item in concerns if item.get("needsConfirmation") is True]
    if not pending:
        return "历史问题核验：无待确认的旧问题。"
    concern = pending[0]
    instruction = (
        "历史问题核验：以下内容是旧记录，不得把旧问题表述为当前事实，也不得据此输出诊断。"
        f"旧记录（{concern.get('relativeTime', '时间未知')}）：{concern.get('summary', '')}。"
        "必须先回答学生本轮问题，再用一句自然的问题询问该问题是否仍在持续；每轮最多确认一个旧问题。"
    )
    if concern.get("requiresSafetyCheck") is True:
        instruction += "需要确认学生当前是否安全，但不能仅凭旧记录触发本轮预警。"
    return instruction


def follow_up_history_for_prompt(text: str, history: list[AiMessage]) -> list[AiMessage]:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized or len(normalized) > 30:
        return []
    if not normalized.startswith(FOLLOW_UP_PREFIXES):
        return []
    return history[-4:]


def risk_input_for_follow_up(text: str, history: list[AiMessage]) -> str:
    normalized = " ".join((text or "").strip().lower().split())
    if not history or normalized.startswith(NEGATIVE_FOLLOW_UP_PREFIXES):
        return text
    if not normalized.startswith(("是", "对", "有", "还在", "仍然", "依然", "更严重")):
        return text
    high_risk_context = next(
        (item.content for item in reversed(history) if has_risk_mention(item.content)),
        "",
    )
    if not high_risk_context:
        return text
    return f"上一轮安全确认：{high_risk_context}\n学生当前确认：{text}"
