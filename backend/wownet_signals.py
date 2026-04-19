from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


KST = timezone(timedelta(hours=9))
WOWLOG_LIST_URL = "https://www.wownet.co.kr/wowlog"
REQUEST_TIMEOUT = 20
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    )
}


@dataclass
class WownetStrongSector:
    name: str
    summary: str
    stock_text: str
    stocks: list[str]


@dataclass
class WownetFeaturedStock:
    summary: str
    stocks: list[str]


@dataclass
class WownetSignal:
    title: str
    post_url: str
    published_date: str
    title_date: str
    strong_sectors: list[dict]
    featured_stocks: list[dict]
    matched_stocks: list[str]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["strongSectors"] = payload.pop("strong_sectors")
        payload["featuredStocks"] = payload.pop("featured_stocks")
        payload["matchedStocks"] = payload.pop("matched_stocks")
        return payload


def _normalize_line(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def _parse_published_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y.%m.%d").date()
    except ValueError:
        return None


def _parse_title_date(title: str, published_date: date | None, reference_date: date) -> date | None:
    match = re.search(r"(?:(\d{1,2})\s*월\s*(\d{1,2})\s*일)|(?:(\d{1,2})\s*/\s*(\d{1,2}))", title or "")
    if not match:
        return None

    month = int(match.group(1) or match.group(3))
    day = int(match.group(2) or match.group(4))
    year = published_date.year if published_date else reference_date.year

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_known_stocks(text: str, known_stocks: list[str]) -> list[str]:
    cleaned = re.sub(r"\s+", "", text or "")
    spans: list[tuple[int, int, str]] = []

    for stock in sorted(set(known_stocks), key=len, reverse=True):
        if not stock or len(stock) < 2:
            continue

        start = cleaned.find(stock)
        while start != -1:
            end = start + len(stock)
            if not any(not (end <= saved_start or start >= saved_end) for saved_start, saved_end, _ in spans):
                spans.append((start, end, stock))
            start = cleaned.find(stock, start + 1)

    ordered: list[str] = []
    seen: set[str] = set()
    for _, _, stock in sorted(spans, key=lambda item: item[0]):
        if stock not in seen:
            ordered.append(stock)
            seen.add(stock)

    return ordered


def _normalize_stock_candidate(token: str) -> str:
    candidate = (token or "").strip(" -")
    candidate = re.sub(r"\s+", "", candidate)
    candidate = re.sub(r"등$", "", candidate)
    candidate = candidate.strip(",")
    if not candidate or len(candidate) < 2 or len(candidate) > 20:
        return ""
    if any(char.isdigit() for char in candidate):
        return ""
    if candidate.endswith("주"):
        return ""
    blocked_terms = ("관련", "업종", "테마", "강세", "상승", "급등", "확산", "기대감", "수혜")
    if any(term in candidate for term in blocked_terms):
        return ""
    return candidate


def _extract_heuristic_stocks(text: str, only_leading_clause: bool = False) -> list[str]:
    target = (text or "").strip()
    if only_leading_clause:
        target = re.split(r"[,，]", target, maxsplit=1)[0]

    tokens = re.split(r"[,，·/]", target)
    candidates: list[str] = []
    for token in tokens:
        candidate = _normalize_stock_candidate(token)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _merge_unique(values: list[str]) -> list[str]:
    merged: list[str] = []
    for value in values:
        if value and value not in merged:
            merged.append(value)
    return merged


def _extract_stock_list(text: str, known_stocks: list[str]) -> list[str]:
    stock_text = (text or "").replace(" ", "")
    stock_text = stock_text.replace(".", ",").replace("，", ",").replace("·", ",").replace("/", ",")
    stock_text = re.sub(r"(?:,?\s*등)\s*$", "", stock_text)

    known_stock_set = set(known_stocks)
    parsed = [
        part.strip()
        for part in stock_text.split(",")
        if part.strip() and part.strip() in known_stock_set
    ]
    known_matches = _extract_known_stocks(stock_text, known_stocks)

    return _merge_unique(parsed + known_matches + _extract_heuristic_stocks(text))


def _get_previous_market_day(reference_date: date) -> date:
    previous = reference_date - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous


def _get_allowed_title_dates(reference_date: date) -> set[date]:
    if reference_date.weekday() >= 5:
        return {_get_previous_market_day(reference_date)}
    return {reference_date, _get_previous_market_day(reference_date)}


def _extract_recent_post_entries(reference_date: date, scan_limit: int = 12) -> list[dict]:
    response = requests.get(WOWLOG_LIST_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    entries: list[dict] = []
    allowed_title_dates = _get_allowed_title_dates(reference_date)

    for anchor in soup.select("#todayHotStocksList article a")[:scan_limit]:
        href = (anchor.get("href") or "").strip()
        title_node = anchor.select_one("h3")
        published_node = anchor.select_one("p")

        title = _normalize_line(title_node.get_text(" ", strip=True) if title_node else "")
        published_text = _normalize_line(published_node.get_text(" ", strip=True) if published_node else "")
        published_date = _parse_published_date(published_text)
        title_date = _parse_title_date(title, published_date, reference_date)

        if not href or not title or not title_date:
            continue

        if title_date not in allowed_title_dates:
            continue

        entries.append(
            {
                "title": title,
                "post_url": urljoin(WOWLOG_LIST_URL, href),
                "published_date": published_text,
                "title_date": title_date.isoformat(),
            }
        )

    return entries


def _extract_section_lines(lines: list[str], start_header: str) -> list[str]:
    start_index = next((idx for idx, line in enumerate(lines) if line == start_header), -1)
    if start_index == -1:
        return []

    section_lines: list[str] = []
    for line in lines[start_index + 1:]:
        if re.match(r"^\d+\.\s*", line):
            break
        if line == "목록":
            break
        if line == "?":
            continue
        section_lines.append(line)

    return section_lines


def _parse_strong_sectors(lines: list[str], known_stocks: list[str]) -> list[dict]:
    sectors: list[dict] = []
    current: dict | None = None

    for line in lines:
        heading_match = re.match(r"^\d+\)\s*(.+)$", line)
        if heading_match:
            if current:
                sectors.append(current)
            current = WownetStrongSector(
                name=_normalize_line(heading_match.group(1)),
                summary="",
                stock_text="",
                stocks=[],
            ).__dict__
            continue

        if current is None:
            continue

        cleaned = re.sub(r"^-\s*", "", line).strip()
        if not cleaned:
            continue

        if re.match(r"^(상승\s*종목|관련\s*종목)\s*:", cleaned):
            stock_text = cleaned.split(":", 1)[1]
            current["stock_text"] = _normalize_line(stock_text)
            current["stocks"] = _extract_stock_list(stock_text, known_stocks)
            continue

        current["summary"] = f"{current['summary']} {cleaned}".strip()

    if current:
        sectors.append(current)

    return sectors


def _parse_featured_stocks(lines: list[str], known_stocks: list[str]) -> list[dict]:
    featured: list[dict] = []

    for line in lines:
        cleaned = re.sub(r"^-\s*", "", line).strip()
        if not cleaned or cleaned == "?":
            continue
        featured.append(
            WownetFeaturedStock(
                summary=cleaned,
                stocks=_merge_unique(
                    _extract_known_stocks(cleaned, known_stocks)
                    + _extract_heuristic_stocks(cleaned, only_leading_clause=True)
                ),
            ).__dict__
        )

    return featured


def _extract_detail_lines(post_url: str) -> list[str]:
    response = requests.get(post_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    content = soup.select_one(".detailContentBox")
    if content is None:
        return []

    lines: list[str] = []
    for raw_line in content.get_text("\n", strip=True).splitlines():
        line = _normalize_line(raw_line)
        if line and line not in {"?", "목록"}:
            lines.append(line)
    return lines


def fetch_latest_wownet_theme_signals(
    known_stocks: list[str],
    reference_date: date | None = None,
    scan_limit: int = 12,
) -> list[dict]:
    reference_date = reference_date or datetime.now(KST).date()
    entries = _extract_recent_post_entries(reference_date, scan_limit=scan_limit)

    signals: list[dict] = []
    for entry in entries:
        detail_lines = _extract_detail_lines(entry["post_url"])
        strong_sector_lines = _extract_section_lines(detail_lines, "4. 강세업종")
        featured_stock_lines = _extract_section_lines(detail_lines, "5. 특징주")

        strong_sectors = _parse_strong_sectors(strong_sector_lines, known_stocks)
        featured_stocks = _parse_featured_stocks(featured_stock_lines, known_stocks)
        if not strong_sectors and not featured_stocks:
            continue

        matched_stocks: list[str] = []
        for sector in strong_sectors:
            for stock in sector.get("stocks", []):
                if stock not in matched_stocks:
                    matched_stocks.append(stock)
        for item in featured_stocks:
            for stock in item.get("stocks", []):
                if stock not in matched_stocks:
                    matched_stocks.append(stock)

        signals.append(
            WownetSignal(
                title=entry["title"],
                post_url=entry["post_url"],
                published_date=entry["published_date"],
                title_date=entry["title_date"],
                strong_sectors=strong_sectors,
                featured_stocks=featured_stocks,
                matched_stocks=matched_stocks,
            ).to_dict()
        )

    return signals
