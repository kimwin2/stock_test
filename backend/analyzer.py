"""
ChatGPT API를 이용한 뉴스 테마 분석 모듈
- 크롤링된 기사들을 분석하여 테마 7개를 추출합니다.
- 각 테마별 요약과 관련 종목을 반환합니다.
"""
from __future__ import annotations

import sys
import io
import json
import os
import re
import requests
from urllib.parse import parse_qs, quote, urlparse
from openai import OpenAI
from dotenv import load_dotenv

try:
    from stock_data import STOCK_CODE_MAP
    from price_signals.store import load_price_signal_payload
    from telegram.store import load_telegram_signals
    from youtube_signals import fetch_latest_youtube_theme_signals
    from antwinner.collector import fetch_antwinner_top_themes, build_antwinner_payload
    from antwinner.store import load_antwinner_payload, save_antwinner_payload
except ModuleNotFoundError:
    from .stock_data import STOCK_CODE_MAP
    from .price_signals.store import load_price_signal_payload
    from .telegram.store import load_telegram_signals
    from .youtube_signals import fetch_latest_youtube_theme_signals
    from .antwinner.collector import fetch_antwinner_top_themes, build_antwinner_payload
    from .antwinner.store import load_antwinner_payload, save_antwinner_payload

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

DEFAULT_THEME_ANALYSIS_MODEL = "gpt-4o-mini"
DEFAULT_THEME_ANALYSIS_REASONING_EFFORT = "minimal"
DEFAULT_THEME_ANALYSIS_MAX_COMPLETION_TOKENS = 12000
DEFAULT_THEME_ANALYSIS_MAX_TOKENS = 3000
DEFAULT_THEME_ANALYSIS_TEMPERATURE = 0.3
GOOGLE_NEWS_FALLBACK_LIMIT = 3
GOOGLE_NEWS_SKIP_KEYWORDS = {
    "블로그",
    "blog",
    "브런치",
    "brunch",
    "프리미엄콘텐츠",
    "premium contents",
    "티스토리",
    "velog",
    "top10",
    "대장주",
    "수혜주",
    "추천 전망",
}


def _convert_to_article_url(url: str) -> str:
    """
    네이버 금융 뉴스 URL을 원본 기사 URL(네이버뉴스)로 변환합니다.

    finance.naver.com/news/news_read.naver?article_id=XXX&office_id=YYY
    → https://n.news.naver.com/mnews/article/YYY/XXX
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if "finance.naver.com" in parsed.hostname and "news_read" in parsed.path:
            params = parse_qs(parsed.query)
            article_id = params.get("article_id", [""])[0]
            office_id = params.get("office_id", [""])[0]
            if article_id and office_id:
                return f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}"
    except Exception:
        pass
    return url


def _build_headline_link(
    *,
    url: str,
    title: str,
    source_type: str,
    confidence: str,
    query: str = "",
    article_index: int = 0,
    source_name: str = "",
    published_at: str = "",
    match_score: float = 0.0,
) -> dict:
    return {
        "url": (url or "").strip(),
        "title": (title or "").strip(),
        "sourceType": source_type,
        "confidence": confidence,
        "query": (query or "").strip(),
        "articleIndex": int(article_index or 0),
        "sourceName": (source_name or "").strip(),
        "publishedAt": (published_at or "").strip(),
        "matchScore": float(match_score or 0.0),
    }


def _dedupe_headline_links(links: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen_urls: set[str] = set()
    for link in links:
        url = (link or {}).get("url", "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(link)
    return deduped


def _build_google_news_search_url(query: str) -> str:
    normalized = " ".join((query or "").split())
    if not normalized:
        return ""
    return f"https://news.google.com/search?q={quote(normalized)}&hl=ko&gl=KR&ceid=KR:ko"


def _build_google_news_queries(theme: dict) -> list[str]:
    theme_name = (theme.get("themeName") or "").strip()
    related_stocks = [stock.strip() for stock in theme.get("relatedStocks", []) if stock and stock.strip()]
    headline_terms = [
        term for term in _extract_meaningful_terms(theme.get("headline", ""))
        if term not in related_stocks and term != theme_name
    ]

    queries: list[str] = []

    def add_query(value: str) -> None:
        normalized = " ".join((value or "").split())
        if normalized and normalized not in queries:
            queries.append(normalized)

    add_query(f"{theme_name} 관련주")
    add_query(f"{theme_name} {' '.join(related_stocks[:2])}".strip())
    add_query(f"{theme_name} {' '.join(headline_terms[:2])}".strip())
    add_query(theme_name)
    add_query(" ".join(related_stocks[:2]))

    return queries[:4]


def _search_google_news_links(query: str, limit: int = GOOGLE_NEWS_FALLBACK_LIMIT) -> list[dict]:
    if not query:
        return []

    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code != 200:
            return []

        from bs4 import BeautifulSoup as BS

        soup = BS(resp.text, "xml")
        results: list[dict] = []
        for item in soup.select("item"):
            title_tag = item.find("title")
            link_tag = item.find("link")
            source_tag = item.find("source")
            pub_date_tag = item.find("pubDate")

            raw_link = link_tag.text.strip() if link_tag and link_tag.text else ""
            title = title_tag.text.strip() if title_tag and title_tag.text else ""
            source_name = source_tag.text.strip() if source_tag and source_tag.text else ""
            published_at = pub_date_tag.text.strip() if pub_date_tag and pub_date_tag.text else ""
            if not raw_link:
                continue
            text_blob = f"{title} {source_name}".lower()
            if any(keyword in text_blob for keyword in GOOGLE_NEWS_SKIP_KEYWORDS):
                continue

            results.append(
                _build_headline_link(
                    url=raw_link,
                    title=title,
                    source_type="google_news_result",
                    confidence="fallback",
                    query=query,
                    source_name=source_name,
                    published_at=published_at,
                )
            )
            if len(results) >= limit:
                break

        return _dedupe_headline_links(results)

    except Exception as e:
        print(f"    [!] Google News 검색 실패 ({query}): {e}")
        return []


def _set_theme_headline_links(theme: dict, links: list[dict]) -> None:
    deduped_links = _dedupe_headline_links(links)
    primary_link = deduped_links[0] if deduped_links else {}

    theme["headlineLinks"] = deduped_links
    theme["headlineLink"] = primary_link
    theme["headlineUrl"] = primary_link.get("url", "")
    theme["headlineLinkSource"] = primary_link.get("sourceType", "")
    theme["headlineLinkConfidence"] = primary_link.get("confidence", "missing")

    primary_title = primary_link.get("title", "")
    if primary_title and primary_link.get("sourceType") != "google_news_search":
        theme["headline"] = primary_title[:80]


def get_openai_client() -> OpenAI:
    """OpenAI 클라이언트를 생성합니다."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.\n"
            ".env 파일에 OPENAI_API_KEY=sk-... 형태로 설정해주세요."
        )
    return OpenAI(api_key=api_key)


def _get_theme_analysis_model() -> str:
    return (os.getenv("THEME_ANALYSIS_MODEL", DEFAULT_THEME_ANALYSIS_MODEL) or DEFAULT_THEME_ANALYSIS_MODEL).strip()


