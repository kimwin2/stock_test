"""
네이버 금융 뉴스 크롤러
- 네이버 증권 메인뉴스에서 기사 100개를 수집합니다.
- 각 기사의 제목, 요약, URL, 날짜를 추출합니다.
"""
from __future__ import annotations

import sys
import io
import requests
from bs4 import BeautifulSoup
import json
import time
import os
from datetime import datetime

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


NAVER_FINANCE_NEWS_URL = "https://finance.naver.com/news/mainnews.naver"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}


def crawl_single_page(page: int) -> list[dict]:
    """단일 페이지에서 뉴스 기사 목록을 크롤링합니다."""
    params = {"page": page}
    try:
        resp = requests.get(NAVER_FINANCE_NEWS_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 페이지 {page} 요청 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    articles = []

    # 네이버 금융 뉴스 메인 - 기사 리스트 파싱
    news_list = soup.select("ul.newsList li")
    if not news_list:
        # 대체 셀렉터 시도
        news_list = soup.select("div.mainNewsList li")
    if not news_list:
        news_list = soup.select("li.block1")

    for item in news_list:
        try:
            # 제목 & URL
            title_tag = item.select_one("dd.articleSubject a") or item.select_one("a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            if not title:
                continue

            href = title_tag.get("href", "")
            if href and not href.startswith("http"):
                href = "https://finance.naver.com" + href

            # 요약 (본문 미리보기)
            summary_tag = item.select_one("dd.articleSummary")
            summary = ""
            if summary_tag:
                # 기자명, 날짜 등의 span 제거 후 텍스트 추출
                for span in summary_tag.select("span"):
                    span.decompose()
                summary = summary_tag.get_text(strip=True)

            # 날짜
            date_tag = item.select_one("span.wdate") or item.select_one("dd.articleSummary span.date")
            date_str = date_tag.get_text(strip=True) if date_tag else ""

            # 출처
            source_tag = item.select_one("span.press")
            source = source_tag.get_text(strip=True) if source_tag else ""

            articles.append({
                "title": title,
                "summary": summary[:200] if summary else "",
                "url": href,
                "date": date_str,
                "source": source,
            })
        except Exception as e:
            print(f"[WARN] 기사 파싱 중 오류: {e}")
            continue

    return articles


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

    print(f"[INFO] 네이버 금융 뉴스 크롤링 시작 (목표: {target_count}개)")

    while len(all_articles) < target_count and page <= max_pages:
        print(f"  [>] 페이지 {page} 크롤링 중...")
        articles = crawl_single_page(page)

        if not articles:
            print(f"  [!] 페이지 {page}에서 기사를 찾지 못했습니다. 대체 방식으로 시도합니다.")
            # 대체: 네이버 금융 뉴스 속보 페이지
            articles = crawl_news_flash(page)

        if not articles:
            print(f"  [X] 페이지 {page}에서 기사를 가져올 수 없습니다.")
            break

        all_articles.extend(articles)
        page += 1
        time.sleep(0.5)  # 서버 부하 방지

    # 중복 제거 (URL 기준)
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        if article["url"] not in seen_urls:
            seen_urls.add(article["url"])
            unique_articles.append(article)

    result = unique_articles[:target_count]
    print(f"[INFO] 크롤링 완료: {len(result)}개 기사 수집")
    return result


def crawl_news_flash(page: int) -> list[dict]:
    """네이버 금융 속보 뉴스 대체 크롤링"""
    url = "https://finance.naver.com/news/news_list.naver"
    params = {"mode": "LSS2D", "section_id": "101", "section_id2": "258", "page": page}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 속보 페이지 {page} 요청 실패: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    articles = []

    # 속보 뉴스 리스트
    for item in soup.select("li.newsList"):
        try:
            a_tag = item.select_one("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if href and not href.startswith("http"):
                href = "https://finance.naver.com" + href

            summary_tag = item.select_one("p")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            articles.append({
                "title": title,
                "summary": summary[:200],
                "url": href,
                "date": "",
                "source": "",
            })
        except Exception:
            continue

    return articles


def crawl_market_news_list(target_count: int = 100) -> list[dict]:
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

    for section in sections:
        for page in range(1, 8):
            if len(all_articles) >= target_count:
                break

            params = {**section, "page": page}
            try:
                resp = requests.get(
                    "https://finance.naver.com/news/news_list.naver",
                    params=params, headers=HEADERS, timeout=10
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")

                for item in soup.select("dd.articleSubject a, li dd a"):
                    title = item.get_text(strip=True)
                    href = item.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://finance.naver.com" + href
                    if title:
                        all_articles.append({
                            "title": title,
                            "summary": "",
                            "url": href,
                            "date": "",
                            "source": "",
                        })

                time.sleep(0.3)
            except Exception as e:
                print(f"[WARN] 섹션 크롤링 오류: {e}")
                continue

    # 중복 제거
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return unique[:target_count]


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


if __name__ == "__main__":
    # 직접 실행 시 테스트
    articles = crawl_naver_finance_news(100)
    if len(articles) < 50:
        print("[INFO] 메인뉴스에서 충분한 기사를 못 찾아, 시장 뉴스 리스트로 보충합니다.")
        more = crawl_market_news_list(100 - len(articles))
        articles.extend(more)
        articles = articles[:100]

    save_articles(articles)
    print(f"\n수집된 기사 예시:")
    for i, a in enumerate(articles[:5]):
        print(f"  [{i+1}] {a['title']}")
