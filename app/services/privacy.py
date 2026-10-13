import re


class PrivacySanitizer:
    patterns = [
        re.compile(r"1[3-9]\d{9}"),
        re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        re.compile(r"\b\d{17}[\dXx]\b"),
    ]

    def sanitize(self, text: str) -> str:
        sanitized = text or ""
        for pattern in self.patterns:
            sanitized = pattern.sub("[已脱敏]", sanitized)
        return sanitized

    def for_staff(self, text: str, user=None) -> str:
        sanitized = self.sanitize(text)
        if user is not None:
            for identifier in (getattr(user, "display_name", ""), getattr(user, "username", "")):
                if identifier and len(identifier.strip()) > 2:
                    sanitized = sanitized.replace(identifier, "[学生]")
        return sanitized