def _build_theme_analysis_request(model_name: str, user_prompt: str) -> dict:
    request = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    if model_name.startswith("gpt-5"):
        request["max_completion_tokens"] = int(
            os.getenv(
                "THEME_ANALYSIS_MAX_COMPLETION_TOKENS",
                str(DEFAULT_THEME_ANALYSIS_MAX_COMPLETION_TOKENS),
            )
        )
        request["reasoning_effort"] = (
            os.getenv("THEME_ANALYSIS_REASONING_EFFORT", DEFAULT_THEME_ANALYSIS_REASONING_EFFORT)
            or DEFAULT_THEME_ANALYSIS_REASONING_EFFORT
        ).strip()
    else:
        request["temperature"] = float(
            os.getenv("THEME_ANALYSIS_TEMPERATURE", str(DEFAULT_THEME_ANALYSIS_TEMPERATURE))
        )
        request["max_tokens"] = int(
            os.getenv("THEME_ANALYSIS_MAX_TOKENS", str(DEFAULT_THEME_ANALYSIS_MAX_TOKENS))
        )

    return request


SYSTEM_PROMPT = """당신은 한국 주식시장 전문 애널리스트입니다. 단타 트레이딩에 특화되어 있으며, 
뉴스를 분석하여 당일 주도 테마를 정확히 파악하는 능력이 뛰어납니다.

오늘 수집된 증권 뉴스 기사들과 외부 고신뢰 시그널을 분석하여 다음을 수행하세요:

1. **테마 7개 추출**: 오늘 가장 주목받는 투자 테마 7개를 선정합니다.
   - 기사에서 반복적으로 언급되는 섹터/이슈를 테마로 선정
   - 거래대금이 몰릴 만한 핫한 테마를 우선 선정
   - ★ 표시가 된 기사는 실제 주가 움직임이 확인된 기사이므로 테마 선정 시 더 높은 가중치를 부여하세요
   - ◆ 표시가 된 기사는 유튜브 외부 시그널과 직접 겹치는 기사이므로 매우 높은 가중치를 부여하세요
   - ● 표시가 된 기사는 개미승리 실시간 상위 테마와 겹치는 기사이므로 매우 높은 가중치를 부여하세요
   - `개미승리 실시간 테마 시그널`은 실제 장중 등락률과 거래대금 기반의 고신뢰 시그널입니다. 상위 테마 중 뉴스에서 조금이라도 관련 기사가 있는 테마는 적극적으로 반영하세요. 다만 관련종목(relatedStocks)은 개미승리 종목만 나열하지 말고, 뉴스와 다른 시그널에서 언급된 종목을 섞어 다양하게 구성하세요
   - `심플 관심종목 TV`의 최신 `내일 관심테마!` 및 `당일 관심테마!` 영상은 고신뢰 선행 시그널입니다. 뉴스와 겹치면 최우선 반영하고, 일부만 겹쳐도 강하게 반영하세요
   - `실시간 텔레그램 시그널`은 장중 선행 시그널입니다. 뉴스가 약하더라도 초기 형성 테마 후보로 강하게 검토하세요
   - `가격 기반 테마 후보`는 급등률, 상한가, 종목 동조화로 포착한 장중 수급 시그널입니다. 기사 반복도가 약해도 실제 종목군이 강하게 움직이면 매우 강하게 반영하세요
   - **종합 판단**: 7개 테마 중 3~4개는 개미승리·유튜브 등 외부 시그널이 뒷받침하는 테마를, 나머지 3~4개는 뉴스 기사 분석에서 독자적으로 도출한 테마를 선정하세요. 관련종목은 여러 소스를 종합하여 다양하게 구성하세요
   
2. **테마 배제 기준 (반드시 준수)**:
   - 단순 정부 정책 발표나 사회적 갈등 이슈는 테마로 선정하지 마세요
   - 거시경제 지표(금리, 환율, CPI 등)만 다루고 수혜 종목이 특정되지 않는 모호한 이슈는 배제하세요
   - 실제로 주가가 움직이는 구체적 섹터/업종 테마만 선정하세요
   - 나쁜 테마 예시 (이런 것은 절대 선정하지 마세요): "K-아이웨어", "고유가 피해지원금", "물가안정", "남북관계"
   - 아래 예시는 단지 형식 참고용일 뿐이며, 오늘 데이터가 가리키는 테마가 더 중요합니다
   - 예시에 없는 새로운 테마라도 기사 반복도, 시세 반응, 종목 응집도가 충분하면 적극적으로 선정하세요
   - 반대로 예시에 있는 테마라도 오늘 데이터 근거가 약하면 과감히 제외하세요
   - 형식 참고 예시: "광통신", "반도체소부장", "2차전지", "방산", "건설", "AI반도체", "조선", "원전"

3. **각 테마별 정보**:
   - themeName: 테마명 (간결하게, 예: "광통신", "반도체소부장", "중동전쟁", "방산")
   - headline: 테마 관련 핵심 뉴스 한줄 요약 (기사 제목 스타일, 50자 이내)
   - representativeArticleIndex: 이 테마를 가장 잘 대표하는 기사 번호 (1부터 시작하는 정수). 기사 목록에 직접 대응하는 기사가 없으면 0
   - relatedStocks: 해당 테마의 대장주 후보 종목명 6개 (한국 상장종목만, 정확한 종목명)
   - reasoning: 이 테마를 선정한 이유 (1-2문장)

4. **선정 자율성**:
   - 당신의 목표는 예시를 맞히는 것이 아니라 오늘 실제 시장 데이터를 가장 잘 설명하는 테마를 찾는 것입니다
   - 기사와 시그널이 강하게 가리키는 경우, 생소하거나 새롭게 형성된 테마도 주저하지 말고 선정하세요
   - 서로 다른 테마를 억지로 예시 범주로 뭉개지 말고, 시장에서 실제로 구분되어 움직일 만하면 분리해서 판단하세요

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요."""

USER_PROMPT_TEMPLATE = """아래는 오늘({date}) 수집된 증권 뉴스 기사 {count}개입니다. 
분석하여 오늘의 주도 테마 7개를 추출해주세요.

=== 📊 개미승리 실시간 테마 시그널 (장중 등락률 상위 5 · 고신뢰) ===
{antwinner_text}

=== 외부 고신뢰 시그널: 심플 관심종목 TV ===
{youtube_text}

=== 실시간 텔레그램 시그널 ===
{telegram_text}

=== 가격 기반 테마 후보 ===
{price_signal_text}

=== 테마 후보 힌트 ===
{candidate_text}

=== 뉴스 기사 목록 ===
{articles_text}

대표 기사로 직접 연결할 만한 뉴스가 없으면 `representativeArticleIndex`는 0으로 작성하세요.

=== 응답 형식 (JSON) ===
{{
  "themes": [
    {{
      "themeName": "테마명",
      "headline": "핵심 뉴스 한줄 요약",
      "representativeArticleIndex": 1,
      "relatedStocks": ["종목1", "종목2", "종목3", "종목4", "종목5", "종목6"],
      "reasoning": "선정 이유"
    }}
  ]
}}"""


