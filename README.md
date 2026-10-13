# EchoMind Python

## 核心能力

- 学生端 SSE 聊天：普通回复逐段流式输出；高风险回复先完整生成、独立复审，审核通过后一次性返回，否则使用固定安全回应。
- Basic Auth 登录，支持学生和管理员角色隔离。
- 事件驱动多 Agent 协作 runtime：Coordinator、Understanding、Safety、Context、Response 通过共享黑板、任务认领和安全审查协作。
- 动态路由 RAG：先判断 `CHAT / CONSULT / RISK`，普通问题不查知识库，咨询和风险场景才进入检索增强。
- Chroma 向量 RAG 知识库：支持 Markdown、txt、PDF 文件上传，自动切块，使用当前 embedding provider 写入向量库，并与 BM25 关键词召回融合后进入本地 reranker；向量不可用时保留本地 BM25 + 词面检索兜底。
- 心理风险评估：明确危险硬规则优先，区分否定、历史、引用、第三方和假设语境，再由 LLM JSON 评估与启发式规则兜底。
- 后台报告：记录情绪标签、情绪分数、风险等级、置信度和摘要，但学生端不展示后台评估结果。
- 数据闭环：仅留存模式保存咨询/风险会话与报告，短期上下文进入 Redis；报告与工具任务同事务入库，高风险可创建个案与预警任务。预警默认仅记录，配置 SMTP 后才发邮件。
- 本地模型接入：支持通过 Ollama 加载本机已有的 GGUF 或 Ollama 模型。
- OpenAI-compatible API 接入：也可切换到 OpenAI 或千问/DashScope 云端模型。
- MCP 工具服务：单独暴露 Excel 报告写入和风险通知能力；正式聊天链路通过持久化工具队列调用同一套工具实现。
- RAG 评测：Recall@K、Precision@K、MRR、NDCG@K、HitRate。

## 技术栈

```text
语言：Python
Web 框架：FastAPI
服务运行：Uvicorn / ASGI
数据库：MySQL，SQLAlchemy ORM，PyMySQL 驱动
短期记忆：Redis
配置管理：pydantic-settings，.env
AI 接入：Ollama，本地 GGUF 模型，OpenAI-compatible API，千问/DashScope，Mock Provider
Agent 编排：事件驱动黑板协作 runtime
RAG：本地知识库切块、provider embeddings、Chroma 向量库、BM25、分数融合、本地 reranker、上下文扩展
流式输出：Server-Sent Events
文档解析：pypdf
Excel 台账：openpyxl
邮件预警：SMTP / smtplib
前端：原生 HTML / CSS / JavaScript
认证：Basic Auth
工具协议：MCP
```

说明：当前 Python 版只保留事件驱动多 Agent runtime，入口在 `app/agents/event_driven_runtime.py`。共享返回类型定义在 `app/agents/result.py`。RAG 默认使用 Chroma 本地持久化向量库做语义召回，同时用 BM25 做关键词召回，再融合并本地 rerank；未安装 Chroma、未配置当前 provider 的 embedding API key 或向量服务异常时，会自动回退到本地 BM25 + `hybrid_score` reranker，避免演示环境中断。

## 目录结构

```text
app/
├── agents/          # 事件驱动多 Agent runtime
├── api/             # FastAPI 路由
├── core/            # 配置、数据库、安全、启动初始化
├── knowledge/       # 内置校园心理知识库
├── mcp_tools/       # MCP 工具服务
├── models/          # SQLAlchemy 实体
├── rag_eval/        # RAG 评测脚本和数据集
├── schemas/         # Pydantic DTO
├── services/        # AI、聊天、知识库、评估、报告、工具服务
└── static/          # 原生前端页面

models/local-ollama-model/
├── Modelfile        # Ollama 模型定义
└── README.md        # GGUF 模型放置说明

scripts/
├── run-dev.sh
├── start-ollama.sh
├── create-local-ollama-model.sh
└── package-release.sh
```

## Agent loop

每轮对话默认进入事件驱动多 Agent 协作 runtime。Coordinator 维护共享黑板和任务板，专业 Agent 根据能力和置信度认领任务，发布 artifact，再由安全审查和最终采纳机制收敛输出：

```text
TURN_STARTED
-> CoordinatorAgent 创建任务
-> UnderstandingAgent / SafetyAgent / ContextAgent / ResponseAgent 认领任务并发布 artifact
-> SafetyAgent 审查候选回复
-> CoordinatorAgent FINAL_ACCEPTED
-> 报告与工具任务同事务落库
-> 普通回复流式输出 / 高风险回复完整生成并复审后输出
```

各 Agent 分工：

