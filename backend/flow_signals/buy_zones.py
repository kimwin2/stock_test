"""매수 타점 통계 — 태린이아빠 "매수타점찾기코드" 변형.

종목별 1년 OHLCV 에서:
  - 당일 고가-종가 평균 하락률  → 매수 타점 가이드
  - 당일 저가-전일종가 평균 괴리율 → 갭다운 타점
  - 당일 고가-전일종가 평균 괴리율 → 강도

이를 통해 "현재가가 오늘 고가 대비 X% 밀려있으면 통계적 매수권"을 알 수 있다.
"""

from __future__ import annotations

import pandas as pd


def compute_buy_zone(df: pd.DataFrame) -> dict:
    """OHLCV → 매수타점 통계."""
    if df.empty or len(df) < 30:
        return {}
    df = df.copy()
    prev_close = df["Close"].shift(1)

    # 당일 고가 대비 종가의 평균 하락률 (%)
    high_to_close = (df["Close"] - df["High"]) / df["High"] * 100
    # 당일 저가 - 전일 종가 평균 (%)
    low_to_prev_close = (df["Low"] - prev_close) / prev_close * 100
    # 시가 - 저가 평균 (%)
    open_to_low = (df["Low"] - df["Open"]) / df["Open"] * 100
    # 전일 종가 - 당일 고가 평균 (%)
    prev_close_to_high = (df["High"] - prev_close) / prev_close * 100

    last_close = float(df["Close"].iloc[-1])
    last_high = float(df["High"].iloc[-1])
    last_open = float(df["Open"].iloc[-1])

    # 현재가 vs 오늘 고가 차이
    today_pullback_pct = (last_close - last_high) / last_high * 100 if last_high else 0

    avg_high_to_close = float(high_to_close.mean(skipna=True))
    avg_low_to_prev = float(low_to_prev_close.mean(skipna=True))
    avg_open_to_low = float(open_to_low.mean(skipna=True))
    avg_prev_to_high = float(prev_close_to_high.mean(skipna=True))

    # "통계적 매수권": 종가가 (고가 × (1 + avg_high_to_close/100)) 보다 낮으면
    statistical_zone_price = last_high * (1 + avg_high_to_close / 100)
    in_zone = last_close <= statistical_zone_price

    return {
        "lastClose": last_close,
        "lastHigh": last_high,
        "todayPullbackPct": round(today_pullback_pct, 2),
        "avgHighToClosePct": round(avg_high_to_close, 2),
        "avgLowToPrevClosePct": round(avg_low_to_prev, 2),
        "avgOpenToLowPct": round(avg_open_to_low, 2),
        "avgPrevToHighPct": round(avg_prev_to_high, 2),
        "buyZonePrice": round(statistical_zone_price, 0),
        "inBuyZone": bool(in_zone),
    }
