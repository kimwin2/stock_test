"""ETF 기반 주도 업종/테마 추출.

태린이아빠 로직:
1. Mansfield RS (60/120/250일) 평균 → 0~100 정규화 → 70 이상 = 주도
2. 변동성 조정 모멘텀 (3/6/12개월 수익률 / 표준편차의 평균)
3. Sortino (다운사이드 변동성 대비 수익률)
4. 10/20/50일 이동평균선 위 종가만 통과

본 구현은 위 셋 중 (1) Mansfield RS + (2) 변동성 조정 모멘텀만.
ETF 풀은 시가총액 상위 + 테마성 ETF 선별.
"""

from __future__ import annotations

import time
import numpy as np
import pandas as pd

from .data_sources import fetch_etf_listing, fetch_stock_ohlcv, fetch_index_ohlcv


# 태린이아빠가 즐겨 쓰는 테마/업종 ETF 핸드픽 (티커 → 라벨)
THEME_ETFS = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "091160": "KODEX 반도체",
    "091230": "TIGER 반도체",
    "396500": "TIGER 반도체TOP10",
    "139260": "TIGER 200 IT",
    "098560": "KODEX 자동차",
    "117460": "KODEX 에너지화학",
    "228810": "TIGER 화장품",
    "227560": "TIGER 200 헬스케어",
    "266390": "KODEX 2차전지산업",
    "305720": "KODEX 2차전지산업",
    "364980": "TIGER KRX BBIG K-뉴딜",
    "117680": "KODEX 철강",
    "157490": "TIGER 소프트웨어",
    "139220": "TIGER 200 건설",
    "140700": "KODEX 보험",
    "139230": "TIGER 200 중공업",
    "117700": "KODEX 조선",
    "446770": "KODEX 미국조선",
    "381180": "TIGER 미국S&P500",
    "133690": "TIGER 미국나스닥100",
    "473460": "KODEX 인공지능반도체",
    "479850": "TIGER 글로벌AI인프라",
    "456600": "KODEX K-방산",
    "449450": "PLUS K방산",
    "471490": "ACE 글로벌HBM반도체액티브",
    "454910": "KODEX K-로봇액티브",
    "445290": "TIGER 코스피고배당",
    "449180": "KOSEF K-게임",
    "395750": "TIGER 글로벌리튬",
    "381170": "TIGER 미국테크TOP10",
    "421970": "PLUS 글로벌HBM반도체",
    "404540": "TIGER 글로벌클라우드컴퓨팅",
    "279530": "KODEX 고배당",
    "433330": "KODEX 미국빅테크TOP7Plus",
    "381190": "TIGER 미국필라델피아반도체",
    "411060": "ACE 글로벌반도체TOP4Plus",
    # 추가: 전력기기/원전/방산 (태린이아빠가 자주 언급)
    "139250": "TIGER 200 중공업",
    "117710": "TIGER 200 IT",
    "139290": "TIGER 200 산업재",
    "143850": "TIGER 미국S&P500선물",
    "278530": "KODEX 200TR",
    "385520": "TIGER 미국나스닥100TR",
    "228790": "TIGER 화장품",
    "192090": "TIGER 차이나CSI300",
    "232080": "TIGER 코스닥150 IT",
}


def _mansfield_rs(etf: pd.Series, benchmark: pd.Series, ma_period: int) -> pd.Series:
    relative = etf / benchmark
    ma = relative.rolling(window=ma_period, min_periods=ma_period).mean()
    return ((relative / ma) - 1) * 100


def _normalize_to_100(x: float, scale: float = 12.0) -> float:
    return float(100 * (1 / (1 + np.exp(-x / scale))))


def _vol_adjusted_momentum(prices: pd.Series, windows=(63, 126, 252)) -> float:
    rets = prices.pct_change(fill_method=None)
    scores = []
    for w in windows:
        if len(rets.dropna()) < w * 0.5:
            continue
        seg = rets.tail(w)
        mean_r = seg.mean(skipna=True)
        std_r = seg.std(skipna=True)
        if std_r and std_r > 0:
            scores.append(mean_r / std_r)
    if not scores:
        return float("nan")
    return float(np.mean(scores))


def compute_etf_rs(
    sleep_sec: float = 0.05,
    benchmark: str = "KS11",
) -> pd.DataFrame:
    benchmark_df = fetch_index_ohlcv(benchmark, days=400)
    if benchmark_df.empty:
        raise RuntimeError("벤치마크 데이터 가져오기 실패")
    bench_close = benchmark_df["Close"]

    rows: list[dict] = []
    for code, label in THEME_ETFS.items():
        try:
            df = fetch_stock_ohlcv(code, days=400)
        except Exception as e:
            print(f"  [!] ETF {code} 실패: {e}")
            continue
        if df.empty or len(df) < 60:
            continue

        close = df["Close"].dropna()
        common = close.index.intersection(bench_close.index)
        etf_aligned = close.loc[common]
        bench_aligned = bench_close.loc[common]

        rs_values = []
        for w in [60, 120, 250]:
            if len(etf_aligned) < w:
                continue
            rs = _mansfield_rs(etf_aligned, bench_aligned, ma_period=w).dropna()
            if not rs.empty:
                rs_values.append(rs.iloc[-1])

        if not rs_values:
            continue
        rs_avg_raw = float(np.mean(rs_values))
        rs_avg_norm = _normalize_to_100(rs_avg_raw)

        vam = _vol_adjusted_momentum(etf_aligned)

        rows.append(
            {
                "code": code,
                "name": label,
                "close": round(float(etf_aligned.iloc[-1]), 2),
                "rsAvg": round(rs_avg_raw, 2),
                "rsNorm": round(rs_avg_norm, 1),
                "volAdjMomentum": round(vam, 4) if pd.notna(vam) else None,
                "ret1m": round(float(etf_aligned.pct_change(20).iloc[-1] * 100), 2)
                    if len(etf_aligned) > 20 else None,
                "ret3m": round(float(etf_aligned.pct_change(60).iloc[-1] * 100), 2)
                    if len(etf_aligned) > 60 else None,
            }
        )
        if sleep_sec:
            time.sleep(sleep_sec)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("rsNorm", ascending=False).reset_index(drop=True)
    return df


def build_leading_sectors(top_n: int = 10) -> dict:
    df = compute_etf_rs()
    if df.empty:
        return {"items": [], "top": [], "leading": []}

    leading = df[df["rsNorm"] >= 70].copy()

    return {
        "all": df.to_dict("records"),
        "top": df.head(top_n).to_dict("records"),
        "leading": leading.to_dict("records"),
        "leadingCount": len(leading),
    }
