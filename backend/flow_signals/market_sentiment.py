"""시장 심리 — Fear & Greed Oscillator.

태린이아빠 원본 한국 F&G 오실레이터를 **실제 KRX 데이터**로 재현한다.
(이전엔 무료 proxy 로 근사했으나 원본 그림과 어긋나 KRX 실데이터로 교체.
 검증: 엑셀 원본 진짜값 오실레이터와 상관 r=0.94, 119일 겹침.)

원본 5-feature (각 0.2 가중, MinMaxScaler 후):
  Momentum = (Close − MA125)/MA125 × 100      ← 지수 종가
  RSI(10)                                       ← 지수 종가
  PutCall  = put/call (1−x)                      ← KOSPI200 옵션 총 콜/풋 거래량 5일 MA 비율
  Volatility = V-KOSPI200 (1−x)                  ← KRX 변동성지수 (MDCSTAT00301)
  BondDiff = 10년 − 5년 국채선물                  ← KTB10 − KTB5 최근월물 선물종가
FGI = Σ(feature×0.2)  (0~1),  Oscillator = MACD(12,26,9) 히스토그램.

실데이터는 flow_signals/krx_source.py (KRX 로그인 필요). KRX_ID/KRX_PW 없거나
실패하면 _build_index_sentiment_proxy 로 자동 폴백(구 proxy 방식).
KOSDAQ 는 원본과 동일하게 VKOSPI·옵션·국채선물은 KOSPI 와 공유하고 지수만 KQ11.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .data_sources import (
    fetch_index_ohlcv,
    fetch_ktb_futures_pair,
    fetch_putcall_proxy_ratio,
)


def _rsi(series: pd.Series, window: int = 10) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window).mean()
    loss = -delta.where(delta < 0, 0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _minmax(series: pd.Series) -> pd.Series:
    """sklearn MinMaxScaler 동등 — NaN 무시, 0~1 로 매핑."""
    s = series.dropna()
    if s.empty or s.min() == s.max():
        return series * np.nan
    return (series - s.min()) / (s.max() - s.min())


# ============================================================
#  실데이터 (KRX) 기반 — 1차 경로
# ============================================================
def fear_greed_real(close: pd.Series, krx: pd.DataFrame) -> pd.DataFrame:
    """지수 종가 + KRX 실데이터(vkospi/call_vol/put_vol/ktb5/ktb10) → F&G + Oscillator.

    원본 태린이아빠 수식 그대로. Fear_Greed_Index 는 0~1 스케일.
    """
    df = pd.DataFrame(index=close.index)
    df["price"] = close
    df["Momentum"] = (close - close.rolling(125).mean()) / close.rolling(125).mean() * 100
    df["RSI_10"] = _rsi(close, 10)
    call5 = krx["call_vol"].rolling(5).mean()
    put5 = krx["put_vol"].rolling(5).mean()
    df["PutCall"] = put5 / call5.replace(0, np.nan)
    df["Volatility"] = krx["vkospi"]
    df["BondDiff"] = krx["ktb10"] - krx["ktb5"]
    df.replace([np.inf, -np.inf], np.nan, inplace=True)

    feats = ["Momentum", "PutCall", "Volatility", "BondDiff", "RSI_10"]
    valid = df.dropna(subset=feats).index
    df["fear_greed"] = np.nan
    if len(valid):
        for f in feats:
            df.loc[valid, f] = _minmax(df.loc[valid, f])
        df.loc[valid, "fear_greed"] = (
            df.loc[valid, "Momentum"] * 0.2
            + (1 - df.loc[valid, "PutCall"]) * 0.2
            + (1 - df.loc[valid, "Volatility"]) * 0.2
            + df.loc[valid, "BondDiff"] * 0.2
            + df.loc[valid, "RSI_10"] * 0.2
        )
    ema12 = df["fear_greed"].ewm(span=12, adjust=False).mean()
    ema26 = df["fear_greed"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df["fg_oscillator"] = macd - macd.ewm(span=9, adjust=False).mean()
    return df


def _sentiment_record(label: str, symbol: str, df: pd.DataFrame) -> dict:
    """fear_greed_real/proxy 결과 DataFrame → 프론트 호환 dict."""
    last = df.dropna(subset=["fear_greed"]).tail(1)
    if last.empty:
        return {"label": label, "error": "insufficient data"}
    fg = float(last["fear_greed"].iloc[0]) * 100.0  # 0~1 → 0~100 표시
    osc = float(last["fg_oscillator"].iloc[0]) if pd.notna(last["fg_oscillator"].iloc[0]) else 0.0
    close = float(last["price"].iloc[0])

    hist = df[["price", "fear_greed", "fg_oscillator"]].tail(120)
    history = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "close": round(float(row["price"]), 2),
            "fearGreed": round(float(row["fear_greed"]) * 100, 2) if pd.notna(row["fear_greed"]) else None,
            "oscillator": round(float(row["fg_oscillator"]), 4) if pd.notna(row["fg_oscillator"]) else None,
        }
        for idx, row in hist.iterrows()
    ]
    return {
        "label": label,
        "symbol": symbol,
        "close": round(close, 2),
        "fearGreed": round(fg, 1),
        "oscillator": round(osc, 4),
        "zone": classify_zone(osc),
        "history": history,
    }


# ============================================================
#  proxy 기반 — 폴백 경로 (KRX 자격증명 없을 때)
# ============================================================
def _rebase_to_100(series: pd.Series) -> pd.Series:
    s = series.dropna()
    if s.empty:
        return series * np.nan
    base = s.iloc[0]
    if base == 0 or pd.isna(base):
        return series * np.nan
    return series / base * 100


def fear_greed_oscillator(
    df: pd.DataFrame,
    bond_df: pd.DataFrame | None = None,
    putcall_proxy: pd.Series | None = None,
) -> pd.DataFrame:
    """[폴백] proxy 입력으로 F&G(0~100) + Oscillator. (구 구현 유지)"""
    df = df.copy()
    close = df["Close"]
    ma125 = close.rolling(125).mean()
    df["fg_momentum"] = (close - ma125) / ma125 * 100
    df["fg_rsi10"] = _rsi(close, 10)
    vol20 = close.pct_change().rolling(20).std() * np.sqrt(252) * 100
    df["fg_inv_vol"] = -vol20.ewm(span=60, adjust=False).mean()
    feats = ["fg_momentum", "fg_rsi10", "fg_inv_vol"]

    if bond_df is not None and not bond_df.empty:
        ba = bond_df.reindex(df.index).ffill()
        df["fg_bond_diff"] = _rebase_to_100(ba["ktb10y"]) - _rebase_to_100(ba["ktb5y"])
        feats.append("fg_bond_diff")
    if putcall_proxy is not None and not putcall_proxy.empty:
        df["fg_inv_putcall"] = -putcall_proxy.reindex(df.index).ffill()
        feats.append("fg_inv_putcall")

    valid_mask = df[feats].notna().all(axis=1)
    for f in feats:
        df[f"{f}_n"] = _minmax(df[f].where(valid_mask)) * 100
    weight = 1.0 / len(feats)
    df["fear_greed"] = sum(df[f"{f}_n"] * weight for f in feats) / 100.0  # 0~1 통일
    df["price"] = close
    fg_n = df["fear_greed"]
    ema12 = fg_n.ewm(span=12, adjust=False).mean()
    ema26 = fg_n.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df["fg_oscillator"] = macd - macd.ewm(span=9, adjust=False).mean()
    return df


def classify_zone(oscillator: float) -> str:
    """Oscillator(MACD 히스토그램) 부호/크기로 5단계 zone."""
    if oscillator is None or pd.isna(oscillator):
        return "-"
    if oscillator >= 0.020:
        return "과열"
    if oscillator >= 0.005:
        return "강세"
    if oscillator >= -0.005:
        return "중립"
    if oscillator >= -0.020:
        return "약세"
    return "공포"


def build_index_sentiment(
    symbol: str,
    label: str,
    bond_df: pd.DataFrame | None = None,
    putcall_proxy: pd.Series | None = None,
) -> dict:
    """[폴백] proxy 경로 index sentiment (구 시그니처 유지)."""
    df = fetch_index_ohlcv(symbol, days=400)
    if df.empty:
        return {"label": label, "error": "no data"}
    df = fear_greed_oscillator(df, bond_df=bond_df, putcall_proxy=putcall_proxy)
    return _sentiment_record(label, symbol, df)


# ============================================================
#  공개 엔트리
# ============================================================
def build_market_sentiment() -> dict:
    use_real = bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))
    base: dict | None = None
    if use_real:
        try:
            base = _build_market_sentiment_real()
        except Exception as e:
            print(f"  [!] KRX 실데이터 F&G 실패 — proxy 폴백: {e}")
            base = None
    if base is None:
        base = _build_market_sentiment_proxy()

    try:
        from .buy_safety import build_buy_safety
        base["buySafety"] = build_buy_safety(base)
    except Exception as e:
        print(f"  [!] buy_safety 계산 실패: {e}")
    return base


def _build_market_sentiment_real() -> dict:
    from . import krx_source

    cache_path = os.getenv("FG_KRX_CACHE", krx_source.DEFAULT_CACHE)
    max_fetch_env = os.getenv("FG_KRX_MAX_FETCH")
    max_fetch = int(max_fetch_env) if max_fetch_env else None
    # MA125 + minmax 윈도우 + 4달 표시 확보
    days = int(os.getenv("FG_KRX_DAYS", "420"))

    kospi = fetch_index_ohlcv("KS11", days=days)["Close"]
    kospi.index = pd.to_datetime(kospi.index)
    krx_kospi = krx_source.fetch_real_fg_inputs(kospi, cache_path=cache_path, max_fetch=max_fetch)
    kospi_df = fear_greed_real(kospi, krx_kospi)

    # KOSDAQ — 원본대로 VKOSPI·옵션·국채선물 공유, 지수만 KQ11 (cache 재사용 → 추가 fetch 거의 없음)
    kosdaq = fetch_index_ohlcv("KQ11", days=days)["Close"]
    kosdaq.index = pd.to_datetime(kosdaq.index)
    krx_kosdaq = krx_source.fetch_real_fg_inputs(kosdaq, cache_path=cache_path, max_fetch=max_fetch)
    kosdaq_df = fear_greed_real(kosdaq, krx_kosdaq)

    return {
        "kospi": _sentiment_record("KOSPI", "KS11", kospi_df),
        "kosdaq": _sentiment_record("KOSDAQ", "KQ11", kosdaq_df),
        "source": "krx-real",
    }


def _build_market_sentiment_proxy() -> dict:
    try:
        bond_df = fetch_ktb_futures_pair(days=400)
    except Exception as e:
        print(f"  [!] 국채선물 ETF 수신 실패 — BondDiff 제외: {e}")
        bond_df = None
    try:
        kospi_pc = fetch_putcall_proxy_ratio("KOSPI", days=400)
    except Exception as e:
        print(f"  [!] KOSPI PutCall proxy 실패: {e}")
        kospi_pc = None
    try:
        kosdaq_pc = fetch_putcall_proxy_ratio("KOSDAQ", days=400)
    except Exception as e:
        print(f"  [!] KOSDAQ PutCall proxy 실패: {e}")
        kosdaq_pc = None
    return {
        "kospi": build_index_sentiment("KS11", "KOSPI", bond_df=bond_df, putcall_proxy=kospi_pc),
        "kosdaq": build_index_sentiment("KQ11", "KOSDAQ", bond_df=bond_df, putcall_proxy=kosdaq_pc),
        "source": "proxy",
    }
