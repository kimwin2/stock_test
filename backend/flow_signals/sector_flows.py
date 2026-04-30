"""외국인/기관 매수 흐름 — 섹터별 합산.

태린이아빠 18시 루틴:
  "외국인 : 반도체와 전력(ESS, 원전, 조선엔진, 전력기기 등) 이 핵심"
  "기관 시총 대비: ..., 기관 금액기준: ..."
  "사모 / 투신 / 연금 별 업종 매수 순위"

KRX의 사모/투신/연금 분리 데이터는 무료로 못 받지만,
외국인 + 기관 합계는 Naver 종목별 데이터로 종목 → 섹터 합산 가능.
"""

from __future__ import annotations

import pandas as pd


def aggregate_by_sector(vacancy_df: pd.DataFrame) -> dict:
    """수급 데이터 → 섹터별 외국인/기관 5일 누적 매수액 (원).

    Returns: dict with foreigner_by_sector, organ_by_sector, total_by_sector
    """
    if vacancy_df.empty:
        return {"foreigner": [], "organ": [], "total": []}

    df = vacancy_df.copy()

    # 섹터별 합산
    grouped = df.groupby("sector").agg(
        foreigner_5d=("foreignerNet5d", "sum"),
        organ_5d=("organNet5d", "sum"),
        total_5d=("institutionNet5d", "sum"),
        count=("code", "count"),
    ).reset_index()

    foreigner = grouped.sort_values("foreigner_5d", ascending=False).head(15)
    organ = grouped.sort_values("organ_5d", ascending=False).head(15)
    total = grouped.sort_values("total_5d", ascending=False).head(15)

    def _to_records(g: pd.DataFrame, value_col: str) -> list[dict]:
        return [
            {
                "sector": r["sector"],
                "amount": int(r[value_col]),
                "stockCount": int(r["count"]),
            }
            for _, r in g.iterrows()
            if r["sector"] != "기타"
        ]

    return {
        "foreigner": _to_records(foreigner, "foreigner_5d"),
        "organ": _to_records(organ, "organ_5d"),
        "total": _to_records(total, "total_5d"),
    }


def top_movers_per_sector(vacancy_df: pd.DataFrame, top_per: int = 3) -> dict:
    """섹터별로 외인+기관 5d 매수가 가장 큰 종목 / 가장 큰 매도 종목."""
    if vacancy_df.empty:
        return {}

    out: dict[str, dict] = {}
    for sector, sub in vacancy_df.groupby("sector"):
        if sector == "기타":
            continue
        sub = sub.sort_values("institutionNet5d", ascending=False)
        out[sector] = {
            "topBuy": sub.head(top_per).to_dict("records"),
            "topSell": sub.tail(top_per).to_dict("records"),
        }
    return out
