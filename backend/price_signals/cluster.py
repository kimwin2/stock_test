from __future__ import annotations

import json
import os
import re

from openai import OpenAI

from .models import PriceThemeCandidate


LLM_MODEL = os.getenv("PRICE_SIGNAL_MODEL", "gpt-4o-mini")
MAX_MOVER_INPUT = 36
MAX_ARTICLE_SNIPPETS_PER_STOCK = 2
MAX_TELEGRAM_SNIPPETS_PER_STOCK = 1
MAX_THEME_COUNT = 10
MIN_THEME_STOCKS = 2
MAX_THEME_STOCKS = 8
GENERIC_THEME_NAMES = {
    "개별주",
    "특징주",
    "잡주",
    "기타",
    "기술주",
    "중소형주",
    "테마주",
    "성장주",
    "실적주",
    "대형주",
}
GENERIC_NAME_FRAGMENTS = {
    "홀딩스",
    "우",
    "스팩",
    "리츠",
    "전자",
    "기술",
    "산업",
    "개발",
    "글로벌",
    "솔루션",
    "시스템",
    "인프라",
    "부품",
    "장비",
    "소프트",
    "소프트웨어",
    "테크",
    "기기",
    "건설",
    "재건",
    "증권",
    "제약",
    "바이오",
    "화학",
    "에너지",
    "전기",
}
GENERIC_THEME_TOKENS = {
    "테마",
    "관련",
    "수혜",
    "모멘텀",
    "산업",
    "기술",
    "솔루션",
    "소프트웨어",
    "하드웨어",
    "부품",
    "장비",
    "플랫폼",
    "인프라",
    "서비스",
    "주",
    "및",
    "관련주",
}
GENERIC_THEME_NAME_PATTERNS = (
    "it 및",
    "소프트웨어",
    "플랫폼",
    "에너지 솔루션",
    "전자 부품",
    "전기차 및 배터리",
)


def _unique_preserve_order(items: list[str], limit: int | None = None) -> list[str]:
    ordered: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if not cleaned or cleaned in ordered:
            continue
        ordered.append(cleaned)
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


def _get_openai_client() -> OpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def _select_movers_for_labeling(movers: list[dict], limit: int = MAX_MOVER_INPUT) -> list[dict]:
    selected = sorted(
        movers,
        key=lambda item: (
            # 저가 상한가 종목 최우선 (5000원 이하 + 상한가)
            bool(item.get("upperLimit")) and int(item.get("price", 99999) or 99999) <= 5000,
            bool(item.get("upperLimit")),
            float(item.get("changeRate", 0.0) or 0.0),
            int(item.get("volumeAmount", 0) or 0),
        ),
        reverse=True,
    )
    return selected[:limit]


def _collect_context_for_stock(stock_name: str, articles: list[dict], telegram_signals: list[dict]) -> dict:
    article_titles = [
        article.get("title", "").strip()
        for article in articles
        if stock_name and stock_name in _article_text(article)
    ]
    telegram_messages = [
        signal.get("text", "").strip()
        for signal in telegram_signals
        if stock_name and stock_name in _telegram_text(signal)
    ]
    return {
        "articles": _unique_preserve_order(article_titles, limit=MAX_ARTICLE_SNIPPETS_PER_STOCK),
        "telegram": _unique_preserve_order(telegram_messages, limit=MAX_TELEGRAM_SNIPPETS_PER_STOCK),
    }


