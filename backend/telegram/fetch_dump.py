"""
지정한 채널(공개 username 또는 invite link)의 최근 메시지를 raw JSON으로 덤프합니다.

사용법:
    cd backend
    python -m telegram.fetch_dump --channel "https://t.me/+Y_Q2Vze1STlhN2Zl" --limit 100 --out telegram/dev/oscillation_raw.json

전제:
    .env 에 TG_API_ID, TG_API_HASH, TG_STRING_SESSION 이 채워져 있어야 합니다.
    비공개 invite link 의 경우 본인 계정이 미리 채널에 가입되어 있거나,
    이 스크립트가 자동으로 ImportChatInviteRequest 를 통해 가입을 시도합니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
    from telethon.errors import (
        UserAlreadyParticipantError,
        InviteHashExpiredError,
        InviteHashInvalidError,
    )
    from telethon.tl.types import ChatInviteAlready
except ImportError:
    print("[X] telethon 패키지가 설치돼 있지 않습니다. 'pip install -r requirements.txt' 후 재시도하세요.")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

from .client import create_telegram_client


KST = timezone(timedelta(hours=9))
INVITE_RE = re.compile(r"(?:https?://)?t\.me/(?:joinchat/|\+)([A-Za-z0-9_-]+)")


def _parse_target(channel: str) -> tuple[str, str | None]:
    m = INVITE_RE.search(channel.strip())
    if m:
        return "invite", m.group(1)
    return "username", channel.strip().lstrip("@") or None


async def _resolve_entity(client, kind: str, value: str | None):
    if kind == "username":
        return await client.get_entity(value)

    try:
        result = await client(CheckChatInviteRequest(value))
    except (InviteHashExpiredError, InviteHashInvalidError) as e:
        raise RuntimeError(f"invite hash 가 유효하지 않습니다: {e}") from e

    if isinstance(result, ChatInviteAlready):
        return result.chat

    try:
        update = await client(ImportChatInviteRequest(value))
        return update.chats[0]
    except UserAlreadyParticipantError:
        return await client.get_entity(value)


def _serialize_message(message: Any) -> dict:
    posted_at = getattr(message, "date", None)
    if posted_at and posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    posted_at_kst = posted_at.astimezone(KST).isoformat() if posted_at else None

    text = getattr(message, "message", "") or getattr(message, "raw_text", "") or ""

    media_kind = None
    media = getattr(message, "media", None)
    if media is not None:
        media_kind = type(media).__name__

    return {
        "id": int(getattr(message, "id", 0) or 0),
        "date": posted_at_kst,
        "text": text,
        "views": int(getattr(message, "views", 0) or 0),
        "forwards": int(getattr(message, "forwards", 0) or 0),
        "reply_to_msg_id": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
        "media": media_kind,
        "edit_date": (
            getattr(message, "edit_date").astimezone(KST).isoformat()
            if getattr(message, "edit_date", None) else None
        ),
    }


async def _dump(channel: str, limit: int, out_path: str) -> dict:
    kind, value = _parse_target(channel)
    if not value:
        raise ValueError(f"채널 식별자를 파싱하지 못했습니다: {channel}")

    client = create_telegram_client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("텔레그램 세션이 유효하지 않습니다. 'python -m telegram.bootstrap' 으로 세션을 발급하세요.")

        entity = await _resolve_entity(client, kind, value)
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(getattr(entity, "id", ""))
        print(f"[OK] 채널 접근 성공: {title}")

        messages: list[dict] = []
        async for message in client.iter_messages(entity, limit=limit):
            if message is None:
                continue
            messages.append(_serialize_message(message))

        payload = {
            "channel_input": channel,
            "channel_title": title,
            "channel_id": int(getattr(entity, "id", 0) or 0),
            "fetched_at": datetime.now(KST).isoformat(),
            "limit": limit,
            "count": len(messages),
            "messages": messages,
        }

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"[OK] {len(messages)}개 메시지를 {out_path} 에 저장했습니다.")
        return payload
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="텔레그램 채널 메시지 덤프")
    parser.add_argument("--channel", required=True, help="@username 또는 https://t.me/+invite_hash")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "dev", "channel_dump.json"),
    )
    args = parser.parse_args()

    asyncio.run(_dump(args.channel, args.limit, args.out))


if __name__ == "__main__":
    main()
