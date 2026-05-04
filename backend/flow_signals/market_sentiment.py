"""시장 심리 지표 — Fear & Greed Oscillator (태린이아빠 스타일).

원본 Colab 코드는 RSI(10) + 변동성지수 + Put/Call ATM + 국채선물 차이 + 모멘텀
의 5개 feature 를 MinMax 정규화 (0~1) 후 가중평균.  옵션 ATM/국채선물 데이터는
무료 소스가 없어 4개로 단순화:
- RSI(10)
- 125일 이동평균 대비 모멘텀
- 변동성 (20일 표준편차의 역수)
- 거래량 모멘텀

Oscillator 정의 (태린이아빠 그래프와 시각적으로 일치):
- fear_greed 를 0~1 로 정규화한 뒤 EMA12 − EMA26 (MACD line)
- 최종 진동 폭은 약 ±0.03 — 태린이아빠 차트와 동일 스케일
- 0 위 = Greed (과열), 0 아래 = Fear (과매도)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data_sources import fetch_index_ohlcv


def _rsi(series: pd.Series, window: int = 10) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _minmax_0_100(series: pd.Series) -> pd.Series:
    s = series.dropna()
    if s.empty or s.min() == s.max():
        return series * np.nan
    return (series - s.min()) / (s.max() - s.min()) * 100


def fear_greed_oscillator(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 OHLCV → Fear&Greed Oscillator (0~100).

    Output: input df + Fear_Greed (0-100), Oscillator (MACD of FG).
    """
    df = df.copy()
    close = df["Close"]

    # 1) RSI(10)
    df["rsi10"] = _rsi(close, 10)

    # 2) 125일 모멘텀
    ma125 = close.rolling(125).mean()
    df["momentum"] = (close - ma125) / ma125 * 100

    # 3) 변동성 (낮을수록 그리디)
    vol20 = close.pct_change().rolling(20).std() * 100
    df["inv_volatility"] = -vol20  # 부호 뒤집어 minmax

    # 4) 거래량 모멘텀 (5일/20일)
    if "Volume" in df.columns:
        vol_ma5 = df["Volume"].rolling(5).mean()
        vol_ma20 = df["Volume"].rolling(20).mean()
        df["volume_momentum"] = (vol_ma5 - vol_ma20) / vol_ma20 * 100
    else:
        df["volume_momentum"] = 0.0

    feats = ["rsi10", "momentum", "inv_volatility", "volume_momentum"]
    for f in feats:
        df[f"{f}_n"] = _minmax_0_100(df[f])

    df["fear_greed"] = df[[f"{f}_n" for f in feats]].mean(axis=1)

    # Oscillator: fear_greed 를 0~1 로 정규화한 후 MACD line (EMA12 − EMA26).
    # 태린이아빠 그래프와 동일한 ±0.03 스케일을 갖도록 설계.
    fg_normalized = df["fear_greed"] / 100.0
    ema12 = fg_normalized.ewm(span=12, adjust=False).mean()
    ema26 = fg_normalized.ewm(span=26, adjust=False).mean()
    df["fg_oscillator"] = ema12 - ema26

    return df


def classify_zone(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    if value >= 75:
        return "과열"
    if value >= 55:
        return "강세"
    if value >= 45:
        return "중립"
    if value >= 25:
        return "약세"
    return "공포"


def build_index_sentiment(symbol: str, label: str) -> dict:
    df = fetch_index_ohlcv(symbol, days=400)
    if df.empty:
        return {"label": label, "error": "no data"}
    df = fear_greed_oscillator(df)
    last = df.dropna(subset=["fear_greed"]).tail(1)
    if last.empty:
        return {"label": label, "error": "insufficient data"}

    fg = float(last["fear_greed"].iloc[0])
    osc = float(last["fg_oscillator"].iloc[0]) if pd.notna(last["fg_oscillator"].iloc[0]) else 0.0
    close = float(last["Close"].iloc[0])

    history = df[["Close", "fear_greed", "fg_oscillator"]].tail(120).copy()
    history.index = history.index.strftime("%Y-%m-%d")
    history_records = [
        {
            "date": idx,
            "close": round(float(row["Close"]), 2),
            "fearGreed": round(float(row["fear_greed"]), 2) if pd.notna(row["fear_greed"]) else None,
            "oscillator": round(float(row["fg_oscillator"]), 4) if pd.notna(row["fg_oscillator"]) else None,
        }
        for idx, row in history.iterrows()
    ]

    return {
        "label": label,
        "symbol": symbol,
        "close": round(close, 2),
        "fearGreed": round(fg, 1),
        "oscillator": round(osc, 4),
        "zone": classify_zone(fg),
        "history": history_records,
    }


def build_market_sentiment() -> dict:
    return {
        "kospi": build_index_sentiment("KS11", "KOSPI"),
        "kosdaq": build_index_sentiment("KQ11", "KOSDAQ"),
    }
