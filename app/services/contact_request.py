from __future__ import annotations

import re
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import ChatSession, ContactRequest, ContactRequestAction, IdentityAccessAudit, PsychologicalReport, SessionAccess, UserAccount
from app.services.privacy_modes import ConversationMode
from app.services.privacy import PrivacySanitizer


PHONE_PATTERN = re.compile(r"1[3-9][0-9]{9}\Z")
OPEN_STATUSES = ("PENDING", "CLAIMED")
FINAL_STATUSES = ("CONTACTED", "UNREACHABLE", "CLOSED")


class ContactRequestService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def _cipher(self) -> Fernet:
        if not self.settings.contact_phone_encryption_key:
            raise ValueError("联系功能尚未配置，请稍后再试")
        try:
            return Fernet(self.settings.contact_phone_encryption_key.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("联系功能的加密配置无效") from exc

    def _report(self, report_id: int) -> PsychologicalReport:
        report = (
            self.db.query(PsychologicalReport)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(PsychologicalReport.id == report_id, SessionAccess.mode == ConversationMode.ASSISTED.value)
            .first()
        )
        if report is None or report.risk_level != "HIGH":
            raise ValueError("该报告不支持主动联系")
        return report

    def _owned_report(self, student: UserAccount, report_id: int) -> PsychologicalReport:
        report = self._report(report_id)
        if report.user_id != student.id or "ROLE_ADMIN" in student.roles:
            raise ValueError("无权操作该报告")
        return report

    def submit(self, student: UserAccount, report_id: int, phone: str, consent: bool) -> ContactRequest:
        self._owned_report(student, report_id)
        if not consent:
            raise ValueError("请先确认主动联系说明")
        normalized = re.sub(r"[\s-]", "", phone)
        if not PHONE_PATTERN.fullmatch(normalized):
            raise ValueError("请填写有效的中国大陆手机号")
        encrypted = self._cipher().encrypt(normalized.encode("utf-8")).decode("ascii")
        request = self.db.query(ContactRequest).filter(ContactRequest.report_id == report_id).with_for_update().first()
        if request is None:
            request = ContactRequest(report_id=report_id, phone_ciphertext=encrypted, status="PENDING")
        else:
            if request.status == "CLAIMED":
                raise ValueError("工作人员已认领；如需更改号码，请先撤回后重新提交")
            if request.status in FINAL_STATUSES:
                raise ValueError("该联系请求已处理完毕；如需再次求助，请开启新对话")
            request.phone_ciphertext = encrypted
            request.status = "PENDING"
            request.updated_at = datetime.utcnow()
        self.db.add(request)
        self.db.flush()
        self._record(request, student, "SUBMITTED")
        self.db.commit()
        self.db.refresh(request)
        return request

    def cancel(self, student: UserAccount, report_id: int) -> ContactRequest:
        self._owned_report(student, report_id)
        request = self.db.query(ContactRequest).filter(ContactRequest.report_id == report_id).with_for_update().first()
        if request is None or request.status not in OPEN_STATUSES:
            raise ValueError("没有待处理的联系请求")
        request.status = "CANCELLED"
        request.phone_ciphertext = ""
        request.updated_at = datetime.utcnow()
        self._record(request, student, "CANCELLED")
        self.db.commit()
        return request

    def active_report_ids(self, report_ids: list[int]) -> set[int]:
        if not report_ids:
            return set()
        rows = (
            self.db.query(ContactRequest.report_id)
            .filter(ContactRequest.report_id.in_(report_ids), ContactRequest.status.in_(OPEN_STATUSES))
            .all()
        )
        return {row.report_id for row in rows}

    def reveal_identity(self, admin: UserAccount, report_id: int, reason: str) -> dict:
        if "ROLE_ADMIN" not in admin.roles:
            raise ValueError("无权核实身份")
        report = self._report(report_id)
        request = self.db.query(ContactRequest).filter(ContactRequest.report_id == report_id).with_for_update().first()
        if request is None or request.status not in OPEN_STATUSES or not request.phone_ciphertext:
            raise ValueError("学生尚未提交有效的联系请求")
        reason = reason.strip()
        if len(reason) < 6:
            raise ValueError("请填写具体的核实身份依据")
        student = self.db.get(UserAccount, report.user_id)
        if student is None:
            raise ValueError("学生账户不存在")
        phone = self._cipher().decrypt(request.phone_ciphertext.encode("ascii")).decode("utf-8")
        audit = IdentityAccessAudit(report_id=report_id, admin_id=admin.id, reason=reason)
        self.db.add(audit)
        self.db.commit()
        return {"username": student.username, "displayName": student.display_name, "phone": phone}

    def queue(self) -> list[dict]:
        rows = (
            self.db.query(ContactRequest, ChatSession.public_id)
            .join(PsychologicalReport, PsychologicalReport.id == ContactRequest.report_id)
            .join(ChatSession, ChatSession.id == PsychologicalReport.session_id)
            .join(SessionAccess, SessionAccess.session_id == PsychologicalReport.session_id)
            .filter(SessionAccess.mode == ConversationMode.ASSISTED.value)
            .order_by(ContactRequest.updated_at.desc())
            .limit(100).all()
        )
        return [
            {
                "reportId": request.report_id,
                "sessionId": session_id,
                "status": request.status,
                "requestedAt": request.requested_at,
                "updatedAt": request.updated_at,
            }
            for request, session_id in rows
        ]

    def claim(self, admin: UserAccount, report_id: int) -> ContactRequest:
        from sqlalchemy import update

        if "ROLE_ADMIN" not in admin.roles:
            raise ValueError("无权认领联系请求")
        self._report(report_id)
        changed = self.db.execute(
            update(ContactRequest)
            .where(ContactRequest.report_id == report_id, ContactRequest.status == "PENDING")
            .values(status="CLAIMED", updated_at=datetime.utcnow())
        ).rowcount
        if not changed:
            self.db.rollback()
            raise ValueError("该联系请求已被认领或已撤回")
        request = self.db.query(ContactRequest).filter(ContactRequest.report_id == report_id).first()
        self._record(request, admin, "CLAIMED")
        self.db.commit()
        return request

    def resolve(self, admin: UserAccount, report_id: int, outcome: str, note: str) -> ContactRequest:
        if "ROLE_ADMIN" not in admin.roles:
            raise ValueError("无权处理联系请求")
        if outcome not in FINAL_STATUSES or len(note.strip()) < 6:
            raise ValueError("请选择处理结果并填写具体处理说明")
        self._report(report_id)
        request = self.db.query(ContactRequest).filter(ContactRequest.report_id == report_id).with_for_update().first()
        if request is None or request.status != "CLAIMED":
            raise ValueError("只有已认领的联系请求可以结案")
        request.status = outcome
        request.updated_at = datetime.utcnow()
        request.phone_ciphertext = ""
        self._record(request, admin, outcome, PrivacySanitizer().sanitize(note.strip()))
        self.db.commit()
        return request

    def actions(self, report_id: int) -> list[dict]:
        self._report(report_id)
        rows = (
            self.db.query(ContactRequestAction, UserAccount.username, UserAccount.roles_csv)
            .join(ContactRequest, ContactRequest.id == ContactRequestAction.request_id)
            .join(UserAccount, UserAccount.id == ContactRequestAction.actor_id)
            .filter(ContactRequest.report_id == report_id)
            .order_by(ContactRequestAction.created_at.asc(), ContactRequestAction.id.asc()).all()
        )
        return [
            {"actor": username if "ROLE_ADMIN" in roles.split(",") else "学生", "action": item.action, "note": item.note, "createdAt": item.created_at}
            for item, username, roles in rows
        ]

    def _record(self, request: ContactRequest, actor: UserAccount, action: str, note: str = "") -> None:
        self.db.add(ContactRequestAction(request_id=request.id, actor_id=actor.id, action=action, note=note))

    def audit_count(self, report_id: int) -> int:
        return self.db.query(IdentityAccessAudit).filter(IdentityAccessAudit.report_id == report_id).count()

    def audits(self, report_id: int) -> list[dict]:
        self._report(report_id)
        rows = self.db.query(IdentityAccessAudit).filter(IdentityAccessAudit.report_id == report_id).order_by(IdentityAccessAudit.accessed_at.desc()).all()
        admin_ids = {row.admin_id for row in rows}
        usernames = {
            user.id: user.username
            for user in self.db.query(UserAccount).filter(UserAccount.id.in_(admin_ids)).all()
        } if admin_ids else {}
        return [
            {
                "adminId": row.admin_id,
                "adminUsername": usernames.get(row.admin_id, ""),
                "reason": row.reason,
                "accessedAt": row.accessed_at,
            }
            for row in rows
        ]
