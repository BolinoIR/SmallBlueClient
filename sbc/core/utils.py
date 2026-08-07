from __future__ import annotations
from urllib.parse import urlparse

def public_chat_id(meeting_id: str | None) -> str:
    """BBB's public chat id is normally the meeting id; session snapshots win."""
    return meeting_id or "MAIN-PUBLIC-GROUP-CHAT"

def server_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url