# 주가 급등/특징주 관련 키워드 — 이 키워드가 포함된 기사는 실제 시장 움직임을 반영
PRIORITY_KEYWORDS = ["특징주", "강세", "상한가", "수주", "급등", "급상승", "상승세", "테마주", "대장주", "거래폭발"]

THEME_CANDIDATE_RULES = [
    {
        "theme": "보안",
        "keywords": ["보안", "해킹", "취약점", "사이버", "양자암호", "PQC", "QKD", "인증", "미토스"],
        "stocks": ["파수", "아톤", "엑스게이트", "케이씨에스", "아이씨티케이"],
    },
    {
        "theme": "양자컴퓨터",
        "keywords": ["양자", "양자컴퓨터", "양자암호", "양자통신", "큐비트", "PQC", "QKD"],
        "stocks": ["아이씨티케이", "엑스게이트", "케이씨에스", "아톤"],
    },
]

PRICE_SIGNAL_RESCUE_SCORE = 150.0
THEME_OVERLAP_MERGE_THRESHOLD = 0.75
POSTPROCESS_REPLACEABLE_THEMES = {
    "햇지",
    "헤지",
    "개별주",
    "실적",
    "지수방어",
    "위험회피",
}
THEME_MERGE_RULES = [
    {
        "primary": "양자컴퓨터",
        "secondary": "보안",
        "threshold": THEME_OVERLAP_MERGE_THRESHOLD,
    },
]
ARTICLE_MATCH_STOPWORDS = {
    "관련",
    "관련주",
    "테마",
    "테마주",
    "종목",
    "주가",
    "강세",
    "약세",
    "급등",
    "상승",
    "하락",
    "장중",
    "평균",
    "등락률",
    "기록",
    "지속",
    "전망",
    "분석",
    "행렬",
    "모멘텀",
    "수혜",
    "주도",
}
MIN_CONFIDENT_ARTICLE_MATCH_SCORE = 6.0
MIN_CONFIDENT_ARTICLE_MATCH_MARGIN = 1.5


def _is_priority_article(title: str) -> bool:
    """기사 제목에 우선순위 키워드가 포함되어 있는지 확인합니다."""
    return any(kw in title for kw in PRIORITY_KEYWORDS)


def _normalize_theme_name(value: str) -> str:
    return "".join((value or "").lower().split())


def _extract_meaningful_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", text or ""):
        normalized = token.strip()
        lowered = normalized.lower()
        if len(normalized) < 2:
            continue
        if normalized in ARTICLE_MATCH_STOPWORDS or lowered in ARTICLE_MATCH_STOPWORDS:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(normalized)
    return terms


def _score_article_relevance(theme: dict, article: dict, article_index: int) -> dict:
    title = article.get("title", "").strip()
    summary = article.get("summary", "").strip()
    title_norm = _normalize_theme_name(title)
    summary_norm = _normalize_theme_name(summary)

    score = 0.0
    exact_theme_hits = 0
    stock_hits = 0
    theme_term_hits = 0
    headline_term_hits = 0
    title_theme_hits = 0

    theme_name = (theme.get("themeName") or "").strip()
    theme_name_norm = _normalize_theme_name(theme_name)
    related_stocks = [stock.strip() for stock in theme.get("relatedStocks", []) if stock and stock.strip()]
    headline_terms = _extract_meaningful_terms(theme.get("headline", ""))
    theme_terms = _extract_meaningful_terms(theme_name)

    if theme_name_norm:
        if theme_name_norm in title_norm:
            score += 10.0
            exact_theme_hits += 1
            title_theme_hits += 1
        elif theme_name_norm in summary_norm:
            score += 6.0
            exact_theme_hits += 1

    for stock in related_stocks[:4]:
        stock_norm = _normalize_theme_name(stock)
        if not stock_norm:
            continue
        if stock_norm in title_norm:
            score += 6.0
            stock_hits += 1
        elif stock_norm in summary_norm:
            score += 4.0
            stock_hits += 1

    for term in theme_terms[:4]:
        term_norm = _normalize_theme_name(term)
        if not term_norm or term_norm == theme_name_norm:
            continue
        if term_norm in title_norm:
            score += 3.0
            theme_term_hits += 1
            title_theme_hits += 1
        elif term_norm in summary_norm:
            score += 1.5
            theme_term_hits += 1

    for term in headline_terms[:4]:
        if term in related_stocks or term in theme_terms:
            continue
        term_norm = _normalize_theme_name(term)
        if not term_norm or term_norm == theme_name_norm:
            continue
        if term_norm in title_norm:
            score += 1.0
            headline_term_hits += 1
        elif term_norm in summary_norm:
            score += 0.5
            headline_term_hits += 1

    return {
        "index": article_index,
        "article": article,
        "score": score,
        "exactThemeHits": exact_theme_hits,
        "stockHits": stock_hits,
        "themeTermHits": theme_term_hits,
        "headlineTermHits": headline_term_hits,
        "titleThemeHits": title_theme_hits,
    }


def _is_confident_article_match(best_match: dict | None, second_match: dict | None = None) -> bool:
    if not best_match:
        return False
    if float(best_match.get("score", 0.0) or 0.0) < MIN_CONFIDENT_ARTICLE_MATCH_SCORE:
        return False
    exact_theme_hits = int(best_match.get("exactThemeHits", 0) or 0)
    stock_hits = int(best_match.get("stockHits", 0) or 0)
    theme_term_hits = int(best_match.get("themeTermHits", 0) or 0)
    title_theme_hits = int(best_match.get("titleThemeHits", 0) or 0)

    if exact_theme_hits <= 0 and theme_term_hits <= 0:
        return False
    if title_theme_hits <= 0 and not (stock_hits > 0 and theme_term_hits > 0):
        return False

    second_score = float((second_match or {}).get("score", 0.0) or 0.0)
    margin = float(best_match.get("score", 0.0) or 0.0) - second_score

    if title_theme_hits > 0:
        return True
    if stock_hits > 0 and theme_term_hits > 0:
        return True
    return margin >= MIN_CONFIDENT_ARTICLE_MATCH_MARGIN


def _rank_representative_articles(theme: dict, sorted_articles: list[dict]) -> list[dict]:
    if not sorted_articles:
        return []

    scored = [
        _score_article_relevance(theme, article, article_index)
        for article_index, article in enumerate(sorted_articles, 1)
    ]
    scored.sort(
        key=lambda item: (
            float(item.get("score", 0.0) or 0.0),
            int(item.get("titleThemeHits", 0) or 0),
            int(item.get("exactThemeHits", 0) or 0),
            int(item.get("themeTermHits", 0) or 0),
            int(item.get("stockHits", 0) or 0),
            int(item.get("headlineTermHits", 0) or 0),
        ),
        reverse=True,
    )
    return scored


