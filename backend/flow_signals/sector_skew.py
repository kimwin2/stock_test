"""업종 쏠림 지수 — 6개월 수익률 기반 (IBK 방식 단순화).

- ETF 6개월 수익률 랭킹 가중치(centered)
- 가중된 수익률의 표준편차 = 쏠림 강도
- 35 이상 → 과열/쏠림
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .data_sources import fetch_stock_ohlcv
from .relative_strength import THEME_ETFS


def compute_crowding_index(scale: float = 100.0, sleep_sec: float = 0.05) -> dict:
    """ETF 6개월 수익률 기반 쏠림 지수 + 시계열."""

    series_map: dict[str, pd.Series] = {}
    for code, label in THEME_ETFS.items():
        try:
            df = fetch_stock_ohlcv(code, days=400)
            if df.empty or len(df) < 130:
                continue
            series_map[label] = df["Close"]
        except Exception as e:
            print(f"  [!] {code} 쏠림계산 실패: {e}")
        if sleep_sec:
            time.sleep(sleep_sec)

    if len(series_map) < 10:
        return {"available": False, "items": [], "history": []}

    price_df = pd.concat(series_map, axis=1).dropna(how="all")
    ret6m = price_df.pct_change(126, fill_method=None)

    # 시계열 쏠림 지수
    history = []
    valid_idx = ret6m.dropna(how="all").index
    for dt in valid_idx[-180:]:  # 최근 6개월
        r = ret6m.loc[dt].dropna()
        if len(r) < 10:
            history.append({"date": dt.strftime("%Y-%m-%d"), "crowding": None})
            continue
        n = len(r)
        rank = r.rank(ascending=False, method="average")
        score = ((n + 1) / 2 - rank) / ((n - 1) / 2)  # centered -1..1
        weighted = score * r
        crowd = float(weighted.std(ddof=1) * scale)
        history.append({"date": dt.strftime("%Y-%m-%d"), "crowding": round(crowd, 2)})

    # 최신 leaders / laggards
    last_dt = valid_idx[-1]
    r = ret6m.loc[last_dt].dropna().sort_values(ascending=False)
    leaders = [{"name": k, "ret6m": round(float(v) * 100, 2)} for k, v in r.head(10).items()]
    laggards = [{"name": k, "ret6m": round(float(v) * 100, 2)} for k, v in r.tail(10).items()]

    latest = history[-1]["crowding"] if history else None
    signal = "확산"
    if latest is not None:
        if latest >= 50:
            signal = "극심쏠림"
        elif latest >= 35:
            signal = "쏠림"
        elif latest >= 20:
            signal = "주의"

    return {
        "available": True,
        "latest": latest,
        "signal": signal,
        "leaders": leaders,
        "laggards": laggards,
        "history": history,
    }
