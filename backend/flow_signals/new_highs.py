"""50일 / 250일 신고가 종목 추출.

"수급 빈집 + 주도섹터 + 신고가" 교집합을 매수 후보로 본다.
"""

from __future__ import annotations

import time
from typing import Iterable

import pandas as pd

from .data_sources import fetch_stock_ohlcv


def detect_new_highs_for_codes(
    codes: Iterable[str],
    code_to_meta: dict[str, dict],
    sleep_sec: float = 0.0,
    progress_every: int = 50,
) -> dict:
    """주어진 코드들의 50d/250d 신고가 여부 + 직전 고점 대비 위치.

    Note: FDR 호출이 코드당 한 번씩이라 비용이 큼. 권장: vacancy candidate 으로 좁힌 뒤 호출.
    """
    high50: list[dict] = []
    high250: list[dict] = []

    codes = list(codes)
    for i, code in enumerate(codes):
        try:
            df = fetch_stock_ohlcv(code, days=400)
        except Exception as e:
            print(f"  [!] {code} OHLCV 실패: {e}")
            if sleep_sec:
                time.sleep(sleep_sec)
            continue

        if df.empty or len(df) < 50:
            continue

        meta = code_to_meta.get(code, {})
        last_close = float(df["Close"].iloc[-1])
        last_high = float(df["High"].iloc[-1])

        # 50일
        max_50 = float(df["High"].tail(50).max())
        is_high_50 = last_high >= max_50 * 0.999

        # 250일
        if len(df) >= 250:
            max_250 = float(df["High"].tail(250).max())
            is_high_250 = last_high >= max_250 * 0.999
        else:
            max_250 = float(df["High"].max())
            is_high_250 = last_high >= max_250 * 0.999

        # 직전 5일 변동률
        ret5 = (last_close / float(df["Close"].iloc[-6]) - 1) * 100 if len(df) > 6 else None

        common = {
            "code": code,
            "name": meta.get("name") or code,
            "sector": meta.get("sector"),
            "close": last_close,
            "ret5d": round(ret5, 2) if ret5 is not None else None,
        }

        if is_high_50:
            high50.append({**common, "high50d": max_50})
        if is_high_250:
            high250.append({**common, "high250d": max_250})

        if progress_every and (i + 1) % progress_every == 0:
            print(f"  [.] new-highs {i + 1}/{len(codes)}")
        if sleep_sec:
            time.sleep(sleep_sec)

    return {
        "high50d": sorted(high50, key=lambda x: x.get("ret5d") or 0, reverse=True),
        "high250d": sorted(high250, key=lambda x: x.get("ret5d") or 0, reverse=True),
    }
