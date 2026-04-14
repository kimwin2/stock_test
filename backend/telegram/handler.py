from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .client import get_channel_username
from .collector import collect_telegram_signals
from .store import load_telegram_state, save_telegram_signal_payload, save_telegram_state


KST = timezone(timedelta(hours=9))


def _is_collection_window(now: datetime | None = None) -> bool:
    current = (now or datetime.now(KST)).astimezone(KST)
    if current.weekday() >= 5:
        return False
    if 8 <= current.hour < 18:
        return True
    return current.hour == 18 and current.minute == 0


def lambda_handler(event, context):
    now = datetime.now(KST)
    channel = get_channel_username()
    print("=" * 60)
    print(">>> Telegram Signal Collector Start")
    print(f"    시각: {now.isoformat()}")
    print(f"    채널: {channel}")
    print(f"    이벤트: {json.dumps(event or {}, ensure_ascii=False, default=str)[:200]}")
    print("=" * 60)

    if not _is_collection_window(now):
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "수집 시간대가 아니어서 건너뜁니다.",
                    "channel": channel,
                    "collectedAt": now.isoformat(),
                },
                ensure_ascii=False,
            ),
        }

    state = load_telegram_state(channel=channel, prefer_local=False)
    last_message_id = int(state.get("lastMessageId", 0) or 0)

    try:
        payload, new_state = collect_telegram_signals(
            channel_username=channel,
            last_message_id=last_message_id,
        )

        signal_uri = save_telegram_signal_payload(payload)
        state_uri = save_telegram_state(new_state)
        item_count = len(payload.get("items", []))

        print(f"[OK] 텔레그램 시그널 {item_count}건 저장 완료")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Telegram signal collection succeeded",
                    "channel": channel,
                    "items": item_count,
                    "signalStore": signal_uri,
                    "stateStore": state_uri,
                    "lastMessageId": payload.get("lastMessageId", 0),
                    "collectedAt": payload.get("collectedAt", now.isoformat()),
                },
                ensure_ascii=False,
            ),
        }

    except Exception as e:
        failed_state = dict(state)
        failed_state["channel"] = channel
        failed_state["lastCollectedAt"] = now.isoformat()
        failed_state["consecutiveFailures"] = int(failed_state.get("consecutiveFailures", 0) or 0) + 1
        save_telegram_state(failed_state)
        raise RuntimeError(f"텔레그램 시그널 수집 실패: {e}") from e
