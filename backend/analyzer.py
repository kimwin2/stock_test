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
import html
import re
from functools import lru_cache
from urllib.parse import urlparse, parse_qs

import requests
from openai import OpenAI
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from mover_signals import fetch_mover_signals
    from stock_data import STOCK_CODE_MAP
    from stock_data import THEME_STOCK_UNIVERSE
    from youtube_signals import fetch_latest_youtube_theme_signals
except ModuleNotFoundError:
    from .mover_signals import fetch_mover_signals
    from .stock_data import STOCK_CODE_MAP
    from .stock_data import THEME_STOCK_UNIVERSE
    from .youtube_signals import fetch_latest_youtube_theme_signals

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


@lru_cache(maxsize=256)
def _resolve_original_article_url(naver_article_url: str) -> str:
    """네이버 뉴스 페이지에서 언론사 원문 링크를 추출합니다."""
    if not naver_article_url:
        return ""

    try:
        resp = requests.get(naver_article_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        origin_link = soup.select_one("a.media_end_head_origin_link")
        if origin_link:
            href = html.unescape(origin_link.get("href", "")).strip()
            if href:
                return href
    except Exception:
        pass

    return naver_article_url


def _convert_to_article_url(url: str) -> str:
    """
    네이버 금융 뉴스 URL을 언론사 원문 기사 URL로 변환합니다.

    finance.naver.com/news/news_read.naver?article_id=XXX&office_id=YYY
    → https://n.news.naver.com/mnews/article/YYY/XXX
    → 언론사 원문 URL
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
                naver_article_url = f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}"
                return _resolve_original_article_url(naver_article_url)
        if "n.news.naver.com" in (parsed.hostname or ""):
            return _resolve_original_article_url(url)
    except Exception:
        pass
    return url


def get_openai_client() -> OpenAI:
    """OpenAI 클라이언트를 생성합니다."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.\n"
            ".env 파일에 OPENAI_API_KEY=sk-... 형태로 설정해주세요."
        )
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """당신은 한국 주식시장 전문 애널리스트입니다. 단타 트레이딩에 특화되어 있으며, 
뉴스를 분석하여 당일 주도 테마를 정확히 파악하는 능력이 뛰어납니다.

오늘 수집된 증권 뉴스 기사들과 외부 고신뢰 시그널을 분석하여 다음을 수행하세요:

1. **테마 7개 추출**: 오늘 가장 주목받는 투자 테마 7개를 선정합니다.
   - 기사에서 반복적으로 언급되는 섹터/이슈를 테마로 선정
   - 거래대금이 몰릴 만한 핫한 테마를 우선 선정
   - 단타 기준으로 당일 강하게 움직인 섹터를 우선 선정하세요
   - ★ 표시가 된 기사는 실제 주가 움직임이 확인된 기사이므로 테마 선정 시 더 높은 가중치를 부여하세요
   - ◆ 표시가 된 기사는 유튜브 외부 시그널과 직접 겹치는 기사이므로 매우 높은 가중치를 부여하세요
   - `심플 관심종목 TV`의 최신 `내일 관심테마!` 및 `당일 관심테마!` 영상은 고신뢰 선행 시그널입니다. 뉴스와 겹치면 최우선 반영하고, 일부만 겹쳐도 강하게 반영하세요
   - 상한가, 20% 이상 급등, 특징주, 거래대금 급증이 확인되는 기사/종목은 반드시 강하게 반영하세요
   - 별도로 제공되는 `상한가/초급등 종목 시그널`은 무조건 먼저 검토하세요. 해당 종목의 당일 관련 뉴스가 있으면 그 뉴스에서 테마를 추론해 주도 섹터 후보에 포함하세요
   
2. **테마 배제 기준 (반드시 준수)**:
   - 단순 정부 정책 발표나 사회적 갈등 이슈는 테마로 선정하지 마세요
   - 거시경제 지표(금리, 환율, CPI 등)만 다루고 수혜 종목이 특정되지 않는 모호한 이슈는 배제하세요
   - 실적부진, 적자확대, 목표가하향, 하락, 급락, 악재 해소 실패, 규제 우려, 소송, 횡령, 불성실공시, 거래정지 같은 악재성 뉴스는 분석 대상에서 사실상 제외하고 테마 선정에 반영하지 마세요
   - 실제로 주가가 움직이는 구체적 섹터/업종 테마만 선정하세요
   - 나쁜 테마 예시 (이런 것은 절대 선정하지 마세요): "K-아이웨어", "고유가 피해지원금", "물가안정", "남북관계"
   - 좋은 테마 예시: "광통신", "반도체소부장", "2차전지", "방산", "건설", "AI반도체", "조선", "원전"

3. **각 테마별 정보**:
   - themeName: 테마명 (간결하게, 예: "광통신", "반도체소부장", "중동전쟁", "방산")
   - headline: 테마 관련 핵심 뉴스 한줄 요약 (기사 제목 스타일, 50자 이내)
   - representativeArticleIndex: 이 테마를 가장 잘 대표하는 기사 번호 (1부터 시작하는 정수)
   - relatedStocks: 해당 테마의 대장주 후보 종목명 6개
     반드시 아래 한국 상장 종목 후보 목록 안에서만 고르세요
     대형주만 고르지 말고, 당일 상한가/급등주/주도주가 있으면 우선 포함하세요
   - reasoning: 이 테마를 선정한 이유 (1-2문장)
   - 미국주식, ADR, ETF, 비상장사, 지수, 해외기업명(NVIDIA, AMD, Intel, Qualcomm 등)은 절대 넣지 마세요

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요."""

