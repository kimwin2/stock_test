"""DART OpenAPI 공시 수집.

금감원 전자공시 공식 무료 API (https://opendart.fss.or.kr) — 상업적 이용 가능.
list.json 응답에 stock_code 가 포함되므로 corpCode.xml 매핑 없이
유니버스 종목코드로 바로 필터링한다.

환경변수: DART_API_KEY (없으면 수집 스킵, 브리핑은 시그널만으로 생성)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# report_nm 키워드 → (카테고리, tone)
# tone: "positive" | "negative" | "watch" (주목) — 브리핑/배지 표시용.
# 순서 중요: 먼저 매칭되는 규칙 적용.
REPORT_RULES: list[tuple[str, str, str]] = [
    ("단일판매", "수주·공급계약", "positive"),
    ("공급계약", "수주·공급계약", "positive"),
    ("유상증자", "유상증자", "negative"),
    ("무상증자", "무상증자", "positive"),
    ("전환사채", "전환사채(CB)", "negative"),
    ("신주인수권부사채", "신주인수권부사채(BW)", "negative"),
    ("교환사채", "교환사채(EB)", "negative"),
    ("자기주식취득", "자사주 취득", "positive"),
    ("자기주식 취득", "자사주 취득", "positive"),
    ("자기주식처분", "자사주 처분", "watch"),
    ("자기주식 처분", "자사주 처분", "watch"),
    ("주식등의대량보유상황보고서", "5% 대량보유 보고", "watch"),
    ("영업(잠정)실적", "잠정 실적", "watch"),
    ("매출액또는손익구조", "손익구조 변동", "watch"),
    ("소송", "소송", "negative"),
    ("합병", "합병", "watch"),
    ("분할", "분할", "watch"),
    ("임상", "임상", "watch"),
    ("품목허가", "품목허가", "positive"),
    ("특허", "특허", "watch"),
    ("감자", "감자", "negative"),
    ("거래정지", "거래정지", "negative"),
    ("관리종목", "관리종목", "negative"),
    ("불성실공시", "불성실공시", "negative"),
    ("조회공시", "조회공시(풍문·급변동)", "watch"),
    ("신규시설투자", "신규 시설투자", "positive"),
    ("타법인주식", "타법인 지분 취득·처분", "watch"),
    ("최대주주변경", "최대주주 변경", "watch"),
    ("횡령", "횡령·배임", "negative"),
    ("배당", "배당", "watch"),
]

# 카테고리 무관하게 노이즈로 제외할 보고서 (개별 임원 지분변동 등 대량 발생)
NOISE_KEYWORDS = [
    "임원ㆍ주요주주특정증권등소유상황보고서",
    "임원·주요주주특정증권등소유상황보고서",
    "동일인등출자계열회사와의상품ㆍ용역거래",
    "최대주주등과의거래신고",
    "기업설명회(IR)개최",
]


def classify_report(report_nm: str) -> tuple[str, str] | None:
    """report_nm → (카테고리, tone). 관심 대상 아니면 None."""
    name = report_nm or ""
    for noise in NOISE_KEYWORDS:
        if noise in name.replace(" ", ""):
            return None
    for kw, category, tone in REPORT_RULES:
        if kw in name:
            return (category, tone)
    return None


def fetch_disclosures(
    api_key: str,
    lookback_days: int = 3,
    max_pages_per_market: int = 15,
    sleep_sec: float = 0.1,
    timeout: int = 10,
) -> list[dict]:
    """최근 lookback_days(달력일) 의 유가/코스닥 공시 목록.

    Returns: [{stock_code, corp_name, report_nm, rcept_no, rcept_dt, corp_cls}]
    """
    now = datetime.now(KST)
    bgn = (now - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")

    out: list[dict] = []
    for corp_cls in ("Y", "K"):  # 유가증권 / 코스닥
        for page in range(1, max_pages_per_market + 1):
            params = {
                "crtfc_key": api_key,
                "bgn_de": bgn,
                "end_de": end,
                "corp_cls": corp_cls,
                "page_no": page,
                "page_count": 100,
            }
            r = requests.get(DART_LIST_URL, params=params, timeout=timeout)
            if r.status_code != 200:
                break
            data = r.json()
            status = data.get("status")
            if status == "013":  # 조회 결과 없음
                break
            if status != "000":
                print(f"  [!] DART list 오류 (status={status}): {data.get('message')}")
                break
            items = data.get("list") or []
            for it in items:
                code = (it.get("stock_code") or "").strip()
                if not code:
                    continue
                out.append({
                    "stock_code": code,
                    "corp_name": it.get("corp_name"),
                    "report_nm": (it.get("report_nm") or "").strip(),
                    "rcept_no": it.get("rcept_no"),
                    "rcept_dt": it.get("rcept_dt"),
                    "corp_cls": corp_cls,
                })
            total_page = int(data.get("total_page") or 1)
            if page >= total_page:
                break
            if sleep_sec:
                time.sleep(sleep_sec)
    return out


def collect_disclosure_events(
    universe_codes: set[str],
    candidate_codes: set[str],
    api_key: str | None = None,
    lookback_days: int = 3,
) -> dict:
    """유니버스/후보 종목의 분류된 공시 이벤트.

    Returns:
      {available, error, candidateEvents: [...], universeEvents: [...]}
      각 이벤트: {code, name, category, tone, title, date, url, isCandidate}
    """
    api_key = api_key or os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        return {
            "available": False,
            "error": "DART_API_KEY 미설정 — 공시 수집 스킵",
            "candidateEvents": [],
            "universeEvents": [],
        }

    try:
        raw = fetch_disclosures(api_key, lookback_days=lookback_days)
    except Exception as e:
        return {
            "available": False,
            "error": f"DART 수집 실패: {e}",
            "candidateEvents": [],
            "universeEvents": [],
        }

    candidate_events: list[dict] = []
    universe_events: list[dict] = []
    seen: set[tuple] = set()
    for it in raw:
        code = it["stock_code"]
        in_candidates = code in candidate_codes
        if not in_candidates and code not in universe_codes:
            continue
        cls = classify_report(it["report_nm"])
        if cls is None:
            continue
        category, tone = cls
        key = (code, category, it["rcept_dt"])
        if key in seen:
            continue
        seen.add(key)
        event = {
            "code": code,
            "name": it["corp_name"],
            "category": category,
            "tone": tone,
            "title": it["report_nm"],
            "date": it["rcept_dt"],
            "url": DART_VIEWER_URL.format(rcept_no=it["rcept_no"]),
            "isCandidate": bool(in_candidates),
        }
        if in_candidates:
            candidate_events.append(event)
        else:
            universe_events.append(event)

    # 최신 우선 정렬, 유니버스 이벤트는 상위 30개만 (페이로드 억제)
    candidate_events.sort(key=lambda e: e["date"] or "", reverse=True)
    universe_events.sort(key=lambda e: e["date"] or "", reverse=True)
    return {
        "available": True,
        "error": None,
        "candidateEvents": candidate_events,
        "universeEvents": universe_events[:30],
    }
