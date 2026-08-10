"""채널 ID 로 직접 메시지 덤프 (invite link 만료 우회용)."""
from __future__ import annotations
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))
except ImportError:
    pass

from telegram.client import create_telegram_client

KST = timezone(timedelta(hours=9))


def _serialize(m):
    posted = getattr(m, "date", None)
    if posted and posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    posted_kst = posted.astimezone(KST).isoformat() if posted else None
    text = getattr(m, "message", "") or getattr(m, "raw_text", "") or ""
    media = getattr(m, "media", None)
    return {
        "id": int(getattr(m, "id", 0) or 0),
        "date": posted_kst,
        "text": text,
        "views": int(getattr(m, "views", 0) or 0),
        "forwards": int(getattr(m, "forwards", 0) or 0),
        "reply_to_msg_id": getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None),
        "media": type(media).__name__ if media else None,
    }


async def main():
    channel_id = int(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    out = sys.argv[3] if len(sys.argv) > 3 else "telegram/dev/tr_father_latest.json"

    client = create_telegram_client()
    await client.start()
    try:
        # Try direct PeerChannel
        from telethon.tl.types import PeerChannel
        entity = await client.get_entity(PeerChannel(channel_id))
        messages = []
        async for msg in client.iter_messages(entity, limit=limit):
            messages.append(_serialize(msg))
        title = getattr(entity, "title", "")
        out_obj = {
            "channel_title": title,
            "channel_id": channel_id,
            "fetched_at": datetime.now(KST).isoformat(),
            "limit": limit,
            "count": len(messages),
            "messages": messages,
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(out_obj, f, ensure_ascii=False, indent=2)
        print(f"[OK] {title} -> {out} ({len(messages)} msgs)")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
