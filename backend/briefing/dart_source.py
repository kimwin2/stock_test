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


# 관심 공시 유형 (pblntf_ty) — REPORT_RULES 의 카테고리를 모두 포괄한다.
#   B 주요사항보고 : 유상증자, CB/BW, 자사주 취득·처분, 합병·분할, 소송, 감자
#   D 지분공시     : 주식등의 대량보유상황보고서(5% 룰)
#   I 거래소공시   : 단일판매·공급계약, 잠정실적, 손익구조 변동, 신규시설투자, 조회공시
# 무필터로 3일치를 받으면 정기공시 시즌에 수천 건이 되어 페이지 상한에 걸리므로
# 유형을 좁혀 호출 수와 누락 위험을 함께 줄인다.
DISCLOSURE_TYPES = ("B", "D", "I")


def _fetch_list_page(
    api_key: str, bgn: str, end: str, corp_cls: str, page: int,
    pblntf_ty: str | None, timeout: int,
) -> tuple[list[dict], int, str | None]:
    """list.json 한 페이지. Returns (items, total_page, error_status)."""
    params = {
        "crtfc_key": api_key,
        "bgn_de": bgn,
        "end_de": end,
        "corp_cls": corp_cls,
        "page_no": page,
        "page_count": 100,
    }
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty
    r = requests.get(DART_LIST_URL, params=params, timeout=timeout)
    if r.status_code != 200:
        return ([], 0, f"HTTP {r.status_code}")
    data = r.json()
    status = data.get("status")
    if status == "013":  # 조회 결과 없음 — 정상
        return ([], 0, None)
    if status != "000":
        return ([], 0, f"status={status} {data.get('message')}")
    return (data.get("list") or [], int(data.get("total_page") or 1), None)


def fetch_disclosures(
    api_key: str,
    lookback_days: int = 3,
    max_pages_per_query: int = 20,
    sleep_sec: float = 0.1,
    timeout: int = 10,
    pblntf_types: tuple[str, ...] | None = DISCLOSURE_TYPES,
) -> list[dict]:
    """최근 lookback_days(달력일) 의 유가/코스닥 공시 목록.

    pblntf_types 로 공시 유형을 좁혀 호출한다. 유형 필터 결과가 0건이면
    (API 스펙 변경 등) 무필터로 1회 재시도해 조용한 누락을 방지한다.

    Returns: [{stock_code, corp_name, report_nm, rcept_no, rcept_dt, corp_cls}]
    """
    now = datetime.now(KST)
    bgn = (now - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")

    queries: list[tuple[str, str | None]] = []
    for corp_cls in ("Y", "K"):  # 유가증권 / 코스닥
        if pblntf_types:
            queries.extend((corp_cls, ty) for ty in pblntf_types)
        else:
            queries.append((corp_cls, None))

    out: list[dict] = []
    truncated: list[str] = []
    ok_queries = 0          # 정상 응답을 한 번이라도 받은 쿼리 수
    last_error: str | None = None
    for corp_cls, pblntf_ty in queries:
        for page in range(1, max_pages_per_query + 1):
            try:
                items, total_page, err = _fetch_list_page(
                    api_key, bgn, end, corp_cls, page, pblntf_ty, timeout
                )
            except Exception as e:
                last_error = f"{corp_cls}/{pblntf_ty}: {e}"
                print(f"  [!] DART list 요청 실패 ({corp_cls}/{pblntf_ty} p{page}): {e}")
                break
            if err:
                last_error = f"{corp_cls}/{pblntf_ty}: {err}"
                print(f"  [!] DART list 오류 ({corp_cls}/{pblntf_ty}): {err}")
                break
            if page == 1:
                ok_queries += 1
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
            if page >= total_page:
                break
            if page == max_pages_per_query and total_page > max_pages_per_query:
                # 상한에 걸려 잘렸다면 조용히 넘어가지 말고 남긴다.
                truncated.append(f"{corp_cls}/{pblntf_ty}: {max_pages_per_query}/{total_page}p")
            if sleep_sec:
                time.sleep(sleep_sec)

    if truncated:
        print(f"  [!] DART 페이지 상한 도달 — 오래된 공시 일부 누락 가능: {', '.join(truncated)}")

    # 모든 쿼리가 실패했다면 "공시 0건" 이 아니라 "수집 실패" 다.
    # 이를 구분하지 않으면 브리핑이 '특이 공시 없음' 이라고 잘못 단언한다.
    if ok_queries == 0:
        raise RuntimeError(f"DART 요청이 모두 실패했습니다 (마지막 오류: {last_error})")

    if not out and pblntf_types:
        print("  [i] DART 유형 필터 결과 0건 — 무필터로 재시도")
        return fetch_disclosures(
            api_key,
            lookback_days=lookback_days,
            max_pages_per_query=max_pages_per_query,
            sleep_sec=sleep_sec,
            timeout=timeout,
            pblntf_types=None,
        )
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
            "reason": "no_key",
            "error": "DART_API_KEY 미설정 — 공시 수집 스킵",
            "candidateEvents": [],
            "universeEvents": [],
        }

    try:
        raw = fetch_disclosures(api_key, lookback_days=lookback_days)
    except Exception as e:
        return {
            "available": False,
            "reason": "fetch_failed",
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
        "reason": "ok",
        "error": None,
        "candidateEvents": candidate_events,
        "universeEvents": universe_events[:30],
    }
