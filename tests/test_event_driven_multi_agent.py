import unittest
import json
from types import SimpleNamespace

from app.agents.autonomous import ResponseAgent, SafetyAgent
from app.agents.coordinator import EventDrivenCoordinator
from app.agents.events import (
    AgentArtifact,
    AgentEvent,
    AgentEventType,
    AgentTask,
    AgentTurnResult,
    CollaborationBlackboard,
    TaskPriority,
    TaskStatus,
)
from app.agents.registry import AgentCapability, AgentDecision, AgentProfile, AgentRegistry
from app.services.agent_models import AgentModelRegistry
from app.schemas.dtos import AiMessage


class StaticRiskAi:
    def complete(self, messages):
        return json.dumps({
            "emotion": "NORMAL",
            "emotionScore": 0.0,
            "risk": "LOW",
            "confidence": 0.8,
            "summary": "未确认当前危险",
        }, ensure_ascii=False)


class FakeModelRegistry:
    def __init__(self):
        self.ai = StaticRiskAi()

    def client_for(self, name):
        return self.ai


class FakeMemory:
    def load_recent(self, session_id):
        return []


class FakePrivateMemory:
    def load(self, agent_name, session_id):
        return []

    def append(self, agent_name, session_id, content):
        return None

    def _key(self, agent_name, session_id):
        return f"agent:{agent_name}:{session_id}"


def runtime_services():
    return SimpleNamespace(
        db=None,
        settings=SimpleNamespace(),
        user=SimpleNamespace(display_name="Student"),
        session=SimpleNamespace(public_id="session-1"),
        ai=StaticRiskAi(),
        model_registry=FakeModelRegistry(),
        memory=FakeMemory(),
        private_memory=FakePrivateMemory(),
        knowledge=None,
    )


class EventDrivenProtocolTests(unittest.TestCase):
    def test_blackboard_is_append_only(self):
        board = CollaborationBlackboard(turn_id="t1")
        task = AgentTask(id="task-1", title="Resolve")
        event = AgentEvent(type=AgentEventType.TASK_CREATED, actor="CoordinatorAgent", task_id=task.id)

        updated = board.add_task(task).append_event(event)

        self.assertEqual(board.tasks, {})
        self.assertEqual(updated.tasks["task-1"].status, TaskStatus.OPEN)
        self.assertEqual(updated.events[0].actor, "CoordinatorAgent")

    def test_artifacts_do_not_overwrite_each_other(self):
        first = AgentArtifact(id="a1", owner="UnderstandingAgent", kind="intent", payload={"intent": "CHAT"})
        second = AgentArtifact(id="a2", owner="SafetyAgent", kind="risk", payload={"risk": "HIGH"})
        board = CollaborationBlackboard(turn_id="t1").add_artifact(first).add_artifact(second)

        self.assertEqual(len(board.artifacts), 2)
        self.assertEqual(board.artifacts_by_kind("risk")[0].payload["risk"], "HIGH")


class DemoAgent:
    def __init__(self, name, capability, confidence):
        self.profile = AgentProfile(
            name=name,
            capabilities=frozenset({capability}),
            system_prompt=f"{name} prompt",
            memory_policy="private",
            model_profile=name,
        )
        self.confidence = confidence

    def decide(self, task, board):
        return AgentDecision(True, self.confidence, f"{self.profile.name} claims")

    def act(self, task, board):
        return AgentTurnResult(
            artifacts=(
                AgentArtifact(
                    id=f"{self.profile.name}:artifact",
                    owner=self.profile.name,
                    kind="demo",
                    payload={"agent": self.profile.name},
                    task_id=task.id,
                ),
            )
        )


class RegistryAndCoordinatorTests(unittest.TestCase):
    def test_registry_sorts_by_claim_confidence_not_list_order(self):
        low = DemoAgent("LowConfidenceAgent", AgentCapability.UNDERSTANDING, 0.2)
        high = DemoAgent("HighConfidenceAgent", AgentCapability.UNDERSTANDING, 0.9)
        registry = AgentRegistry([low, high])
        task = AgentTask(id="task-1", title="Understand", required_capabilities=frozenset({AgentCapability.UNDERSTANDING.value}))

        candidates = registry.candidates_for(task, CollaborationBlackboard(turn_id="t1"))

        self.assertEqual([agent.profile.name for agent in candidates], ["HighConfidenceAgent", "LowConfidenceAgent"])

    def test_coordinator_uses_claims_for_open_tasks(self):
        settings = SimpleNamespace(
            agent_max_rounds=1,
            agent_max_claims_per_round=2,
            agent_max_claims_per_agent=1,
            agent_final_acceptance_min_confidence=0.6,
        )
        coordinator_agent = SimpleNamespace(
            name="CoordinatorAgent",
            root_task=lambda board: AgentTask(
                id="task:root",
                title="Resolve user turn",
                description=board.user_input,
                priority=TaskPriority.NORMAL,
                metadata={"kind": "root"},
            ),
            remember_acceptance=lambda artifact_id, reason: None,
        )
        registry = AgentRegistry([
            DemoAgent("AgentA", AgentCapability.UNDERSTANDING, 0.8),
            DemoAgent("AgentB", AgentCapability.SAFETY, 0.7),
        ])
        board = CollaborationBlackboard(turn_id="t1", user_input="hello", model_input="hello")

        result = EventDrivenCoordinator(registry, coordinator_agent, settings).run(board)

        claimed = [event.actor for event in result.events if event.type == AgentEventType.TASK_CLAIMED]
        self.assertIn("AgentA", claimed)
        self.assertIn("AgentB", claimed)


