"""시장 심리 — Fear & Greed Oscillator (참고 자료 5-feature 설계 미러링).

참고 자료 원본 (구글 코랩) 의 5개 feature 설계를 최대한 그대로 따라간다:

| Feature       | 원본                          | 우리 구현                                                  |
|---------------|-------------------------------|------------------------------------------------------------|
| Momentum      | (Close − MA125)/MA125 × 100    | 동일                                                        |
| (1 − Vol)     | 1 − V-KOSPI 200 (정규화 후)    | 1 − 20일 수익률 stdev (V-KOSPI 무료 소스 없음)              |
| BondDiff      | 10년 − 5년 국채선물 지수 (정규화) | 10y/5y 국채선물 ETF 100 으로 리베이스 후 차이                |
| RSI(10)       | 10일 RSI                      | 동일                                                        |
| (1 − PutCall) | 1 − Put ATM/Call ATM (정규화)  | 1 − (인버스ETF 거래대금 / 정방향ETF 거래대금) 의 정규화      |

가중치는 원본과 동일하게 5 features × 0.2.
계산 후 F&G 인덱스(0~100)에 MACD(12,26,9) 를 씌워 히스토그램 = oscillator.
원본은 F&G 자체에 MACD 를 적용 (스케일 0~1) → 우리도 fear_greed/100 으로 통일.

원본과 차이가 남는 부분 (불가피):
- V-KOSPI 200 은 KRX 로그인 필요 → 20일 stdev (실현 변동성) 로 근사
- KOSPI200 옵션 ATM 은 무료 소스 없음 → 인버스/정방향 ETF 거래대금 비율로 근사
- 참고 자료 의 "10년국채선물지수" 정체 불명 — 우리는 표준 KRX 상장
  TIGER 국채선물10년 (305080) + KODEX 국채선물5년 (453850) 사용
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data_sources import fetch_index_ohlcv, fetch_ktb_futures_pair, fetch_putcall_proxy_ratio


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


def _rebase_to_100(series: pd.Series) -> pd.Series:
    """첫 유효값을 100 으로 리베이스. 채권 ETF 의 절대 가격 차이 제거용."""
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
    """일봉 OHLCV → F&G(0~100) + Oscillator (MACD 히스토그램).

    Parameters
    ----------
    df : pd.DataFrame
        DatetimeIndex + Close 컬럼 (FDR 형식).
    bond_df : pd.DataFrame | None
        ktb5y/ktb10y 컬럼 가진 5년/10년 국채선물 ETF 종가. None 이면 BondDiff 제외.
    putcall_proxy : pd.Series | None
        인버스 ETF 거래대금 / 정방향 ETF 거래대금 비율 (date index).
        None 이면 PutCall 제외.
    """
    df = df.copy()
    close = df["Close"]

    # === 5-feature 계산 (원본 설계) ===
    # 1) Momentum — (Close − MA125)/MA125 × 100
    ma125 = close.rolling(125).mean()
    df["fg_momentum"] = (close - ma125) / ma125 * 100

    # 2) RSI(10)
    df["fg_rsi10"] = _rsi(close, 10)

    # 3) (1 − Volatility): 20일 수익률 표준편차의 60일 EMA — V-KOSPI 200 대용
    #    실현 vol 자체는 시장 잠잠해지면 즉시 떨어지지만 내재 vol(V-KOSPI)은 헤지 수요로
    #    끈끈하게 유지됨 → EMA 로 부드럽게 처리해서 V-KOSPI 의 sticky 성질 모방.
    #    원본은 변동성 자체를 minmax 후 (1 − v) 처리 → 우리는 음수화 후 minmax 결과가 동일
    vol20 = close.pct_change().rolling(20).std() * np.sqrt(252) * 100  # 연율화 %
    vol_smooth = vol20.ewm(span=60, adjust=False).mean()
    df["fg_inv_vol"] = -vol_smooth

    feats = ["fg_momentum", "fg_rsi10", "fg_inv_vol"]

    # 4) BondDiff — 10년 국채선물 ETF − 5년 국채선물 ETF (각각 100 으로 리베이스)
    if bond_df is not None and not bond_df.empty:
        bond_aligned = bond_df.reindex(df.index).ffill()
        b5_rebased = _rebase_to_100(bond_aligned["ktb5y"])
        b10_rebased = _rebase_to_100(bond_aligned["ktb10y"])
        df["fg_bond_diff"] = b10_rebased - b5_rebased
        feats.append("fg_bond_diff")
    else:
        df["fg_bond_diff"] = np.nan

    # 5) (1 − PutCall_proxy) — 인버스/정방향 ETF 거래대금 비율을 옵션 PutCall 대용
    #    높은 비율 = bear 매수 우세 = fear → minmax 후 (1 − x) 처리하면 fear 신호로 작동
    #    음수화 후 minmax 결과가 (1 − x) 와 동치
    if putcall_proxy is not None and not putcall_proxy.empty:
        pc_aligned = putcall_proxy.reindex(df.index).ffill()
        df["fg_inv_putcall"] = -pc_aligned
        feats.append("fg_inv_putcall")
    else:
        df["fg_inv_putcall"] = np.nan

    # === 정규화 + 가중평균 (원본은 sklearn MinMaxScaler — 동등 구현) ===
    valid_mask = df[feats].notna().all(axis=1)
    for f in feats:
        df[f"{f}_n"] = _minmax(df[f].where(valid_mask)) * 100

    weight = 1.0 / len(feats)
    df["fear_greed"] = sum(df[f"{f}_n"] * weight for f in feats)

    # === Oscillator: F&G/100 의 MACD(12,26,9) 히스토그램 ===
    fg_n = df["fear_greed"] / 100.0
    ema12 = fg_n.ewm(span=12, adjust=False).mean()
    ema26 = fg_n.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["fg_oscillator"] = macd - signal

    return df


def classify_zone(oscillator: float) -> str:
    """참고 자료 관점: zone 은 Oscillator(MACD 히스토그램) 의 부호/크기로 분류.

    F&G 인덱스 절대값(0~100) 은 1년 윈도우 MinMax 결과라 추세 전환을 늦게 잡음.
    참고 자료 차트의 진짜 시그널은 oscillator 의 zero-cross — 이게 zone 라벨이 돼야 함.
    Oscillator 일반 진폭 ≈ ±0.03 기준으로 5단계.
    """
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
    df = fetch_index_ohlcv(symbol, days=400)
    if df.empty:
        return {"label": label, "error": "no data"}
    df = fear_greed_oscillator(df, bond_df=bond_df, putcall_proxy=putcall_proxy)
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
        "zone": classify_zone(osc),
        "history": history_records,
    }


def build_market_sentiment() -> dict:
    # 채권 ETF 한 번만 받아서 KOSPI/KOSDAQ 양쪽에 공유
    try:
        bond_df = fetch_ktb_futures_pair(days=400)
    except Exception as e:
        print(f"  [!] 국채선물 ETF 수신 실패 — BondDiff 제외: {e}")
        bond_df = None
    # 인버스/정방향 ETF 거래대금 비율 (PutCall proxy) — KOSPI/KOSDAQ 별도 ETF 사용
    try:
        kospi_pc = fetch_putcall_proxy_ratio("KOSPI", days=400)
    except Exception as e:
        print(f"  [!] KOSPI PutCall proxy 수신 실패: {e}")
        kospi_pc = None
    try:
        kosdaq_pc = fetch_putcall_proxy_ratio("KOSDAQ", days=400)
    except Exception as e:
        print(f"  [!] KOSDAQ PutCall proxy 수신 실패: {e}")
        kosdaq_pc = None
    return {
        "kospi": build_index_sentiment("KS11", "KOSPI", bond_df=bond_df, putcall_proxy=kospi_pc),
        "kosdaq": build_index_sentiment("KQ11", "KOSDAQ", bond_df=bond_df, putcall_proxy=kosdaq_pc),
    }
