# EchoMind 架构与 Agent 面试学习指南

这份文档面向两个目标：

1. 帮你快速讲清楚 EchoMind 这个项目的架构、功能和技术细节。
2. 把项目里的 Agent 设计转化成实习面试中可以展开回答的知识点。

项目可以概括为：**EchoMind 是一个基于 FastAPI 的校园心理支持智能体系统，前端用原生 HTML/CSS/JavaScript，后端用事件驱动多 Agent runtime 组织对话理解、风险评估、RAG 上下文检索、候选提示词和安全审查，并把留存模式的咨询/风险场景沉淀到 MySQL、Redis、Excel 台账、风险个案和预警链路中。**

当前实现补充：高风险模型最终回答不会逐 token 暴露，需完整生成并经规则检查与独立模型复审后输出；正式聊天的报告、trace、工具任务与用户消息同事务提交。学生自愿留号的联系请求另有待处理、认领、结案状态和身份查看审计。Mock 模式不执行独立模型复审，且本系统不承诺临床级安全或实时危机救援。

---

## 1. 面试开场版项目介绍

你可以这样介绍这个项目：

> 我做的是一个校园心理支持场景的 Agent 应用。系统后端基于 FastAPI 和 Uvicorn，对学生端提供 SSE 流式聊天，对管理员端提供报告、个案、知识库和预警记录查看。核心不是单次 LLM 调用，而是一个事件驱动的多 Agent 协作 runtime：Coordinator 维护任务板和黑板，UnderstandingAgent 负责意图识别，SafetyAgent 负责风险评估和回复安全审查，ContextAgent 负责短期记忆、历史摘要、RAG 检索和 skill 约束聚合，ResponseAgent 生成候选回复。最终回复必须经过 SafetyAgent 审查，再由 Coordinator 采纳后才进入流式输出。

再补一句业务闭环：

> 对于普通聊天，系统不会强行走心理知识库；只有咨询和风险场景才触发 RAG 和报告沉淀。中高风险场景会异步写入 Excel 台账、创建风险个案，高风险还会记录或发送预警。这样既保证学生端响应不被工具调用阻塞，又保留后台可追踪、可审计的风险处理链路。

---

## 2. 技术栈总览

后端：

- Python 3.12
- FastAPI
- Uvicorn / ASGI
- SQLAlchemy ORM
- PyMySQL
- Redis
- pydantic-settings
- httpx
- openpyxl
- pypdf
- mcp

AI 和 Agent：

- Ollama 本地模型
- Ollama 本地 GGUF 模型
- OpenAI-compatible Chat Completions
- Qwen / DashScope OpenAI-compatible API
- Mock Provider，用于无模型、本地演示和测试
- 事件驱动多 Agent runtime
- RAG：Chroma 向量库 + OpenAI embedding + BM25 + 本地 rerank
- 标准 Skills：心理支持、安全计划、转介建议、睡眠、焦虑、学业压力、辅导员交接摘要

前端：

- 原生 HTML / CSS / JavaScript
- `fetch` 调 API
- `ReadableStream` 解析 SSE token
- `sessionStorage` 保存 Basic Auth token

存储和运维：

- MySQL：用户、会话、消息、知识库分块、心理报告、风险个案、工具任务、死信、trace
- Redis：短期对话记忆和每个 Agent 的私有记忆
- Excel：风险台账
- SMTP / log 模式：高风险预警
- Docker Compose：MySQL、Redis、app 服务

---

## 3. 目录结构怎么读

核心目录：

```text
app/
├── main.py                  # FastAPI app 创建、启动初始化、静态页面挂载
├── api/routes.py            # HTTP API：聊天、用户信息、管理端、知识库
├── core/                    # 配置、数据库、安全、枚举、启动 seed
├── agents/                  # 多 Agent runtime、黑板、任务、协作协议
├── services/                # 业务服务：AI、聊天、RAG、记忆、报告、工具队列
├── models/entities.py       # SQLAlchemy 数据模型
├── schemas/dtos.py          # Pydantic DTO
├── knowledge/               # 内置校园心理知识库 Markdown
├── mcp_tools/server.py      # MCP 工具服务
├── rag_eval/runner.py       # RAG 评测脚本
├── harness/runner.py        # 工程级 harness 验证脚本
└── static/                  # 登录页、学生端、管理端静态前端

skills/
├── supportive_response_baseline/
├── high_risk_safety_plan/
├── anxiety_grounding_support/
├── sleep_routine_support/
├── academic_stress_planning/
├── referral_resource_guidance/
└── counselor_handoff_summary/
```

建议读源码顺序：

1. `app/main.py`：知道服务如何启动。
2. `app/api/routes.py`：知道系统对外暴露哪些能力。
3. `app/services/chat.py`：知道聊天请求如何变成 SSE。
4. `app/agents/harness.py`：知道一轮对话的业务编排。
5. `app/agents/event_driven_runtime.py`、`app/agents/coordinator.py`、`app/agents/autonomous.py`：理解多 Agent 协作。
6. `app/services/knowledge.py`、`app/services/vector_store.py`：理解 RAG。
7. `app/services/tool_queue.py`、`app/services/tools.py`、`app/mcp_tools/server.py`：理解风险后处理工具链。
8. `tests/` 和 `app/harness/runner.py`：理解项目如何证明功能正确。