- `CoordinatorAgent`：维护任务板、预算、安全门槛、冲突仲裁和最终采纳。
- `UnderstandingAgent`：判断 `CHAT / CONSULT / RISK`，发布 intent artifact。
- `SafetyAgent`：独立评估风险，必要时发布 `SAFETY_OVERRIDE`，并审查候选回复。
- `ContextAgent`：按需聚合 Redis / MySQL 记忆、RAG 检索结果和 Skill 约束。
- `ResponseAgent`：根据黑板 artifact 生成候选回复 prompt，等待安全审查和采纳。

SafetyAgent 在黑板阶段审查的是候选提示词，不是模型最终回答。高风险回答会在聊天层完整生成，再做规则最低检查和一次独立模型审核；审核调用失败或审核不通过时，返回固定安全回应。Mock 模式只执行规则检查，不能代替真实模型安全评估。这仍不是临床安全保证，系统也不提供实时危机救援。

## 留存模式与主动联系

学生首次选择留存模式后，对话会保存；没有发送实际消息的空会话不会出现在历史列表。咨询或风险内容形成报告；管理员在普通报告、列表和台账中看到去标识化内容。私密模式可继续与 AI 对话，不进入历史、报告或管理员工作台。

高风险留存对话结束后，学生可主动提交手机号申请工作人员联系。手机号单独加密保存，不写入聊天文本或普通报告。请求状态为 `PENDING -> CLAIMED -> CONTACTED/UNREACHABLE/CLOSED`，学生可在未结案前撤回；撤回或结案会清除系统中保存的手机号密文。后台队列只显示报告编号和状态。管理员仅能在有效请求下填写核实依据并留下审计后查看账户信息和手机号，处理结果与操作人也会留痕。已发生的身份查看无法通过撤回消除；主动联系不是实时救援承诺。

## 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 已包含：

```text
chromadb
pymysql
redis
```

`AGENT_FRAMEWORK` 仍会读取环境变量，但当前只支持 `event_driven_multi_agent`。历史值或未知值会在状态接口中标记为 fallback，并实际使用事件驱动 runtime。

## MySQL 和 Redis 配置

留存模式默认使用 MySQL 保存业务数据和聊天消息，使用 Redis 保存短期对话记忆；私密模式不写入这些业务记录。启动服务前先创建数据库：

```sql
CREATE DATABASE echomind DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'echomind'@'%' IDENTIFIED BY 'echomind';
GRANT ALL PRIVILEGES ON echomind.* TO 'echomind'@'%';
FLUSH PRIVILEGES;
```

`.env` 中配置连接：

```env
DATABASE_URL=mysql+pymysql://echomind:echomind@127.0.0.1:3306/echomind?charset=utf8mb4
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_MEMORY_TTL_SECONDS=86400
REDIS_MEMORY_MAX_MESSAGES=40
MEMORY_STALE_CONCERN_DAYS=7
```

完整聊天记录写入 MySQL 的 `chat_sessions`、`chat_messages` 等表。Redis 只保存每个会话最近 `REDIS_MEMORY_MAX_MESSAGES` 条短期上下文，并通过 `REDIS_MEMORY_TTL_SECONDS` 自动过期。历史消息进入心理支持上下文时会带相对时间；超过 `MEMORY_STALE_CONCERN_DAYS` 天且未再次确认的问题只作为待确认线索，不会被当作当前事实。

## 演示账号

默认账号：

```text
student / student123
admin / admin123
```

这些是本地演示凭据，不能直接用于公网部署。

## Docker Compose 一键启动

仓库提供 `Dockerfile` 和 `docker-compose.yml`，会启动：

- `mysql`：MySQL 8.0，容器内端口 `3306`，宿主机映射 `13306`
- `redis`：Redis 7，容器内端口 `6379`，宿主机映射 `16379`
- `app`：EchoMind FastAPI 服务，宿主机端口 `8080`

现有 MySQL 数据卷不会因为修改 `MYSQL_DATABASE` 自动创建新库或新用户。若之前用 Docker Compose 运行过旧版本，先备份并确认两个账号的迁移方案，再切换 Compose 数据库配置；不要删除旧卷来解决启动错误。本机 PyCharm 运行时继续使用原 `.env` 的数据库连接即可保留账号。

默认配置会让应用容器访问宿主机 Ollama：

```bash
docker compose up -d --build
```

如果本机 Ollama 已经有可用模型，容器即可使用真实本地聊天模型链路。可通过 `OLLAMA_MODEL` 指定具体模型名：

```text
OLLAMA_MODEL=local-ollama-model:latest
```

## Chroma 向量库与快照

