from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import ChatMessage, ChatSession, ContactRequest, CounselorReply, PsychologicalReport, SessionAccess, UserAccount
from app.services.privacy_modes import ConversationMode, NOTICE_VERSION, review_status


class SessionAccessService:
    def __init__(self, db: Session):
        self.db = db

    def start_assisted(self, user: UserAccount, notice_version: str) -> ChatSession:
        if notice_version != NOTICE_VERSION:
            raise ValueError("请先阅读当前版本的隐私告知")
        session = ChatSession(public_id=uuid.uuid4().hex, user_id=user.id, title="新会话")
        self.db.add(session)
        self.db.flush()
        self.db.add(SessionAccess(session_id=session.id, mode=ConversationMode.ASSISTED.value, notice_version=notice_version))
        self.db.commit()
        self.db.refresh(session)
        return session

    def require_assisted(self, user: UserAccount, public_id: str) -> ChatSession:
        session = (
            self.db.query(ChatSession)
            .join(SessionAccess, SessionAccess.session_id == ChatSession.id)
            .filter(
                ChatSession.public_id == public_id,
                ChatSession.user_id == user.id,
                SessionAccess.mode == ConversationMode.ASSISTED.value,
            )
            .first()
        )
        if session is None:
            raise ValueError("会话不存在或无权访问")
        return session

    def list_assisted(self, user: UserAccount) -> list[dict]:
        sessions = (
            self.db.query(ChatSession)
            .join(SessionAccess, SessionAccess.session_id == ChatSession.id)
            .filter(
                ChatSession.user_id == user.id,
                SessionAccess.mode == ConversationMode.ASSISTED.value,
                ChatSession.messages.any(ChatMessage.role == "USER"),
            )
            .order_by(ChatSession.updated_at.desc())
            .all()
        )
        return [self._summary(session) for session in sessions]

    def detail(self, user: UserAccount, public_id: str) -> dict:
        session = self.require_assisted(user, public_id)
        messages = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )
        reports = self.db.query(PsychologicalReport).filter(PsychologicalReport.session_id == session.id).all()
        report_ids = [report.id for report in reports]
        contact_requests = (
            self.db.query(ContactRequest).filter(ContactRequest.report_id.in_(report_ids)).all()
            if report_ids else []
        )
        request_status = {request.report_id: request.status for request in contact_requests}
        replies = (
            self.db.query(CounselorReply).filter(CounselorReply.report_id.in_(report_ids)).order_by(CounselorReply.created_at.asc()).all()
            if report_ids else []
        )
        result = {
            **self._summary(session),
            "messages": [{"role": row.role, "content": row.content, "createdAt": row.created_at.isoformat()} for row in messages],
            "replies": [{"id": row.id, "reportId": row.report_id, "content": row.content, "createdAt": row.created_at.isoformat(), "wasUnread": row.read_at is None} for row in replies],
            "contactOffers": [
                {"reportId": report.id, "status": request_status.get(report.id, "NONE")}
                for report in reports if report.risk_level == "HIGH"
            ],
        }
        unread = [row for row in replies if row.read_at is None]
        if unread:
            for row in unread:
                row.read_at = datetime.utcnow()
            self.db.commit()
        return result

    def publish_reply(self, author: UserAccount, report_id: int, content: str) -> CounselorReply:
        self._require_assisted_report(report_id)
        if self.db.query(CounselorReply).filter(CounselorReply.report_id == report_id).first():
            raise ValueError("这份报告已有回复")
        reply = CounselorReply(report_id=report_id, author_id=author.id, content=content.strip())
        if not reply.content:
            raise ValueError("回复不能为空")
        self.db.add(reply)
        self.db.commit()
        self.db.refresh(reply)
        return reply

    def reply_for_report(self, report_id: int) -> CounselorReply | None:
        self._require_assisted_report(report_id)
        return self.db.query(CounselorReply).filter(CounselorReply.report_id == report_id).first()

    def _require_assisted_report(self, report_id: int) -> PsychologicalReport:
        report = (
            self.db.query(PsychologicalReport)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(PsychologicalReport.id == report_id, SessionAccess.mode == ConversationMode.ASSISTED.value)
            .first()
        )
        if report is None:
            raise ValueError("报告不存在或未授权人工跟进")
        return report

    def _summary(self, session: ChatSession) -> dict:
        reports = self.db.query(PsychologicalReport.id).filter(PsychologicalReport.session_id == session.id).all()
        report_ids = [row.id for row in reports]
        replies = self.db.query(CounselorReply).filter(CounselorReply.report_id.in_(report_ids)).all() if report_ids else []
        return {
            "sessionId": session.public_id,
            "title": session.title,
            "updatedAt": session.updated_at.isoformat(),
            "reviewStatus": review_status(len(reports), len(replies), sum(row.read_at is None for row in replies)),
        }
