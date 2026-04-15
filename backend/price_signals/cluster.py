from __future__ import annotations

from .models import PriceThemeCandidate
from .taxonomy import THEME_RULES


def _unique_preserve_order(items: list[str], limit: int | None = None) -> list[str]:
    ordered: list[str] = []
    for item in items:
        if not item or item in ordered:
            continue
        ordered.append(item)
        if limit is not None and len(ordered) >= limit:
            break
    return ordered


def _article_text(article: dict) -> str:
    return " ".join([
        article.get("title", ""),
        article.get("summary", ""),
        article.get("source", ""),
    ])


def _telegram_text(signal: dict) -> str:
    return " ".join([
        signal.get("text", ""),
        " ".join(signal.get("keywords", [])),
        " ".join(signal.get("matchedStocks", [])),
    ])


def _score_rule_match(text: str, rule: dict) -> tuple[float, list[str]]:
    hits: list[str] = []
    stock_hits = [stock for stock in rule["stock_names"] if stock and stock in text]
    keyword_hits = [keyword for keyword in rule["keywords"] if keyword and keyword in text]

    for item in stock_hits + keyword_hits:
        if item not in hits:
            hits.append(item)

    score = (len(stock_hits) * 3.0) + (len(keyword_hits) * 1.0)
    return score, hits


def discover_theme_candidates(
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[dict]:
    candidates: list[dict] = []

    for rule in THEME_RULES:
        matched_movers = [item for item in movers if item.get("name") in rule["stock_names"]]
        scored_articles: list[tuple[float, dict, list[str]]] = []
        for article in articles:
            score, hits = _score_rule_match(_article_text(article), rule)
            if score >= 2.0:
                scored_articles.append((score, article, hits))

        scored_telegram: list[tuple[float, dict, list[str]]] = []
        for signal in telegram_signals:
            score, hits = _score_rule_match(_telegram_text(signal), rule)
            if score >= 2.0:
                scored_telegram.append((score, signal, hits))

        article_hits = [article for _, article, _ in scored_articles]
        telegram_hits = [signal for _, signal, _ in scored_telegram]

        if not matched_movers and not article_hits and not telegram_hits:
            continue

        upper_limit_count = sum(1 for item in matched_movers if item.get("upperLimit"))
        avg_change = (
            sum(float(item.get("changeRate", 0.0) or 0.0) for item in matched_movers) / len(matched_movers)
            if matched_movers else 0.0
        )
        mover_score = sum(min(35.0, float(item.get("changeRate", 0.0) or 0.0)) for item in matched_movers)
        score = round(
            mover_score
            + (upper_limit_count * 12.0)
            + (len(article_hits) * 2.5)
            + (len(telegram_hits) * 3.0)
            + (len(matched_movers) * 8.0),
            2,
        )

        matched_stocks = _unique_preserve_order([item.get("name", "") for item in matched_movers] + rule["stock_names"], limit=6)
        scored_articles.sort(key=lambda item: item[0], reverse=True)
        matched_articles = _unique_preserve_order([article.get("title", "") for _, article, _ in scored_articles], limit=4)
        matched_messages = _unique_preserve_order([signal.get("text", "")[:140] for signal in telegram_hits], limit=3)
        matched_terms = []
        for _, _, hits in scored_articles[:3] + scored_telegram[:3]:
            matched_terms.extend(hits)
        keywords = _unique_preserve_order(matched_terms + rule["keywords"], limit=6)

        reasoning_parts = []
        if matched_movers:
            reasoning_parts.append(
                f"급등주 {len(matched_movers)}종목이 묶였고 평균 상승률은 {avg_change:.1f}%입니다."
            )
        if upper_limit_count:
            reasoning_parts.append(f"상한가 종목 {upper_limit_count}개가 포함됐습니다.")
        if article_hits:
            reasoning_parts.append(f"연관 기사 {len(article_hits)}건이 감지됐습니다.")
        if telegram_hits:
            reasoning_parts.append(f"텔레그램 연관 시그널 {len(telegram_hits)}건이 확인됐습니다.")

        candidates.append(
            PriceThemeCandidate(
                theme_name=rule["theme"],
                score=score,
                matched_stocks=matched_stocks,
                matched_articles=matched_articles,
                matched_telegram_messages=matched_messages,
                keywords=keywords,
                reasoning=" ".join(reasoning_parts) or "가격 군집 기반 초기 테마 후보입니다.",
            ).to_dict()
        )

    candidates.sort(
        key=lambda item: (
            float(item.get("score", 0.0) or 0.0),
            len(item.get("matchedStocks", [])),
            len(item.get("matchedArticles", [])),
        ),
        reverse=True,
    )
    return candidates