应用启动时会同步 `app/knowledge/*.md` 内置默认知识库到数据库。当前默认文档覆盖校园心理支持总则、风险等级策略、焦虑恐慌、情绪低落、睡眠作息、学业压力、考试季、人际关系、新生适应、咨询转介和隐私边界等主题；如果默认 md 内容发生变化，重启后对应来源会按当前切块规则刷新入库。

知识库默认优先使用 Chroma 持久化向量库，embedding 会跟随当前 provider：`AI_PROVIDER=qwen` 时使用 DashScope 兼容接口和 `QWEN_EMBEDDING_MODEL`，其他模式默认使用 OpenAI `OPENAI_EMBEDDING_MODEL`。关键词召回使用 `bm25s` 构建在 `data/bm25` 的持久化本地索引；启动时校验知识库指纹，知识上传或更新后自动重建。查询时会同时取向量候选和 BM25 候选，按配置权重融合后进入本地 reranker。没有对应 provider 的 API key、缺少 `chromadb` 或向量调用失败时，会回退到本地 BM25 + `hybrid_score` reranker：

```env
OPENAI_API_KEY=你的_API_Key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
QWEN_API_KEY=你的千问_API_Key
QWEN_EMBEDDING_MODEL=text-embedding-v4
KNOWLEDGE_VECTOR_ENABLED=true
KNOWLEDGE_VECTOR_REQUIRED=false
KNOWLEDGE_CANDIDATE_K=16
KNOWLEDGE_HYBRID_VECTOR_WEIGHT=0.65
KNOWLEDGE_HYBRID_BM25_WEIGHT=0.35
KNOWLEDGE_RERANK_ENABLED=true
KNOWLEDGE_BM25_ENABLED=true
KNOWLEDGE_BM25_REQUIRED=false
KNOWLEDGE_BM25_INDEX_DIR=data/bm25
KNOWLEDGE_BM25_MMAP=false
CHROMA_PERSIST_DIR=data/chroma
CHROMA_SNAPSHOT_DIR=data/chroma-snapshots
```

管理员接口：

```bash
curl -u admin:admin123 http://127.0.0.1:8080/api/admin/knowledge/status
curl -u admin:admin123 -X POST http://127.0.0.1:8080/api/admin/knowledge/rebuild-vector
curl -u admin:admin123 -X POST http://127.0.0.1:8080/api/admin/knowledge/rebuild-bm25
curl -u admin:admin123 -X POST http://127.0.0.1:8080/api/admin/knowledge/backup
```

当 `KNOWLEDGE_VECTOR_REQUIRED=false` 时，如果 Chroma 或 embedding 服务不可用，系统会降级到本地 BM25 + 词面 rerank；设为 `true` 则启动或检索失败时直接暴露错误。

## 工具队列、限流与死信

心理报告生成时，用户消息、报告、Agent trace 和 `tool_jobs` 在同一数据库事务提交。工具链不会阻塞模型回复：

```text
EXCEL_REPORT
CASE_CREATE -> ALERT_SEND
```

队列任务用数据库条件更新认领、定期续租，超时后才允许恢复。Excel 写入检查报告 ID 避免单进程重试重复行，个案创建保持幂等；预警发送使用独立线程池并支持每分钟限流。失败任务会按延迟重试，超过 `TOOL_QUEUE_MAX_ATTEMPTS` 后进入 `dead_letter_records`。跨进程共享 Excel 文件、全局邮件限流和 SMTP 投递仍不能保证严格 exactly-once。

```env
TOOL_QUEUE_EXCEL_WORKERS=1
TOOL_QUEUE_EMAIL_WORKERS=2
ALERT_EMAIL_RATE_LIMIT_PER_MINUTE=30
ALERT_EMAIL_DELIVERY_MODE=log
```

`ALERT_EMAIL_DELIVERY_MODE=log` 适合本地演示；生产发邮件时改为 `smtp` 并配置 SMTP。

## 邮件预警配置

