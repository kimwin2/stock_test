"""Naver fchart XML fetcher.

Endpoint: https://fchart.stock.naver.com/sise.nhn
    ?symbol={6자리 코드}
    &timeframe={day|week|month|minute}
    &count={N}
    &requestType=0

Response (EUC-KR XML):
    <chartdata symbol="005930" name="삼성전자" count="N" timeframe="day" origintime="...">
        <item data="20260102|120200|128500|120200|128500|30463279" />
        ...
    </chartdata>

분봉 (minute) 은 OHLC 가 null 로 오고 close+volume 만 채워지는 경우가 많음
(pre/post-market 시간대). 그 경우 c 값으로 o=h=l=c 보정.
"""

from __future__ import annotations

import re
from typing import Optional

import requests

NAVER_FCHART_URL = "https://fchart.stock.naver.com/sise.nhn"
NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.naver.com/",
}

# 허용 timeframe → (Naver 파라미터, 기본 count)
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "minute": ("minute", 390),  # 1일치 분봉 (9:00~15:30 = 390분)
    "day":    ("day", 220),     # 약 1년치 일봉
    "week":   ("week", 156),    # 약 3년치 주봉
    "month":  ("month", 120),   # 약 10년치 월봉
}

ITEM_RE = re.compile(r'<item\s+data="([^"]+)"\s*/>')


def _parse_num(s: str) -> Optional[float]:
    s = s.strip()
    if not s or s.lower() == "null":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> Optional[int]:
    v = _parse_num(s)
    return int(v) if v is not None else None


def _parse_xml(xml_text: str) -> tuple[Optional[str], list[dict]]:
    """Naver fchart XML 파싱.

    Returns (name, candles).
    candles 각 원소: {t, o, h, l, c, v}
        - t: "YYYYMMDD" 또는 "YYYYMMDDHHMM" (Naver 그대로)
        - o/h/l/c: 정수 또는 None
        - v: 정수 거래량
    """
    name_match = re.search(r'name="([^"]*)"', xml_text)
    name = name_match.group(1) if name_match else None

    candles: list[dict] = []
    for m in ITEM_RE.finditer(xml_text):
        parts = m.group(1).split("|")
        if len(parts) < 6:
            continue
        t, o_s, h_s, l_s, c_s, v_s = parts[:6]
        o = _parse_int(o_s)
        h = _parse_int(h_s)
        l = _parse_int(l_s)
        c = _parse_int(c_s)
        v = _parse_int(v_s) or 0
        # 분봉에서 OHL 가 null 인 경우 close 로 보정 (라인 차트로만 그릴 때 필요)
        if c is not None:
            if o is None: o = c
            if h is None: h = c
            if l is None: l = c
        else:
            # close 도 없으면 의미 없는 행이므로 스킵
            continue
        candles.append({"t": t.strip(), "o": o, "h": h, "l": l, "c": c, "v": v})
    return name, candles


def fetch_candles(
    code: str,
    timeframe: str = "day",
    count: Optional[int] = None,
    timeout: float = 8.0,
) -> dict:
    """주어진 종목·timeframe 의 캔들 데이터를 Naver fchart 에서 가져온다.

    Args:
        code: 6자리 종목 코드 (예: "005930")
        timeframe: "minute" | "day" | "week" | "month"
        count: 가져올 캔들 수. None 이면 timeframe 기본값.
        timeout: HTTP timeout(초).

    Returns:
        {
            "code": str,
            "name": str,           # Naver 가 알려준 종목명
            "timeframe": str,
            "candles": list[dict], # 각 {t, o, h, l, c, v}
        }

    Raises:
        ValueError: 잘못된 timeframe 또는 code 포맷.
        RuntimeError: HTTP 실패 또는 빈 응답.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe!r}. allowed={list(TIMEFRAMES)}")
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"Invalid stock code: {code!r}")

    naver_tf, default_count = TIMEFRAMES[timeframe]
    n = max(1, min(int(count or default_count), 1000))

    params = {
        "symbol": code,
        "timeframe": naver_tf,
        "count": str(n),
        "requestType": "0",
    }
    try:
        r = requests.get(NAVER_FCHART_URL, params=params, headers=NAVER_HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"Naver fchart request failed: {e}") from e

    if r.status_code != 200:
        raise RuntimeError(f"Naver fchart HTTP {r.status_code} for {code}/{timeframe}")

    # Naver 가 EUC-KR 헤더로 보내지만 데이터(숫자/날짜) 는 ASCII 라
    # encoding 강제 후 디코딩.
    r.encoding = "euc-kr"
    name, candles = _parse_xml(r.text)
    if not candles:
        raise RuntimeError(f"Naver fchart empty for {code}/{timeframe}")

    # Naver 가 count 파라미터를 무시하는 경우 (분봉에서 자주 발생) 대비.
    # 최신순으로 잘라서 정확히 N 개만 반환.
    if len(candles) > n:
        candles = candles[-n:]

    return {
        "code": code,
        "name": name,
        "timeframe": timeframe,
        "candles": candles,
    }
