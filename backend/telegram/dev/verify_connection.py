"""텔레그램 연결 검증 스크립트 — StringSession 기반으로 연결/인증/채널 접근을 테스트한다."""

from __future__ import annotations

import asyncio
import os
import sys

# backend 디렉터리를 sys.path에 추가하여 상대 import 없이 client.py를 사용
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from telethon import TelegramClient
from telethon.sessions import StringSession


async def verify() -> None:
    api_id = os.environ["TG_API_ID"]
    api_hash = os.environ["TG_API_HASH"]
    session_string = os.environ["TG_STRING_SESSION"]
    channel = os.getenv("TG_CHANNEL_USERNAME", "@faststocknews")

    print("=" * 50)
    print("텔레그램 연결 검증")
    print("=" * 50)

    client = TelegramClient(StringSession(session_string), int(api_id), api_hash)
    await client.connect()

    try:
        # 1) 인증 상태 확인
        authorized = await client.is_user_authorized()
        print(f"\n[1] 인증 상태: {'✅ 유효' if authorized else '❌ 유효하지 않음'}")
        if not authorized:
            print("    → StringSession이 만료되었거나 잘못되었습니다.")
            print("    → bootstrap.py로 세션을 다시 발급하세요.")
            return

        # 2) 현재 사용자 정보
        me = await client.get_me()
        print(f"[2] 로그인 사용자: {me.first_name} {me.last_name or ''} (id={me.id}, phone={me.phone})")

        # 3) 채널 접근 테스트
        print(f"\n[3] 채널 접근 테스트: {channel}")
        try:
            entity = await client.get_entity(channel)
            title = getattr(entity, "title", str(entity.id))
            print(f"    ✅ 채널 접속 성공: {title}")
        except Exception as e:
            print(f"    ❌ 채널 접근 실패: {e}")
            return

        # 4) 최근 메시지 샘플 가져오기
        print(f"\n[4] 최근 메시지 3건 미리보기:")
        count = 0
        async for msg in client.iter_messages(entity, limit=3):
            count += 1
            text_preview = (msg.message or "(미디어 전용)")[:80].replace("\n", " ")
            posted = msg.date.strftime("%Y-%m-%d %H:%M:%S UTC") if msg.date else "N/A"
            views = getattr(msg, "views", 0) or 0
            print(f"    #{msg.id} | {posted} | 조회 {views:,}")
            print(f"      {text_preview}")
        if count == 0:
            print("    (메시지를 찾을 수 없습니다)")

        print("\n" + "=" * 50)
        print("✅ 텔레그램 모듈 연결 검증 완료 — 정상 동작 확인")
        print("=" * 50)

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(verify())