def _select_confident_article_match(
    scored: list[dict],
    preferred_index: int = 0,
    excluded_indices: set[int] | None = None,
    excluded_urls: set[str] | None = None,
) -> dict | None:
    excluded_indices = set(excluded_indices or set())
    excluded_urls = {url for url in (excluded_urls or set()) if url}
    available = [
        item for item in scored
        if item.get("index") not in excluded_indices
        and _convert_to_article_url(item.get("article", {}).get("url", "")) not in excluded_urls
    ]

    if not available:
        return None

    best_match = available[0]
    second_match = available[1] if len(available) > 1 else None

    if preferred_index > 0:
        preferred_match = next((item for item in available if item["index"] == preferred_index), None)
        preferred_second = (
            best_match if preferred_match and preferred_match["index"] != best_match["index"] else second_match
        )
        if preferred_match and _is_confident_article_match(preferred_match, preferred_second):
            preferred_score = float(preferred_match.get("score", 0.0) or 0.0)
            best_score = float(best_match.get("score", 0.0) or 0.0)
            if preferred_score >= best_score - MIN_CONFIDENT_ARTICLE_MATCH_MARGIN:
                return preferred_match

    if _is_confident_article_match(best_match, second_match):
        return best_match
    return None


def _resolve_representative_article(
    theme: dict,
    sorted_articles: list[dict],
    excluded_indices: set[int] | None = None,
    excluded_urls: set[str] | None = None,
) -> dict | None:
    scored = _rank_representative_articles(theme, sorted_articles)
    if not scored:
        return None

    raw_index = theme.get("representativeArticleIndex", 0)
    try:
        preferred_index = int(raw_index or 0)
    except (TypeError, ValueError):
        preferred_index = 0

    return _select_confident_article_match(
        scored,
        preferred_index=preferred_index,
        excluded_indices=excluded_indices,
        excluded_urls=excluded_urls,
    )


def _build_local_headline_link(match: dict) -> dict:
    article = match.get("article", {}) or {}
    return _build_headline_link(
        url=_convert_to_article_url(article.get("url", "")),
        title=article.get("title", ""),
        source_type="crawler_article",
        confidence="verified",
        article_index=int(match.get("index", 0) or 0),
        source_name=article.get("source", ""),
        published_at=article.get("date", ""),
        match_score=float(match.get("score", 0.0) or 0.0),
    )


def _build_google_headline_links(theme: dict, used_urls: set[str] | None = None) -> list[dict]:
    used_urls = {url for url in (used_urls or set()) if url}
    queries = _build_google_news_queries(theme)
    results: list[dict] = []

    for query in queries:
        for link in _search_google_news_links(query):
            if link.get("url") in used_urls:
                continue
            results.append(link)
            used_urls.add(link.get("url", ""))
            if len(results) >= GOOGLE_NEWS_FALLBACK_LIMIT:
                break
        if len(results) >= GOOGLE_NEWS_FALLBACK_LIMIT:
            break

    if results:
        search_query = results[0].get("query", "")
        search_url = _build_google_news_search_url(search_query)
        if search_url and search_url not in used_urls:
            results.append(
                _build_headline_link(
                    url=search_url,
                    title=f"{search_query} Google 뉴스 검색",
                    source_type="google_news_search",
                    confidence="fallback",
                    query=search_query,
                )
            )
        return _dedupe_headline_links(results)

    fallback_query = queries[0] if queries else (theme.get("themeName", "") or "").strip()
    search_url = _build_google_news_search_url(fallback_query)
    if not search_url:
        return []
    return [
        _build_headline_link(
            url=search_url,
            title=f"{fallback_query} Google 뉴스 검색",
            source_type="google_news_search",
            confidence="fallback",
            query=fallback_query,
        )
    ]


def _bind_verified_headline(
    theme: dict,
    sorted_articles: list[dict],
    used_article_indices: set[int] | None = None,
    used_urls: set[str] | None = None,
) -> None:
    used_article_indices = used_article_indices if used_article_indices is not None else set()
    used_urls = used_urls if used_urls is not None else set()

    match = _resolve_representative_article(
        theme,
        sorted_articles,
        excluded_indices=used_article_indices,
        excluded_urls=used_urls,
    )
    if match:
        local_link = _build_local_headline_link(match)
        theme["representativeArticleIndex"] = match["index"]
        _set_theme_headline_links(theme, [local_link])
        used_article_indices.add(int(match["index"]))
        if local_link.get("url"):
            used_urls.add(local_link["url"])
        return

    theme["representativeArticleIndex"] = 0
    google_links = _build_google_headline_links(theme, used_urls=used_urls)
    _set_theme_headline_links(theme, google_links)
    for link in google_links:
        if link.get("url"):
            used_urls.add(link["url"])
    if google_links:
        print(
            f"  [!] 테마 '{theme.get('themeName', '')}'에 로컬 대표 기사가 없어 "
            f"{google_links[0].get('sourceType', 'fallback')} 링크를 사용합니다."
        )
        return

    print(f"  [!] 테마 '{theme.get('themeName', '')}'에 대표 기사 링크를 찾지 못했습니다.")


def _count_rule_matches(text: str, rule: dict) -> tuple[int, list[str]]:
    hits: list[str] = []
    haystack = text or ""
    for keyword in rule.get("keywords", []):
        if keyword and keyword in haystack and keyword not in hits:
            hits.append(keyword)
    for stock in rule.get("stocks", []):
        if stock and stock in haystack and stock not in hits:
            hits.append(stock)
    return len(hits), hits


def _get_youtube_signals() -> list[dict]:
    try:
        return fetch_latest_youtube_theme_signals(list(STOCK_CODE_MAP.keys()))
    except Exception as e:
        print(f"  [!] 유튜브 시그널 수집 실패: {e}")
        return []


def _get_telegram_signals() -> list[dict]:
    try:
        return load_telegram_signals(channel=os.getenv("TG_CHANNEL_USERNAME", "@faststocknews"))
    except Exception as e:
        print(f"  [!] 텔레그램 시그널 로드 실패: {e}")
        return []


def _get_price_signal_payload() -> dict:
    try:
        return load_price_signal_payload() or {}
    except Exception as e:
        print(f"  [!] 가격 기반 시그널 로드 실패: {e}")
        return {}


def _get_antwinner_signals() -> list[dict]:
    """개미승리 실시간 상위 5개 테마 시그널을 수집합니다."""
    try:
        themes = fetch_antwinner_top_themes(top_n=5)
    except Exception as e:
        print(f"  [!] 개미승리 시그널 수집 실패: {e}")

        themes = []

    if themes:
        payload = build_antwinner_payload(themes)
        try:
            save_antwinner_payload(payload)
        except Exception as e:
            print(f"  [!] 개미승리 시그널 저장 실패: {e}")
        return themes

    # 실패 시 캐시 시도
    try:
        cached = load_antwinner_payload()
        if cached:
            print("  [!] 캐시에서 개미승리 데이터 로드")
            return cached.get("themes", [])
    except Exception as e:
        print(f"  [!] 개미승리 캐시 로드 실패: {e}")

    return []


