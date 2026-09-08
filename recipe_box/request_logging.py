from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_QUERY_PARAMETERS = frozenset({"token", "code", "password", "new_password"})


def sanitize_request_target(target: str) -> str:
    """Redact sensitive query values while preserving safe request metadata."""
    parsed = urlsplit(target)
    if not parsed.query:
        return target
    safe_query = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        safe_query.append((name, "<redacted>" if name.lower() in SENSITIVE_QUERY_PARAMETERS else value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_query), parsed.fragment))