高风险消息会触发心理报告，并由工具队列异步写入 Excel 台账、创建个案和记录预警。发送邮件前需要在 `.env` 中配置 SMTP：

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-account@example.com
SMTP_PASSWORD=your-smtp-password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
ALERT_EMAIL_FROM=your-account@example.com
ALERT_EMAIL_TO=counselor@example.com,admin@example.com
ALERT_EMAIL_SUBJECT_PREFIX=[EchoMind 高风险预警]
```

未配置 SMTP 或收件人时，系统不会中断聊天流程，但会在 `alert_records` 中写入 `FAILED` 记录，提示缺少的配置项。

## 接入本地 GGUF 模型

Python 版默认预留一个通用本地模型名：

```text
local-ollama-model:latest
```

模型目录：

```text
models/local-ollama-model/
```

需要放入的 GGUF 权重：

```text
models/local-ollama-model/model.gguf
```

如果本机已经有其他位置的 GGUF 模型文件，可以通过 `UPSTREAM_GGUF` 指定路径并建立软链接：

```bash
UPSTREAM_GGUF=/path/to/model.gguf ./scripts/create-local-ollama-model.sh
```

创建 Ollama 模型：

```bash
./scripts/create-local-ollama-model.sh
```

启动 Ollama：

```bash
./scripts/start-ollama.sh
```

启动 Python 服务：

```bash
AI_PROVIDER=ollama ./scripts/run-dev.sh
```

查看模型接入状态：

```bash
curl -u student:student123 http://127.0.0.1:8080/api/agent/status
```

返回结果中的 `localModel.ggufExists` 和 `localModel.modelfileExists` 会显示模型资产是否就绪。
同时 `agentFramework.active` 会显示当前实际使用的 Agent 编排框架：

```text
event_driven_multi_agent
```

## 接入 OpenAI-compatible API

```bash
AI_PROVIDER=openai \
OPENAI_API_KEY=你的_API_Key \
OPENAI_MODEL=gpt-4o-mini \
OPENAI_EMBEDDING_MODEL=text-embedding-3-small \
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

知识库向量检索会根据 `AI_PROVIDER` 选择 embedding provider：`openai` 使用 `OPENAI_API_KEY/OPENAI_EMBEDDING_MODEL`，`qwen` 使用 `QWEN_API_KEY/QWEN_EMBEDDING_MODEL`。相关配置：

```env
KNOWLEDGE_VECTOR_ENABLED=true
KNOWLEDGE_VECTOR_REQUIRED=false
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
QWEN_EMBEDDING_MODEL=text-embedding-v4
KNOWLEDGE_CANDIDATE_K=16
KNOWLEDGE_HYBRID_VECTOR_WEIGHT=0.65
KNOWLEDGE_HYBRID_BM25_WEIGHT=0.35
KNOWLEDGE_RERANK_ENABLED=true
KNOWLEDGE_BM25_ENABLED=true
KNOWLEDGE_BM25_INDEX_DIR=data/bm25
CHROMA_PERSIST_DIR=data/chroma
CHROMA_COLLECTION_NAME=echomind_knowledge
```

当 `KNOWLEDGE_VECTOR_REQUIRED=false` 时，缺少 API key 或 Chroma 不可用不会阻断聊天，系统会回退到本地 BM25 + `hybrid_score` reranker。若交付验收要求必须走 Chroma 向量检索，可设置 `KNOWLEDGE_VECTOR_REQUIRED=true`。

## 接入千问/DashScope

千问使用 OpenAI-compatible Chat Completions 接口，应用侧可直接将 `AI_PROVIDER` 切换为 `qwen`：

```bash
AI_PROVIDER=qwen \
QWEN_API_KEY=你的千问_API_Key \
QWEN_MODEL=qwen-plus \
QWEN_EMBEDDING_MODEL=text-embedding-v4 \
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
KNOWLEDGE_VECTOR_ENABLED=true \
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Windows PowerShell 示例：

```powershell
$env:AI_PROVIDER="qwen"
$env:QWEN_API_KEY="你的千问_API_Key"
$env:QWEN_MODEL="qwen-plus"
$env:QWEN_EMBEDDING_MODEL="text-embedding-v4"
$env:QWEN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:KNOWLEDGE_VECTOR_ENABLED="true"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

`KNOWLEDGE_VECTOR_ENABLED=false` 会关闭 Chroma 主检索路径，不影响千问作为聊天大模型使用。要让 RAG 也走 DashScope embedding 和 Chroma，保持 `KNOWLEDGE_VECTOR_ENABLED=true`，并配置 `QWEN_API_KEY` 与 `QWEN_EMBEDDING_MODEL`。

## 调用示例

先读取 `/api/privacy/notice` 的当前版本，再创建留存会话并记录返回的 `sessionId`：

```bash
curl -u student:student123 http://127.0.0.1:8080/api/privacy/notice
curl -u student:student123 -H 'Content-Type: application/json' \
  -d '{"noticeVersion":"2026-10-08-v1"}' http://127.0.0.1:8080/api/student/sessions
```

以下示例把返回值填入 `<留存会话ID>`。普通学生聊天逐段流式输出：

```bash
curl -N -u student:student123 \
  -H 'Content-Type: application/json' \
  -d '{"message":"我最近很焦虑，晚上总是睡不着","sessionId":"<留存会话ID>"}' \
  http://127.0.0.1:8080/api/chat/stream
```

