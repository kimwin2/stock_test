"""장 시작 전(08:00~09:00) 시세 — 넥스트레이드(NXT) 프리마켓.

## 왜 필요한가

KRX 정규장은 09:00 에 열린다. 그 전에는 네이버 `basic` API 가 전일 종가를
그대로 돌려주므로 **전 종목 등락률이 0.0%** 다. 그런데 theme Lambda 는
08:00 부터 10분 간격으로 돈다.

이 구간에서 무슨 일이 벌어졌나 (2026-08-19 08:32 실측):

    코데즈컴바인  price=3510  prevClose=3510  open=high=low=3510  rate=0.0
    삼성전자      price=268500 prevClose=268500 ...              rate=0.0

전 종목이 0.0% 라 테마 생존 게이트(대장주 ≥ 3%)가 **모든 테마를 떨어뜨리고**,
`MIN_THEMES=3` 미달 → 완화 재선정 → 완화 기준이 `RELAXED_LEAD_RATE = 0.0` 이라
`0.0 < 0.0` 이 거짓 → **전부 통과**. 그날 payload 의 테마 4개가 전부
`gateRelaxed: true` 였다. 즉 매 평일 08:00~09:00 에는 품질 게이트가 하루도
빠짐없이 무력화되고, 화면에는 검증되지 않은 테마가 올라간다.

## 무엇으로 메우나

넥스트레이드(NXT)는 08:00~08:50 에 프리마켓을 연다. 이 시간대에 실제 체결이
일어나므로 **진짜 등락률**이 존재한다. 그걸 가져오면 게이트를 그대로 쓸 수 있다.

## 설계 원칙 — 틀려도 오늘보다 나빠지지 않게

프리마켓 시세 API 의 정확한 응답 형태는 계약이 공개돼 있지 않고 바뀔 수 있다.
그래서 이 모듈은 **셋 다** 지킨다:

1. **창 밖에서는 아무 일도 안 한다.** 평일 08:00~09:00(KST) 밖이면 즉시 None.
   정규장 동작에 영향을 줄 수 있는 코드 경로가 아예 없다.
2. **의심스러우면 버린다.** 받은 값이 상식 범위를 벗어나거나(±30% 밖),
   전일 종가와 같아 '움직임 없음'이면 None 을 돌려 기존 경로로 넘긴다.
   낡은 값을 프리마켓 시세로 착각하는 게 가장 위험하다.
3. **한 번 실패하면 그만둔다.** 후보 엔드포인트가 연속으로 죽으면 회로를 끊어
   나머지 종목은 시도조차 하지 않는다. Lambda 가 타임아웃에 걸리면
   프리마켓 개선이 아니라 파이프라인 정지가 된다.

None 을 돌려주면 호출부(`stock_data.get_stock_detail`)는 기존 경로를 그대로
탄다. 이 모듈이 통째로 실패해도 결과는 '오늘과 같음' 이다.
"""
from __future__ import annotations

import re
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional

import requests

KST = timezone(timedelta(hours=9))

# 넥스트레이드 프리마켓은 08:00~08:50. 09:00 정규장 개장까지를 창으로 잡는다
# (08:50~09:00 은 체결이 없지만 마지막 프리마켓 가격이 여전히 유효한 정보다).
PREMARKET_START = dtime(8, 0)
PREMARKET_END = dtime(9, 0)

# 상·하한 ±30%. 이 밖의 값은 파싱이 틀렸다는 뜻이지 시세가 아니다.
SANE_RATE_LIMIT = 30.5

# 연속 실패가 이만큼 쌓이면 이번 실행에서는 더 시도하지 않는다.
FAIL_STREAK_LIMIT = 5
_fail_streak = 0
_disabled = False

TIMEOUT = 3.5
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json",
}


def reset() -> None:
    """실행 단위 상태 초기화. Lambda 컨테이너가 재사용돼도 창마다 새로 시작한다."""
    global _fail_streak, _disabled
    _fail_streak = 0
    _disabled = False


def is_premarket(now: Optional[datetime] = None) -> bool:
    """지금이 평일 프리마켓 창(08:00~09:00 KST)인가."""
    now = now or datetime.now(KST)
    if now.weekday() >= 5:            # 토·일
        return False
    return PREMARKET_START <= now.timetz().replace(tzinfo=None) < PREMARKET_END


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    f = _to_float(v)
    return None if f is None else int(f)