---

## 4. 启动和运行链路

FastAPI 入口在 `app/main.py`：

- `create_app()` 创建 FastAPI 实例。
- 注册 no-cache 中间件，避免前端静态资源缓存影响调试。
- startup 时执行：
  - `create_schema()`：根据 SQLAlchemy model 自动建表。
  - `seed_data(db)`：创建默认用户，并同步内置知识库。
  - `get_tool_queue_worker(settings).start()`：启动后台工具队列 worker。
- shutdown 时停止工具队列。
- `app.include_router(router)` 挂载 API。
- `app.mount("/", StaticFiles(..., html=True))` 挂载静态前端。

第一次本地运行推荐 mock 模式：

```powershell
docker compose --env-file .env.example up -d mysql redis

$env:AI_PROVIDER="mock"
$env:DATABASE_URL="mysql+pymysql://echomind:echomind@127.0.0.1:13306/echomind?charset=utf8mb4"
$env:REDIS_URL="redis://127.0.0.1:16379/0"
$env:KNOWLEDGE_VECTOR_ENABLED="false"

.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

为什么推荐 mock：

- 不需要 Ollama。
- 不需要 OpenAI API key。
- 不需要 embedding 服务。
- 可以先验证 FastAPI、MySQL、Redis、前端、Agent runtime、SSE、报告链路。

---

## 5. HTTP API 分层

API 入口在 `app/api/routes.py`。

公共和登录相关：

- `GET /actuator/health`：健康检查。
- `GET /api/profile`：读取当前 Basic Auth 用户信息。

学生端：

- `POST /api/chat/stream`：学生聊天，返回 `text/event-stream`。
- `GET /api/reports/me`：学生查看自己的报告记录。

Agent 状态：

- `GET /api/agent/status`：展示当前 provider、model、多 Agent runtime、模型资产、skills、协作机制。

管理员端：

- `GET /api/admin/reports`：心理报告。
- `GET /api/admin/excel-records`：Excel 台账写入记录。
- `GET /api/admin/alerts`：预警记录。
- `GET /api/admin/cases`：风险个案。
- `GET /api/admin/cases/{case_id}/notes`：个案备注。
- `GET /api/admin/tool-jobs`：工具队列任务。
- `GET /api/admin/dead-letters`：死信任务。
- `GET /api/admin/agent-traces`：Agent 运行 trace。
- `GET /api/admin/tool-audits`：工具审计记录。
- `GET /api/admin/conversations/{session_id}`：会话全文。

知识库管理：

- `POST /api/admin/knowledge`：写入文本知识。
- `POST /api/admin/knowledge/file`：上传 txt、Markdown、PDF 等文件入库。
- `GET /api/admin/knowledge/status`：知识库状态。
- `POST /api/admin/knowledge/rebuild-vector`：重建 Chroma 向量索引。
- `POST /api/admin/knowledge/backup`：备份 Chroma 快照。

认证方式：

- 项目用 Basic Auth。
- `app/core/security.py` 里用 SHA-256 hash 存密码。
- `current_user()` 负责解析 Authorization header。
- `require_admin()` 负责管理员角色校验。

面试注意：这不是生产级认证方案。可以说“演示项目用 Basic Auth 简化登录，真实生产会换成更安全的密码哈希算法、JWT/session、权限模型和 HTTPS 部署”。

---

## 6. 一轮聊天的完整调用链

一轮学生聊天大致是：

```text
浏览器 student.js
  -> POST /api/chat/stream
    -> ChatService.stream_chat()
      -> EchoMindAgentHarness.run()
        -> PrivacySanitizer.sanitize()
        -> resolve ChatSession
        -> create_agent_runtime(...).run()
          -> EventDrivenAgentRuntimeService.run()
            -> EventDrivenCoordinator.run()
              -> UnderstandingAgent / SafetyAgent / ContextAgent / ResponseAgent
            -> AgentRunResult
        -> 保存学生消息
        -> 咨询/风险场景创建 PsychologicalReport
        -> 保存 AgentRunTrace
      -> AiClient.stream(outcome.response_messages)
      -> 普通风险 SSE 流式返回；高风险完整生成并独立审核后才返回
      -> 保存助手消息
      -> 工具任务已在报告事务中入队，由 worker 异步执行
      -> SSE done
