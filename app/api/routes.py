from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.agents.factory import agent_framework_status
from app.agents.event_driven_runtime import EventDrivenAgentRuntimeService
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import current_user, require_admin
from app.models.entities import UserAccount
from app.schemas.dtos import AssistedSessionStartRequest, ContactRequestSubmit, ContactResolutionRequest, CounselorReplyRequest, IdentityAccessRequest, KnowledgeIngestRequest, KnowledgeIngestResponse, ChatRequest, PrivateChatRequest, authority
from app.services.chat import ChatService
from app.services.contact_request import ContactRequestService
from app.services.knowledge import KnowledgeService
from app.services.model_assets import local_model_status
from app.services.report import ReportService
from app.services.privacy_modes import NOTICE_VERSION
from app.services.session_access import SessionAccessService
from app.services.skills import EchoMindSkillLibrary

router = APIRouter()


@router.get("/actuator/health")
def health():
    return {"status": "UP"}


@router.get("/api/profile")
def profile(user: Annotated[UserAccount, Depends(current_user)]):
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "roles": [authority(role) for role in user.roles],
    }


@router.get("/api/privacy/notice")
def privacy_notice():
    settings = get_settings()
    return {
        "version": NOTICE_VERSION,
        "provider": settings.ai_provider.lower(),
        "assisted": "本次对话会保存，咨询或风险内容可能形成脱敏报告，供心理中心工作人员查看和回复。",
        "private": "本次对话仍会发送给所配置的模型生成回复，但不会保存在本项目的历史、报告或管理员后台；刷新页面后无法恢复。私密模式不提供人工跟进或实时救援。",
    }


@router.post("/api/student/sessions")
def start_assisted_session(
    request: AssistedSessionStartRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if "ROLE_ADMIN" in user.roles:
        raise HTTPException(403, "管理员账号不能创建学生会话")
    try:
        session = SessionAccessService(db).start_assisted(user, request.noticeVersion)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"sessionId": session.public_id, "mode": "ASSISTED"}


@router.get("/api/student/sessions")
def assisted_sessions(user: Annotated[UserAccount, Depends(current_user)], db: Annotated[Session, Depends(get_db)]):
    return SessionAccessService(db).list_assisted(user)


