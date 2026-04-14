from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

from .client import create_telegram_client, get_channel_username
from .models import TelegramSignalItem, TelegramSignalPayload, TelegramState
from .scoring import extract_keywords, score_signal_message

try:
    from stock_data import STOCK_CODE_MAP
except ModuleNotFoundError:
    STOCK_CODE_MAP = {}


KST = timezone(timedelta(hours=9))
WHITESPACE_RE = re.compile(r"\s+")
NOISE_PATTERNS = [
    "광고",
    "유료",
    "문의",
    "오픈채팅",
]


def _normalize_text(*parts: str) -> str:
    joined = " ".join(part for part in parts if part)
    joined = joined.replace("\u200b", " ").replace("\xa0", " ")
    return WHITESPACE_RE.sub(" ", joined).strip()


def _build_message_url(channel_username: str, message_id: int) -> str:
    clean = channel_username.lstrip("@")
    return f"https://t.me/{clean}/{message_id}"


def _match_stocks(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    matches: list[str] = []
    for stock in sorted(set(STOCK_CODE_MAP.keys()), key=len, reverse=True):
        if len(stock) < 2:
            continue
        if stock.replace(" ", "") in compact and stock not in matches:
            matches.append(stock)
    return matches[:10]


def _should_skip_message(text: str) -> bool:
    if not text or len(text.strip()) < 8:
        return True
    return any(pattern in text for pattern in NOISE_PATTERNS)


async def _collect_signals_async(
    channel_username: str,
    last_message_id: int,
    lookback_minutes: int,
    max_items: int,
    min_score: float,
) -> tuple[dict, dict]:
    now = datetime.now(KST)
    cutoff = now - timedelta(minutes=lookback_minutes)
    client = create_telegram_client()
    items: list[dict] = []
    max_seen_message_id = last_message_id

    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("텔레그램 세션이 유효하지 않습니다. bootstrap.py로 세션을 다시 발급하세요.")

        channel = await client.get_entity(channel_username)
        fetch_limit = max(max_items * 5, 50)

        async for message in client.iter_messages(channel, limit=fetch_limit):
            if message is None or getattr(message, "id", None) is None:
                continue

            message_id = int(message.id)
            max_seen_message_id = max(max_seen_message_id, message_id)

            if last_message_id and message_id <= last_message_id:
                break

            posted_at = getattr(message, "date", None)
            if posted_at is None:
                continue

            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            posted_at_kst = posted_at.astimezone(KST)
            if posted_at_kst < cutoff:
                break

            text = _normalize_text(
                getattr(message, "message", "") or getattr(message, "raw_text", "") or ""
            )
            if _should_skip_message(text):
                continue

            matched_stocks = _match_stocks(text)
            keywords = extract_keywords(text, matched_stocks=matched_stocks)
            views = int(getattr(message, "views", 0) or 0)
            forwards = int(getattr(message, "forwards", 0) or 0)
            score = score_signal_message(
                text=text,
                matched_stocks=matched_stocks,
                posted_at=posted_at_kst,
                now=now,
                views=views,
                forwards=forwards,
            )

            if score < min_score:
                continue

            items.append(
                TelegramSignalItem(
                    message_id=message_id,
                    posted_at=posted_at_kst.isoformat(),
                    text=text,
                    views=views,
                    forwards=forwards,
                    url=_build_message_url(channel_username, message_id),
                    matched_stocks=matched_stocks,
                    keywords=keywords,
                    score=score,
                ).to_dict()
            )
    finally:
        await client.disconnect()

    ranked = sorted(items, key=lambda item: (item.get("score", 0.0), item.get("messageId", 0)), reverse=True)[:max_items]
    ranked.sort(key=lambda item: item.get("messageId", 0))

    payload = TelegramSignalPayload(
        channel=channel_username,
        collected_at=now.isoformat(),
        window_minutes=lookback_minutes,
        last_message_id=max_seen_message_id,
        items=[],
    ).to_dict()
    payload["items"] = ranked

    state = TelegramState(
        channel=channel_username,
        last_message_id=max_seen_message_id,
        last_collected_at=now.isoformat(),
        last_success_at=now.isoformat(),
        consecutive_failures=0,
    ).to_dict()

    return payload, state


def collect_telegram_signals(
    channel_username: str | None = None,
    last_message_id: int = 0,
    lookback_minutes: int | None = None,
    max_items: int | None = None,
    min_score: float | None = None,
) -> tuple[dict, dict]:
    resolved_channel = channel_username or get_channel_username()
    resolved_lookback = lookback_minutes if lookback_minutes is not None else int(os.getenv("TG_LOOKBACK_MINUTES", "60"))
    resolved_max_items = max_items if max_items is not None else int(os.getenv("TG_MAX_ITEMS", "20"))
    resolved_min_score = min_score if min_score is not None else float(os.getenv("TG_MIN_SCORE", "0.35"))

    return asyncio.run(
        _collect_signals_async(
            channel_username=resolved_channel,
            last_message_id=last_message_id,
            lookback_minutes=resolved_lookback,
            max_items=resolved_max_items,
            min_score=resolved_min_score,
        )
    )
