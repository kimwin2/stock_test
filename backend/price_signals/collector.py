from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .cluster import discover_theme_candidates
from .models import PriceSignalPayload, RisingStockItem

try:
    from crawler import crawl_naver_finance_news_with_fallback
    from stock_data import get_stock_detail, search_stock_code
    from telegram.store import load_telegram_signals
except ModuleNotFoundError:
    from ..crawler import crawl_naver_finance_news_with_fallback
    from ..stock_data import get_stock_detail, search_stock_code
    from ..telegram.store import load_telegram_signals


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
KST = timezone(timedelta(hours=9))
RISE_URL = "https://finance.naver.com/sise/sise_rise.naver"
MARKET_MAP = {
    "KOSPI": "0",
    "KOSDAQ": "1",
}


def _parse_number(value: str) -> int:
    cleaned = re.sub(r"[^0-9-]", "", value or "")
    return int(cleaned or "0")


def _parse_float(value: str) -> float:
    cleaned = (value or "").replace("%", "").replace(",", "").replace("+", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _extract_code(href: str, stock_name: str) -> str:
    if href:
        parsed = urlparse(urljoin("https://finance.naver.com", href))
        query = parse_qs(parsed.query)
        if "code" in query and query["code"]:
            return query["code"][0]
    return search_stock_code(stock_name) or ""


def _parse_rise_table(html: str, market: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table.type_2 tr")
    items: list[dict] = []

    for tr in rows:
        link = tr.select_one("a.tltle")
        tds = tr.select("td")
        if not link or len(tds) < 10:
            continue

        name = link.get_text(strip=True)
        if not name:
            continue

        item = RisingStockItem(
            name=name,
            code=_extract_code(link.get("href", ""), name),
            market=market,
            rank=_parse_number(tds[0].get_text(" ", strip=True)),
            price=_parse_number(tds[2].get_text(" ", strip=True)),
            diff_text=tds[3].get_text(" ", strip=True),
            change_rate=_parse_float(tds[4].get_text(" ", strip=True)),
            volume=_parse_number(tds[5].get_text(" ", strip=True)),
            bid_price=_parse_number(tds[6].get_text(" ", strip=True)),
            ask_price=_parse_number(tds[7].get_text(" ", strip=True)),
            bid_volume=_parse_number(tds[8].get_text(" ", strip=True)),
            ask_volume=_parse_number(tds[9].get_text(" ", strip=True)),
            per=tds[10].get_text(" ", strip=True) if len(tds) > 10 else "",
            roe=tds[11].get_text(" ", strip=True) if len(tds) > 11 else "",
            upper_limit="상한가" in tds[3].get_text(" ", strip=True),
            source_url=urljoin("https://finance.naver.com", link.get("href", "")),
        )
        items.append(item.to_dict())
        if len(items) >= limit:
            break

    return items


def fetch_top_movers(markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"), limit_per_market: int = 30) -> list[dict]:
    movers: list[dict] = []
    for market in markets:
        market_code = MARKET_MAP.get(market)
        if market_code is None:
            continue

        resp = requests.get(
            RISE_URL,
            params={"sosok": market_code},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        movers.extend(_parse_rise_table(resp.text, market=market, limit=limit_per_market))

    return movers


def enrich_movers_with_stock_detail(movers: list[dict], max_items: int = 20) -> list[dict]:
    enriched: list[dict] = []
    for idx, item in enumerate(movers):
        current = dict(item)
        if idx < max_items and current.get("code"):
            detail = get_stock_detail(current["code"])
            if detail:
                current["volumeAmount"] = max(int(detail.get("volume_raw", 0) or 0), int(current.get("volumeAmount", 0) or 0))
                current["price"] = int(detail.get("price", current.get("price", 0)) or 0)
                current["changeRate"] = float(detail.get("changeRate", current.get("changeRate", 0.0)) or 0.0)
        enriched.append(current)
    return enriched


def _recent_telegram_signals(hours: int = 3) -> list[dict]:
    signals = load_telegram_signals(channel=os.getenv("TG_CHANNEL_USERNAME", "@faststocknews")) or []
    if not signals:
        return []

    cutoff = datetime.now(KST) - timedelta(hours=hours)
    recent: list[dict] = []
    for signal in signals:
        posted_at = signal.get("postedAt", "")
        try:
            dt = datetime.fromisoformat(posted_at)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        if dt.astimezone(KST) >= cutoff:
            recent.append(signal)
    return recent


def collect_price_theme_signals(
    market_limit: int = 30,
    news_target: int = 240,
    telegram_hours: int = 3,
    articles: list[dict] | None = None,
    telegram_signals: list[dict] | None = None,
) -> dict:
    movers = fetch_top_movers(limit_per_market=market_limit)
    movers = enrich_movers_with_stock_detail(movers)
    if articles is None:
        articles = crawl_naver_finance_news_with_fallback(news_target)
    if telegram_signals is None:
        telegram_signals = _recent_telegram_signals(hours=telegram_hours)
    candidates = discover_theme_candidates(movers, articles, telegram_signals)

    payload = PriceSignalPayload(
        collected_at=datetime.now(KST).isoformat(),
        markets=["KOSPI", "KOSDAQ"],
        movers=movers,
        candidates=candidates,
    ).to_dict()
    return payload
