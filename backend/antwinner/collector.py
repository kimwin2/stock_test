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

# ── 테마가 아닌 버킷 ────────────────────────────────────────────────
# 개미승리 카테고리에는 업종이 아니라 **그날 장이 어땠는지**를 가리키는
# 버킷이 섞여 있다. 이건 테마가 아니다. 테마는 "이 종목들이 무슨 사업을
# 해서 같이 움직였나"에 답해야 하는데, '하락장' 은 그 답을 하지 않는다.
#
# 실사고 (2026-08-18): 개미승리 등락률 2위가 `하락장` 이었다.
#   `analyzer._apply_antwinner_top2_postprocess` 가 상위 2개를 **검증 없이
#   강제 삽입**하므로 그대로 테마 카드가 됐고, 코데즈컴바인(의류)·
#   신라섬유(섬유)·양지사(문구)가 한 카드에 묶였다. 업종 공통점이 없다.
#   같은 날 근거가 훨씬 단단한 급등클러스터 후보 `의류 및 패션`
#   (134점 · 좋은사람들·코데즈컴바인·인디에프 동반 상한가)은 자리를 못 얻었다.
#   즉 가짜 테마 하나가 진짜 테마 하나를 밀어낸다.
#
# 걸러내는 축은 셋이다 — 장세 / 매매 스타일 / 가격 패턴.
# 재무 속성(저PBR·고배당)이나 유통물량(품절주)은 **일부러 넣지 않았다.**
# 그건 "왜 올랐나"에 실제로 답하는 근거라 테마로 볼 여지가 있다.
# 여기 목록은 추측으로 늘리지 말고, 아래 로그에 찍힌 실제 버킷명만 보고 늘린다.
NON_THEME_BUCKETS = {
    # 장세
    "하락장", "상승장", "급등장", "급락장", "횡보장", "약세장", "강세장", "보합장",
    "지수방어", "위험회피", "안전자산선호", "헤지", "햇지", "순환매",
    # 매매 스타일 · 수급 형태
    "개별주", "테마주", "급등주", "주도주", "단타", "스윙", "낙폭과대", "반등주",
    # 가격 패턴 · 이벤트
    "상한가", "하한가", "신고가", "신저가", "저가주", "동전주", "실적", "실적주",
}


def _norm_bucket(name: str) -> str:
    """비교용 정규화 — 공백/가운뎃점 제거."""
    return "".join(str(name or "").split()).replace("·", "").replace("/", "")


_NON_THEME_NORM = {_norm_bucket(x) for x in NON_THEME_BUCKETS}


def is_non_theme_bucket(name: str) -> bool:
    """업종이 아니라 장세·매매스타일·가격패턴을 가리키는 이름인가."""
    return _norm_bucket(name) in _NON_THEME_NORM


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

    # 테마가 아닌 버킷은 **상위 N 을 자르기 전에** 뺀다.
    # 자른 뒤에 빼면 그 자리가 그냥 비어 진짜 테마 하나를 손해 본다.
    kept, dropped = [], []
    for theme in sorted_themes:
        name = theme.get("thema", "")
        if is_non_theme_bucket(name):
            dropped.append(name)
        else:
            kept.append(theme)
    if dropped:
        print(f"  [제외] 테마가 아닌 버킷 {len(dropped)}개: {', '.join(dropped)}")

    top_themes = kept[:top_n]

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
