from enum import Enum


NOTICE_VERSION = "2026-10-08-v1"


class ConversationMode(str, Enum):
    ASSISTED = "ASSISTED"
    PRIVATE = "PRIVATE"


def review_status(report_count: int, reply_count: int, unread_count: int) -> str:
    if unread_count:
        return "UNREAD"
    if report_count > reply_count:
        return "WAITING"
    if reply_count:
        return "READ"
    return "NONE"
