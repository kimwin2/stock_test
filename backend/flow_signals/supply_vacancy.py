"""수급 빈집 (Supply Vacancy) — 핵심 지표.

원리:
- "외국인 + 기관 누적 매수금액"이 최근에 줄어들었지만 (수급이 식음)
- 동시에 그 종목이 주도 섹터에 속해 있다면
- 다시 채워질 가능성이 높은 매수 후보다.

핵심 두 컬럼 (참고 엑셀 모델):
1. "거래대금: 최근일 - 5일평균"  ← 거래대금이 최근 줄었나
2. "연금/사모/투신 매수대금: 최근일 - 5일평균"  ← 큰손 자금이 줄었나

우리는 무료 데이터로 다음을 사용:
- foreigner_amount + organ_amount = institutional_amount (외인+기관 매수액 일자별)
- 5일 누적 / 20일 누적 비교 → 비어있는 정도 점수
- 거래대금 최근 vs 5일평균 → 거래 활성도

Score 정의:
  vacancy = (5일 누적) - (20일 평균을 5일로 환산)  (음수일수록 빈집)
"""

from __future__ import annotations

import time
from typing import Iterable

import numpy as np
import pandas as pd

from .data_sources import fetch_naver_investor_trend, parse_investor_trend, fetch_stock_ohlcv
from .buy_zones import compute_buy_zone


def compute_vacancy_score(trend: pd.DataFrame) -> dict | None:
    """투자자 트렌드 (날짜 정렬) → 빈집 점수.

    빈집이 클수록 (음수일수록) "수급이 빠진" 상태.
    Returns: dict with vacancyScore, foreignerNet5d, organNet5d, etc.
    """
    if trend is None or trend.empty or len(trend) < 5:
        return None

    df = trend.sort_values("date")

    # 5일 누적 (단위: 원) — 가장 최근 5일
    foreigner5 = float(df["foreigner_amount"].tail(5).sum())
    organ5 = float(df["organ_amount"].tail(5).sum())
    inst5 = foreigner5 + organ5

    # 20일이 없으면 가용한 만큼
    n = min(len(df), 20)
    foreigner_n = float(df["foreigner_amount"].tail(n).sum())
    organ_n = float(df["organ_amount"].tail(n).sum())
    inst_n = foreigner_n + organ_n

    # 5일 환산 평균
    inst_per_5d_baseline = inst_n / max(1, n) * 5

    vacancy = inst5 - inst_per_5d_baseline  # 음수 = 빈집

    # 거래대금 5일 평균 변화율 (close × volume)
    if "close" in df.columns and "volume" in df.columns:
        df = df.copy()
        df["trading_value"] = df["close"] * df["volume"]
        v_recent = float(df["trading_value"].tail(5).mean())
        v_baseline = float(df["trading_value"].tail(n).mean())
        v_ratio = v_recent / v_baseline if v_baseline > 0 else None
    else:
        v_recent = None
        v_ratio = None

    last_close = float(df["close"].iloc[-1]) if not df.empty else None
    foreigner_hold = df["foreigner_hold_ratio"].iloc[-1] if "foreigner_hold_ratio" in df.columns else None

    # 일별 외인+기관 매수액 (마지막 10일) — 차트 오버레이용
    last10 = df.tail(min(10, len(df)))
    daily_flow = [
        {
            "date": r["date"].strftime("%Y-%m-%d"),
            "instAmount": float(r["foreigner_amount"] + r["organ_amount"]),
            "foreigner": float(r["foreigner_amount"]),
            "organ": float(r["organ_amount"]),
        }
        for _, r in last10.iterrows()
    ]

    return {
        "vacancyScore": round(vacancy, 0),
        "foreignerNet5d": round(foreigner5, 0),
        "organNet5d": round(organ5, 0),
        "institutionNet5d": round(inst5, 0),
        "institutionNet20d": round(inst_n, 0),
        "tradingValue5dAvg": round(v_recent, 0) if v_recent is not None else None,
        "tradingValueRatio": round(v_ratio, 3) if v_ratio is not None else None,
        "close": last_close,
        "foreignerHoldRatio": foreigner_hold,
        "lastDate": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "dailyFlow10d": daily_flow,
    }


def collect_universe_vacancy(
    universe: pd.DataFrame,
    sleep_sec: float = 0.12,
    on_error: str = "skip",
    progress_every: int = 50,
) -> pd.DataFrame:
    """유니버스 전 종목의 빈집 점수 수집.

    universe DataFrame은 columns: code, name, market, marketCap, sector
    """
    rows: list[dict] = []
    total = len(universe)
    for idx, row in universe.iterrows():
        code = row["code"]
        try:
            raw = fetch_naver_investor_trend(code)
            trend = parse_investor_trend(raw)
            score = compute_vacancy_score(trend)
        except Exception as e:
            if on_error == "raise":
                raise
            print(f"  [!] {code} 실패: {e}")
            score = None

        if score:
            score.update(
                {
                    "code": code,
                    "name": row["name"],
                    "market": row["market"],
                    "marketCap": int(row["marketCap"]),
                    "sector": row["sector"],
                }
            )
            rows.append(score)

        if progress_every and (idx + 1) % progress_every == 0:
            print(f"  [.] {idx + 1}/{total}")

        if sleep_sec:
            time.sleep(sleep_sec)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _compute_percentile(score: float, all_scores: list[float]) -> float:
    """vacancyScore 의 universe percentile (0=가장 빈집, 100=가장 찼음)."""
    if not all_scores:
        return 50.0
    less_than = sum(1 for s in all_scores if s < score)
    return round(less_than / len(all_scores) * 100, 1)


