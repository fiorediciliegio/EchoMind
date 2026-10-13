from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import AlertRecord, AgentRunTrace, CaseNote, ChatMessage, ChatSession, ContactRequest, DeadLetterRecord, ExcelRecord, PsychologicalReport, RiskCase, SessionAccess, ToolAuditRecord, ToolJob, UserAccount
from app.schemas.dtos import AgentRunTraceResponse, CaseNoteResponse, ConversationMessageResponse, ConversationResponse, DeadLetterResponse, ReportResponse, RiskCaseResponse, ToolAuditResponse, ToolJobResponse, ToolRecordResponse
from app.services.privacy import PrivacySanitizer
from app.services.privacy_modes import ConversationMode


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def latest_reports(self, user_id: int | None = None) -> list[ReportResponse]:
        query = self.db.query(PsychologicalReport).order_by(PsychologicalReport.created_at.desc())
        if user_id is not None:
            query = query.filter(PsychologicalReport.user_id == user_id)
        else:
            query = query.join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id).filter(SessionAccess.mode == ConversationMode.ASSISTED.value)
        reports = query.limit(100).all()
        ids = [item.id for item in reports]
        active_ids = {
            row.report_id for row in self.db.query(ContactRequest.report_id)
            .filter(ContactRequest.report_id.in_(ids), ContactRequest.status.in_(["PENDING", "CLAIMED"])).all()
        } if ids else set()
        return [self._report_response(item, staff_view=user_id is None, contact_requested=item.id in active_ids) for item in reports]

    def excel_records(self) -> list[ToolRecordResponse]:
        rows = (
            self.db.query(ExcelRecord)
            .join(PsychologicalReport, PsychologicalReport.id == ExcelRecord.report_id)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(SessionAccess.mode == ConversationMode.ASSISTED.value)
            .order_by(ExcelRecord.created_at.desc()).limit(100).all()
        )
        return [
            ToolRecordResponse(id=row.id, reportId=row.report_id, status=row.status, message=row.message, createdAt=row.created_at, filePath=row.file_path)
            for row in rows
        ]

    def alert_records(self) -> list[ToolRecordResponse]:
        rows = (
            self.db.query(AlertRecord)
            .join(PsychologicalReport, PsychologicalReport.id == AlertRecord.report_id)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(SessionAccess.mode == ConversationMode.ASSISTED.value)
            .order_by(AlertRecord.created_at.desc()).limit(100).all()
        )
        return [
            ToolRecordResponse(
                id=row.id,
                reportId=row.report_id,
                status=row.status,
                message=row.message,
                createdAt=row.created_at,
                channel=row.channel,
                recipient=row.recipient,
            )
            for row in rows
        ]

    def risk_cases(self) -> list[RiskCaseResponse]:
        rows = (
            self.db.query(RiskCase)
            .join(PsychologicalReport, PsychologicalReport.id == RiskCase.report_id)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(SessionAccess.mode == ConversationMode.ASSISTED.value)
            .order_by(RiskCase.updated_at.desc()).limit(100).all()
        )
        return [
            RiskCaseResponse(
                id=row.id,
                reportId=row.report_id,
                riskLevel=row.risk_level,
                status=row.status,
                owner=row.owner,
                summary=self._staff_text(row.summary, row.report_id),
                handoffSummary=self._staff_text(row.handoff_summary, row.report_id),
                acknowledgedBy=row.acknowledged_by,
                acknowledgedAt=row.acknowledged_at,
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
        ]

    def _staff_text(self, text: str, report_id: int) -> str:
        report = self.db.get(PsychologicalReport, report_id)
        user = self.db.get(UserAccount, report.user_id) if report else None
        return PrivacySanitizer().for_staff(text, user)

    def case_notes(self, case_id: int) -> list[CaseNoteResponse]:
        rows = self.db.query(CaseNote).filter(CaseNote.case_id == case_id).order_by(CaseNote.created_at.asc()).all()
        return [
            CaseNoteResponse(id=row.id, caseId=row.case_id, actor=row.actor, note=row.note, createdAt=row.created_at)
            for row in rows
        ]

    def tool_jobs(self) -> list[ToolJobResponse]:
        rows = self.db.query(ToolJob).order_by(ToolJob.created_at.desc()).limit(100).all()
        return [
            ToolJobResponse(
                id=row.id,
                reportId=row.report_id,
                kind=row.kind,
                status=row.status,
                attempts=row.attempts,
                maxAttempts=row.max_attempts,
                dependsOnJobId=row.depends_on_job_id,
                runAfter=row.run_after,
                lastError=row.last_error,
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
        ]

    def dead_letters(self) -> list[DeadLetterResponse]:
        rows = self.db.query(DeadLetterRecord).order_by(DeadLetterRecord.created_at.desc()).limit(100).all()
        return [
            DeadLetterResponse(
                id=row.id,
                jobId=row.job_id,
                reportId=row.report_id,
                kind=row.kind,
                reason=row.reason,
                payload=row.payload,
                createdAt=row.created_at,
            )
            for row in rows
        ]


    def agent_run_traces(self) -> list[AgentRunTraceResponse]:
        rows = self.db.query(AgentRunTrace).order_by(AgentRunTrace.created_at.desc()).limit(100).all()
        responses = []
        for row in rows:
            user = self.db.get(UserAccount, row.user_id)
            session = self.db.get(ChatSession, row.session_id)
            responses.append(
                AgentRunTraceResponse(
                    id=row.id,
                    sessionId=session.public_id if session else "",
                    reportId=row.report_id,
                    username=user.username if user else "",
                    intent=row.intent,
                    riskLevel=row.risk_level,
                    originalInput=row.original_input,
                    sanitizedInput=row.sanitized_input,
                    memoryBrief=row.memory_brief,
                    agentSteps=_loads(row.agent_steps_json, []),
                    retrievedKnowledge=_loads(row.retrieved_knowledge_json, []),
                    responseMessages=_loads(row.response_messages_json, []),
                    assessment=_loads(row.assessment_json, {}),
                    createdAt=row.created_at,
                )
            )
        return responses

    def tool_audits(self) -> list[ToolAuditResponse]:
        rows = self.db.query(ToolAuditRecord).order_by(ToolAuditRecord.created_at.desc()).limit(100).all()
        return [
            ToolAuditResponse(
                id=row.id,
                jobId=row.job_id,
                reportId=row.report_id,
                toolName=row.tool_name,
                policy=row.policy,
                allowed=row.allowed,
                status=row.status,
                reason=row.reason,
                payload=_loads(row.payload, {}),
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
        ]

    def conversation(self, public_id: str) -> ConversationResponse:
        session = (
            self.db.query(ChatSession)
            .join(SessionAccess, SessionAccess.session_id == ChatSession.id)
            .filter(ChatSession.public_id == public_id, SessionAccess.mode == ConversationMode.ASSISTED.value)
            .first()
        )
        if session is None:
            raise ValueError("Session not found")
        rows = self.db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc()).all()
        user = self.db.get(UserAccount, session.user_id)
        sanitizer = PrivacySanitizer()
        return ConversationResponse(
            sessionId=session.public_id,
            title=sanitizer.for_staff(session.title, user),
            messages=[ConversationMessageResponse(role=row.role, content=sanitizer.for_staff(row.content, user), createdAt=row.created_at) for row in rows],
        )

    def _report_response(self, report: PsychologicalReport, *, staff_view: bool = False, contact_requested: bool = False) -> ReportResponse:
        user = self.db.get(UserAccount, report.user_id)
        session = self.db.get(ChatSession, report.session_id)
        sanitizer = PrivacySanitizer()
        return ReportResponse(
            id=report.id,
            sessionId=session.public_id if session else "",
            username="" if staff_view else (user.username if user else ""),
            displayName=f"报告 #{report.id}" if staff_view else (user.display_name if user else ""),
            content=sanitizer.for_staff(report.content, user) if staff_view else report.content,
            intent=report.intent,
            emotion=report.emotion,
            emotionScore=report.emotion_score,
            riskLevel=report.risk_level,
            confidence=report.confidence,
            summary=sanitizer.for_staff(report.summary, user) if staff_view else report.summary,
            createdAt=report.created_at,
            contactRequested=contact_requested,
        )


def _loads(raw: str, default):
    import json

    try:
        return json.loads(raw or "")
    except Exception:
        return default
