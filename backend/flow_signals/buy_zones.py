"""매수 타점 통계 — 매수타점 통계 코드 변형.

종목별 1년 OHLCV 에서:
  - 당일 고가-종가 평균 하락률  → 매수 타점 가이드
  - 당일 저가-전일종가 평균 괴리율 → 갭다운 타점
  - 당일 고가-전일종가 평균 괴리율 → 강도

이를 통해 "현재가가 오늘 고가 대비 X% 밀려있으면 통계적 매수권"을 알 수 있다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _safe_pct(numer: pd.Series, denom: pd.Series) -> pd.Series:
    """0/NaN 분모로 인한 inf 를 NaN 으로 치환한 안전한 백분율 계산."""
    denom_safe = denom.where(denom != 0)
    return (numer / denom_safe * 100).replace([np.inf, -np.inf], np.nan)


def _finite_or_none(value: float) -> float | None:
    """NaN/inf 를 None 으로 치환 (JSON 직렬화 호환)."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def compute_buy_zone(df: pd.DataFrame) -> dict:
    """OHLCV → 매수타점 통계."""
    if df.empty or len(df) < 30:
        return {}
    df = df.copy()
    prev_close = df["Close"].shift(1)

    # 당일 고가 대비 종가의 평균 하락률 (%)
    high_to_close = _safe_pct(df["Close"] - df["High"], df["High"])
    # 당일 저가 - 전일 종가 평균 (%)
    low_to_prev_close = _safe_pct(df["Low"] - prev_close, prev_close)
    # 시가 - 저가 평균 (%)
    open_to_low = _safe_pct(df["Low"] - df["Open"], df["Open"])
    # 전일 종가 - 당일 고가 평균 (%)
    prev_close_to_high = _safe_pct(df["High"] - prev_close, prev_close)

    last_close = float(df["Close"].iloc[-1])
    last_high = float(df["High"].iloc[-1])

    # 현재가 vs 오늘 고가 차이
    today_pullback_pct = (last_close - last_high) / last_high * 100 if last_high else 0.0

    avg_high_to_close = float(high_to_close.mean(skipna=True))
    avg_low_to_prev = float(low_to_prev_close.mean(skipna=True))
    avg_open_to_low = float(open_to_low.mean(skipna=True))
    avg_prev_to_high = float(prev_close_to_high.mean(skipna=True))

    if math.isnan(avg_high_to_close) or math.isinf(avg_high_to_close) or not last_high:
        statistical_zone_price = float("nan")
        in_zone = False
    else:
        statistical_zone_price = last_high * (1 + avg_high_to_close / 100)
        in_zone = last_close <= statistical_zone_price

    def _round(value: float, ndigits: int) -> float | None:
        cleaned = _finite_or_none(value)
        return None if cleaned is None else round(cleaned, ndigits)

    return {
        "lastClose": last_close,
        "lastHigh": last_high,
        "todayPullbackPct": _round(today_pullback_pct, 2),
        "avgHighToClosePct": _round(avg_high_to_close, 2),
        "avgLowToPrevClosePct": _round(avg_low_to_prev, 2),
        "avgOpenToLowPct": _round(avg_open_to_low, 2),
        "avgPrevToHighPct": _round(avg_prev_to_high, 2),
        "buyZonePrice": _round(statistical_zone_price, 0),
        "inBuyZone": bool(in_zone),
    }