def _get_antwinner_keywords(antwinner_signals: list[dict]) -> set[str]:
    """개미승리 상위 테마에서 테마명 + 종목명 키워드를 추출합니다."""
    keywords = set()
    for theme in antwinner_signals:
        thema = theme.get("thema", "")
        if thema:
            keywords.add(thema)
        for company in theme.get("companies", []):
            stockname = company.get("stockname", "")
            if stockname:
                keywords.add(stockname)
    return keywords


def _is_antwinner_weighted_article(article: dict, antwinner_keywords: set[str]) -> bool:
    """기사가 개미승리 상위 테마와 관련 있는지 확인합니다."""
    haystack = " ".join([
        article.get("title", ""),
        article.get("summary", ""),
        article.get("source", ""),
    ])
    return any(keyword in haystack for keyword in antwinner_keywords)


def _get_youtube_keywords(youtube_signals: list[dict]) -> set[str]:
    keywords = set()
    for signal in youtube_signals:
        keywords.update(signal.get("sectors", []))
        keywords.update(signal.get("stocks", []))
    return {keyword for keyword in keywords if keyword}


def _is_youtube_weighted_article(article: dict, youtube_keywords: set[str]) -> bool:
    haystack = " ".join([
        article.get("title", ""),
        article.get("summary", ""),
        article.get("source", ""),
    ])
    return any(keyword in haystack for keyword in youtube_keywords)


def format_antwinner_signals_for_prompt(antwinner_signals: list[dict]) -> str:
    """개미승리 상위 테마를 프롬프트 텍스트로 포맷합니다."""
    if not antwinner_signals:
        return "수집 실패 또는 해당 데이터 없음"

    lines = []
    for i, theme in enumerate(antwinner_signals, 1):
        thema = theme.get("thema", "")
        avg_rate = theme.get("average_rate", 0.0)
        rising_ratio = theme.get("rising_ratio", "")
        companies = theme.get("companies", [])

        stock_details = []
        for c in companies[:3]:  # 프롬프트에는 대표 종목 3개만 표시
            stock_details.append(
                f"{c.get('stockname', '')}({c.get('fluctuation', '')})"
            )
        stocks_str = ", ".join(stock_details)

        lines.append(
            f"  {i}. [{thema}] 평균등락률 {avg_rate:+.2f}% | 상승비율 {rising_ratio}"
            f" | 종목: {stocks_str}"
        )
    return "\n".join(lines)


def format_youtube_signals_for_prompt(youtube_signals: list[dict]) -> str:
    if not youtube_signals:
        return "수집 실패 또는 해당 영상 없음"

    lines = []
    for signal in youtube_signals:
        sectors = ", ".join(signal.get("sectors", []))
        stocks = ", ".join(signal.get("stocks", []))
        lines.append(
            f"- {signal['signal_type']} 관심테마"
            f" (업로드일 {signal.get('upload_date', '미상')}, URL: {signal.get('video_url', '')})"
            f" | 섹터: {sectors} | 종목: {stocks}"
        )
    return "\n".join(lines)


def format_telegram_signals_for_prompt(telegram_signals: list[dict], limit: int = 12) -> str:
    if not telegram_signals:
        return "수집 실패 또는 해당 메시지 없음"

    lines = []
    ordered = sorted(
        telegram_signals,
        key=lambda item: (item.get("score", 0.0), item.get("postedAt", "")),
        reverse=True,
    )[:limit]

    for signal in ordered:
        posted_at = signal.get("postedAt", "")
        posted_label = posted_at[11:16] if len(posted_at) >= 16 else posted_at or "시각 미상"
        matched_stocks = ", ".join(signal.get("matchedStocks", [])) or "매칭 종목 없음"
        keywords = ", ".join(signal.get("keywords", [])) or "키워드 없음"
        lines.append(
            f"- {posted_label} | {signal.get('text', '')}"
            f" | 종목: {matched_stocks} | 키워드: {keywords} | score={signal.get('score', 0.0):.2f}"
        )
    return "\n".join(lines)


def format_theme_candidates_for_prompt(articles: list[dict], telegram_signals: list[dict]) -> str:
    if not articles and not telegram_signals:
        return "추가 후보 없음"

    lines = []
    for rule in THEME_CANDIDATE_RULES:
        article_hits = 0
        telegram_hits = 0
        matched_terms: list[str] = []
        article_examples: list[str] = []

        for article in articles:
            combined = " ".join([
                article.get("title", ""),
                article.get("summary", ""),
                article.get("source", ""),
            ])
            hit_count, hits = _count_rule_matches(combined, rule)
            if hit_count:
                article_hits += 1
                for hit in hits:
                    if hit not in matched_terms:
                        matched_terms.append(hit)
                title = article.get("title", "").strip()
                if title and title not in article_examples and len(article_examples) < 3:
                    article_examples.append(title)

        for signal in telegram_signals:
            combined = " ".join([
                signal.get("text", ""),
                " ".join(signal.get("keywords", [])),
                " ".join(signal.get("matchedStocks", [])),
            ])
            hit_count, hits = _count_rule_matches(combined, rule)
            if hit_count:
                telegram_hits += 1
                for hit in hits:
                    if hit not in matched_terms:
                        matched_terms.append(hit)

        if not article_hits and not telegram_hits:
            continue

        stocks = ", ".join(rule.get("stocks", []))
        terms = ", ".join(matched_terms[:8]) or "매칭어 없음"
        examples = " / ".join(article_examples) if article_examples else "연관 기사 제목 없음"
        lines.append(
            f"- {rule['theme']} | 기사 {article_hits}건 | 텔레그램 {telegram_hits}건"
            f" | 대표 종목: {stocks} | 매칭어: {terms} | 기사 예시: {examples}"
        )

    return "\n".join(lines) if lines else "추가 후보 없음"


def format_price_signal_candidates_for_prompt(price_signal_payload: dict, limit: int = 8) -> str:
    candidates = (price_signal_payload or {}).get("candidates", [])
    if not candidates:
        return "수집 실패 또는 해당 가격 시그널 없음"

    lines = []
    for candidate in candidates[:limit]:
        matched_stocks = ", ".join(candidate.get("matchedStocks", [])) or "대표 종목 없음"
        keywords = ", ".join(candidate.get("keywords", [])) or "키워드 없음"
        reasoning = candidate.get("reasoning", "")
        lines.append(
            f"- {candidate.get('themeName', '')}"
            f" | score={float(candidate.get('score', 0.0) or 0.0):.2f}"
            f" | 종목: {matched_stocks}"
            f" | 키워드: {keywords}"
            f" | 근거: {reasoning}"
        )
    return "\n".join(lines)


def _find_representative_article_index(articles: list[dict], candidate: dict) -> int:
    theme_stub = {
        "themeName": candidate.get("themeName", ""),
        "headline": candidate.get("matchedArticles", [""])[0] if candidate.get("matchedArticles") else "",
        "relatedStocks": list(candidate.get("matchedStocks", []))[:6],
        "representativeArticleIndex": 0,
    }
    match = _resolve_representative_article(theme_stub, articles)
    return int(match["index"]) if match else 0