def _vacancy_zone(percentile: float) -> str:
    """percentile → zone 라벨."""
    if percentile < 25:
        return "빈집"
    if percentile > 75:
        return "찼음"
    return "정상"


def enrich_with_chart_and_buyzone(
    candidates: list[dict],
    all_vacancy_scores: list[float] | None = None,
    sleep_sec: float = 0.0,
    progress_every: int = 30,
) -> list[dict]:
    """후보 종목들에 60일 가격 차트 + 매수 타점 통계 + 수급 percentile 추가.

    각 후보 dict 에 다음 키들이 추가됨:
      priceHistory60d, dateHistory60d, ma10, ma20
      newHigh50d, newHigh250d, ret5d, max250d, buyZone
      aboveMA10, aboveMA20
      vacancyPercentile (0~100), vacancyZone ("빈집"/"정상"/"찼음")
    """
    scores = list(all_vacancy_scores) if all_vacancy_scores is not None else []
    out: list[dict] = []
    for i, item in enumerate(candidates):
        code = item.get("code")
        if not code:
            continue
        try:
            df = fetch_stock_ohlcv(code, days=400)
        except Exception as e:
            print(f"  [!] enrich {code} 실패: {e}")
            out.append(item)
            if sleep_sec:
                time.sleep(sleep_sec)
            continue

        if df.empty or len(df) < 30:
            out.append(item)
            continue

        recent = df.tail(60).copy()
        price_hist = [round(float(v), 0) for v in recent["Close"]]
        date_hist = [d.strftime("%Y-%m-%d") for d in recent.index]

        # 이동평균
        ma10 = float(df["Close"].rolling(10).mean().iloc[-1]) if len(df) >= 10 else None
        ma20 = float(df["Close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else None

        last_close = float(df["Close"].iloc[-1])
        last_high = float(df["High"].iloc[-1])
        max_50 = float(df["High"].tail(50).max())
        max_250 = float(df["High"].tail(min(250, len(df))).max())

        ret5d = None
        if len(df) > 6:
            ret5d = round((last_close / float(df["Close"].iloc[-6]) - 1) * 100, 2)

        buy_zone = compute_buy_zone(df)

        # 수급 percentile (전 유니버스 vacancyScore 기준)
        vac_score = item.get("vacancyScore")
        if vac_score is not None and scores:
            percentile = _compute_percentile(float(vac_score), scores)
            zone = _vacancy_zone(percentile)
        else:
            percentile = None
            zone = None

        enriched = {
            **item,
            "priceHistory60d": price_hist,
            "dateHistory60d": date_hist,
            "ma10": round(ma10, 0) if ma10 is not None else None,
            "ma20": round(ma20, 0) if ma20 is not None else None,
            "newHigh50d": bool(last_high >= max_50 * 0.999),
            "newHigh250d": bool(last_high >= max_250 * 0.999),
            "ret5d": ret5d,
            "max250d": round(max_250, 0),
            "buyZone": buy_zone,
            "aboveMA10": bool(ma10 is not None and last_close >= ma10),
            "aboveMA20": bool(ma20 is not None and last_close >= ma20),
            "vacancyPercentile": percentile,
            "vacancyZone": zone,
        }
        out.append(enriched)

        if progress_every and (i + 1) % progress_every == 0:
            print(f"  [.] enrich {i + 1}/{len(candidates)}")
        if sleep_sec:
            time.sleep(sleep_sec)

    return out


def rank_vacancy_by_sector(
    df: pd.DataFrame,
    leading_sectors: list[str] | None = None,
    top_n: int = 30,
) -> dict:
    """빈집 상위 종목 + 주도 섹터 필터.

    leading_sectors: 주도 업종 라벨 리스트. 없으면 전체에서 상위만.
    """
    if df.empty:
        return {"top": [], "leadingTop": [], "byLeadingSector": {}}

    df = df.copy()
    df = df[df["institutionNet5d"] < 0]  # 빈집 = 외인+기관 5일 순매도
    df = df.sort_values("vacancyScore", ascending=True)  # 더 음수 = 더 빈집

    overall_top = df.head(top_n).to_dict("records")

    leading_top: list[dict] = []
    by_sector: dict[str, list[dict]] = {}
    if leading_sectors:
        leading_df = df[df["sector"].isin(leading_sectors)]
        leading_top = leading_df.head(top_n).to_dict("records")
        for sector in leading_sectors:
            sector_df = df[df["sector"] == sector].head(8)
            if not sector_df.empty:
                by_sector[sector] = sector_df.to_dict("records")

    return {
        "top": overall_top,
        "leadingTop": leading_top,
        "byLeadingSector": by_sector,
        "totalAnalyzed": int(len(df)),
    }
