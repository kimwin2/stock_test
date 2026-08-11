"""외국인/기관 매수 흐름 — 섹터별 합산.

장 마감 후 18시 루틴:
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
        market_cap=("marketCap", "sum"),
        count=("code", "count"),
    ).reset_index()

    # 시총 정규화 강도 (만분율) — 절대금액만 쓰면 시총 큰 섹터(금융·반도체)가
    # 항상 상위를 차지해 "주도 섹터" 판정이 무의미해진다.
    for col, out_col in (("foreigner_5d", "foreigner_str"), ("organ_5d", "organ_str"), ("total_5d", "total_str")):
        grouped[out_col] = grouped.apply(
            lambda r, c=col: (r[c] / r["market_cap"] * 1e4) if r["market_cap"] else 0.0, axis=1
        )

    foreigner = grouped.sort_values("foreigner_5d", ascending=False).head(15)
    organ = grouped.sort_values("organ_5d", ascending=False).head(15)
    total = grouped.sort_values("total_5d", ascending=False).head(15)

    STRENGTH_COL = {"foreigner_5d": "foreigner_str", "organ_5d": "organ_str", "total_5d": "total_str"}

    def _to_records(g: pd.DataFrame, value_col: str) -> list[dict]:
        scol = STRENGTH_COL[value_col]
        return [
            {
                "sector": r["sector"],
                "amount": int(r[value_col]),
                "stockCount": int(r["count"]),
                # strength: 섹터 시총 대비 5일 순매수 (만분율). 1.0 = 섹터 시총의 0.01%
                "strength": round(float(r[scol]), 2),
                "marketCap": int(r["market_cap"]) if pd.notna(r["market_cap"]) else 0,
            }
            for _, r in g.iterrows()
            if r["sector"] != "기타"
        ]

    # bySector — 잘리지 않은 전체 섹터 표.
    #
    # 위 세 리스트는 화면 표시용이라 주체별 head(15) 로 잘린다. 그런데 주도섹터
    # 판정에서 "외인과 기관이 **둘 다** 사는 섹터" 를 구하려면 잘린 표로는 안 된다.
    # 한쪽 상위 15 밖으로 밀린 섹터가 교집합에서 통째로 사라지기 때문이다.
    # 실측(2026-08-11): 반도체장비는 기관 강도 20.2 로 그날 1위였는데 외국인
    # 상위 15 에는 없어서, 잘린 표로 교집합을 잡으면 그날의 진짜 주도섹터가
    # 후보에서 빠졌다. 판정 로직은 반드시 이 전체 표를 쓸 것.
    by_sector = {
        r["sector"]: {
            "sector": r["sector"],
            "stockCount": int(r["count"]),
            "marketCap": int(r["market_cap"]) if pd.notna(r["market_cap"]) else 0,
            "foreignerAmount": int(r["foreigner_5d"]),
            "organAmount": int(r["organ_5d"]),
            "totalAmount": int(r["total_5d"]),
            "foreignerStrength": round(float(r["foreigner_str"]), 2),
            "organStrength": round(float(r["organ_str"]), 2),
            "totalStrength": round(float(r["total_str"]), 2),
        }
        for _, r in grouped.iterrows()
        if r["sector"] != "기타"
    }

    return {
        "foreigner": _to_records(foreigner, "foreigner_5d"),
        "organ": _to_records(organ, "organ_5d"),
        "total": _to_records(total, "total_5d"),
        "bySector": by_sector,
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