# ── 후보 엔드포인트 ────────────────────────────────────────────────
# 응답 형태가 서로 다르므로 파서를 함께 둔다. 하나라도 '움직인 값'을 주면 채택한다.
# 순서는 신뢰도순 — 앞쪽이 형태가 오래 안정적이었던 것.

def _parse_polling(data: dict) -> Optional[dict]:
    """polling.finance.naver.com — {"datas":[{"nv":현재가,"cv":대비,"cr":등락률,"pcv":전일종가,"aa":거래대금}]}"""
    rows = data.get("datas") or []
    if not rows:
        return None
    d = rows[0]
    return {
        "price": _to_int(d.get("nv")),
        "prevClose": _to_int(d.get("pcv")),
        "changeRate": _to_float(d.get("cr")),
        "changeAmount": _to_int(d.get("cv")),
        "volumeRaw": _to_int(d.get("aa")) or 0,   # 누적 거래대금
        "name": d.get("nm") or "",
    }


def _parse_basic(data: dict) -> Optional[dict]:
    """m.stock.naver.com/api/stock/{code}/basic — 문자열 필드"""
    price = _to_int(data.get("closePrice"))
    change = _to_int(data.get("compareToPreviousClosePrice"))
    rate = _to_float(data.get("fluctuationsRatio"))
    if price is None:
        return None
    return {
        "price": price,
        "prevClose": (price - change) if change is not None else None,
        "changeRate": rate,
        "changeAmount": change,
        "volumeRaw": _to_int(data.get("accumulatedTradingValue")) or 0,
        "name": data.get("stockName") or "",
    }


CANDIDATES = [
    # NXT 를 명시적으로 요구하는 형태부터 — 통합 시세가 정규장 종가를 주는 경우를 피한다.
    ("https://polling.finance.naver.com/api/realtime/domestic/stock/NXT{code}", _parse_polling),
    ("https://polling.finance.naver.com/api/realtime/domestic/stock/{code}", _parse_polling),
    ("https://m.stock.naver.com/api/stock/{code}/basic?market=NXT", _parse_basic),
    ("https://m.stock.naver.com/api/stock/{code}/basic", _parse_basic),
]


def _sane(q: dict) -> bool:
    """받은 값이 시세로 말이 되나.

    '움직임 없음'(등락률 0 또는 전일종가와 동일)은 **의도적으로 탈락**시킨다.
    프리마켓에 체결이 없었다는 뜻이고, 그걸 시세로 채택하면 애초에 고치려던
    문제(0% 를 실제 값으로 오인)를 그대로 되풀이한다.
    """
    price = q.get("price")
    rate = q.get("changeRate")
    prev = q.get("prevClose")
    if not price or price <= 0:
        return False
    if rate is None or abs(rate) > SANE_RATE_LIMIT:
        return False
    if rate == 0:
        return False
    if prev:
        if prev <= 0:
            return False
        # 등락률과 가격이 서로를 설명해야 한다. 두 필드가 다른 장(정규장/프리마켓)에서
        # 왔으면 여기서 어긋난다.
        implied = (price - prev) / prev * 100
        if abs(implied - rate) > 1.0:
            return False
    return True


def fetch_premarket_quote(code: str, now: Optional[datetime] = None) -> Optional[dict]:
    """프리마켓 시세. 창 밖이거나 못 믿을 값이면 None.

    None 이면 호출부는 기존 경로(정규장 API)를 그대로 쓴다.
    """
    global _fail_streak, _disabled

    if _disabled or not is_premarket(now):
        return None

    for url_tpl, parser in CANDIDATES:
        url = url_tpl.format(code=code)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            parsed = parser(resp.json())
        except Exception:
            continue
        if not parsed or not _sane(parsed):
            continue

        _fail_streak = 0
        parsed["source"] = "nxt-premarket"
        return parsed

    # 이 종목은 실패. 연속으로 쌓이면 이번 실행에서는 포기한다.
    _fail_streak += 1
    if _fail_streak >= FAIL_STREAK_LIMIT:
        _disabled = True
        print(f"  [i] 프리마켓 시세 조회 {FAIL_STREAK_LIMIT}회 연속 실패 — "
              f"이번 실행에서는 정규장 API 만 사용합니다.")
    return None
