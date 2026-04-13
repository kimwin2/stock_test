from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import re


@dataclass
class ThemeEngineConfig:
    min_rising: int = 2
    min_confirmed: int = 3
    strong_change_rate: float = 12.0
    base_score_per_riser: int = 12
    strong_bonus: int = 15
    news_bonus: int = 10
    max_news_hits: int = 3


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", (name or "")).lower()


def _collect_theme_candidates(mover_signals: list[dict], theme_universe: dict[str, list[str]]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}

    for signal in mover_signals:
        stock_name = signal.get("stock_name", "")
        suggested = signal.get("suggested_themes", []) or []

        matched = set(suggested)
        for theme_name, stocks in theme_universe.items():
            if stock_name in stocks:
                matched.add(theme_name)

        for theme_name in matched:
            buckets.setdefault(theme_name, []).append(signal)

    return buckets


def _find_theme_news_hits(theme_name: str, articles: list[dict], stocks: Iterable[str]) -> list[dict]:
    keywords = [theme_name, *stocks]
    hits = []
    for article in articles:
        title = article.get("title", "")
        summary = article.get("summary", "")
        haystack = f"{title} {summary}"
        if any(keyword and keyword in haystack for keyword in keywords):
            hits.append({
                "title": title,
                "url": article.get("url", ""),
            })
        if len(hits) >= 5:
            break
    return hits


def detect_theme_signals(
    mover_signals: list[dict],
    articles: list[dict],
    theme_universe: dict[str, list[str]],
    config: ThemeEngineConfig | None = None,
) -> list[dict]:
    config = config or ThemeEngineConfig()
    buckets = _collect_theme_candidates(mover_signals, theme_universe)
    results: list[dict] = []

    for theme_name, signals in buckets.items():
        if not signals:
            continue

        ordered = sorted(signals, key=lambda item: item.get("change_rate", 0.0), reverse=True)
        leader = ordered[0]
        rising_count = sum(1 for item in ordered if item.get("change_rate", 0.0) >= 3.0)
        strong_count = sum(1 for item in ordered if item.get("change_rate", 0.0) >= config.strong_change_rate)
        followers = [item for item in ordered[1:] if item.get("change_rate", 0.0) >= 2.0]
        stocks = [item.get("stock_name", "") for item in ordered if item.get("stock_name", "")]

        news_hits = _find_theme_news_hits(theme_name, articles, stocks)
        news_score = min(len(news_hits), config.max_news_hits) * config.news_bonus

        score = (
            rising_count * config.base_score_per_riser
            + strong_count * config.strong_bonus
            + news_score
        )

        if rising_count >= config.min_confirmed and score >= 70:
            state = "THEME_CONFIRMED"
        elif rising_count >= config.min_rising and score >= 40:
            state = "THEME_FORMING"
        else:
            state = "LEADER_DETECTED"

        results.append({
            "themeName": theme_name,
            "state": state,
            "score": score,
            "leader": {
                "name": leader.get("stock_name", ""),
                "changeRate": leader.get("change_rate", 0.0),
                "status": leader.get("status", ""),
            },
            "followers": [
                {
                    "name": item.get("stock_name", ""),
                    "changeRate": item.get("change_rate", 0.0),
                    "status": item.get("status", ""),
                }
                for item in followers[:5]
            ],
            "evidence": {
                "risingCount": rising_count,
                "strongCount": strong_count,
                "newsHits": news_hits[:3],
            },
        })

    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    return results


def merge_theme_signals_into_themes(themes: list[dict], theme_signals: list[dict]) -> None:
    normalized_signals = {
        _normalize_name(signal.get("themeName", "")): signal
        for signal in theme_signals
    }

    for theme in themes:
        key = _normalize_name(theme.get("themeName", ""))
        signal = normalized_signals.get(key)
        if not signal:
            continue
        theme["signal"] = {
            "state": signal.get("state"),
            "score": signal.get("score"),
            "leader": signal.get("leader"),
            "followers": signal.get("followers", []),
            "source": signal.get("source"),
        }


def build_fallback_signals_from_themes(themes: list[dict]) -> list[dict]:
    results: list[dict] = []
    for theme in themes:
        theme_name = theme.get("themeName", "")
        related = theme.get("relatedStocks", [])
        if not related:
            continue
        leader_name = related[0]
        followers = [
            {"name": name, "changeRate": 0.0, "status": "N/A"}
            for name in related[1:6]
        ]
        results.append({
            "themeName": theme_name,
            "state": "LEADER_DETECTED",
            "score": 0,
            "leader": {
                "name": leader_name,
                "changeRate": 0.0,
                "status": "N/A",
            },
            "followers": followers,
            "evidence": {
                "risingCount": 0,
                "strongCount": 0,
                "newsHits": [],
            },
            "source": "FALLBACK",
        })
    return results
