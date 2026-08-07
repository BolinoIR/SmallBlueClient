from __future__ import annotations
from urllib.parse import urlparse

PUBLIC_CHAT_ID = "MAIN-PUBLIC-GROUP-CHAT"


def public_chat_id(meeting_id: str | None = None) -> str:
    """Return BBB HTML5's source-defined public-chat group identifier.

    ``meetingId`` is used for routing by the backend, but it is *not* the
    ``chatId`` accepted by ``chatSendMessage``. BBB's HTML5 default settings
    define ``chat.public_group_id`` as ``MAIN-PUBLIC-GROUP-CHAT``.
    """
    del meeting_id  # Kept for the existing public helper signature.
    return PUBLIC_CHAT_ID

def server_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url