USER_PROMPT_TEMPLATE = """아래는 오늘({date}) 수집된 증권 뉴스 기사 중 선별된 {count}개입니다. 
분석하여 오늘의 주도 테마 7개를 추출해주세요.

=== 외부 고신뢰 시그널: 심플 관심종목 TV ===
{youtube_text}

=== 상한가/초급등 종목 시그널 ===
{mover_text}

=== 뉴스 기사 목록 ===
{articles_text}

=== 한국 상장 종목 후보 목록 ===
{stock_candidate_block}

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
NEGATIVE_KEYWORDS = [
    "실적부진", "부진", "적자", "적자전환", "급락", "하락", "하한가", "하락세",
    "목표가 하향", "목표가하향", "불성실공시", "악재", "우려", "소송", "횡령",
    "배임", "거래정지", "상장폐지", "리콜", "압수수색", "구속", "사망", "피해",
]


def _is_priority_article(title: str) -> bool:
    """기사 제목에 우선순위 키워드가 포함되어 있는지 확인합니다."""
    return any(kw in title for kw in PRIORITY_KEYWORDS)


def _is_negative_article(article: dict) -> bool:
    haystack = " ".join([
        article.get("title", ""),
        article.get("summary", ""),
    ])
    return any(keyword in haystack for keyword in NEGATIVE_KEYWORDS)


def _get_youtube_signals() -> list[dict]:
    try:
        return fetch_latest_youtube_theme_signals(list(STOCK_CODE_MAP.keys()))
    except Exception as e:
        print(f"  [!] 유튜브 시그널 수집 실패: {e}")
        return []


def _is_korean_listed_stock(stock_name: str) -> bool:
    if not stock_name:
        return False

    if stock_name in STOCK_CODE_MAP:
        return True

    for name in STOCK_CODE_MAP:
        if stock_name.startswith(name) or name.startswith(stock_name):
            return True

    return False


def _sanitize_related_stocks(theme: dict) -> None:
    stocks = theme.get("relatedStocks", [])
    sanitized = []
    for stock in stocks:
        if _is_korean_listed_stock(stock) and stock not in sanitized:
            sanitized.append(stock)
    theme["relatedStocks"] = sanitized


def _theme_matches_name(theme: dict, theme_name: str) -> bool:
    current = _normalize_theme_name(theme.get("themeName", ""))
    target = _normalize_theme_name(theme_name)
    return current == target or target in current or current in target


def _find_representative_article_index(
    theme_name: str,
    related_stocks: list[str],
    sorted_articles: list[dict],
) -> int:
    keywords = [theme_name, *related_stocks]
    for idx, article in enumerate(sorted_articles, 1):
        haystack = " ".join([article.get("title", ""), article.get("summary", "")])
        if any(keyword and keyword in haystack for keyword in keywords):
            return idx
    return 1


def supplement_missing_mover_themes(
    themes: list[dict],
    mover_signals: list[dict],
    sorted_articles: list[dict],
) -> list[dict]:
    weak_theme_keywords = ["금리", "환율", "물가", "CPI", "추경"]
    supplemented = list(themes)

    for theme_name, universe in THEME_STOCK_UNIVERSE.items():
        if any(_theme_matches_name(theme, theme_name) for theme in supplemented):
            continue

        mover_cluster = [
            signal for signal in mover_signals
            if signal.get("stock_name") in universe
        ]
        if len(mover_cluster) < 2:
            continue

        strong_cluster = any("상한가" in signal.get("status", "") for signal in mover_cluster)
        avg_change = sum(signal.get("change_rate", 0.0) for signal in mover_cluster) / len(mover_cluster)
        if not strong_cluster and avg_change < 18:
            continue

        ordered_stocks = []
        for signal in sorted(mover_cluster, key=lambda item: item.get("change_rate", 0.0), reverse=True):
            stock_name = signal.get("stock_name", "")
            if stock_name and stock_name not in ordered_stocks:
                ordered_stocks.append(stock_name)
        for stock_name in universe:
            if stock_name not in ordered_stocks:
                ordered_stocks.append(stock_name)

        new_theme = {
            "themeName": theme_name,
            "headline": f"{theme_name} 관련주 강세",
            "representativeArticleIndex": _find_representative_article_index(theme_name, ordered_stocks, sorted_articles),
            "relatedStocks": ordered_stocks[:6],
            "reasoning": f"{theme_name} 관련 종목이 상한가/급등 흐름으로 동시 부각돼 독립 테마로 보강했습니다.",
        }

        replace_index = next(
            (
                index for index, theme in enumerate(supplemented)
                if any(keyword in theme.get("themeName", "") for keyword in weak_theme_keywords)
            ),
            None,
        )

        if replace_index is not None:
            replaced_name = supplemented[replace_index].get("themeName", "")
            print(f"  [!] 급등주 기반 테마 보강: {replaced_name} -> {theme_name}")
            supplemented[replace_index] = new_theme
        else:
            print(f"  [!] 급등주 기반 테마 추가: {theme_name}")
            supplemented.append(new_theme)

    return supplemented


def _normalize_theme_name(theme_name: str) -> str:
    compact = re.sub(r"\s+", "", (theme_name or "")).lower()
    replacements = {
        "\uc778\uacf5\uc9c0\ub2a5": "ai",
        "\uc5d0\uc774\uc544\uc774": "ai",
        "\ubc18\ub3c4\uccb4": "semiconductor",
        "\uad11\ud1b5\uc2e0": "optical",
        "\uc18c\ubd80\uc7a5": "materials",
    }
    for source, target in replacements.items():
        compact = compact.replace(source, target)
    return compact


def _theme_family_key(theme_name: str) -> str:
    normalized = _normalize_theme_name(theme_name)
    if "semiconductor" in normalized and "ai" in normalized:
        return "ai_semiconductor"
    if "semiconductor" in normalized:
        return "semiconductor"
    return normalized


def _should_skip_as_duplicate_theme(theme: dict, kept_themes: list[dict]) -> bool:
    current_name = theme.get("themeName", "")
    current_family = _theme_family_key(current_name)
    current_normalized = _normalize_theme_name(current_name)
    current_stocks = set(theme.get("relatedStocks", []))

    for kept_theme in kept_themes:
        kept_name = kept_theme.get("themeName", "")
        kept_stocks = set(kept_theme.get("relatedStocks", []))
        overlap_count = len(current_stocks & kept_stocks)
        min_size = min(len(current_stocks), len(kept_stocks))
        overlap_ratio = overlap_count / min_size if min_size else 0

        if current_normalized == _normalize_theme_name(kept_name):
            return True
        if current_family == _theme_family_key(kept_name) and overlap_count >= 2:
            return True
        if overlap_count >= 4 and overlap_ratio >= 0.8:
            return True

    return False


def prune_duplicate_themes(themes: list[dict]) -> list[dict]:
    pruned = []
    for theme in themes:
        if _should_skip_as_duplicate_theme(theme, pruned):
            print(f"  [!] 중복 테마 제외: {theme.get('themeName', '')}")
            continue
        pruned.append(theme)
    return pruned


def merge_mover_signals_into_themes(themes: list[dict], mover_signals: list[dict]) -> None:
    for theme in themes:
        theme_name = theme.get("themeName", "").replace(" ", "")
        stocks = theme.get("relatedStocks", [])

        for signal in mover_signals:
            stock_name = signal.get("stock_name", "")
            suggested_themes = [t.replace(" ", "") for t in signal.get("suggested_themes", [])]
            universe_matches = any(
                stock_name in universe
                for key, universe in THEME_STOCK_UNIVERSE.items()
                if key.replace(" ", "") in theme_name or theme_name in key.replace(" ", "")
            )
            signal_matches_theme = any(
                suggested in theme_name or theme_name in suggested
                for suggested in suggested_themes
            )

            if (universe_matches or signal_matches_theme) and stock_name not in stocks:
                stocks.insert(0, stock_name)

        deduped = []
        for stock in stocks:
            if stock not in deduped:
                deduped.append(stock)
        theme["relatedStocks"] = deduped


def build_stock_candidate_prompt_block() -> str:
    lines = []
    for theme_name, stocks in THEME_STOCK_UNIVERSE.items():
        stock_text = ", ".join(stocks)
        lines.append(f"- {theme_name}: {stock_text}")
    return "\n".join(lines)


def format_mover_signals_for_prompt(mover_signals: list[dict]) -> str:
    if not mover_signals:
        return "해당 없음"

    lines = []
    for signal in mover_signals:
        matched_articles = " / ".join(signal.get("matched_articles", [])[:2]) or "관련 기사 없음"
        suggested_themes = ", ".join(signal.get("suggested_themes", [])) or "추론 전"
        lines.append(
            f"- {signal['stock_name']} ({signal['market']}, {signal['change_rate']:.2f}%, {signal['status']})"
            f" | 추정테마: {suggested_themes} | 당일뉴스: {matched_articles}"
        )
    return "\n".join(lines)


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


def sort_articles_for_prompt(articles: list[dict], youtube_signals: list[dict] | None = None) -> list[dict]:
    """프롬프트에 넣을 우선순위 순서로 기사 리스트를 정렬합니다."""
    youtube_keywords = _get_youtube_keywords(youtube_signals or [])

    youtube_priority = []
    priority = []
    normal = []
    excluded_negative = 0
    for article in articles:
        title = article.get("title", "").strip()
        if _is_negative_article(article) and not _is_priority_article(title):
            excluded_negative += 1
            continue
        if _is_youtube_weighted_article(article, youtube_keywords):
            youtube_priority.append(article)
        elif _is_priority_article(title):
            priority.append(article)
        else:
            normal.append(article)

    if youtube_priority:
        print(f"  [◆] 유튜브 시그널 연관 기사 {len(youtube_priority)}개를 최상단에 배치했습니다.")
    if priority:
        print(f"  [★] 우선순위 기사 {len(priority)}개를 최상단에 배치했습니다.")
    if excluded_negative:
        print(f"  [-] 악재성 기사 {excluded_negative}개를 프롬프트에서 제외했습니다.")

    return youtube_priority + priority + normal


def format_articles_for_prompt(sorted_articles: list[dict], youtube_signals: list[dict] | None = None) -> str:
    """
    기사 리스트를 프롬프트에 넣을 텍스트로 변환합니다.
    [특징주], 강세, 상한가, 급등 등의 키워드가 포함된 기사를 최상단에 배치하고
    ★ 마커를 붙여 ChatGPT가 가중치를 줄 수 있도록 합니다.
    """
    youtube_keywords = _get_youtube_keywords(youtube_signals or [])

    lines = []
    for i, article in enumerate(sorted_articles, 1):
        title = article.get("title", "").strip()
        summary = article.get("summary", "").strip()
        is_youtube_weighted = _is_youtube_weighted_article(article, youtube_keywords)
        marker = "◆" if is_youtube_weighted else ("★" if _is_priority_article(title) else "")
        if summary:
            lines.append(f"{i}. {marker}[{title}] {summary}")
        else:
            lines.append(f"{i}. {marker}{title}")

    return "\n".join(lines)


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

    youtube_signals = _get_youtube_signals()
    mover_signals = fetch_mover_signals(articles)
    stock_candidate_block = build_stock_candidate_prompt_block()
    youtube_text = format_youtube_signals_for_prompt(youtube_signals)
    mover_text = format_mover_signals_for_prompt(mover_signals)
    sorted_articles = sort_articles_for_prompt(articles, youtube_signals)
    articles_text = format_articles_for_prompt(sorted_articles, youtube_signals)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        count=len(sorted_articles),
        youtube_text=youtube_text,
        mover_text=mover_text,
        articles_text=articles_text,
        stock_candidate_block=stock_candidate_block,
    )

    print(f"[INFO] ChatGPT API 호출 중... (기사 {len(articles)}개 분석)")
    print(f"  [>] 프롬프트 길이: {len(user_prompt):,}자")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # 일관성 높게
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        print(f"  [OK] ChatGPT 응답 수신 완료")
        print(f"  [>] 토큰 사용: input={response.usage.prompt_tokens}, output={response.usage.completion_tokens}")

        result = json.loads(result_text)
        result["youtubeSignals"] = youtube_signals
        result["moverSignals"] = mover_signals

        # 검증: themes 키 존재 및 5개인지
        if "themes" not in result:
            raise ValueError("응답에 'themes' 키가 없습니다.")

        themes = result["themes"]
        if len(themes) < 7:
            print(f"  [!] 테마가 {len(themes)}개만 추출되었습니다 (목표: 7개)")

         # 각 테마 검증 및 대표 기사 URL 매핑
        merge_mover_signals_into_themes(themes, mover_signals)
        themes = supplement_missing_mover_themes(themes, mover_signals, sorted_articles)
        for theme in themes:
            _sanitize_related_stocks(theme)
            if "themeName" not in theme:
                raise ValueError(f"테마에 'themeName'이 없습니다: {theme}")
            if "relatedStocks" not in theme or len(theme["relatedStocks"]) < 4:
                print(f"  [!] 테마 '{theme.get('themeName')}'의 관련 종목이 부족합니다.")

            # 대표 기사 URL 매핑 → 원본 기사 URL로 변환
            article_idx = theme.get("representativeArticleIndex", 1)
            if isinstance(article_idx, int) and 1 <= article_idx <= len(sorted_articles):
                raw_url = sorted_articles[article_idx - 1].get("url", "")
            else:
                raw_url = sorted_articles[0].get("url", "") if sorted_articles else ""
            theme["headlineUrl"] = _convert_to_article_url(raw_url)

        print(f"\n[INFO] 추출된 테마:")
        themes = prune_duplicate_themes(themes)
        result["themes"] = themes

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
