"""
인포스탁(infostock.co.kr) 장중 강세 테마 데이터 수집기
- 장중 특징주 및 특징테마 > 오전/오후장 테마동향에서 로그인 없이 확인 가능한 강세 테마 상위 3개를 가져옵니다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

API_BASE_URL = "https://api.infostock.co.kr:9081/web"
SOURCE_URL = "https://infostock.co.kr/Company/MiddleFeatures"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://infostock.co.kr",
    "Referer": SOURCE_URL,
}
TOP_N = 3
THEME_MENU_TYPE = "MENU_MIDDLE_FEATURED"
THEME_NEWS_TYPES = ("MARKET_FLASH_THEME_PM", "MARKET_FLASH_THEME_AM")
DAILY_THEME_MENU_TYPE = "MENU_DAILY_FEATURED_THEME"


def _post_json(path: str, payload: dict[str, Any]) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/{path.lstrip('/')}",
        json=payload,
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _select_latest_theme_item(items: list[dict]) -> dict | None:
    for item in items:
        if item.get("newsType1") in THEME_NEWS_TYPES:
            return item
    return None


def _split_top_level_themes(raw_text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0

    for ch in raw_text or "":
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1

        if ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                parts.append(token)
            current = []
            continue

        current.append(ch)

    token = "".join(current).strip()
    if token:
        parts.append(token)

    return parts


def _normalize_spacing(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return text.strip(" ,/")


def _simplify_theme_name(value: str) -> str:
    text = _normalize_spacing(value)
    text = re.sub(r"\s*등\.{0,3}$", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = _normalize_spacing(text)
    return text.strip(" -")


def _compact_theme_key(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _simplify_theme_name(value)).lower()


def _theme_names_are_similar(left: str, right: str) -> bool:
    left_key = _compact_theme_key(left)
    right_key = _compact_theme_key(right)
    if not left_key or not right_key:
        return False
    return left_key == right_key or left_key in right_key or right_key in left_key


def _extract_strong_theme_names(content: str, top_n: int) -> list[dict]:
    soup = BeautifulSoup(content or "", "html.parser")
    strong_text = ""

    for cell in soup.find_all("td"):
        label = cell.get_text(" ", strip=True)
        if label == "강세 테마":
            sibling = cell.find_next_sibling("td")
            if sibling:
                strong_text = sibling.get_text(" ", strip=True)
                break

    if not strong_text:
        return []

    strong_text = re.sub(r"\s+등\.{0,3}\s*$", "", strong_text)

    results: list[dict] = []
    seen: set[str] = set()
    for raw_name in _split_top_level_themes(strong_text):
        simplified = _simplify_theme_name(raw_name)
        if not simplified:
            continue

        dedupe_key = re.sub(r"[^0-9A-Za-z가-힣]+", "", simplified).lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append(
            {
                "rank": len(results) + 1,
                "themeName": simplified,
                "rawThemeName": _normalize_spacing(raw_name),
            }
        )
        if len(results) >= top_n:
            break

    return results


def _load_daily_theme_reference_map() -> dict[str, dict]:
    try:
        daily_payload = _post_json("flash/list", {"menuType": DAILY_THEME_MENU_TYPE, "count": 5})
    except Exception:
        return {}

    items = (daily_payload.get("data") or {}).get("items", [])
    daily_item = next(
        (
            item
            for item in items
            if item.get("newsType1") == "MARKET_THEME_DAILY" and item.get("content")
        ),
        None,
    )
    if not daily_item:
        return {}

    soup = BeautifulSoup(daily_item.get("content", ""), "html.parser")
    reference_map: dict[str, dict] = {}
    for table in soup.select("table.dataframe"):
        anchors = [_normalize_spacing(anchor.get_text(" ", strip=True)) for anchor in table.select("a")]
        if len(anchors) < 2:
            continue

        raw_theme_name = anchors[0]
        theme_name = _simplify_theme_name(raw_theme_name)
        if not theme_name or theme_name in reference_map:
            continue

        reference_stocks: list[str] = []
        for stock_name in anchors[1:]:
            if stock_name and stock_name not in reference_stocks:
                reference_stocks.append(stock_name)

        reference_map[theme_name] = {
            "dailyThemeName": raw_theme_name,
            "dailyThemeTitle": daily_item.get("title", "").strip(),
            "referenceStocks": reference_stocks[:10],
        }

    return reference_map


def fetch_infostock_top_themes(top_n: int = TOP_N) -> list[dict]:
    """
    인포스탁 장중 테마동향에서 강세 테마 상위 top_n개를 수집합니다.
    """
    print("[INFO] 인포스탁 장중 강세 테마 데이터 수집 중...")

    try:
        board = _post_json("market/board", {"menuType": THEME_MENU_TYPE, "count": 10})
    except requests.RequestException as e:
        print(f"  [!] 인포스탁 목록 요청 실패: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"  [!] 인포스탁 목록 JSON 파싱 실패: {e}")
        return []

    items = (board.get("data") or {}).get("items", [])
    latest_item = _select_latest_theme_item(items)
    if not latest_item:
        print("  [!] 인포스탁 테마동향 항목을 찾지 못했습니다.")
        return []

    detail_payload = {
        "newsType": latest_item.get("newsType1", ""),
        "id": latest_item.get("id", ""),
    }

    try:
        detail = _post_json("market/detail", detail_payload)
    except requests.RequestException as e:
        print(f"  [!] 인포스탁 상세 요청 실패: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"  [!] 인포스탁 상세 JSON 파싱 실패: {e}")
        return []

    news_items = (detail.get("data") or {}).get("newsItem", [])
    content = (news_items[0] if news_items else {}).get("content", "")
    themes = _extract_strong_theme_names(content, top_n=top_n)
    if not themes:
        print("  [!] 인포스탁 강세 테마 추출 실패")
        return []

    daily_reference_map = _load_daily_theme_reference_map()
    enriched: list[dict] = []
    for theme in themes:
        matched_daily = next(
            (
                data
                for daily_theme_name, data in daily_reference_map.items()
                if _theme_names_are_similar(theme.get("themeName", ""), daily_theme_name)
                or _theme_names_are_similar(theme.get("rawThemeName", ""), daily_theme_name)
            ),
            {},
        )
        enriched.append(
            {
                **theme,
                "title": latest_item.get("title", "").strip(),
                "sendDate": latest_item.get("sendDate", ""),
                "createTime": latest_item.get("createTime", ""),
                "newsType": latest_item.get("newsType1", ""),
                "detailId": latest_item.get("id", ""),
                "sourceUrl": SOURCE_URL,
                "referenceStocks": list(matched_daily.get("referenceStocks", [])),
                "dailyThemeName": matched_daily.get("dailyThemeName", ""),
                "dailyThemeTitle": matched_daily.get("dailyThemeTitle", ""),
            }
        )

    print(
        "  [OK] 인포스탁 강세 테마 "
        f"{len(enriched)}개 수집 완료 ({latest_item.get('title', '').strip()})"
    )
    for theme in enriched:
        print(f"    {theme['rank']}. {theme['themeName']} (원문: {theme['rawThemeName']})")

    return enriched


def build_infostock_payload(themes: list[dict]) -> dict:
    """수집한 테마 데이터를 저장 가능한 payload 형태로 조립합니다."""
    source_meta = {}
    if themes:
        first = themes[0]
        source_meta = {
            "title": first.get("title", ""),
            "sendDate": first.get("sendDate", ""),
            "createTime": first.get("createTime", ""),
            "newsType": first.get("newsType", ""),
            "detailId": first.get("detailId", ""),
            "sourceUrl": first.get("sourceUrl", SOURCE_URL),
        }

    return {
        "collectedAt": datetime.now().isoformat(),
        "source": "infostock.co.kr",
        "menuType": THEME_MENU_TYPE,
        "topN": len(themes),
        "sourceMeta": source_meta,
        "themes": themes,
    }
