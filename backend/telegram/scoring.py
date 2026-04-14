from __future__ import annotations

import math
import re
from datetime import datetime, timezone


HIGH_SIGNAL_KEYWORDS = [
    "속보",
    "특징주",
    "강세",
    "상한가",
    "급등",
    "수주",
    "관련주",
    "테마",
    "호재",
    "돌파",
    "정책",
    "계약",
]

TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]{2,20}")
HASHTAG_PATTERN = re.compile(r"#([0-9A-Za-z가-힣_]{2,20})")

STOPWORDS = {
    "오늘",
    "관련",
    "시장",
    "체크",
    "정리",
    "가능성",
    "이슈",
    "오전",
    "오후",
    "현재",
    "주요",
    "매수",
    "매도",
    "기업",
    "종목",
}


def extract_keywords(text: str, matched_stocks: list[str] | None = None, max_keywords: int = 6) -> list[str]:
    matched = set(matched_stocks or [])
    keywords: list[str] = []

    def add_keyword(keyword: str) -> None:
        if not keyword or keyword in matched or keyword in keywords:
            return
        keywords.append(keyword)

    for hashtag in HASHTAG_PATTERN.findall(text):
        add_keyword(hashtag)
        if len(keywords) >= max_keywords:
            return keywords

    for keyword in HIGH_SIGNAL_KEYWORDS:
        if keyword in text:
            add_keyword(keyword)
            if len(keywords) >= max_keywords:
                return keywords

    for token in TOKEN_PATTERN.findall(text):
        if token in STOPWORDS or token in matched:
            continue
        if token.isdigit():
            continue
        add_keyword(token)
        if len(keywords) >= max_keywords:
            break

    return keywords


def score_signal_message(
    text: str,
    matched_stocks: list[str],
    posted_at: datetime,
    now: datetime,
    views: int = 0,
    forwards: int = 0,
) -> float:
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_minutes = max(0.0, (now - posted_at).total_seconds() / 60)
    recency_score = max(0.0, 1.0 - (age_minutes / 60.0))

    keyword_hits = sum(1 for keyword in HIGH_SIGNAL_KEYWORDS if keyword in text)
    keyword_score = min(0.18, keyword_hits * 0.06)
    stock_score = min(0.32, len(matched_stocks) * 0.12)
    views_score = min(0.12, math.log10(max(views, 1)) / 40 if views > 0 else 0.0)
    forwards_score = min(0.10, math.log10(max(forwards, 1)) / 15 if forwards > 0 else 0.0)
    text_penalty = 0.08 if len(text.strip()) < 12 else 0.0

    score = 0.2 + (recency_score * 0.28) + keyword_score + stock_score + views_score + forwards_score - text_penalty
    return round(max(0.0, min(score, 0.99)), 2)