```

这里有一个重要设计：**Agent runtime 不是直接产出最终字符串，而是产出一组 `AiMessage`。这些 messages 再交给 `AiClient.stream()` 调用真实模型或 mock provider。**

这样做的好处：

- Agent 负责“规划 prompt 和安全约束”。
- AI provider 负责“生成自然语言回复”。
- 聊天服务负责“流式传输、持久化、后处理”。
- 测试时可以把 provider 换成 mock，不影响 Agent 逻辑验证。

---

## 7. 多 Agent runtime 设计

核心文件：

- `app/agents/events.py`
- `app/agents/registry.py`
- `app/agents/coordinator.py`
- `app/agents/autonomous.py`
- `app/agents/event_driven_runtime.py`
- `app/agents/result.py`

### 7.1 核心对象

`CollaborationBlackboard` 是共享黑板，包含：

- `turn_id`
- `user_id`
- `session_id`
- `user_input`
- `model_input`
- `tasks`
- `messages`
- `artifacts`
- `events`
- `final_artifact_id`

`AgentTask` 是任务板上的任务，包含：

- 任务 ID、标题、描述
- 优先级：`LOW / NORMAL / HIGH / CRITICAL`
- 状态：`OPEN / CLAIMED / BLOCKED / CLOSED`
- 所需能力：例如 `UNDERSTANDING`、`SAFETY`
- 创建者、认领者、依赖、metadata

`AgentArtifact` 是 Agent 产出的结构化结果，例如：

- `intent`
- `risk`
- `context`
- `response_proposal`
- `safety_review`
- `critique`

`AgentEvent` 是协作日志，例如：

- `TURN_STARTED`
- `TASK_CREATED`
- `TASK_CLAIMED`
- `ARTIFACT_PUBLISHED`
- `SAFETY_OVERRIDE`
- `REVISION_REQUESTED`
- `FINAL_ACCEPTED`
- `BUDGET_EXHAUSTED`

面试表达：

> 我把 Agent 协作抽象成任务、消息、artifact 和事件。Agent 不直接互相调用，而是在共享黑板上发布结构化 artifact，由 Coordinator 根据任务缺口和安全策略推动下一步。这比硬编码链式调用更容易扩展新 Agent，也更容易做 trace 和审计。

### 7.2 AgentRegistry

`AgentRegistry` 负责：

- 保存可用 Agent。
- 根据任务要求的 capability 筛选 Agent。
- 调用每个 Agent 的 `decide(task, board)`。
- 按 claim confidence 排序候选。

关键点：Agent 是否执行，不是固定顺序决定，而是由：

- 任务所需能力。
- 当前黑板状态。
- Agent 自己的 `decide()`。
- claim confidence。
- Coordinator 的预算限制。

### 7.3 Coordinator 的职责

`EventDrivenCoordinator` 做这些事：

- 创建 root task。
- 每轮开始写入 `ROUND_STARTED`。
- 根据缺失 artifact 派生任务：
  - 缺 intent -> understand task。
  - 缺 risk -> assess-safety task。
  - 咨询/风险需要 context -> gather-context task。
  - 有足够上下文 -> propose-response task。
  - 有候选回复 -> review-response task。
  - 安全审查不通过 -> revise-response task。
- 从 registry 获取可认领任务的 Agent。
- 控制预算：
  - 最大轮数 `agent_max_rounds`
  - 每轮最大认领数 `agent_max_claims_per_round`
  - 每个 Agent 最大认领数 `agent_max_claims_per_agent`
- 尝试 final acceptance：
  - 存在 response proposal。
  - 存在对应的 safety review。
  - review approved。
  - response confidence 达到阈值。

面试表达：

> Coordinator 在这个系统里不是“总控大模型”，而是确定性调度器。它根据黑板上缺什么 artifact 来创建任务，根据优先级和置信度调度 Agent，最后执行安全审查后的采纳策略。这样可以降低多 Agent 系统的不确定性，也方便测试。

### 7.4 各 Agent 分工

`UnderstandingAgent`：

- 能力：`UNDERSTANDING`
- 产物：`intent`
- 判断 `CHAT / CONSULT / RISK`
- 对普通编程、作业、校园事务等识别为 `CHAT`
- 对焦虑、压力、失眠等识别为 `CONSULT`
- 对自伤、自杀、即时危险等识别为 `RISK`

`SafetyAgent`：

- 能力：`SAFETY`
- 产物：`risk`、`safety_review` 或 `critique`
- 先用统一规则层识别明确危险，并区分否定、历史、引用、第三方和假设语境。
- 再用模型 JSON 评估风险。
- 模型失败时回退到启发式规则。
- 如果高风险，会发布 `SAFETY_OVERRIDE`。
- 候选回复生成后，它还负责审查回复是否包含必要安全指引。

`ContextAgent`：

- 能力：`CONTEXT`
- 产物：`context`
- 读取 Redis 短期记忆。
- Redis 无数据时从 MySQL 会话消息回填。
- 对历史进行压缩摘要。
- 对咨询/风险场景改写检索 query。
- 调用 `KnowledgeService.retrieve()` 获取 RAG 证据。
- 选择需要注入的标准 skill。

`ResponseAgent`：

- 能力：`RESPONSE`
- 产物：`response_proposal`
- 普通聊天走 normal chat prompt。
- 咨询/风险走 support prompt。
- 把 RAG 知识、skill 指引、记忆摘要、私有记忆合并成模型 messages。
- 回复 proposal 不是最终输出，必须交给 SafetyAgent 审查。

`CoordinatorAgent`：

- 能力：`COORDINATION`
- 创建 root task。
- 记录最终采纳。
- 具体调度逻辑在 `EventDrivenCoordinator`，不是让它作为一个 LLM agent 自由决策。

---

## 8. Agent 面试知识点：这个项目能讲什么

### 8.1 什么是 Agent

面试简答：

> Agent 可以理解为带有目标、上下文、工具、记忆和决策循环的 LLM 应用单元。它不只是一次 prompt completion，而是会观察状态、决定下一步动作、调用工具或其他模块、把结果写回状态，然后继续迭代直到任务完成。

在 EchoMind 中，一个 Agent 包含：

- `AgentProfile`：名字、能力、system prompt、记忆策略、模型 profile、工具权限。
- `decide(task, board)`：判断自己是否要认领当前任务。
- `act(task, board)`：执行任务并产出 message、artifact、后续 task 或 event。

### 8.2 单 Agent 和多 Agent 区别

单 Agent：

- 一个 agent 负责理解、检索、工具调用、生成回复。
- 简单，但职责容易混在一起。
- 风险场景下，自己生成自己审查，安全边界较弱。

多 Agent：

- 把职责拆成理解、风险、安全审查、上下文、回复。
- 每个 Agent 有自己的 prompt、模型 profile、记忆和权限。
- 可以互相制衡，例如 ResponseAgent 不能绕过 SafetyAgent。
- 更适合需要审计、协作和安全控制的场景。

EchoMind 的多 Agent 不是为了炫技，而是业务驱动：

- 心理支持场景安全要求高。
- 咨询和风险场景需要不同处理。
- 学生端不能暴露后台风险评分。
- 高风险需要工具链后处理。
- 管理端需要 trace 和报告。

### 8.3 黑板架构是什么

黑板架构可以这样讲：

> Blackboard architecture 是多 Agent 协作的一种模式。所有 Agent 共享一个状态空间，Agent 不直接互相强耦合调用，而是读取黑板上的任务、消息和 artifact，再把自己的结果追加回黑板。调度器根据黑板状态判断下一步缺什么。

EchoMind 的黑板是 `CollaborationBlackboard`：

- 任务板：`tasks`
- 消息总线：`messages`
- 结构化产物：`artifacts`
- 协作日志：`events`
- 最终采纳结果：`final_artifact_id`

优点：

- 解耦 Agent。
- 新增 Agent 成本低。
- 所有过程可追踪。
- 可以做审计和回放。
- 容易测试单个协议对象。

代价：

- 状态结构设计要清楚。
- artifact schema 要稳定。
- 调度器需要防止循环和预算失控。
- 多 Agent 结果冲突时需要明确仲裁策略。

### 8.4 ReAct、Plan-and-Execute、黑板模式怎么区分

ReAct：

- LLM 交替执行 Reasoning 和 Acting。
- 典型循环是 Thought -> Action -> Observation。
- 适合工具调用型任务。

Plan-and-Execute：

- 先规划任务步骤。
- 再执行每个步骤。
- 适合长任务或明确目标任务。

黑板模式：

- 多个 Agent 共享一个状态空间。
- Agent 根据状态自主认领任务。
- 适合多角色协作、审计和动态任务派生。

EchoMind 更接近：

```text
Blackboard + claim-based actor runtime + safety-gated response acceptance
```

它不是典型 ReAct，因为没有让一个模型连续思考并调用工具；也不是简单 Plan-and-Execute，因为任务不是一次性固定规划，而是根据 artifact 缺口动态派生。

### 8.5 为什么要有 SafetyAgent

项目场景是心理健康支持，高风险内容必须谨慎。

SafetyAgent 做两件事：

1. 输入风险评估：
   - 先硬规则命中高风险。
   - 再 LLM JSON 评估。
   - 最后启发式兜底。

2. 输出安全审查：
   - 检查候选回复是否满足高风险场景要求。
   - 不通过则发布 critique，要求 ResponseAgent 修订。

面试表达：

> 我没有让生成回复的 Agent 自己做最终安全判断，而是把风险评估和输出审查拆成独立 SafetyAgent，并通过 Coordinator 规定只有 safety review 通过的 response artifact 才能 final accept。这是一个比较重要的安全 gate。

### 8.6 Agent 的记忆怎么设计

项目里有两层记忆：

1. 用户会话短期记忆：
   - Redis key：`echomind:short-term-memory:{session_public_id}`
   - 存最近 N 条消息。
   - TTL 自动过期。
   - 写入前做隐私脱敏。

2. Agent 私有记忆：
   - Redis key：`agent:{agent_name}:{session_public_id}`
   - 每个 Agent 存自己的判断摘要。
   - 例如 UnderstandingAgent 记 intent，SafetyAgent 记 risk，ContextAgent 记检索情况。

还有历史压缩：

- `compact_history_for_prompt()` 会生成内部摘要。
- 只保留最近若干条消息。
- 避免 prompt 过长。
- 摘要禁止作为学生端展示内容。

面试表达：

> 我把长期业务记录和短期 prompt 记忆分开。MySQL 保存完整会话和报告，Redis 保存最近上下文用于对话连续性。进入 prompt 前会做脱敏和压缩，减少 token 成本和隐私风险。每个 Agent 还有私有记忆 key，避免不同职责的判断互相污染。

### 8.7 Agent 的工具权限怎么设计

`AgentProfile` 里有 `tool_permissions`，例如：

- UnderstandingAgent：`llm.intent`
- SafetyAgent：`llm.risk`、`rules.high_risk`、`response.review`
- ContextAgent：`redis.memory`、`mysql.messages`、`rag.retrieve`、`skills.read`
- ResponseAgent：`llm.response_plan`
- CoordinatorAgent：`taskboard.write`、`blackboard.accept`

当前这些权限更多是 profile 描述和架构约束，没有做成完整运行时权限拦截器。工具后处理层另有 `ToolPolicyRegistry`，但当前 `ToolQueueWorker` 还没有实际调用 `ToolGovernanceService` 去写审计记录。

面试时可以诚实这样讲：

> 代码已经把 Agent 的工具权限和后处理工具策略建模出来了，但运行时强制权限拦截还可以继续增强。下一步我会把 ToolGovernanceService 接入 ToolQueueWorker，在每个 job 执行前写审计、做 allow/deny 判断，执行后更新状态。

这比夸大功能要好得多。

---

## 9. RAG 设计

核心文件：

- `app/services/knowledge.py`
- `app/services/vector_store.py`
- `app/knowledge/*.md`
- `app/rag_eval/runner.py`

### 9.1 知识入库

启动时 `seed_data(db)` 会读取 `app/knowledge/*.md`，调用：

```text
KnowledgeService.ensure_source()
```

入库流程：

1. 读取 Markdown 内容。
2. `chunk_text()` 按 `knowledge_chunk_size` 和 `knowledge_chunk_overlap` 切块。
3. 保存到 MySQL 的 `knowledge_chunks`。
4. 如果 Chroma 和 embedding 可用，则同步向量索引。

管理员也可以：

- 直接提交文本知识。
- 上传文件知识。
- 对 PDF 用 `pypdf` 抽文本。

### 9.2 检索流程

`KnowledgeService.retrieve()` 的流程：

```text
query
  -> vector retrieve from Chroma
  -> BM25 retrieve from persistent bm25s index
  -> normalize vector score and BM25 score
  -> weighted fusion
  -> local rerank
  -> expand best chunk with neighbor chunks
  -> top_k SearchResult
```

融合分数：

- 向量权重：`KNOWLEDGE_HYBRID_VECTOR_WEIGHT`
- BM25 权重：`KNOWLEDGE_HYBRID_BM25_WEIGHT`
- 默认候选数：`KNOWLEDGE_CANDIDATE_K`

BM25 索引默认保存在 `data/bm25`。应用启动时读取 MySQL 知识块并校验内容指纹：指纹一致时直接加载磁盘索引，不一致时重建；管理员上传或更新知识后也会自动重建。查询阶段直接访问已加载索引，不再为每次请求扫描并重新分词全部 MySQL chunk。索引不可用且 `KNOWLEDGE_BM25_REQUIRED=false` 时，系统保留原有逐块计算 BM25 的兼容回退。

本地 rerank 考虑：

- base score
- lexical similarity
- query token coverage
- phrase match

### 9.3 降级策略

Chroma 主路径依赖：

- `KNOWLEDGE_VECTOR_ENABLED=true`
- `OPENAI_API_KEY`
- `chromadb`
- OpenAI embedding endpoint

如果：

- 没有 API key。
- Chroma 不可用。
- embedding 调用失败。
- `KNOWLEDGE_VECTOR_REQUIRED=false`。

系统会回退到：

```text
local BM25 + hybrid_score reranker
```

面试表达：

> 我没有把 RAG 做成单点依赖。向量检索不可用时，系统可以回退到持久化本地 BM25 索引和词面 rerank，BM25 索引异常时还保留逐块计算的兼容路径。如果生产验收要求强依赖向量库或 BM25 索引，可以分别启用对应的 `REQUIRED` 配置，让错误显式暴露。

### 9.4 动态路由 RAG

不是所有消息都检索知识库。

- `CHAT`：普通聊天、编程、作业、校园事务，不走 RAG。
- `CONSULT`：压力、焦虑、低落、失眠等，走 RAG。
- `RISK`：自伤、自杀、即时危险，走 RAG + safety skill。

这样做的原因：

- 降低无关检索噪声。
- 降低成本和延迟。
- 避免普通问题被过度心理化。
- 把心理知识库用于真正需要的场景。

---

## 10. 风险评估与报告链路

核心文件：

- `app/services/assessment.py`
- `app/agents/autonomous.py` 中的 `SafetyAgent`
- `app/agents/harness.py`
- `app/models/entities.py`
- `app/services/report.py`

风险评估分三层：

1. 硬规则：
   - 明确意图、当前念头、计划、工具、时间、已经实施、失控或无法保证安全时直接 `HIGH`。
   - 明确否定、历史陈述、引用、第三方和知识性假设不会仅凭风险词直接触发 `HIGH`。
   - 明确高风险不调用模型且不能被模型降级；语义模糊时要求回复进一步确认当前安全。

2. LLM JSON：
   - prompt 要求模型只输出 JSON。
   - 字段包括 `emotion`、`emotionScore`、`risk`、`confidence`、`summary`。

3. 启发式兜底：
   - 模型调用失败或 JSON 解析失败时，根据关键词判断焦虑、低落、中低风险。

报告创建规则：

- `AgentRunResult.requires_report` 当前定义为 `intent != CHAT`。
- 所以咨询和风险都会创建心理报告。
- 普通聊天不创建报告。

学生端输出限制：

- 不展示风险等级。
- 不展示报告 ID。
- 不展示 emotionScore。
- 不展示后台标签。

管理员端可查看：

- 原始输入。
- 风险等级。
- 情绪标签。
- 置信度。
- 摘要。
- trace。
- 工具记录。
- 会话全文。

---

## 11. 工具队列、MCP 和后处理

核心文件：

- `app/services/tool_queue.py`
- `app/services/tools.py`
- `app/services/mcp_client.py`
- `app/mcp_tools/server.py`
- `app/services/tool_governance.py`

### 11.1 为什么要异步工具队列

高风险或咨询场景会触发后处理：

- Excel 台账。
- 风险个案。
- 高风险预警。

这些操作可能慢、失败或依赖外部服务。项目没有让学生端一直等工具调用，而是：

```text
报告与工具任务先在同一事务提交
  -> 普通回复流式输出 / 高风险回复审核后输出
  -> 助手消息保存，worker 异步执行任务
  -> worker 异步执行
```

好处：

- 学生端体验稳定。
- 工具失败不会中断对话。
- 可以重试。
- 可以死信记录。
- 可以管理端查看状态。

### 11.2 工具任务类型

`ToolJobKind`：

- `EXCEL_REPORT`
- `CASE_CREATE`
- `ALERT_SEND`
- `RISK_ALERT`，兼容旧逻辑

调度规则：

- 所有咨询/风险报告写 Excel。
- 中高风险创建 case。
- 高风险发送 alert。
- `ALERT_SEND` 依赖 `CASE_CREATE` 成功。

### 11.3 工具实现

`ToolOrchestrationService`：

- `write_excel(report)`：用 `openpyxl` 写 Excel，进程内锁串行化写入。
- `create_case(report)`：为中高风险创建风险个案，并生成辅导员交接摘要。
- `send_case_alert(case)`：发送或记录高风险预警。
- `notify(report)`：根据 `ALERT_EMAIL_DELIVERY_MODE` 选择 log 或 smtp。
- `acknowledge_case()`、`add_case_note()`：MCP 侧可用于确认和备注。

本地演示默认：

```text
ALERT_EMAIL_DELIVERY_MODE=log
```

所以不会真的发邮件，只写记录。

### 11.4 MCP 工具服务

`app/mcp_tools/server.py` 暴露：

- `echomind_excel_report`
- `echomind_case_create`
- `echomind_alert_send`
- `echomind_alert_ack`
- `echomind_case_note_add`
- `echomind_alert_notify`

`EchoMindMcpToolClient` 会通过 stdio 启动：

```text
python -m app.mcp_tools.server
```

正式聊天始终使用持久化工具队列；MCP server 是独立的工具入口，不是聊天请求的自动回退路径。旧的 `TOOL_QUEUE_ENABLED` 参数不再关闭正式聊天队列。

---

## 12. AI Provider 设计

核心文件：

- `app/services/ai.py`
- `app/services/agent_models.py`
- `app/core/config.py`

支持 provider：

- `mock`
- `ollama`
- `openai`
- `qwen`

`AiClient.complete()` 用于非流式调用：

- 意图分类。
- 风险 JSON 评估。
- query rewrite。
- memory summary。

`AiClient.stream()` 用于最终学生端输出：

- Ollama 走 `/api/chat`。
- OpenAI / Qwen 走 OpenAI-compatible `/chat/completions`。
- Mock 把固定文本切成小块模拟流式 token。

`AgentModelRegistry` 的作用：

- 支持不同 Agent 使用不同 provider/model/temperature/max_tokens。
- 默认跟随全局 `AI_PROVIDER`。
- 可通过环境变量单独覆盖某个 Agent 的模型。

面试可以讲：

> 这个项目把 provider 适配和 Agent 编排解耦了。Agent 只关心 `AiMessage`，不关心底层是 Ollama、OpenAI、Qwen 还是 mock。这样本地演示、云端模型和测试都能复用同一套 Agent runtime。

注意一个代码细节：

- `AgentModelRegistry.client_for()` 对 `openai` 会设置 `openai_model`。
- 对其他 provider 当前走 `ollama_model` 分支。
- `qwen` 的默认 profile 能读取 `qwen_model`，但单 Agent qwen 覆盖时还可以进一步增强映射逻辑。

---

## 13. 前端功能

核心文件：

- `app/static/index.html`
- `app/static/app.js`
- `app/static/student.html`
- `app/static/student.js`
- `app/static/admin.html`
- `app/static/admin.js`
- `app/static/styles.css`

登录页：

- 输入用户名密码。
- 用 `btoa(username:password)` 生成 Basic Auth token。
- token 存在 `sessionStorage`。
- 根据角色跳转学生端或管理端。

学生端：

- 显示服务状态和模型状态。
- 支持新会话。
- 支持快捷输入。
- 发送消息到 `/api/chat/stream`。
- 通过 `ReadableStream` 读取 SSE。
- `meta` 事件拿 sessionId。
- `token` 事件增量追加到气泡。
- `done` 事件结束。
- `error` 事件显示模型错误。

管理端：

- 查看报告总数、高风险数、个案、Excel、预警。
- 展示趋势和分布。
- 查看风险个案和交接摘要。
- 查看报告和会话全文。
- 上传知识库文件。
- 触发向量重建和备份。

---

## 14. 数据库模型

核心文件：`app/models/entities.py`

主要表：

- `user_accounts`：用户、密码 hash、角色。
- `chat_sessions`：会话。
- `chat_messages`：完整消息记录。
- `knowledge_chunks`：知识库分块和 embedding JSON。
- `psychological_reports`：心理报告。
- `risk_cases`：风险个案。
- `case_notes`：个案备注。
- `alert_records`：预警记录。
- `excel_records`：Excel 写入记录。
- `tool_jobs`：异步工具任务。
- `dead_letter_records`：死信。
- `agent_run_traces`：Agent 运行过程。
- `tool_audit_records`：工具审计记录。

面试重点：

- MySQL 保存完整业务事实。
- Redis 保存短期上下文。
- Chroma 保存向量索引。
- Excel 是辅导员/管理员可读台账。
- trace 让 Agent 的每轮协作可审计。

---

## 15. 测试和验证

单元测试在 `tests/`。

覆盖点：

- `test_event_driven_multi_agent.py`
  - 黑板 append-only。
  - artifact 不互相覆盖。
  - registry 按 claim confidence 排序。
  - coordinator 使用 claim 处理 open tasks。
  - 不同 Agent 可以覆盖不同模型。
  - qwen provider 默认使用 qwen model。

- `test_privacy_and_assessment.py`
  - 手机、邮箱、身份证脱敏。
  - Redis memory 写入时脱敏。
  - 明确高风险硬规则优先，不调用模型。
  - 否定和模糊语境进入语义评估或安全确认。

- `test_risk_context.py`
  - 覆盖明确自伤/自杀、伤害他人、已经实施和英文高风险表达。
  - 覆盖否定、双重否定、转折、历史、引用、第三方、假设和模糊语境。

- `test_memory_compaction.py`
  - 历史摘要和最近消息保留。
  - 可关闭 compaction。
  - 摘要长度受限。

- `test_skills.py`
  - skill registry 加载。
  - skill frontmatter 校验。
  - 状态 warning。

- `test_tool_governance.py`
  - 高风险预警只允许 HIGH。
  - 中风险允许 case create，低风险不允许。
  - 未知工具阻断。

- `test_ai_providers.py`
  - Qwen 使用 DashScope OpenAI-compatible endpoint。

工程 harness 在 `app/harness/runner.py`，覆盖更完整：

- Risk Safety Harness。
- Agent Routing Harness。
- Standard Skills Harness。
- RAG Harness。
- API Harness。
- Tool Queue Harness。

运行命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m app.harness.runner
```

---

## 16. 面试高频问答

### Q1：你这个项目为什么需要 Agent？

答：

> 因为它不是普通问答，而是校园心理支持场景。系统需要先区分普通聊天、心理咨询和风险干预，再决定是否检索知识库、是否创建报告、是否触发工具链。尤其高风险场景要求独立安全评估和回复审查。所以我用 Agent runtime 把理解、风险、上下文、回复和安全审查拆开，每个 Agent 有明确职责，最终由 Coordinator 通过黑板状态和安全 gate 采纳回复。

### Q2：你的多 Agent 是怎么协作的？

答：

> 我用了任务板和黑板模式。每轮对话开始后，Coordinator 创建 root task，并根据缺失 artifact 派生理解、风险评估、上下文收集、回复生成和安全审查任务。每个 Agent 根据自己的 capability 和当前黑板状态决定是否 claim 任务，执行后发布 artifact。Coordinator 不规定固定链路，而是根据 artifact 是否齐全、SafetyAgent 是否批准、置信度是否达标来决定 final acceptance。

### Q3：如何避免多 Agent 乱跑或者无限循环？

答：

> 系统有预算控制：最大轮数、每轮最大 claim 数、每个 Agent 最大 claim 数。任务状态有 OPEN、CLAIMED、CLOSED，Agent 完成后任务关闭。Coordinator 只根据明确缺失的 artifact 创建任务，而且 final acceptance 有清晰条件。如果预算耗尽，会写入 `BUDGET_EXHAUSTED` 事件并回退到 fallback messages。

### Q4：风险识别怎么做？

答：

> 风险识别不是只依赖模型。第一层是语境化硬规则：明确意图、念头、计划、工具、时间、已经实施、失控或无法保证安全时直接判 HIGH，同时区分否定、历史、引用、第三方和假设，避免裸关键词误报。明确 HIGH 不调用模型且不能被降级；模糊表达要求确认当前安全。第二层是 LLM JSON 评估，提取 emotion、risk、confidence 和 summary。第三层是启发式兜底，在模型失败时根据焦虑、抑郁、压力等信号给出保守判断。

### Q5：RAG 怎么实现？

答：

> 知识库来自内置 Markdown 和管理员上传文件，启动时切块写入 MySQL。如果配置了 OpenAI API key 和 Chroma，会把分块 embedding 写入 Chroma。检索时向量召回和 BM25 关键词召回并行，分数归一化后按权重融合，再用本地 rerank 结合词面相似度、覆盖率和短语匹配排序。向量不可用时回退到本地 BM25，保证系统可用性。

### Q6：为什么不是所有对话都走 RAG？

答：

> 普通学习、编程、校园事务不需要心理知识库，强行 RAG 会增加延迟和噪声，还可能把普通问题过度心理化。所以 UnderstandingAgent 先做动态路由，只有 CONSULT 和 RISK 才走 ContextAgent 的 RAG 和 skill 注入。

### Q7：学生端为什么不能看到风险等级？

答：

> 心理支持场景要避免给学生贴标签或造成额外压力。风险等级、emotionScore、报告 ID 属于后台评估和辅导员跟进信息。学生端只看到自然、共情、有边界的支持回复；管理员端才查看结构化报告和 trace。

### Q8：工具调用为什么要异步？

答：

> Excel 写入、个案创建、邮件预警都可能慢或失败。如果同步阻塞，会影响学生端关键回复。项目采用工具队列，让学生先收到 SSE 回复，再后台执行工具任务。工具任务有依赖、重试、限流和死信记录，管理端能追踪处理结果。

### Q9：Mock provider 有什么意义？

答：

> Mock provider 可以在没有模型、没有 API key、没有 embedding 服务时跑通全链路。它让本地演示和测试稳定可复现，也帮助区分“业务编排 bug”和“模型服务不可用”。

### Q10：这个项目还有哪些可以改进？

答：

> 第一，认证现在是演示级 Basic Auth，生产应该换成更完整的认证授权方案。第二，AgentProfile 里有工具权限，但还可以加运行时权限拦截。第三，ToolGovernanceService 已经有策略和审计模型，但当前工具队列还没有完全接入执行前授权和执行后审计。第四，RAG 可以增加更强的 reranker、引用展示和 query rewriting 评估。第五，高风险场景可以接入真实校内值班系统，并完善告警确认闭环。

---

## 17. 面试时可以重点讲的亮点

1. **事件驱动多 Agent runtime**
   - 不是固定链式 prompt。
   - 用任务板、黑板、artifact、event 组织协作。
   - Agent 自主认领任务，Coordinator 控制预算和采纳。

2. **安全优先的 Agent gate**
   - SafetyAgent 独立评估输入风险。
   - SafetyAgent 审查输出。
   - 高风险可发布 `SAFETY_OVERRIDE`。
   - 未过审的候选回复不能 final accept。

3. **动态 RAG 路由**
   - CHAT 不检索。
   - CONSULT/RISK 才检索。
   - 降低噪声、成本和过度心理化。

4. **混合检索和降级**
   - Chroma vector + BM25 + local rerank。
   - 无 API key 时回退到 BM25。
   - 演示环境不容易崩。

5. **数据闭环**
   - 学生消息、助手消息、报告、trace 都落库。
   - 中高风险生成个案。
   - 高风险预警。
   - 工具任务支持重试和死信。

6. **可测试性**
   - Mock provider。
   - 单元测试覆盖协议、风险、记忆、skills、provider。
   - 工程 harness 覆盖端到端关键路径。

---

## 18. 建议你的学习路线

第一阶段：能跑和能讲

- 跑通 mock 模式。
- 登录学生端发三类消息：
  - 普通编程问题。
  - 压力/失眠咨询。
  - 高风险表达测试。
- 登录管理端看报告、个案、Excel、alert。
- 能画出一轮聊天链路。

第二阶段：读 Agent runtime

- 读 `events.py`，弄懂 task、artifact、event、blackboard。
- 读 `registry.py`，弄懂 capability 和 claim confidence。
- 读 `coordinator.py`，弄懂任务派生和 final acceptance。
- 读 `autonomous.py`，逐个看四个业务 Agent。
- 手动画一次 `CHAT`、`CONSULT`、`RISK` 的不同路径。

第三阶段：读 RAG 和记忆

- 读 `knowledge.py` 的 chunk、BM25、fusion、rerank。
- 读 `vector_store.py` 的 Chroma 和 embedding。
- 读 `memory.py` 的 Redis、脱敏、压缩摘要。
- 理解为什么 RAG 只在咨询/风险场景触发。

第四阶段：准备面试项目话术

- 准备 2 分钟项目介绍。
- 准备 5 分钟 Agent runtime 深挖。
- 准备 RAG 问答。
- 准备安全和合规问答。
- 准备改进点，不要把未完成能力说成已完成。

第五阶段：做一个小增强练手

推荐增强：

- 把 `ToolGovernanceService` 接入 `ToolQueueWorker`。
- 在工具任务执行前授权，执行后写审计状态。
- 补单元测试，证明 LOW 风险不能触发高风险 alert。

这个增强很适合面试讲，因为它体现了：

- Agent 工具权限。
- 安全策略。
- 审计。
- 异步任务。
- 测试驱动。

---

## 19. 你应该熟悉的源码入口清单

必须熟：

- `app/main.py`
- `app/api/routes.py`
- `app/services/chat.py`
- `app/agents/harness.py`
- `app/agents/events.py`
- `app/agents/coordinator.py`
- `app/agents/autonomous.py`
- `app/services/knowledge.py`
- `app/services/assessment.py`
- `app/services/memory.py`

建议熟：

- `app/services/ai.py`
- `app/services/agent_models.py`
- `app/services/skills.py`
- `app/services/tool_queue.py`
- `app/services/tools.py`
- `app/services/trace.py`
- `app/models/entities.py`
- `app/harness/runner.py`

可以按需看：

- `app/static/student.js`
- `app/static/admin.js`
- `app/mcp_tools/server.py`
- `app/rag_eval/runner.py`
- `tests/*.py`

---

## 20. 一句话总结

EchoMind 的核心价值不是“套了一个大模型接口”，而是把校园心理支持这个高风险业务拆成了可审计、可测试、可降级的 Agent 协作系统：**先识别意图和风险，再按需检索知识和加载 skills，生成候选回复后做安全审查，最后流式输出给学生，同时把咨询/风险事件沉淀为后台报告、个案和工具处理记录。**