def _build_theme_from_price_candidate(candidate: dict, articles: list[dict]) -> dict:
    article_idx = _find_representative_article_index(articles, candidate)
    headline = candidate.get("matchedArticles", [""])[0] if candidate.get("matchedArticles") else f"{candidate.get('themeName', '')} 테마 강세"
    return {
        "themeName": candidate.get("themeName", ""),
        "headline": headline[:50],
        "representativeArticleIndex": article_idx,
        "relatedStocks": list(candidate.get("matchedStocks", []))[:6],
        "reasoning": candidate.get("reasoning", "가격 기반 급등주 군집에서 포착된 강한 테마입니다."),
        "injectedByPostProcess": True,
        "source": "price_signals",
    }


def _find_replaceable_theme_index(themes: list[dict], price_candidates: list[dict]) -> int | None:
    candidate_names = {_normalize_theme_name(item.get("themeName", "")) for item in price_candidates[:6]}
    replaceable = {_normalize_theme_name(name) for name in POSTPROCESS_REPLACEABLE_THEMES}

    for idx, theme in enumerate(themes):
        normalized = _normalize_theme_name(theme.get("themeName", ""))
        if normalized in replaceable:
            return idx

    fallback_idx = None
    fallback_score = float("inf")
    for idx, theme in enumerate(themes):
        normalized = _normalize_theme_name(theme.get("themeName", ""))
        if normalized in candidate_names:
            continue
        stock_count = len(theme.get("relatedStocks", []))
        score = stock_count * 10
        if score < fallback_score:
            fallback_score = score
            fallback_idx = idx
    return fallback_idx


def _merge_overlapping_themes(themes: list[dict]) -> tuple[list[dict], set[str]]:
    merged = list(themes)
    removed_names: set[str] = set()

    for rule in THEME_MERGE_RULES:
        primary_idx = next(
            (idx for idx, theme in enumerate(merged) if _normalize_theme_name(theme.get("themeName", "")) == _normalize_theme_name(rule["primary"])),
            None,
        )
        secondary_idx = next(
            (idx for idx, theme in enumerate(merged) if _normalize_theme_name(theme.get("themeName", "")) == _normalize_theme_name(rule["secondary"])),
            None,
        )

        if primary_idx is None or secondary_idx is None or primary_idx == secondary_idx:
            continue

        primary = merged[primary_idx]
        secondary = merged[secondary_idx]
        primary_stocks = {stock for stock in primary.get("relatedStocks", []) if stock}
        secondary_stocks = {stock for stock in secondary.get("relatedStocks", []) if stock}
        if not primary_stocks or not secondary_stocks:
            continue

        overlap = primary_stocks & secondary_stocks
        overlap_ratio = len(overlap) / min(len(primary_stocks), len(secondary_stocks))
        if overlap_ratio < float(rule.get("threshold", THEME_OVERLAP_MERGE_THRESHOLD)):
            continue

        merged_stocks = []
        for stock in list(primary.get("relatedStocks", [])) + list(secondary.get("relatedStocks", [])):
            if stock and stock not in merged_stocks:
                merged_stocks.append(stock)
        primary["relatedStocks"] = merged_stocks[:6]

        merged_from = list(primary.get("mergedFromThemes", []))
        if secondary.get("themeName") not in merged_from:
            merged_from.append(secondary.get("themeName"))
        primary["mergedFromThemes"] = merged_from
        primary["mergeNote"] = (
            f"{secondary.get('themeName')} 테마와 종목 겹침 {len(overlap)}개로 높아 "
            f"{primary.get('themeName')}로 통합했습니다."
        )

        print(
            f"  [병합] '{secondary.get('themeName', '')}' 테마를 "
            f"'{primary.get('themeName', '')}' 테마로 통합했습니다. "
            f"(겹침 {len(overlap)}개, 비율 {overlap_ratio:.2f})"
        )

        removed_names.add(_normalize_theme_name(secondary.get("themeName", "")))
        del merged[secondary_idx]
        if secondary_idx < primary_idx:
            primary_idx -= 1

    return merged, removed_names


def _refill_themes_after_merge(
    themes: list[dict],
    price_candidates: list[dict],
    articles: list[dict],
    target_count: int = 7,
    excluded_names: set[str] | None = None,
) -> list[dict]:
    refilled = list(themes)
    existing_names = {_normalize_theme_name(theme.get("themeName", "")) for theme in refilled}
    excluded = set(excluded_names or set())

    for candidate in price_candidates:
        if len(refilled) >= target_count:
            break

        normalized = _normalize_theme_name(candidate.get("themeName", ""))
        if normalized in existing_names:
            continue
        if normalized in excluded:
            continue
        if len(candidate.get("matchedStocks", [])) < 4:
            continue

        refilled.append(_build_theme_from_price_candidate(candidate, articles))
        existing_names.add(normalized)
        print(f"  [보강] 병합 후 부족한 슬롯에 가격 기반 후보 '{candidate.get('themeName', '')}'를 추가했습니다.")

    return refilled


def apply_price_signal_postprocess(result: dict, articles: list[dict]) -> dict:
    themes = list(result.get("themes", []))
    price_candidates = list(result.get("priceSignalCandidates", []))
    if not themes or not price_candidates:
        return result

    existing_names = {_normalize_theme_name(theme.get("themeName", "")) for theme in themes}
    rescued_candidates = [
        candidate for candidate in price_candidates
        if float(candidate.get("score", 0.0) or 0.0) >= PRICE_SIGNAL_RESCUE_SCORE
        and len(candidate.get("matchedStocks", [])) >= 4
        and _normalize_theme_name(candidate.get("themeName", "")) not in existing_names
    ]

    for candidate in rescued_candidates:
        replace_idx = _find_replaceable_theme_index(themes, price_candidates)
        if replace_idx is None:
            if len(themes) >= 7:
                break
            themes.append(_build_theme_from_price_candidate(candidate, articles))
        else:
            replaced_name = themes[replace_idx].get("themeName", "")
            themes[replace_idx] = _build_theme_from_price_candidate(candidate, articles)
            themes[replace_idx]["replacedThemeName"] = replaced_name
            print(
                f"  [보정] 가격 기반 후보 '{candidate.get('themeName', '')}'를 반영하며 "
                f"'{replaced_name}' 테마를 교체했습니다."
            )
        existing_names.add(_normalize_theme_name(candidate.get("themeName", "")))

    themes, removed_names = _merge_overlapping_themes(themes)
    themes = _refill_themes_after_merge(
        themes,
        price_candidates,
        articles,
        target_count=7,
        excluded_names=removed_names,
    )
    result["themes"] = themes[:7]
    return result


def sort_articles_for_prompt(
    articles: list[dict],
    youtube_signals: list[dict] | None = None,
    antwinner_signals: list[dict] | None = None,
) -> list[dict]:
    youtube_keywords = _get_youtube_keywords(youtube_signals or [])
    antwinner_keywords = _get_antwinner_keywords(antwinner_signals or [])

    antwinner_priority = []
    youtube_priority = []
    priority = []
    normal = []
    for article in articles:
        title = article.get("title", "").strip()
        if _is_antwinner_weighted_article(article, antwinner_keywords):
            antwinner_priority.append(article)
        elif _is_youtube_weighted_article(article, youtube_keywords):
            youtube_priority.append(article)
        elif _is_priority_article(title):
            priority.append(article)
        else:
            normal.append(article)

    return antwinner_priority + youtube_priority + priority + normal


