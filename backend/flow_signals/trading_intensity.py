"""거래대금 강도 (Trading Intensity, TI) — 태린이아빠 핵심 매수 시그널.

원본 코드 (거래대금 강도 코랩) 변형:
  TI_raw = 0.5*z(VolSpike) + 0.3*z(Turnover) + 0.2*z(GapTrend)
  TI_acc7 = TI_raw 의 7일 rolling sum
  TI_acc7_0_100 = MinMax 정규화

시그널: TI 가 바닥(<20) 에서 올라오면서 신고가가 나면 매수.
        TI 가 과열(>80) 이면 신고가가 나도 식는다.
"""

from __future__ import annotations

import time
from typing import Iterable

import numpy as np
import pandas as pd

from .data_sources import fetch_stock_ohlcv


VOL_MA = 20
ZSCORE_WIN = 60
ACC_WIN = 7
W_VOL_SPIKE = 0.50
W_TURNOVER = 0.30
W_GAP_TREND = 0.20


def _rolling_zscore(series: pd.Series, window: int = ZSCORE_WIN, min_periods: int = 20) -> pd.Series:
    mean = series.rolling(window, min_periods=min_periods).mean()
    std = series.rolling(window, min_periods=min_periods).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def _minmax_0_100(series: pd.Series) -> pd.Series:
    s = series.dropna()
    if s.empty or s.min() == s.max():
        return series * np.nan
    return (series - s.min()) / (s.max() - s.min()) * 100


def compute_trading_intensity(df: pd.DataFrame, listed_shares: float | None = None) -> pd.DataFrame:
    """OHLCV → TI_acc7_0_100 (0-100).

    listed_shares: 회전율 계산용 발행주식수. None 이면 turnover 항을 0으로.
    """
    df = df.copy()
    df["PrevClose"] = df["Close"].shift(1)
    df["VolMA"] = df["Volume"].rolling(VOL_MA, min_periods=max(5, VOL_MA // 2)).mean()
    df["VolSpike"] = df["Volume"] / df["VolMA"]

    if listed_shares and listed_shares > 0:
        df["Turnover"] = df["Volume"] / float(listed_shares)
    else:
        # 발행주식수를 모르면 거래대금 / 시가총액 근사 대신 z(Volume) 만 두 배로
        df["Turnover"] = df["VolSpike"]

    df["Gap"] = df["Open"] / df["PrevClose"] - 1.0
    df["IntradayTrend"] = df["Close"] / df["Open"] - 1.0
    df["GapTrend"] = df["Gap"] + df["IntradayTrend"]

    df["z_VolSpike"] = _rolling_zscore(df["VolSpike"])
    df["z_Turnover"] = _rolling_zscore(df["Turnover"])
    df["z_GapTrend"] = _rolling_zscore(df["GapTrend"])

    df["TI_raw"] = (
        W_VOL_SPIKE * df["z_VolSpike"].fillna(0)
        + W_TURNOVER * df["z_Turnover"].fillna(0)
        + W_GAP_TREND * df["z_GapTrend"].fillna(0)
    )
    df["TI_acc7"] = df["TI_raw"].rolling(ACC_WIN, min_periods=ACC_WIN).sum()
    df["TI_0_100"] = _minmax_0_100(df["TI_acc7"])
    return df


def classify_ti_zone(ti: float | None) -> str:
    if ti is None or pd.isna(ti):
        return "-"
    if ti >= 80:
        return "과열"
    if ti >= 60:
        return "강세"
    if ti >= 40:
        return "중립"
    if ti >= 20:
        return "약세"
    return "바닥"


def compute_ti_for_codes(
    codes: Iterable[str],
    code_to_meta: dict[str, dict],
    sleep_sec: float = 0.0,
    progress_every: int = 30,
) -> list[dict]:
    """후보 종목들의 TI 시계열 + 최신값.

    Returns: list of dicts with code, name, ti, zone, tiHistory(60d), priceHistory(60d).
    """
    out: list[dict] = []
    codes = list(codes)
    for i, code in enumerate(codes):
        try:
            df = fetch_stock_ohlcv(code, days=200)
        except Exception as e:
            print(f"  [!] TI {code} 실패: {e}")
            if sleep_sec:
                time.sleep(sleep_sec)
            continue

        if df.empty or len(df) < 70:
            continue

        ti_df = compute_trading_intensity(df)
        last = ti_df.dropna(subset=["TI_0_100"]).tail(1)
        if last.empty:
            continue

        ti = float(last["TI_0_100"].iloc[0])

        # 60일 history
        recent = ti_df.tail(60).copy()
        ti_hist = [None if pd.isna(v) else round(float(v), 1) for v in recent["TI_0_100"]]
        price_hist = [None if pd.isna(v) else round(float(v), 0) for v in recent["Close"]]
        date_hist = [d.strftime("%Y-%m-%d") for d in recent.index]

        meta = code_to_meta.get(code, {})
        out.append(
            {
                "code": code,
                "name": meta.get("name") or code,
                "sector": meta.get("sector"),
                "ti": round(ti, 1),
                "zone": classify_ti_zone(ti),
                "tiHistory": ti_hist,
                "priceHistory": price_hist,
                "dateHistory": date_hist,
                "close": round(float(last["Close"].iloc[0]), 0),
            }
        )

        if progress_every and (i + 1) % progress_every == 0:
            print(f"  [.] TI {i + 1}/{len(codes)}")
        if sleep_sec:
            time.sleep(sleep_sec)

    return out