@router.get("/api/student/sessions/{session_id}")
def assisted_session_detail(session_id: str, user: Annotated[UserAccount, Depends(current_user)], db: Annotated[Session, Depends(get_db)]):
    try:
        return SessionAccessService(db).detail(user, session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/student/reports/{report_id}/contact-request")
def submit_contact_request(
    report_id: int,
    request: ContactRequestSubmit,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        saved = ContactRequestService(db, get_settings()).submit(user, report_id, request.phone, request.consentToContact)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"reportId": saved.report_id, "status": saved.status, "requestedAt": saved.requested_at}


@router.delete("/api/student/reports/{report_id}/contact-request")
def cancel_contact_request(
    report_id: int,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        saved = ContactRequestService(db, get_settings()).cancel(user, report_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"reportId": saved.report_id, "status": saved.status}


@router.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if "ROLE_ADMIN" in user.roles:
        raise HTTPException(403, "管理员账号只能查看后台记录，不能发起学生对话。")
    if not request.sessionId:
        raise HTTPException(400, "请先选择会话模式")
    try:
        SessionAccessService(db).require_assisted(user, request.sessionId)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    service = ChatService(db, get_settings())
    return StreamingResponse(service.stream_chat(user, request), media_type="text/event-stream")


@router.post("/api/chat/private-stream")
async def private_chat_stream(
    request: PrivateChatRequest,
    user: Annotated[UserAccount, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if "ROLE_ADMIN" in user.roles:
        raise HTTPException(403, "管理员账号不能发起学生对话")
    if request.noticeVersion != NOTICE_VERSION:
        raise HTTPException(400, "请先阅读当前版本的隐私告知")
    return StreamingResponse(ChatService(db, get_settings()).stream_private_chat(user, request), media_type="text/event-stream")


@router.get("/api/agent/status")
def agent_status(user: Annotated[UserAccount, Depends(current_user)]):
    settings = get_settings()
    provider = settings.ai_provider.lower()
    model = (
        settings.ollama_model
        if provider == "ollama"
        else settings.openai_model
        if provider == "openai"
        else settings.qwen_model
        if provider == "qwen"
        else "mock"
    )
    framework = agent_framework_status(settings)
    return {
        "provider": provider,
        "model": model,
        "realModelEnabled": provider in {"ollama", "openai", "qwen"},
        "agentFramework": framework,
        "localModel": local_model_status(settings),
        "agents": [
            {"name": "CoordinatorAgent", "status": "READY", "description": "维护任务板、预算、安全门槛、冲突仲裁和最终采纳"},
            {"name": "UnderstandingAgent", "status": "READY", "description": "独立理解用户输入，发布 intent artifact"},
            {"name": "SafetyAgent", "status": "READY", "description": "独立风险评估、SAFETY_OVERRIDE 和候选回复安全审查"},
            {"name": "ContextAgent", "status": "READY", "description": "独立记忆视图、RAG 检索和 skill 上下文聚合"},
            {"name": "ResponseAgent", "status": "READY", "description": "根据黑板 artifact 发布候选回复方案"},
        ],
        "skills": EchoMindSkillLibrary.status_items(),
        "runtimeHarness": {
            "name": "EchoMindAgentHarness",
            "status": "READY",
            "description": "统一管理单轮 Agent run 的输入脱敏、上下文注入、风险报告、工具计划和 trace 输出",
        },
        "loop": {
            "type": "event-driven-multi-agent",
            "maxSteps": EventDrivenAgentRuntimeService.max_steps,
            "scheduler": "claim-based-actor-runtime",
        },
        "collaboration": {
            "scheduler": "claim-based",
            "state": "append-only-blackboard",
            "messageBus": "per-agent inbox over shared mailbox",
            "fixedWorkflow": False,
            "agentIsolation": {
                "prompt": "per-agent system prompt",
                "memory": "per-agent private Redis key",
                "model": "per-agent model profile",
                "tools": "per-agent tool permissions",
            },
        },
    }


@router.get("/api/reports/me")
def my_reports(user: Annotated[UserAccount, Depends(current_user)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).latest_reports(user.id)


@router.get("/api/admin/reports")
def admin_reports(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).latest_reports()


@router.post("/api/admin/reports/{report_id}/identity-access")
def reveal_report_identity(
    report_id: int,
    request: IdentityAccessRequest,
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        identity = ContactRequestService(db, get_settings()).reveal_identity(user, report_id, request.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(identity, headers={"Cache-Control": "no-store"})


@router.get("/api/admin/contact-requests")
def admin_contact_requests(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ContactRequestService(db, get_settings()).queue()


@router.post("/api/admin/contact-requests/{report_id}/claim")
def claim_contact_request(report_id: int, user: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        request = ContactRequestService(db, get_settings()).claim(user, report_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"reportId": report_id, "status": request.status}


@router.post("/api/admin/contact-requests/{report_id}/resolve")
def resolve_contact_request(
    report_id: int,
    resolution: ContactResolutionRequest,
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        request = ContactRequestService(db, get_settings()).resolve(user, report_id, resolution.outcome, resolution.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"reportId": report_id, "status": request.status}


@router.get("/api/admin/contact-requests/{report_id}/actions")
def contact_request_actions(report_id: int, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        return ContactRequestService(db, get_settings()).actions(report_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/admin/reports/{report_id}/identity-access-audits")
def report_identity_access_audits(
    report_id: int,
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        return ContactRequestService(db, get_settings()).audits(report_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/admin/reports/{report_id}/reply")
def admin_report_reply(report_id: int, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        reply = SessionAccessService(db).reply_for_report(report_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "reportId": report_id,
        "reply": None if reply is None else {"id": reply.id, "content": reply.content, "createdAt": reply.created_at},
    }


@router.post("/api/admin/reports/{report_id}/reply")
def publish_counselor_reply(
    report_id: int,
    request: CounselorReplyRequest,
    user: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    try:
        reply = SessionAccessService(db).publish_reply(user, report_id, request.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": reply.id, "reportId": reply.report_id, "content": reply.content, "createdAt": reply.created_at}


@router.get("/api/admin/excel-records")
def admin_excel(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).excel_records()


@router.get("/api/admin/alerts")
def admin_alerts(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).alert_records()


@router.get("/api/admin/cases")
def admin_cases(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).risk_cases()


@router.get("/api/admin/cases/{case_id}/notes")
def admin_case_notes(case_id: int, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(403, "原始个案备注可能包含敏感信息，此接口暂不开放")


@router.get("/api/admin/tool-jobs")
def admin_tool_jobs(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(403, "工具诊断信息可能包含敏感内容，此接口暂不开放")


@router.get("/api/admin/dead-letters")
def admin_dead_letters(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(403, "原始失败载荷可能包含敏感内容，此接口暂不开放")


@router.get("/api/admin/agent-traces")
def admin_agent_traces(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(403, "原始 Agent trace 包含敏感内容，后台访问已关闭")


@router.get("/api/admin/tool-audits")
def admin_tool_audits(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    raise HTTPException(403, "原始工具审计可能包含敏感内容，此接口暂不开放")


@router.get("/api/admin/conversations/{session_id}")
def admin_conversation(session_id: str, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        return ReportService(db).conversation(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/admin/knowledge")
def ingest_knowledge(
    request: KnowledgeIngestRequest,
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    chunks = KnowledgeService(db, get_settings()).ingest(request.source, request.content)
    return KnowledgeIngestResponse(source=request.source, chunks=chunks)


@router.get("/api/admin/knowledge/status")
def knowledge_status(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return KnowledgeService(db, get_settings()).status()


@router.post("/api/admin/knowledge/rebuild-vector")
def rebuild_knowledge_vector(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        indexed = KnowledgeService(db, get_settings()).rebuild_vector_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"indexedChunks": indexed}


@router.post("/api/admin/knowledge/rebuild-bm25")
def rebuild_knowledge_bm25(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        indexed = KnowledgeService(db, get_settings()).rebuild_bm25_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"indexedChunks": indexed}


@router.post("/api/admin/knowledge/backup")
def backup_knowledge_vector(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        snapshot = KnowledgeService(db, get_settings()).backup_vector_index()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"snapshot": snapshot}


@router.post("/api/admin/knowledge/file")
async def ingest_file(
    _: Annotated[UserAccount, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
):
    chunks = KnowledgeService(db, get_settings()).ingest_file(file.filename or "uploaded-file", await file.read())
    return KnowledgeIngestResponse(source=file.filename or "uploaded-file", chunks=chunks)