def format_articles_for_prompt(
    articles: list[dict],
    youtube_signals: list[dict] | None = None,
    antwinner_signals: list[dict] | None = None,
) -> str:
    """
    기사 리스트를 프롬프트에 넣을 텍스트로 변환합니다.
    [특징주], 강세, 상한가, 급등 등의 키워드가 포함된 기사를 최상단에 배치하고
    ★ 마커를 붙여 ChatGPT가 가중치를 줄 수 있도록 합니다.
    개미승리 관련 기사는 ● 마커로 최고 가중치를 부여합니다.
    """
    youtube_keywords = _get_youtube_keywords(youtube_signals or [])
    antwinner_keywords = _get_antwinner_keywords(antwinner_signals or [])
    sorted_articles = sort_articles_for_prompt(articles, youtube_signals, antwinner_signals)
    antwinner_priority_count = sum(1 for article in sorted_articles if _is_antwinner_weighted_article(article, antwinner_keywords))
    youtube_priority_count = sum(
        1 for article in sorted_articles
        if not _is_antwinner_weighted_article(article, antwinner_keywords)
        and _is_youtube_weighted_article(article, youtube_keywords)
    )
    priority_count = sum(
        1 for article in sorted_articles
        if not _is_antwinner_weighted_article(article, antwinner_keywords)
        and not _is_youtube_weighted_article(article, youtube_keywords)
        and _is_priority_article(article.get("title", "").strip())
    )

    lines = []
    for i, article in enumerate(sorted_articles, 1):
        title = article.get("title", "").strip()
        summary = article.get("summary", "").strip()
        is_antwinner_weighted = _is_antwinner_weighted_article(article, antwinner_keywords)
        is_youtube_weighted = _is_youtube_weighted_article(article, youtube_keywords)
        if is_antwinner_weighted:
            marker = "●"
        elif is_youtube_weighted:
            marker = "◆"
        elif _is_priority_article(title):
            marker = "★"
        else:
            marker = ""
        if summary:
            lines.append(f"{i}. {marker}[{title}] {summary}")
        else:
            lines.append(f"{i}. {marker}{title}")

    if antwinner_priority_count:
        print(f"  [●] 개미승리 테마 연관 기사 {antwinner_priority_count}개를 최상단에 배치했습니다.")
    if youtube_priority_count:
        print(f"  [◆] 유튜브 시그널 연관 기사 {youtube_priority_count}개를 최상단에 배치했습니다.")
    if priority_count:
        print(f"  [★] 우선순위 기사 {priority_count}개를 최상단에 배치했습니다.")

    return "\n".join(lines)

def _build_antwinner_stock_codes(antwinner_signals: list[dict]) -> dict[str, str]:
    """개미승리 데이터에서 종목명→종목코드 매핑을 추출합니다."""
    codes = {}
    for theme in antwinner_signals:
        for c in theme.get("companies", []):
            name = c.get("stockname", "")
            code = c.get("stock_code", "")
            if name and code:
                codes[name] = code
    return codes


def _apply_antwinner_top2_postprocess(result: dict, antwinner_signals: list[dict]) -> dict:
    """
    개미승리 상위 2개 테마가 GPT 결과에 반드시 포함되도록 후처리합니다.
    - 이미 포함된 경우: 상위 종목을 relatedStocks 앞쪽에 보장
    - 누락된 경우: 가장 약한 테마를 교체
    - 개미승리 종목 코드를 _antwinner_stock_codes에 직접 주입 (이름 검색 우회)
    """
    if not antwinner_signals or len(antwinner_signals) < 2:
        return result

    themes = result.get("themes", [])
    if not themes:
        return result

    # 개미승리 전체 종목 코드 매핑 추출
    ant_stock_codes = _build_antwinner_stock_codes(antwinner_signals)

    top2_antwinner = antwinner_signals[:2]
    gpt_theme_names = [t.get("themeName", "").strip() for t in themes]

    for rank, ant_theme in enumerate(top2_antwinner, 1):
        ant_name = ant_theme.get("thema", "")
        ant_companies = ant_theme.get("companies", [])
        # 상위 2개 종목 (반드시 포함)
        must_stocks = [c.get("stockname", "") for c in ant_companies[:2] if c.get("stockname")]

        # GPT 결과에서 유사한 테마 찾기
        matched_idx = None
        for i, gpt_name in enumerate(gpt_theme_names):
            if ant_name == gpt_name or ant_name in gpt_name or gpt_name in ant_name:
                matched_idx = i
                break

        if matched_idx is not None:
            # 이미 있는 테마 → 종목을 개미승리 종목으로 완전 교체 (GPT의 잘못된 종목명 제거)
            ant_stock_names = [c.get("stockname", "") for c in ant_companies if c.get("stockname")]
            themes[matched_idx]["relatedStocks"] = ant_stock_names[:6]
            themes[matched_idx]["_from_antwinner"] = True
            themes[matched_idx]["_antwinner_stock_codes"] = ant_stock_codes
            print(f"  [●] 개미승리 {rank}위 '{ant_name}' 이미 포함 → 종목을 개미승리 데이터로 교체 {ant_stock_names[:4]}")
        else:
            # 누락된 테마 → 마지막(가장 약한) 비-개미승리 테마를 교체
            new_theme = {
                "themeName": ant_name,
                "headline": f"{ant_name} 관련주 장중 강세, 평균 등락률 {ant_theme.get('average_rate', 0):+.1f}%",
                "representativeArticleIndex": 0,
                "relatedStocks": must_stocks.copy(),
                "reasoning": f"개미승리 실시간 등락률 {rank}위 테마 (평균 {ant_theme.get('average_rate', 0):+.1f}%, 상승비율 {ant_theme.get('rising_ratio', '')})",
                "_from_antwinner": True,
                "_antwinner_stock_codes": ant_stock_codes,
            }
            _fill_remaining_stocks(new_theme, ant_companies, themes)

            # 이미 삽입된 개미승리 테마를 건너뛰고 가장 뒤의 일반 테마를 교체
            replace_idx = len(themes) - 1
            while replace_idx >= 0 and themes[replace_idx].get("_from_antwinner"):
                replace_idx -= 1
            if replace_idx >= 0:
                replaced_name = themes[replace_idx].get("themeName", "")
                themes[replace_idx] = new_theme
                gpt_theme_names[replace_idx] = ant_name
                print(f"  [●] 개미승리 {rank}위 '{ant_name}' 강제 삽입 ('{replaced_name}' 교체)")
            else:
                print(f"  [!] 개미승리 {rank}위 '{ant_name}' 삽입할 슬롯 없음")

    # 모든 테마에 개미승리 종목 코드 매핑 전달
    for theme in themes:
        if "_antwinner_stock_codes" not in theme:
            theme["_antwinner_stock_codes"] = ant_stock_codes

    result["themes"] = themes
    return result


