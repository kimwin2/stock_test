"""flow_signals 파이프라인 오케스트레이션.

전체 흐름:
1. 시장 심리 (KOSPI/KOSDAQ Pier&Grid 단순화)
2. 주도 업종 (ETF Mansfield RS + 변동성 조정 모멘텀)
3. 업종 쏠림 지수
4. 유니버스 구성 (시총 상위 N개)
5. 종목별 수급 빈집 점수 (외인+기관 5일 vs 20일)
6. 수급 빈집 + 주도 섹터 교집합
7. 신고가 (수급 빈집 후보 한정)
8. 최종 JSON 조립
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .market_sentiment import build_market_sentiment
from .relative_strength import build_leading_sectors
from .sector_skew import compute_crowding_index
from .universe import build_universe
from .supply_vacancy import collect_universe_vacancy, rank_vacancy_by_sector, enrich_with_chart_and_buyzone
from .new_highs import detect_new_highs_for_codes
from .sector_flows import aggregate_by_sector, top_movers_per_sector
from .trading_intensity import compute_ti_for_codes


KST = timezone(timedelta(hours=9))


def build_cash_recommendation(market_sentiment: dict, crowding: dict) -> dict:
    """피어그리드 + 쏠림지수 기반 권고 현금 비중.

    피어그리드 기반:
      과열(75+) → 현금 30%
      강세(55+) → 현금 10%
      중립(45+) → 현금 10%
      약세(25+) → 현금 5%
      공포(<25) → 현금 0%
    쏠림 35+ 면 현금 10%p 추가.
    """
    if not market_sentiment or "kospi" not in market_sentiment:
        return {"available": False}

    fg_kospi = market_sentiment.get("kospi", {}).get("fearGreed")
    fg_kosdaq = market_sentiment.get("kosdaq", {}).get("fearGreed")
    if fg_kospi is None and fg_kosdaq is None:
        return {"available": False}

    fg = fg_kospi if fg_kospi is not None else fg_kosdaq

    if fg >= 75:
        cash_pct, level = 30, "리스크 최대"
    elif fg >= 55:
        cash_pct, level = 10, "리스크 보통"
    elif fg >= 45:
        cash_pct, level = 10, "관망"
    elif fg >= 25:
        cash_pct, level = 5, "비중확대 시작"
    else:
        cash_pct, level = 0, "공격 진입"

    crowd_signal = (crowding or {}).get("signal")
    if crowd_signal in ("쏠림", "극심쏠림"):
        cash_pct += 10
        level += " · 쏠림가산"

    return {
        "available": True,
        "cashPct": min(cash_pct, 50),
        "level": level,
        "fearGreed": fg,
        "crowdingSignal": crowd_signal,
    }


def _resolve_leading_sectors_from_etfs(leading_etfs: list[dict]) -> list[str]:
    """주도 ETF 라벨에서 우리 sector taxonomy 라벨 추출."""
    sector_map = [
        ("반도체", "반도체"),
        ("2차전지", "2차전지"),
        ("배터리", "2차전지"),
        ("리튬", "2차전지"),
        ("자동차", "자동차"),
        ("화장품", "화장품/소비재"),
        ("화학", "화학"),
        ("에너지", "화학"),
        ("게임", "게임/IT"),
        ("소프트", "게임/IT"),
        ("코스닥150 IT", "게임/IT"),
        ("IT", "게임/IT"),
        ("로봇", "로봇"),
        ("AI", "AI/반도체팹리스"),
        ("HBM", "반도체"),
        ("방산", "방산"),
        ("조선", "조선"),
        ("중공업", "조선"),
        ("산업재", "전력기기"),
        ("바이오", "바이오"),
        ("헬스케어", "바이오"),
        ("우주", "우주항공"),
        ("건설", "건설/인프라"),
        ("철강", "건설/인프라"),
        ("보험", "금융"),
        ("배당", "금융"),
        ("미국", "기타"),  # 해외 ETF 는 한국 sector 매핑 없음
        ("나스닥", "기타"),
        ("S&P", "기타"),
        ("차이나", "기타"),
    ]
    sectors: set[str] = set()
    for etf in leading_etfs:
        name = etf.get("name") or ""
        for kw, sector in sector_map:
            if kw in name:
                if sector != "기타":
                    sectors.add(sector)
    return sorted(sectors)


def build_flow_dashboard(
    top_n_kospi: int = 400,
    top_n_kosdaq: int = 200,
    new_high_candidates_only: int = 80,
) -> dict:
    print("=" * 60)
    print(">>> Flow Signals Pipeline Start")
    print(f"   시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    started = time.time()

    # ─────────────────────────────────────────
    # Step 1: Market sentiment (Pier&Grid)
    # ─────────────────────────────────────────
    print("\n[Step 1] 시장 심리 (Pier&Grid)")
    try:
        market_sentiment = build_market_sentiment()
    except Exception as e:
        print(f"  [!] 실패: {e}")
        market_sentiment = {"error": str(e)}

    # ─────────────────────────────────────────
    # Step 2: Leading sectors (ETF RS)
    # ─────────────────────────────────────────
    print("\n[Step 2] 주도 업종 (ETF RS)")
    try:
        leading = build_leading_sectors()
    except Exception as e:
        print(f"  [!] 실패: {e}")
        leading = {"items": [], "top": [], "leading": [], "error": str(e)}

    leading_sectors_etf = _resolve_leading_sectors_from_etfs(leading.get("leading", []))
    leading_sectors = list(leading_sectors_etf)
    print(f"   ETF RS70+ 기반 주도 섹터: {leading_sectors_etf or '(미해결)'}")

    # ─────────────────────────────────────────
    # Step 3: Sector skew (쏠림 지수)
    # ─────────────────────────────────────────
    print("\n[Step 3] 업종 쏠림 지수")
    try:
        crowding = compute_crowding_index()
    except Exception as e:
        print(f"  [!] 실패: {e}")
        crowding = {"available": False, "error": str(e)}

    # ─────────────────────────────────────────
    # Step 4: Universe
    # ─────────────────────────────────────────
    print(f"\n[Step 4] 유니버스 구성 (KOSPI {top_n_kospi} + KOSDAQ {top_n_kosdaq})")
    try:
        universe = build_universe(top_n_kospi=top_n_kospi, top_n_kosdaq=top_n_kosdaq)
        print(f"   유니버스 크기: {len(universe)}개")
    except Exception as e:
        print(f"  [!] 실패: {e}")
        return {
            "updatedAt": datetime.now(KST).isoformat(),
            "error": f"universe 구성 실패: {e}",
            "marketSentiment": market_sentiment,
            "leadingSectors": leading,
            "crowding": crowding,
        }

    # ─────────────────────────────────────────
    # Step 5: Supply vacancy
    # ─────────────────────────────────────────
    print(f"\n[Step 5] 종목별 수급 빈집 점수 ({len(universe)}개)")
    vacancy_df = collect_universe_vacancy(universe, sleep_sec=0.10, progress_every=100)
    print(f"   수집 성공: {len(vacancy_df)}개")

    # ─────────────────────────────────────────
    # Step 6: Vacancy ranking + leading sector intersection
    # ─────────────────────────────────────────
    print("\n[Step 6] 수급 빈집 랭킹 + 주도 섹터 교집합")
    vacancy_result = rank_vacancy_by_sector(
        vacancy_df,
        leading_sectors=leading_sectors,
        top_n=30,
    )

    # ─────────────────────────────────────────
    # Step 6b: 외인/기관 섹터별 흐름
    # ─────────────────────────────────────────
    print("\n[Step 6b] 외인/기관 섹터별 흐름 합산")
    try:
        sector_flows = aggregate_by_sector(vacancy_df)
        sector_movers = top_movers_per_sector(vacancy_df)
    except Exception as e:
        print(f"  [!] 섹터 흐름 실패: {e}")
        sector_flows = {"foreigner": [], "organ": [], "total": []}
        sector_movers = {}

    # leadingSectors 보강 — 기관/외인 매수 상위 섹터를 추가 (ETF 없는 섹터를 잡기 위함)
    flow_sectors: list[str] = []
    for entry in (sector_flows.get("organ") or [])[:5]:
        if entry["amount"] > 0 and entry["sector"] not in flow_sectors:
            flow_sectors.append(entry["sector"])
    for entry in (sector_flows.get("foreigner") or [])[:5]:
        if entry["amount"] > 0 and entry["sector"] not in flow_sectors:
            flow_sectors.append(entry["sector"])
    leading_sectors_flow = flow_sectors[:6]
    print(f"   기관/외인 매수 상위 섹터: {leading_sectors_flow}")
    for sector in leading_sectors_flow:
        if sector not in leading_sectors:
            leading_sectors.append(sector)
    print(f"   최종 주도 섹터(통합): {leading_sectors}")

    # ─────────────────────────────────────────
    # Step 7: 매수 후보 = 빈집 ∩ 주도섹터 → 차트 + 매수타점 + 신고가 enrichment
    # ─────────────────────────────────────────
    enriched_candidates: list[dict] = []
    candidate_codes: list[str] = []
    code_to_meta: dict[str, dict] = {}

    if not vacancy_df.empty:
        if leading_sectors:
            cand_df = vacancy_df[vacancy_df["sector"].isin(leading_sectors)].head(new_high_candidates_only)
        else:
            cand_df = vacancy_df.head(new_high_candidates_only)
        candidate_codes = cand_df["code"].tolist()
        for _, r in cand_df.iterrows():
            code_to_meta[r["code"]] = {"name": r["name"], "sector": r["sector"]}

        candidate_dicts = cand_df.to_dict("records")
        print(f"\n[Step 7] 매수후보 enrichment (차트+매수타점) {len(candidate_dicts)}개")
        try:
            enriched_candidates = enrich_with_chart_and_buyzone(candidate_dicts, sleep_sec=0.0, progress_every=20)
        except Exception as e:
            print(f"  [!] enrichment 실패: {e}")
            enriched_candidates = candidate_dicts

    # 매수 후보 우선순위: 추세살아있음(MA10위) + 신고가 가까움 + 빈집정도
    def _candidate_score(c: dict) -> float:
        score = 0.0
        if c.get("aboveMA10"):
            score += 30
        if c.get("aboveMA20"):
            score += 10
        if c.get("newHigh250d"):
            score += 40
        elif c.get("newHigh50d"):
            score += 25
        # 250일 고점에 가까울수록 가산
        if c.get("max250d") and c.get("close"):
            ratio = c["close"] / c["max250d"]
            score += min(20, max(0, (ratio - 0.85) / 0.15 * 20))
        if c.get("buyZone", {}).get("inBuyZone"):
            score += 15
        # vacancy 정도 (음수일수록 빈집 큼)
        v = c.get("vacancyScore") or 0
        score += max(0, min(20, -v / 1e10))
        return score

    enriched_candidates.sort(key=_candidate_score, reverse=True)

    # ─────────────────────────────────────────
    # Step 7b: 50일/250일 신고가 리스트
    # ─────────────────────────────────────────
    new_highs = {
        "high50d": [c for c in enriched_candidates if c.get("newHigh50d")][:15],
        "high250d": [c for c in enriched_candidates if c.get("newHigh250d")][:15],
    }

    # ─────────────────────────────────────────
    # Step 8: TI (거래대금 강도) — 매수후보 중 신고가 또는 RS 강한 종목 한정
    # ─────────────────────────────────────────
    ti_candidates = [c for c in enriched_candidates if c.get("newHigh50d") or c.get("aboveMA20")][:12]
    ti_codes = [c["code"] for c in ti_candidates]
    if ti_codes:
        print(f"\n[Step 8] 거래대금 강도 (TI) 계산 {len(ti_codes)}개")
        try:
            ti_results = compute_ti_for_codes(
                ti_codes,
                {c["code"]: {"name": c["name"], "sector": c["sector"]} for c in ti_candidates},
                progress_every=10,
            )
        except Exception as e:
            print(f"  [!] TI 실패: {e}")
            ti_results = []
    else:
        ti_results = []

    # ─────────────────────────────────────────
    # Step 9: 매도 시그널 — 신고가 갱신 후 음전 (매수 후보 풀 안에서)
    # ─────────────────────────────────────────
    exit_signals = []
    for c in enriched_candidates:
        ph = c.get("priceHistory60d") or []
        if len(ph) < 3:
            continue
        last_close = ph[-1]
        last_high_window = max(ph[-5:])
        # 신고가 후 음전 — 최근 5일 내 신고가 친 다음 종가가 그 고점에서 -2% 이상 빠짐
        if last_high_window > 0 and last_close < last_high_window * 0.98:
            # MA10 이탈도 동시에 체크
            ma10 = c.get("ma10")
            if ma10 and last_close < ma10:
                exit_signals.append({
                    "code": c["code"],
                    "name": c["name"],
                    "sector": c["sector"],
                    "lastClose": last_close,
                    "recentHigh": last_high_window,
                    "drawdownFromHighPct": round((last_close / last_high_window - 1) * 100, 2),
                    "ma10": ma10,
                })

    # ─────────────────────────────────────────
    # Step 10: Cash recommendation
    # ─────────────────────────────────────────
    cash_recommendation = build_cash_recommendation(market_sentiment, crowding)

    # ─────────────────────────────────────────
    # Step 11: 조립
    # ─────────────────────────────────────────
    elapsed = time.time() - started
    payload = {
        "updatedAt": datetime.now(KST).isoformat(),
        "elapsedSeconds": round(elapsed, 1),
        "marketSentiment": market_sentiment,
        "crowding": crowding,
        "cashRecommendation": cash_recommendation,
        "leadingSectors": leading,
        "leadingSectorLabels": leading_sectors,
        "supplyVacancy": vacancy_result,
        "buyCandidates": enriched_candidates[:30],
        "sectorFlows": sector_flows,
        "sectorMovers": sector_movers,
        "newHighs": new_highs,
        "tradingIntensity": ti_results,
        "exitSignals": exit_signals[:15],
        "universeSize": int(len(universe)),
        "vacancyAnalyzed": int(len(vacancy_df)),
    }

    print("\n" + "=" * 60)
    print("=== Flow Pipeline Result ===")
    print("=" * 60)
    print(f"   소요: {elapsed:.1f}s")
    print(f"   주도 섹터: {leading_sectors}")
    print(f"   빈집 분석 종목: {len(vacancy_df)}")
    print(f"   빈집 + 주도섹터 매수후보: {len(vacancy_result.get('leadingTop', []))}")
    print(f"   50일 신고가: {len(new_highs.get('high50d', []))}, 250일 신고가: {len(new_highs.get('high250d', []))}")

    return payload


def _sanitize_for_json(obj):
    """Infinity / NaN 을 null 로 변환 (브라우저 JSON.parse 호환)."""
    import math
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def save_flow_dashboard(payload: dict, output_path: str | None = None) -> str:
    if output_path is None:
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_dir = os.path.dirname(backend_dir)
        output_path = os.path.join(project_dir, "frontend", "flow_dashboard.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    sanitized = _sanitize_for_json(payload)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sanitized, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"\n[OK] flow_dashboard 저장: {output_path}")
    return output_path


if __name__ == "__main__":
    payload = build_flow_dashboard(top_n_kospi=200, top_n_kosdaq=100)
    save_flow_dashboard(payload)