def _build_mover_payload(movers: list[dict], articles: list[dict], telegram_signals: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for mover in _select_movers_for_labeling(movers):
        name = mover.get("name", "")
        context = _collect_context_for_stock(name, articles, telegram_signals)
        payload.append(
            {
                "name": name,
                "market": mover.get("market", ""),
                "price": mover.get("price", 0),
                "changeRate": mover.get("changeRate", 0.0),
                "upperLimit": bool(mover.get("upperLimit")),
                "volumeAmount": mover.get("volumeAmount", 0),
                "articleTitles": context["articles"],
                "telegramSnippets": context["telegram"],
            }
        )
    return payload


def _request_llm_json(system_prompt: str, user_prompt: str) -> dict:
    client = _get_openai_client()
    if client is None:
        print("  [!] OPENAI_API_KEY가 없어 price_signals LLM 라벨링을 건너뜁니다.")
        return {}

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=2500,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _label_theme_candidates_with_llm(movers: list[dict], articles: list[dict], telegram_signals: list[dict]) -> list[dict]:
    mover_payload = _build_mover_payload(movers, articles, telegram_signals)
    if not mover_payload:
        return []

    system_prompt = """당신은 한국 주식시장 초단타 테마 분석가입니다.
오늘 급등한 종목들을 보고, 같은 재료로 움직인 종목군을 테마로 묶어야 합니다.

핵심 규칙:
- 미리 정의된 섹터 목록에 맞추려 하지 말고, 오늘 실제 움직임에서 공통 재료를 추론하세요.
- 종목 이름 자체가 강한 단서를 주면 적극 활용하세요. 예: 전선, 조선, 원전, 양자암호, 보안.
- generic한 이름(개별주, 특징주, 잡주, 기타, 기술주, 중소형주, 저가주)은 금지합니다.
- 최대한 구체적인 이름을 쓰세요. 'IT 및 소프트웨어'보다 '양자암호 보안'이 낫습니다.
- **업종이 다른 종목은 반드시 다른 테마로 분리하세요.** 예: 광통신 기업과 가구 유통 기업과 반도체 장비 기업은 절대 같은 테마로 묶지 마세요.
- 한 테마는 2~6개 종목을 포함해야 합니다.
- **3~5개 테마**를 만들어야 합니다. 종목이 많으면 5개까지, 적으면 3개라도 만드세요.
- 어떤 테마에도 속하지 않는 종목은 무리하게 넣지 말고 제외하세요.
- 저가 상한가(5000원 이하 + 상한가) 종목이라도 공통 업종/재료가 없으면 같은 테마로 묶지 마세요.
- 각 테마의 reasoning에 종목들의 공통 업종/재료를 명확히 설명하세요.
- 검증에 도움이 되도록 keywords를 3~6개 넣으세요.

JSON만 출력하세요."""

    user_prompt = (
        "아래는 오늘 급등주와 종목별 문맥입니다.\n"
        "이 종목들을 공통 업종/재료별로 3~5개 테마로 분류하세요.\n"
        "업종이 다른 종목을 한 테마에 넣지 마세요. 분류 불가능한 종목은 제외하세요.\n\n"
        "출력 형식:\n"
        "{\n"
        '  "themes": [\n'
        "    {\n"
        '      "themeName": "구체적인 테마명",\n'
        '      "stockNames": ["종목1", "종목2"],\n'
        '      "keywords": ["키워드1", "키워드2"],\n'
        '      "reasoning": "이 종목들의 공통 업종/재료 설명",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"{json.dumps(mover_payload, ensure_ascii=False, indent=2)}"
    )

    parsed = _request_llm_json(system_prompt, user_prompt)
    themes = parsed.get("themes", [])
    print(f"  [LLM] 급등주 1차 군집 라벨링 완료: {len(themes)}개 후보")
    return themes


def _refine_theme_candidates_with_llm(
    labeled_themes: list[dict],
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[dict]:
    if not labeled_themes:
        return []

    mover_payload = _build_mover_payload(movers, articles, telegram_signals)
    if not mover_payload:
        return labeled_themes

    system_prompt = """당신은 한국 주식시장의 장중 급등주를 최종 테마로 정리하는 검수자입니다.
1차 후보 테마를 보고, 최종 테마를 더 정확하게 다듬으세요.

핵심 규칙:
- 반드시 제공된 moverUniverse 안의 종목만 사용하세요.
- 서로 많이 겹치는 후보는 더 구체적인 이름 하나로 합치세요.
- 종목 포함 범위를 다시 판단하세요. 같은 가족 종목이 급등주에 함께 있다면 빠뜨리지 마세요.
- 너무 넓은 업종명은 피하고, 오늘 시장이 인식할 만한 이름으로 구체화하세요.
- 기사와 텔레그램은 검증 재료이지 필수 조건은 아닙니다.
- **3~5개 후보**만 남기고, 각 테마는 2~6종목까지만 포함하세요.
- **업종이 다른 종목은 절대 같은 테마에 넣지 마세요.** 광통신, 가구유통, 반도체장비 등은 별개 테마입니다.
- 분류 불가능한 개별 종목은 억지로 테마에 넣지 말고 제외하세요.
- 각 테마의 reasoning에 종목들의 실제 공통점을 구체적으로 명시하세요.

JSON만 출력하세요."""

    user_prompt = (
        "아래는 오늘 급등주 universe와 1차 씨앗 테마입니다.\n"
        "최종 후보로 재정리하세요.\n\n"
        "출력 형식:\n"
        "{\n"
        '  "themes": [\n'
        "    {\n"
        '      "themeName": "구체적인 테마명",\n'
        '      "stockNames": ["종목1", "종목2"],\n'
        '      "keywords": ["키워드1", "키워드2"],\n'
        '      "reasoning": "왜 이런 최종 군집이 맞는지",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "[moverUniverse]\n"
        f"{json.dumps(mover_payload, ensure_ascii=False, indent=2)}\n\n"
        "[seedThemes]\n"
        f"{json.dumps(labeled_themes[:MAX_THEME_COUNT], ensure_ascii=False, indent=2)}"
    )

    parsed = _request_llm_json(system_prompt, user_prompt)
    themes = parsed.get("themes", [])
    print(f"  [LLM] 급등주 2차 군집 정제 완료: {len(themes)}개 후보")
    return themes


def _score_match(text: str, stock_names: list[str], keywords: list[str]) -> tuple[float, list[str]]:
    hits: list[str] = []
    stock_hits = [name for name in stock_names if name and name in text]
    keyword_hits = [keyword for keyword in keywords if keyword and keyword in text]

    for item in stock_hits + keyword_hits:
        if item not in hits:
            hits.append(item)

    score = (len(stock_hits) * 3.0) + (len(keyword_hits) * 1.0)
    return score, hits


def _normalize_fragment(fragment: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣]", "", fragment or "").strip()
    return cleaned


def _common_name_fragments(stock_names: list[str]) -> list[str]:
    if len(stock_names) < 2:
        return []

    fragment_counts: dict[str, int] = {}
    for idx, stock_name in enumerate(stock_names):
        normalized = _normalize_fragment(stock_name)
        if len(normalized) < 2:
            continue
        seen_in_name: set[str] = set()
        max_size = min(4, len(normalized))
        for size in range(2, max_size + 1):
            for start in range(0, len(normalized) - size + 1):
                fragment = normalized[start:start + size]
                if fragment in seen_in_name or fragment in GENERIC_NAME_FRAGMENTS:
                    continue
                seen_in_name.add(fragment)
                fragment_counts[fragment] = fragment_counts.get(fragment, 0) + 1

    return [
        fragment
        for fragment, count in sorted(fragment_counts.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
        if count >= 2
    ]


def _fragment_support_count(stock_names: list[str], fragment: str) -> int:
    return sum(1 for stock_name in stock_names if fragment and fragment in stock_name)


def _theme_keywords(theme_name: str, keywords: list[str]) -> list[str]:
    tokens = re.findall(r"[0-9A-Za-z가-힣]+", theme_name or "")
    combined = _unique_preserve_order(tokens + keywords, limit=12)
    return [
        token
        for token in combined
        if len(token) >= 2 and token not in GENERIC_THEME_TOKENS
    ]


def _is_theme_name_too_generic(theme_name: str) -> bool:
    normalized = (theme_name or "").strip().lower()
    if not normalized:
        return True
    if any(pattern in normalized for pattern in GENERIC_THEME_NAME_PATTERNS):
        return True

    tokens = [
        token.lower()
        for token in re.findall(r"[0-9A-Za-z가-힣]+", theme_name or "")
        if len(token) >= 2
    ]
    meaningful = [
        token for token in tokens
        if token not in GENERIC_THEME_TOKENS and token not in {"it", "ai", "sw", "ict"}
    ]
    return not meaningful


def _stock_theme_support_score(
    stock_name: str,
    theme_name: str,
    keywords: list[str],
    excluded_terms: list[str],
    articles: list[dict],
    telegram_signals: list[dict],
) -> float:
    context = _collect_context_for_stock(stock_name, articles, telegram_signals)
    context_text = " ".join(context["articles"] + context["telegram"])
    score = 0.0

    theme_tokens = _theme_keywords(theme_name, keywords)
    excluded = set(excluded_terms)
    for token in theme_tokens:
        if token in excluded:
            continue
        if token and token in stock_name:
            score += 2.0
        if token and token in context_text:
            score += 1.0

    return score


def _select_theme_stocks(
    theme_name: str,
    stock_names: list[str],
    keywords: list[str],
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[str]:
    seed_names = _unique_preserve_order(stock_names, limit=MAX_THEME_STOCKS)
    common_fragments = _common_name_fragments(seed_names)
    theme_tokens = _theme_keywords(theme_name, keywords)

    scored_members: list[tuple[float, str]] = []
    sorted_movers = sorted(
        movers,
        key=lambda item: (
            float(item.get("changeRate", 0.0) or 0.0),
            bool(item.get("upperLimit")),
            int(item.get("volumeAmount", 0) or 0),
        ),
        reverse=True,
    )

    for mover in sorted_movers:
        name = mover.get("name", "")
        if not name:
            continue

        context = _collect_context_for_stock(name, articles, telegram_signals)
        context_text = " ".join(context["articles"] + context["telegram"])
        score = 0.0

        if name in seed_names:
            score += 1.5
        for fragment in common_fragments:
            if fragment and fragment in name:
                score += 3.0
        for token in theme_tokens:
            if token and token not in GENERIC_NAME_FRAGMENTS and token in name:
                score += 2.0
            if token and token in context_text:
                score += 1.0
        for keyword in keywords:
            if keyword and keyword in context_text:
                score += 1.0
        if mover.get("upperLimit"):
            score += 5.0
            # 저가 상한가 추가 보너스 (5000원 이하)
            price = int(mover.get("price", 99999) or 99999)
            if price <= 5000:
                score += 4.0
            elif price <= 10000:
                score += 2.0

        if score > 0.0:
            scored_members.append((score, name))

    scored_members.sort(key=lambda item: item[0], reverse=True)
    selected = [
        name for score, name in scored_members
        if score >= 2.5
    ][:MAX_THEME_STOCKS]

    if len(selected) < MIN_THEME_STOCKS:
        for score, name in scored_members:
            if name not in selected:
                selected.append(name)
            if len(selected) >= MIN_THEME_STOCKS:
                break

    return _unique_preserve_order(selected, limit=MAX_THEME_STOCKS)


def _validate_labeled_themes(
    labeled_themes: list[dict],
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[dict]:
    mover_map = {item.get("name", ""): item for item in movers}
    candidates: list[dict] = []

    for theme in labeled_themes:
        theme_name = (theme.get("themeName") or "").strip()
        if not theme_name or theme_name in GENERIC_THEME_NAMES:
            continue

        stock_names = _select_theme_stocks(
            theme_name=theme_name,
            stock_names=[stock for stock in theme.get("stockNames", []) if stock in mover_map],
            keywords=theme.get("keywords", []),
            movers=movers,
            articles=articles,
            telegram_signals=telegram_signals,
        )
        stock_names = _unique_preserve_order(
            [stock for stock in stock_names if stock in mover_map],
            limit=MAX_THEME_STOCKS,
        )
        if len(stock_names) < MIN_THEME_STOCKS:
            continue

        keywords = _unique_preserve_order(theme.get("keywords", []), limit=6)
        confidence = float(theme.get("confidence", 0.5) or 0.5)

        matched_movers = [mover_map[name] for name in stock_names if name in mover_map]
        upper_limit_count = sum(1 for item in matched_movers if item.get("upperLimit"))
        avg_change = (
            sum(float(item.get("changeRate", 0.0) or 0.0) for item in matched_movers) / len(matched_movers)
            if matched_movers else 0.0
        )
        mover_score = sum(min(35.0, float(item.get("changeRate", 0.0) or 0.0)) for item in matched_movers)

        scored_articles: list[tuple[float, dict, list[str]]] = []
        for article in articles:
            score, hits = _score_match(_article_text(article), stock_names, keywords)
            if score >= 2.0:
                scored_articles.append((score, article, hits))

        scored_telegram: list[tuple[float, dict, list[str]]] = []
        for signal in telegram_signals:
            score, hits = _score_match(_telegram_text(signal), stock_names, keywords)
            if score >= 2.0:
                scored_telegram.append((score, signal, hits))

        scored_articles.sort(key=lambda item: item[0], reverse=True)
        scored_telegram.sort(key=lambda item: item[0], reverse=True)

        matched_articles = _unique_preserve_order(
            [article.get("title", "") for _, article, _ in scored_articles],
            limit=4,
        )
        matched_messages = _unique_preserve_order(
            [signal.get("text", "")[:140] for _, signal, _ in scored_telegram],
            limit=3,
        )

        support_terms: list[str] = []
        for _, _, hits in scored_articles[:3] + scored_telegram[:3]:
            support_terms.extend(hits)
        keywords_out = _unique_preserve_order(keywords + support_terms + stock_names, limit=6)

        # 저가 상한가 종목 가중치
        low_price_upper_count = sum(
            1 for item in matched_movers
            if item.get("upperLimit") and int(item.get("price", 99999) or 99999) <= 5000
        )

        score = round(
            mover_score
            + (upper_limit_count * 12.0)
            + (low_price_upper_count * 8.0)  # 저가 상한가 추가 보너스
            + (len(scored_articles) * 2.5)
            + (len(scored_telegram) * 3.0)
            + (len(matched_movers) * 8.0)
            + (confidence * 20.0),
            2,
        )

        reasoning_parts = []
        reasoning = (theme.get("reasoning") or "").strip()
        if reasoning:
            reasoning_parts.append(reasoning)
        reasoning_parts.append(f"급등주 {len(matched_movers)}종목이 묶였고 평균 상승률은 {avg_change:.1f}%입니다.")
        if upper_limit_count:
            reasoning_parts.append(f"상한가 종목 {upper_limit_count}개가 포함됐습니다.")
        if low_price_upper_count:
            reasoning_parts.append(f"저가 상한가(5000원 이하) 종목 {low_price_upper_count}개가 포함되어 테마 강도가 매우 높습니다.")
        if scored_articles:
            reasoning_parts.append(f"연관 기사 {len(scored_articles)}건이 감지됐습니다.")
        if scored_telegram:
            reasoning_parts.append(f"텔레그램 연관 시그널 {len(scored_telegram)}건이 확인됐습니다.")

        candidates.append(
            PriceThemeCandidate(
                theme_name=theme_name,
                score=score,
                matched_stocks=stock_names,
                matched_articles=matched_articles,
                matched_telegram_messages=matched_messages,
                keywords=keywords_out,
                reasoning=" ".join(reasoning_parts),
            ).to_dict()
        )

    candidates = _prune_candidates_with_llm(candidates, movers, articles, telegram_signals)

    deduped: list[dict] = []
    used_stock_sets: list[set[str]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            float(item.get("score", 0.0) or 0.0),
            len(item.get("matchedStocks", [])),
            len(item.get("matchedArticles", [])),
        ),
        reverse=True,
    ):
        normalized_name = (candidate.get("themeName") or "").strip().lower()
        stock_set = set(candidate.get("matchedStocks", []))
        if any((saved.get("themeName") or "").strip().lower() == normalized_name for saved in deduped):
            continue
        if any(len(stock_set & saved_set) >= min(len(stock_set), len(saved_set)) for saved_set in used_stock_sets):
            continue
        deduped.append(candidate)
        used_stock_sets.append(stock_set)

    return deduped


def _prune_candidates_with_llm(
    candidates: list[dict],
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[dict]:
    if not candidates:
        return []

    mover_map = {item.get("name", ""): item for item in movers}
    auto_keep_names: set[str] = set()
    review_payload: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)[:MAX_THEME_COUNT]:
        stock_names = candidate.get("matchedStocks", [])
        common_fragments = _common_name_fragments(stock_names)
        strong_family = any(
            _fragment_support_count(stock_names, fragment) >= 3
            for fragment in common_fragments
        )
        pair_family = (
            len(stock_names) == 2
            and any(_fragment_support_count(stock_names, fragment) == 2 for fragment in common_fragments)
        )
        stock_support_scores = [
            _stock_theme_support_score(
                stock_name=stock_name,
                theme_name=candidate.get("themeName", ""),
                keywords=candidate.get("keywords", []),
                excluded_terms=stock_names,
                articles=articles,
                telegram_signals=telegram_signals,
            )
            for stock_name in stock_names
        ]
        covered_stock_count = sum(1 for score in stock_support_scores if score >= 1.0)
        weak_generic_pair = (
            len(stock_names) <= 2
            and _is_theme_name_too_generic(candidate.get("themeName", ""))
            and len(candidate.get("matchedArticles", [])) == 0
            and len(candidate.get("matchedTelegramMessages", [])) == 0
            and not common_fragments
        )
        weak_cross_stock_theme = (
            len(stock_names) == 2
            and not pair_family
            and covered_stock_count < 2
        )

        if strong_family:
            auto_keep_names.add((candidate.get("themeName") or "").strip())
            continue
        if pair_family:
            auto_keep_names.add((candidate.get("themeName") or "").strip())
            continue
        if weak_generic_pair:
            continue
        if weak_cross_stock_theme:
            continue

        stock_payload = []
        for stock_name in stock_names:
            mover = mover_map.get(stock_name, {})
            context = _collect_context_for_stock(stock_name, articles, telegram_signals)
            stock_payload.append(
                {
                    "name": stock_name,
                    "changeRate": mover.get("changeRate", 0.0),
                    "upperLimit": bool(mover.get("upperLimit")),
                    "articleTitles": context["articles"],
                    "telegramSnippets": context["telegram"],
                }
            )
        review_payload.append(
            {
                "themeName": candidate.get("themeName", ""),
                "score": candidate.get("score", 0.0),
                "keywords": candidate.get("keywords", []),
                "reasoning": candidate.get("reasoning", ""),
                "commonFragments": common_fragments[:4],
                "coveredStockCount": covered_stock_count,
                "stocks": stock_payload,
                "articleCount": len(candidate.get("matchedArticles", [])),
                "telegramCount": len(candidate.get("matchedTelegramMessages", [])),
            }
        )

    system_prompt = """당신은 한국 주식시장 장중 테마 군집 검수자입니다.
이미 생성된 급등 테마 후보들 중, 오늘 실제 공통 재료로 보기 어려운 군집을 제거하세요.

핵심 규칙:
- 이름만 그럴듯한데 종목 조합이 어색하면 제거하세요.
- 2종목짜리 후보는 특히 보수적으로 보세요.
- 종목명, 기사, 텔레그램 문맥 어디에도 공통 재료 단서가 약하면 제거하세요.
- 반대로 기사/텔레그램이 약해도 종목명 가족이나 급등 흐름이 매우 선명하면 유지할 수 있습니다.
- generic한 업종명보다 오늘 시장이 인식할 만한 구체 테마를 우선 유지하세요.

JSON만 출력하세요."""

    user_prompt = (
        "아래 후보들을 keep/drop 판단하세요.\n"
        "출력 형식:\n"
        "{\n"
        '  "approvedThemes": [\n'
        "    {\n"
        '      "themeName": "유지할 테마명",\n'
        '      "verdict": "keep",\n'
        '      "reason": "짧은 이유"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"{json.dumps(review_payload, ensure_ascii=False, indent=2)}"
    )

    parsed = _request_llm_json(system_prompt, user_prompt)
    approved_names = set(auto_keep_names)
    approved_names.update({
        (item.get("themeName") or "").strip()
        for item in parsed.get("approvedThemes", [])
        if (item.get("verdict") or "").strip().lower() == "keep"
    })
    if not approved_names:
        return candidates

    pruned = [candidate for candidate in candidates if (candidate.get("themeName") or "").strip() in approved_names]
    print(f"  [LLM] 급등주 최종 검수 완료: {len(pruned)}개 유지")
    return pruned


def discover_theme_candidates(
    movers: list[dict],
    articles: list[dict],
    telegram_signals: list[dict],
) -> list[dict]:
    labeled_themes = _label_theme_candidates_with_llm(movers, articles, telegram_signals)
    refined_themes = _refine_theme_candidates_with_llm(labeled_themes, movers, articles, telegram_signals)
    final_themes = refined_themes or labeled_themes
    return _validate_labeled_themes(final_themes, movers, articles, telegram_signals)
