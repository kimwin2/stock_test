import time

import requests


def crawl_daum_finance_news(keyword="특징주", per_page=100, max_count=200):
    url = "https://finance.daum.net/api/news/search"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://finance.daum.net/news",
    }

    articles = []
    page = 1

    while len(articles) < max_count:
        params = {
            "keyword": keyword,
            "page": page,
            "perPage": per_page,
            "pagination": "true",
        }

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            articles.append({
                "title": item.get("title", ""),
                "summary": item.get("content", ""),
                "url": f"https://finance.daum.net/news/{item.get('newsId')}",
                "date": item.get("createdAt", ""),
                "source": "Daum",
                "cpName": item.get("cpName", ""),
            })
            if len(articles) >= max_count:
                break

        page += 1
        time.sleep(0.5)

    return articles[:max_count]
