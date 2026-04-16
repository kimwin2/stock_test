"""collector 모듈을 실행하여 수집된 메시지 전체를 JSON으로 출력한다."""

from __future__ import annotations

import json
import os
import sys

# backend 디렉터리를 path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",".."))

from telegram.collector import collect_telegram_signals

def main() -> None:
    # lookback=960분(16시간) — 오늘 장전 08:00부터 현재(23:05)까지 전부 수집
    # max_items=200, min_score=0.0  → 필터 없이 전부 가져오기
    payload, state = collect_telegram_signals(
        last_message_id=0,
        lookback_minutes=960,
        max_items=200,
        min_score=0.0,
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n--- STATE ---")
    print(json.dumps(state, ensure_ascii=False, indent=2))

    items = payload.get("items", [])
    print(f"\n총 수집 건수: {len(items)}")


if __name__ == "__main__":
    main()
