"""
네이버 금융 뉴스 크롤러
- 네이버 증권 메인뉴스를 우선 수집합니다.
- 메인뉴스가 부족하면 news_list 섹션으로 보충해 목표 개수를 채웁니다.
- 각 기사의 제목, 요약, URL, 날짜를 추출합니다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from daum_crawler import crawl_daum_finance_news
except ModuleNotFoundError:
    from .daum_crawler import crawl_daum_finance_news

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


NAVER_FINANCE_NEWS_URL = "https://finance.naver.com/news/mainnews.naver"
NAVER_NEWS_LIST_URL = "https://finance.naver.com/news/news_list.naver"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}


def _normalize_article_url(href: str) -> str:
    """기사 URL을 중복 제거에 적합한 형태로 정규화합니다."""
    absolute_url = urljoin("https://finance.naver.com", href)
    parsed = urlparse(absolute_url)
    query = parse_qs(parsed.query)
    article_id = query.get("article_id", [""])[0]
    office_id = query.get("office_id", [""])[0]

    if article_id and office_id:
        return (
            "https://finance.naver.com/news/news_read.naver"
            f"?article_id={article_id}&office_id={office_id}"
        )

    return absolute_url


def _parse_article_list(html: str, base_url: str = "https://finance.naver.com") -> list[dict]:
    """네이버 금융 기사 목록 페이지에서 기사 리스트를 파싱합니다."""
    soup = BeautifulSoup(html, "lxml")
    articles = []
    news_list = soup.select("ul.newsList li, ul.realtimeNewsList li")

    for item in news_list:
        try:
            title_tag = item.select_one("dd.articleSubject a") or item.select_one("a")
            if not title_tag:
                continue

            title = title_tag.get_text(" ", strip=True)
            if not title:
                continue

            href = title_tag.get("href", "").strip()
            if not href:
                continue

            summary_tag = item.select_one("dd.articleSummary")
            summary = ""
            if summary_tag:
                summary_texts = list(summary_tag.stripped_strings)
                if summary_texts:
                    filtered = [
                        text for text in summary_texts
                        if text not in {"|"}
                        and text != item.select_one("span.press").get_text(strip=True)
                        if item.select_one("span.press")
                    ]
                    if not filtered:
                        filtered = summary_texts
                    summary = " ".join(filtered[:-2] if len(filtered) >= 3 else filtered).strip()

            date_tag = item.select_one("span.wdate") or item.select_one("dd.articleSummary span.date")
            source_tag = item.select_one("span.press")

            articles.append({
                "title": title,
                "summary": summary[:200],
                "url": _normalize_article_url(urljoin(base_url, href)),
                "date": date_tag.get_text(strip=True) if date_tag else "",
                "source": source_tag.get_text(strip=True) if source_tag else "",
            })
        except Exception as e:
            print(f"[WARN] 기사 파싱 중 오류: {e}")

    return articles


def _dedupe_articles(articles: list[dict]) -> list[dict]:
    """URL 기준으로 기사 중복을 제거합니다."""
    seen_urls = set()
    unique_articles = []

    for article in articles:
        key = article.get("url") or article.get("title")
        if key in seen_urls:
            continue
        seen_urls.add(key)
        unique_articles.append(article)

    return unique_articles


def crawl_single_page(page: int, date: str | None = None) -> list[dict]:
    """메인뉴스 단일 페이지에서 기사 목록을 크롤링합니다."""
    params = {"page": page}
    if date:
        params["date"] = date
    try:
        resp = requests.get(NAVER_FINANCE_NEWS_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 페이지 {page} 요청 실패: {e}")
        return []

    return _parse_article_list(resp.text)


def crawl_naver_finance_news(target_count: int = 100) -> list[dict]:
    """
    네이버 금융 뉴스를 target_count개만큼 크롤링합니다.

    Args:
        target_count: 수집할 기사 수 (기본 100개)

    Returns:
        기사 리스트 [{title, summary, url, date, source}, ...]
    """
    all_articles = []
    page = 1
    max_pages = 40  # 안전장치: 최대 40페이지까지만
    previous_page_urls: set[str] = set()

    print(f"[INFO] 네이버 금융 뉴스 크롤링 시작 (목표: {target_count}개)")

    while len(_dedupe_articles(all_articles)) < target_count and page <= max_pages:
        print(f"  [>] 페이지 {page} 크롤링 중...")
        articles = crawl_single_page(page)

        if not articles:
            print(f"  [!] 페이지 {page}에서 기사를 찾지 못했습니다. 대체 방식으로 시도합니다.")
            # 대체: 네이버 금융 뉴스 속보 페이지
            articles = crawl_news_flash(page)

        if not articles:
            print(f"  [X] 페이지 {page}에서 기사를 가져올 수 없습니다.")
            break

        page_urls = {article["url"] for article in articles}
        if page_urls == previous_page_urls:
            print(f"  [!] 페이지 {page}가 이전 페이지와 동일해 크롤링을 종료합니다.")
            break

        all_articles.extend(articles)
        all_articles = _dedupe_articles(all_articles)
        previous_page_urls = page_urls
        page += 1
        time.sleep(0.5)  # 서버 부하 방지

    result = all_articles[:target_count]
    print(f"[INFO] 메인뉴스 크롤링 완료: {len(result)}개 기사 수집")
    return result


def crawl_mainnews_archive(target_count: int, recent_days: int = 7) -> list[dict]:
    """최근 며칠치 메인뉴스 아카이브에서 추가 기사를 수집합니다."""
    all_articles = []
    base_date = datetime.now().date()

    for day_offset in range(1, recent_days + 1):
        target_date = (base_date - timedelta(days=day_offset)).strftime("%Y-%m-%d")
        previous_page_urls: set[str] = set()

        for page in range(1, 15):
            if len(_dedupe_articles(all_articles)) >= target_count:
                break

            articles = crawl_single_page(page, date=target_date)
            if not articles:
                break

            page_urls = {article["url"] for article in articles}
            if page_urls == previous_page_urls:
                break

            all_articles.extend(articles)
            all_articles = _dedupe_articles(all_articles)
            previous_page_urls = page_urls
            time.sleep(0.3)

        if len(_dedupe_articles(all_articles)) >= target_count:
            break

    return all_articles[:target_count]


def crawl_news_flash(page: int) -> list[dict]:
    """네이버 금융 속보 뉴스 대체 크롤링"""
    url = NAVER_NEWS_LIST_URL
    params = {"mode": "LSS2D", "section_id": "101", "section_id2": "258", "page": page}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 속보 페이지 {page} 요청 실패: {e}")
        return []

    return _parse_article_list(resp.text)


def crawl_market_news_list(target_count: int = 100, recent_days: int = 7) -> list[dict]:
    """
    네이버 증권 시장 뉴스 크롤링 (더 안정적인 대체 방식)
    https://finance.naver.com/news/news_list.naver 사용
    """
    all_articles = []
    # 여러 섹션에서 수집
    sections = [
        {"mode": "LSS2D", "section_id": "101", "section_id2": "258"},  # 증권 속보
        {"mode": "LSS2D", "section_id": "101", "section_id2": "259"},  # 시황/전망
        {"mode": "LSS2D", "section_id": "101", "section_id2": "261"},  # 공시/실적
    ]

    base_date = datetime.now().date()

    for day_offset in range(recent_days):
        target_date = (base_date - timedelta(days=day_offset)).strftime("%Y%m%d")
        for section in sections:
            previous_page_urls: set[str] = set()
            for page in range(1, 15):
                if len(_dedupe_articles(all_articles)) >= target_count:
                    break

                params = {**section, "date": target_date, "page": page}
                try:
                    resp = requests.get(
                        NAVER_NEWS_LIST_URL,
                        params=params, headers=HEADERS, timeout=10
                    )
                    resp.raise_for_status()
                    articles = _parse_article_list(resp.text)
                    if not articles:
                        break

                    page_urls = {article["url"] for article in articles}
                    if page_urls == previous_page_urls:
                        break

                    all_articles.extend(articles)
                    all_articles = _dedupe_articles(all_articles)
                    previous_page_urls = page_urls

                    time.sleep(0.3)
                except Exception as e:
                    print(f"[WARN] 섹션 크롤링 오류: {e}")
                    continue

            if len(_dedupe_articles(all_articles)) >= target_count:
                break

        if len(_dedupe_articles(all_articles)) >= target_count:
            break

    return all_articles[:target_count]


def crawl_naver_finance_news_with_fallback(target_count: int = 200) -> list[dict]:
    """메인뉴스 우선, 부족하면 네이버 news_list 섹션으로 보충합니다."""
    articles = crawl_naver_finance_news(target_count)
    if len(articles) >= target_count:
        return articles[:target_count]

    missing_count = target_count - len(articles)
    print(f"[INFO] 메인뉴스 {len(articles)}개 수집. news_list로 {missing_count}개 보충합니다.")
    articles.extend(crawl_market_news_list(missing_count))
    articles = _dedupe_articles(articles)

    if len(articles) < target_count:
        missing_count = target_count - len(articles)
        print(f"[INFO] 아직 {missing_count}개 부족해 과거 메인뉴스 아카이브로 보충합니다.")
        articles.extend(crawl_mainnews_archive(missing_count))
        articles = _dedupe_articles(articles)

    if len(articles) < target_count:
        print(f"[WARN] 네이버 기사 수가 {len(articles)}개로 목표 {target_count}개를 채우지 못했습니다.")

    return articles[:target_count]


def save_articles(articles: list[dict], filepath: str = None) -> str:
    """크롤링된 기사를 JSON 파일로 저장합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "crawled_articles.json")

    data = {
        "crawledAt": datetime.now().isoformat(),
        "count": len(articles),
        "articles": articles,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 기사 {len(articles)}개를 {filepath}에 저장했습니다.")
    return filepath


def load_articles(filepath: str = None) -> list[dict]:
    """저장된 기사 JSON 파일을 로드합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "crawled_articles.json")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("articles", [])

def crawl_all_news(keyword="특징주", target_count=400):
    """Naver와 Daum 뉴스를 함께 크롤링합니다."""
    print(f"[INFO] {keyword} crawling start...")

    naver_target = min(200, target_count)
    daum_target = max(0, target_count - naver_target)

    naver_articles = crawl_naver_finance_news_with_fallback(naver_target)

    daum_articles = crawl_daum_finance_news(keyword=keyword, max_count=daum_target) if daum_target else []

    all_articles = naver_articles + daum_articles
    save_articles(all_articles)
    return all_articles


if __name__ == "__main__":
    # 직접 실행 시 테스트
    articles = crawl_naver_finance_news_with_fallback(100)

    save_articles(articles)
    print(f"\n수집된 기사 예시:")
    for i, a in enumerate(articles[:5]):
        print(f"  [{i+1}] {a['title']}")
