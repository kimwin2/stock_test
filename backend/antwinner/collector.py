"""
개미승리(antwinner.com) 테마 데이터 수집기
- /api/all-themes 에서 등락률 상위 10개 테마 + 종목을 가져옵니다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests

ALL_THEMES_URL = "https://antwinner.com/api/all-themes"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://antwinner.com/",
}

TOP_N = 5  # 상위 5개 테마만 수집


def _parse_rate(rate_str: str) -> float:
    """'11.37%' → 11.37 으로 변환"""
    try:
        return float(str(rate_str).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _trim_companies(companies: list[dict], max_stocks: int = 6) -> list[dict]:
    """테마당 종목을 등락률 상위 max_stocks 개로 제한합니다."""
    sorted_companies = sorted(
        companies,
        key=lambda c: _parse_rate(c.get("fluctuation", "0%")),
        reverse=True,
    )
    return sorted_companies[:max_stocks]


def fetch_antwinner_top_themes(top_n: int = TOP_N) -> list[dict]:
    """
    개미승리 /api/all-themes 에서 등락률 상위 테마 top_n개를 수집합니다.

    Returns:
        [
            {
                "thema": "유리기판",
                "average_rate": 11.37,
                "all_avg_rate": 7.27,
                "rising_ratio": "86.67%",
                "stock_count": 15,
                "companies": [
                    {
                        "stockname": "한빛레이저",
                        "stock_code": "452190",
                        "fluctuation": "29.92%",
                        "current_price": "6,860",
                        "volume": "1626억"
                    }, ...  (최대 6개)
                ]
            }, ...
        ]
    """
    print(f"[INFO] 개미승리(antwinner.com) 테마 데이터 수집 중...")

    try:
        resp = requests.get(ALL_THEMES_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        raw_themes: list[dict] = resp.json()
    except requests.RequestException as e:
        print(f"  [!] 개미승리 API 요청 실패: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"  [!] 개미승리 JSON 파싱 실패: {e}")
        return []

    if not raw_themes:
        print("  [!] 개미승리 테마 데이터가 비어 있습니다.")
        return []

    # average_rate 기준 내림차순 정렬 (이미 정렬돼 있지만 보장)
    for theme in raw_themes:
        theme["_avg_rate"] = _parse_rate(theme.get("average_rate", "0%"))

    sorted_themes = sorted(raw_themes, key=lambda t: t["_avg_rate"], reverse=True)
    top_themes = sorted_themes[:top_n]

    results = []
    for theme in top_themes:
        results.append({
            "thema": theme.get("thema", ""),
            "average_rate": theme["_avg_rate"],
            "all_avg_rate": _parse_rate(theme.get("all_avg_rate", "0%")),
            "rising_ratio": theme.get("rising_ratio", ""),
            "stock_count": theme.get("stock_count", 0),
            "companies": _trim_companies(theme.get("companies", [])),
        })

    print(f"  [OK] 상위 {len(results)}개 테마 수집 완료")
    for i, t in enumerate(results, 1):
        stocks = ", ".join(c["stockname"] for c in t["companies"][:3])
        print(f"    {i}. {t['thema']} ({t['average_rate']:+.2f}%) → [{stocks}]")

    return results


def build_antwinner_payload(themes: list[dict]) -> dict:
    """수집한 테마 데이터를 저장 가능한 payload 형태로 조립합니다."""
    return {
        "collectedAt": datetime.now().isoformat(),
        "source": "antwinner.com",
        "topN": len(themes),
        "themes": themes,
    }
