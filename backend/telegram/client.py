from __future__ import annotations

import os
from typing import Any

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:  # Optional until telegram collector is enabled
    TelegramClient = None
    StringSession = None


def _require_telethon() -> None:
    if TelegramClient is None or StringSession is None:
        raise RuntimeError(
            "telethon 패키지가 설치되지 않았습니다. "
            "backend/requirements.txt 설치 후 다시 시도하세요."
        )


def create_telegram_client(
    api_id: str | int | None = None,
    api_hash: str | None = None,
    session_string: str | None = None,
) -> Any:
    _require_telethon()

    resolved_api_id = api_id or os.getenv("TG_API_ID", "").strip()
    resolved_api_hash = api_hash or os.getenv("TG_API_HASH", "").strip()
    resolved_session = session_string or os.getenv("TG_STRING_SESSION", "").strip()

    if not resolved_api_id or not resolved_api_hash or not resolved_session:
        raise ValueError("TG_API_ID, TG_API_HASH, TG_STRING_SESSION 환경변수가 모두 필요합니다.")

    return TelegramClient(
        StringSession(str(resolved_session)),
        int(resolved_api_id),
        str(resolved_api_hash),
    )


def get_channel_username(default: str = "@faststocknews") -> str:
    return os.getenv("TG_CHANNEL_USERNAME", default).strip() or default