class ContextualRiskRuntimeTests(unittest.TestCase):
    def test_clear_negation_does_not_emit_safety_override(self):
        agent = SafetyAgent(runtime_services())
        task = AgentTask(id="risk-1", title="Assess", metadata={"kind": "risk"})
        board = CollaborationBlackboard(
            turn_id="t1",
            user_input="我不想伤害自己",
            model_input="我不想伤害自己",
        )

        result = agent.act(task, board)

        self.assertFalse(any(event.type == AgentEventType.SAFETY_OVERRIDE for event in result.events))
        self.assertEqual(result.artifacts[0].payload["riskSignalKind"], "NEGATED_CURRENT")

    def test_explicit_danger_still_emits_safety_override(self):
        agent = SafetyAgent(runtime_services())
        task = AgentTask(id="risk-2", title="Assess", metadata={"kind": "risk"})
        board = CollaborationBlackboard(
            turn_id="t2",
            user_input="我今晚准备自杀",
            model_input="我今晚准备自杀",
        )

        result = agent.act(task, board)

        override = next(event for event in result.events if event.type == AgentEventType.SAFETY_OVERRIDE)
        self.assertEqual(override.metadata["source"], "hard_rule")
        self.assertEqual(result.artifacts[0].payload["risk"], "HIGH")

    def test_ambiguous_risk_requires_response_confirmation(self):
        services = runtime_services()
        safety_agent = SafetyAgent(services)
        risk_task = AgentTask(id="risk-3", title="Assess", metadata={"kind": "risk"})
        board = CollaborationBlackboard(
            turn_id="t3",
            user_input="可能会伤害自己",
            model_input="可能会伤害自己",
        )
        risk_result = safety_agent.act(risk_task, board)
        board = board.add_artifact(AgentArtifact(
            id="intent-1",
            owner="UnderstandingAgent",
            kind="intent",
            payload={"intent": "RISK"},
        )).add_artifact(risk_result.artifacts[0])
        response_task = AgentTask(id="response-1", title="Respond", metadata={"kind": "response"})

        response_result = ResponseAgent(services).act(response_task, board)

        messages = response_result.artifacts[0].payload["messages"]
        system_text = "\n".join(message.content for message in messages if isinstance(message, AiMessage) and message.role == "system")
        self.assertIn("必须询问学生当前是否有伤害自己的想法、计划、工具或失控风险", system_text)


class AgentModelRegistryTests(unittest.TestCase):
    def test_agent_can_override_model_without_changing_global_default(self):
        settings = SimpleNamespace(
            ai_provider="ollama",
            ollama_model="default-model",
            openai_model="default-openai",
            qwen_model="default-qwen",
            ai_temperature=0.35,
            ai_max_tokens=512,
            agent_model_understanding_model="small-understanding-model",
            agent_model_understanding_provider="ollama",
            agent_model_safety_model="safety-model",
            agent_model_safety_provider="ollama",
        )

        registry = AgentModelRegistry(settings)

        self.assertEqual(registry.profile_for("UnderstandingAgent").model, "small-understanding-model")
        self.assertEqual(registry.profile_for("ResponseAgent").model, "default-model")

    def test_qwen_provider_uses_qwen_model_by_default(self):
        settings = SimpleNamespace(
            ai_provider="qwen",
            ollama_model="default-ollama",
            openai_model="default-openai",
            qwen_model="qwen-plus",
            ai_temperature=0.35,
            ai_max_tokens=512,
        )

        registry = AgentModelRegistry(settings)

        profile = registry.profile_for("ResponseAgent")
        self.assertEqual(profile.provider, "qwen")
        self.assertEqual(profile.model, "qwen-plus")


if __name__ == "__main__":
    unittest.main()