高风险示例，会先完整生成并复审回答，再触发心理报告、风险个案创建和预警工具计划；Excel 保留为台账输出，邮件/log 是预警通道之一：

```bash
curl -N -u student:student123 \
  -H 'Content-Type: application/json' \
  -d '{"message":"我不想活了，感觉撑不下去了","sessionId":"<留存会话ID>"}' \
  http://127.0.0.1:8080/api/chat/stream
```

管理员查看报告：

```bash
curl -u admin:admin123 http://127.0.0.1:8080/api/admin/reports
```

管理员追加知识库：

```bash
curl -u admin:admin123 \
  -H 'Content-Type: application/json' \
  -d '{"source":"sleep-guide","content":"失眠时可先固定起床时间，减少睡前屏幕刺激，必要时联系校心理中心。"}' \
  http://127.0.0.1:8080/api/admin/knowledge
```

追加知识库时，系统会同步写入 MySQL 分块和 Chroma 向量库，并自动重建持久化 BM25 索引；已有分块会在首次向量检索时自动补建 Chroma 索引。普通 `CHAT + LOW` 对话不会调度 `ContextAgent`，因此也不会执行 Chroma 或 BM25 查询。

## RAG 评测

```bash
AI_PROVIDER=mock python -m app.rag_eval.runner
```

评测报告输出到：

```text
target/rag-eval-report.json
```

## 单元测试

当前 `tests/` 里的基础回归用例使用 Python 标准库 `unittest`，不依赖 `pytest`：

```bash
python -m unittest discover -s tests
```

## Agent Runtime Harness

线上对话通过 `EchoMindAgentHarness` 组织一次 Agent run。Harness 不改变事件驱动 runtime 内部的多 Agent 协作方式，而是在外层统一管理：

- 输入脱敏和 session 解析。
- Agent runtime 调用和多 Agent 协作结果接入。
- 心理报告落库和工具计划生成。
- 学生与助手消息持久化。
- Agent steps、知识召回、风险结果等 trace 数据输出。

因此 HTTP 层只负责认证和 SSE 流式输出，Agent 后处理逻辑集中在 runtime harness 内。

## Engineering Harness

项目提供一键工程 harness，用 mock AI、临时 SQLite、内存短期记忆和本地输出验证核心链路：

- Risk Safety Harness：高风险识别、报告生成、后台元数据不外显、工具队列入队。
- Agent Routing Harness：通过 `EchoMindAgentHarness` 验证 CHAT / CONSULT / RISK 路由和多 Agent 步骤。
- Standard Skills Harness：验证 `skills/*/SKILL.md` 标准 Skill 加载、选择逻辑和交接摘要模板渲染。
- RAG Harness：基于内置评测集验证 Recall@K、MRR、NDCG 和 HitRate。
- API Harness：健康检查、认证授权、SSE 聊天、管理员知识库接口。
- Tool Queue Harness：Excel / case / alert 依赖、幂等、限流和 dead letter。

```bash
python3 -m app.harness.runner
```

报告输出到：

```text
target/harness/harness-report.json
target/harness/rag-eval-report.json
```

## MCP 工具服务

MCP Python 包建议使用 Python 3.10 或 3.11 安装运行。

```bash
python -m app.mcp_tools.server
```

业务后端触发报告后处理时始终通过异步持久化工具队列复用同一套工具实现；MCP server 可独立运行供外部调用。旧的 `TOOL_QUEUE_ENABLED` 配置保留兼容但不再关闭正式聊天队列。

暴露工具：

- `echomind_excel_report`
- `echomind_case_create`
- `echomind_alert_send`
- `echomind_alert_ack`
- `echomind_case_note_add`
- `echomind_alert_notify`

内置标准 Skills 位于 `skills/*/SKILL.md`，运行时由 `EchoMindSkillRegistry` 加载：

- `supportive_response_baseline`：心理咨询与风险回复的基础共情、边界和学生端表达规则。
- `high_risk_safety_plan`：高风险时引导模型优先完成短期安全计划。
- `anxiety_grounding_support`：焦虑、惊恐、崩溃场景的稳定化和 grounding 指引。
- `sleep_routine_support`：失眠、睡眠节律紊乱场景的安全睡眠建议。
- `academic_stress_planning`：考试、作业、论文、绩点压力的下一步拆解。
- `referral_resource_guidance`：校内心理中心、辅导员、可信任支持人和紧急资源转介。
- `counselor_handoff_summary`：生成给辅导员/管理员看的个案交接摘要模板。