def _ensure_top_stocks(existing_stocks: list[str], must_stocks: list[str]) -> None:
    """must_stocks가 existing_stocks 앞쪽에 포함되도록 보장합니다."""
    for stock in must_stocks:
        if stock not in existing_stocks:
            if len(existing_stocks) >= 6:
                existing_stocks.pop()
            existing_stocks.insert(0, stock)
        else:
            idx = existing_stocks.index(stock)
            if idx > 1:
                existing_stocks.pop(idx)
                existing_stocks.insert(0, stock)


def _fill_remaining_stocks(
    new_theme: dict,
    ant_companies: list[dict],
    existing_themes: list[dict],
) -> None:
    """신규 삽입 테마의 종목을 6개로 채웁니다. 개미승리 3~4위 + 기존 테마 교차 종목."""
    current = new_theme.get("relatedStocks", [])

    for c in ant_companies[2:4]:
        stockname = c.get("stockname", "")
        if stockname and stockname not in current:
            current.append(stockname)

    for c in ant_companies[4:]:
        if len(current) >= 6:
            break
        stockname = c.get("stockname", "")
        if stockname and stockname not in current:
            current.append(stockname)

    new_theme["relatedStocks"] = current[:6]


def _deduplicate_stocks_across_themes(themes: list[dict], max_occurrences: int = 2) -> None:
    """전체 테마를 통틀어 동일 종목이 최대 max_occurrences 번만 나오도록 중복을 제거합니다. (우선순위: 앞쪽 테마)"""
    stock_counts = {}
    for theme in themes:
        filtered_stocks = []
        for stock in theme.get("relatedStocks", []):
            if not stock:
                continue
            count = stock_counts.get(stock, 0)
            if count < max_occurrences:
                filtered_stocks.append(stock)
                stock_counts[stock] = count + 1
            else:
                print(f"  [중복제한] 종목 '{stock}'이 이미 {max_occurrences}번 출현하여 '{theme.get('themeName', '')}' 테마에서 제외됩니다.")
        theme["relatedStocks"] = filtered_stocks


def analyze_themes(articles: list[dict], date_str: str = None) -> dict:
    """
    ChatGPT API를 사용하여 기사들에서 테마를 추출합니다.

    Args:
        articles: 크롤링된 기사 리스트
        date_str: 날짜 문자열 (예: "2026-04-10")

    Returns:
        {
            "themes": [
                {
                    "themeName": str,
                    "headline": str,
                    "relatedStocks": [str, ...],
                    "reasoning": str
                }, ...
            ]
        }
    """
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")

    client = get_openai_client()

    antwinner_signals = _get_antwinner_signals()
    youtube_signals = _get_youtube_signals()
    telegram_signals = _get_telegram_signals()
    price_signal_payload = _get_price_signal_payload()
    sorted_articles = sort_articles_for_prompt(articles, youtube_signals, antwinner_signals)
    antwinner_text = format_antwinner_signals_for_prompt(antwinner_signals)
    youtube_text = format_youtube_signals_for_prompt(youtube_signals)
    telegram_text = format_telegram_signals_for_prompt(telegram_signals)
    price_signal_text = format_price_signal_candidates_for_prompt(price_signal_payload)
    candidate_text = format_theme_candidates_for_prompt(articles, telegram_signals)
    articles_text = format_articles_for_prompt(articles, youtube_signals, antwinner_signals)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        count=len(articles),
        antwinner_text=antwinner_text,
        youtube_text=youtube_text,
        telegram_text=telegram_text,
        price_signal_text=price_signal_text,
        candidate_text=candidate_text,
        articles_text=articles_text,
    )

    model_name = _get_theme_analysis_model()

    print(f"[INFO] ChatGPT API 호출 중... (기사 {len(articles)}개 분석)")
    print(f"  [>] 분석 모델: {model_name}")
    print(f"  [>] 프롬프트 길이: {len(user_prompt):,}자")

    try:
        response = client.chat.completions.create(**_build_theme_analysis_request(model_name, user_prompt))

        result_text = response.choices[0].message.content
        print(f"  [OK] ChatGPT 응답 수신 완료")
        print(f"  [>] 토큰 사용: input={response.usage.prompt_tokens}, output={response.usage.completion_tokens}")

        result = json.loads(result_text)
        result["antwinnerSignals"] = antwinner_signals
        result["youtubeSignals"] = youtube_signals
        result["telegramSignals"] = telegram_signals
        result["priceSignalPayload"] = price_signal_payload
        result["priceSignalCandidates"] = price_signal_payload.get("candidates", [])
        result = apply_price_signal_postprocess(result, sorted_articles)

        # ── 개미승리 상위 2개 테마 강제 포함 후처리 ──
        result = _apply_antwinner_top2_postprocess(result, antwinner_signals)

        # 검증: themes 키 존재 및 5개인지
        if "themes" not in result:
            raise ValueError("응답에 'themes' 키가 없습니다.")

        themes = result["themes"]
        if len(themes) < 7:
            print(f"  [!] 테마가 {len(themes)}개만 추출되었습니다 (목표: 7개)")

        # 전체 섹터 통틀어서 같은 종목 최대 2번까지만 노출
        _deduplicate_stocks_across_themes(themes, max_occurrences=2)

         # 각 테마 검증 및 대표 기사 URL 매핑
        used_article_indices: set[int] = set()
        used_headline_urls: set[str] = set()
        for theme in themes:
            if "themeName" not in theme:
                raise ValueError(f"테마에 'themeName'이 없습니다: {theme}")
            if "relatedStocks" not in theme or len(theme["relatedStocks"]) < 4:
                print(f"  [!] 테마 '{theme.get('themeName')}'의 관련 종목이 부족합니다.")

            theme.pop("_from_antwinner", False)
            _bind_verified_headline(theme, sorted_articles, used_article_indices, used_headline_urls)

        print(f"\n[INFO] 추출된 테마:")
        for i, theme in enumerate(themes, 1):
            stocks_str = ", ".join(theme.get("relatedStocks", [])[:4])
            print(f"  {i}. {theme['themeName']}: {theme.get('headline', '')} → [{stocks_str}]")

        return result

    except json.JSONDecodeError as e:
        print(f"[ERROR] ChatGPT 응답 JSON 파싱 실패: {e}")
        print(f"  원본 응답: {result_text[:500]}")
        raise
    except Exception as e:
        print(f"[ERROR] ChatGPT API 호출 실패: {e}")
        raise


def save_analysis(analysis: dict, filepath: str = None) -> str:
    """분석 결과를 JSON 파일로 저장합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "theme_analysis.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 테마 분석 결과를 {filepath}에 저장했습니다.")
    return filepath


def load_analysis(filepath: str = None) -> dict:
    """저장된 분석 결과를 로드합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "theme_analysis.json")

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # 테스트: 저장된 기사 파일이 있으면 분석
    from crawler import load_articles
    articles = load_articles()
    if articles:
        result = analyze_themes(articles)
        save_analysis(result)
    else:
        print("크롤링된 기사가 없습니다. 먼저 crawler.py를 실행하세요.")
