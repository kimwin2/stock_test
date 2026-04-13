from __future__ import annotations

from dataclasses import dataclass, asdict
import re

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}

GENERIC_THEME_STOPWORDS = {
    "관련주", "테마", "업종", "섹터", "수혜주", "급등", "강세", "상한가", "주가",
    "증시", "시장", "코스피", "코스닥", "국내", "오늘", "정치", "실적", "이슈",
}


@dataclass
class MoverSignal:
    stock_name: str
    stock_code: str
    market: str
    price: str
    change_rate: float
    status: str
    matched_articles: list[str]
    suggested_themes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_rows(url: str, market_label: str) -> list[dict]:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    rows = []

    for tr in soup.select("table.type_2 tr"):
        cols = tr.select("td")
        if len(cols) < 5:
            continue

        link = tr.select_one("a[href*='/item/main.naver?code=']")
        if not link:
            continue

        href = link.get("href", "")
        stock_code = href.split("code=")[-1].strip()
        stock_name = link.get_text(strip=True)
        price = cols[2].get_text(strip=True)
        change_text = cols[3].get_text(" ", strip=True)
        rate_text = cols[4].get_text(strip=True).replace("%", "").replace("+", "")

        try:
            change_rate = float(rate_text)
        except ValueError:
            continue

        rows.append({
            "stock_name": stock_name,
            "stock_code": stock_code,
            "market": market_label,
            "price": price,
            "change_rate": change_rate,
            "status": change_text,
        })

    return rows


def _infer_themes_from_articles(stock_name: str, articles: list[dict]) -> tuple[list[str], list[str]]:
    matched_articles = []
    theme_keywords = []

    keyword_map = {
        "중동전쟁": ["중동", "이란", "호르무즈", "전쟁", "휴전"],
        "해운": ["해운", "유조선", "운임", "항로", "호르무즈"],
        "광통신": ["광통신", "통신", "광케이블", "광섬유"],
        "방산": ["방산", "국방", "미사일", "무기", "전투기"],
        "건설": ["건설", "재건", "인프라", "원전"],
        "원전": ["원전", "원자력", "SMR"],
        "반도체": ["반도체", "HBM", "AI", "메모리", "파운드리"],
        "2차전지": ["2차전지", "배터리", "양극재", "전고체"],
        "바이오": ["바이오", "제약", "임상", "의약"],
        "에너지": ["에너지", "태양광", "전력", "전기", "풍력"],
    }

    dynamic_patterns = [
        r"([가-힣A-Za-z0-9·\-/]{2,20})\s*(?:관련주|테마|업종|섹터|수혜주)",
        r"(?:관련주|테마|업종|섹터|수혜주)\s*([가-힣A-Za-z0-9·\-/]{2,20})",
    ]

    for article in articles:
        text = " ".join([article.get("title", ""), article.get("summary", "")])
        if stock_name not in text:
            continue
        matched_articles.append(article.get("title", ""))
        for theme, keywords in keyword_map.items():
            if any(keyword in text for keyword in keywords) and theme not in theme_keywords:
                theme_keywords.append(theme)
        for pattern in dynamic_patterns:
            for raw_candidate in re.findall(pattern, text):
                candidate = re.sub(r"[^\w가-힣]+", "", raw_candidate).strip()
                if len(candidate) < 2 or len(candidate) > 12:
                    continue
                if stock_name in candidate or candidate in stock_name:
                    continue
                if candidate in GENERIC_THEME_STOPWORDS:
                    continue
                if candidate not in theme_keywords:
                    theme_keywords.append(candidate)

    return matched_articles[:3], theme_keywords


def fetch_mover_signals(articles: list[dict]) -> list[dict]:
    rows = []
    rows.extend(_extract_rows("https://finance.naver.com/sise/sise_rise.naver?sosok=0", "KOSPI"))
    rows.extend(_extract_rows("https://finance.naver.com/sise/sise_rise.naver?sosok=1", "KOSDAQ"))

    deduped = {}
    for row in rows:
        name = row["stock_name"]
        current = deduped.get(name)
        if current is None or row["change_rate"] > current["change_rate"]:
            deduped[name] = row

    selected = []
    for row in deduped.values():
        is_upper = "상한가" in row["status"]
        is_large_mover = row["change_rate"] >= 15.0
        if not (is_upper or is_large_mover):
            continue

        matched_articles, suggested_themes = _infer_themes_from_articles(row["stock_name"], articles)
        selected.append(MoverSignal(
            stock_name=row["stock_name"],
            stock_code=row["stock_code"],
            market=row["market"],
            price=row["price"],
            change_rate=row["change_rate"],
            status=row["status"],
            matched_articles=matched_articles,
            suggested_themes=suggested_themes,
        ).to_dict())

    selected.sort(key=lambda item: item["change_rate"], reverse=True)
    return selected[:20]
