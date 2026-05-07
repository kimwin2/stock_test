"""flow_signals 파이프라인 오케스트레이션.

전체 흐름:
1. 시장 심리 (KOSPI/KOSDAQ Fear & Greed 단순화)
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
    """Fear & Greed + 쏠림지수 기반 권고 현금 비중.

    Fear & Greed 기반:
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
    # 매핑은 ETF 명에 키워드가 등장하는 순서대로 매칭. 더 specific 한 키워드를 위에 둔다.
    # (예: "200 IT" 는 사실상 반도체 ETF 라 게임/IT 가 아닌 반도체로 매핑)
    sector_map = [
        # 반도체 (specific 먼저)
        ("HBM", "반도체"),
        ("팹리스", "AI/반도체팹리스"),
        ("기판", "반도체장비"),
        ("OSAT", "반도체장비"),
        ("패키징", "반도체장비"),
        ("디스플레이", "반도체"),
        ("200 IT", "반도체"),       # KOSPI 200 IT — 삼성전자/하이닉스 비중 큼
        ("코스닥150 IT", "반도체"),  # 코스닥 IT — 반도체 비중 큼
        ("반도체", "반도체"),
        # 2차전지/ESS
        ("2차전지", "2차전지"),
        ("배터리", "2차전지"),
        ("리튬", "2차전지"),
        ("ESS", "2차전지"),
        # 자동차/소비재
        ("자동차", "자동차"),
        ("화장품", "화장품/소비재"),
        # 화학/에너지
        ("화학", "화학"),
        ("에너지", "화학"),
        # 게임/IT (남은 IT 류)
        ("게임", "게임/IT"),
        ("소프트", "게임/IT"),
        ("IT", "게임/IT"),
        # 로봇/AI
        ("로봇", "로봇"),
        ("AI", "AI/반도체팹리스"),
        # 방산/조선/중공업
        ("방산", "방산"),
        ("조선", "조선"),
        ("중공업", "조선"),
        # 전력 인프라
        ("전력", "전력기기"),
        ("산업재", "전력기기"),
        ("원전", "원전"),
        # 바이오/우주
        ("바이오", "바이오"),
        ("헬스케어", "바이오"),
        ("우주", "우주항공"),
        ("위성", "우주항공"),
        # 건설 (좁게 — 시멘트/철강 ETF 는 매핑 X 로 두어 노이즈 차단)
        ("건설", "건설/인프라"),
        ("인프라", "건설/인프라"),
        # 금융 (증권 가장 먼저)
        ("증권", "금융"),
        ("KRX 증권", "금융"),
        ("보험", "금융"),
        ("배당", "금융"),
        ("은행", "금융"),
        # 연료전지/신재생
        ("연료전지", "연료전지/수소"),
        ("수소", "연료전지/수소"),
        ("태양광", "신재생"),
        ("신재생", "신재생"),
        ("그린뉴딜", "신재생"),
        # 해외 ETF 는 한국 sector 매핑 없음
        ("미국", "기타"),
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
    # Step 1: Market sentiment (Fear & Greed)
    # ─────────────────────────────────────────
    print("\n[Step 1] 시장 심리 (Fear & Greed)")
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

    # leadingSectors 보강 — 기관/외인 매수 상위 섹터를 추가 (ETF 없는 섹터를 잡기 위함).
    # top5 컷오프는 "금융/연료전지/신재생" 같이 시총은 적지만 분명한 주도 섹터를 놓쳐서 top8 로 확대.
    # 또한 vacancy_df 에 들어있는 모든 섹터 중 매수 우위 (organ+foreigner > 0) 면 후보 인정.
    flow_sectors: list[str] = []
    for entry in (sector_flows.get("organ") or [])[:8]:
        if entry["amount"] > 0 and entry["sector"] not in flow_sectors:
            flow_sectors.append(entry["sector"])
    for entry in (sector_flows.get("foreigner") or [])[:8]:
        if entry["amount"] > 0 and entry["sector"] not in flow_sectors:
            flow_sectors.append(entry["sector"])
    leading_sectors_flow = flow_sectors[:10]
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
        # 1) 빈집 필터 (외인+기관 5d 순매도)
        filtered = vacancy_df[vacancy_df["institutionNet5d"] < 0]
        # 2) 주도 섹터 매칭
        if leading_sectors:
            filtered = filtered[filtered["sector"].isin(leading_sectors)]
        # 3) 가장 빈집 (vacancyScore 가장 작은 음수) 순 정렬 + head
        cand_df = filtered.sort_values("vacancyScore", ascending=True).head(new_high_candidates_only)
        candidate_codes = cand_df["code"].tolist()
        for _, r in cand_df.iterrows():
            code_to_meta[r["code"]] = {"name": r["name"], "sector": r["sector"]}

        candidate_dicts = cand_df.to_dict("records")
        # vacancyScore percentile 계산용 전 유니버스 점수 (NaN/None 제외)
        import math
        all_scores = [
            float(s) for s in vacancy_df["vacancyScore"].tolist()
            if s is not None and not (isinstance(s, float) and math.isnan(s))
        ]
        print(f"\n[Step 7] 매수후보 enrichment (차트+매수타점+수급percentile) {len(candidate_dicts)}개")
        try:
            enriched_candidates = enrich_with_chart_and_buyzone(
                candidate_dicts,
                all_vacancy_scores=all_scores,
                sleep_sec=0.0,
                progress_every=20,
            )
        except Exception as e:
            print(f"  [!] enrichment 실패: {e}")
            enriched_candidates = candidate_dicts

    # 매수 후보 우선순위: 추세살아있음(MA10위) + 신고가 가까움 + 빈집정도
    # 참고 자료 강조점: "지금" 비어있는 상태 (currentVacancyDays / currentlyVacant)
    # + "주도섹터 1·2위 위주" + 단기 모멘텀 (시세강도) 가산
    top_sectors_for_scoring = leading_sectors[:2] if leading_sectors else []

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
        # 누적 vacancy 정도 (음수일수록 빈집 큼)
        v = c.get("vacancyScore") or 0
        score += max(0, min(20, -v / 1e10))
        # 현재성: 매도 연속 일수 (참고 자료: "지금 비어있는지" 가 핵심)
        streak = c.get("currentVacancyDays") or 0
        score += min(15, streak * 5)  # 1일 5점, 2일 10점, 3일+ 15점 캡
        if c.get("currentlyVacant"):
            score += 5
        # 강한섹터 1·2위 가산 — 텔레그램 분석상 "최우선" 섹터에 베팅 집중
        if c.get("sector") in top_sectors_for_scoring:
            score += 25
        # 단기 모멘텀 (시세강도) — RS 가 우리 종목 db 에 없어 5d 수익률로 대용.
        # 양수면 가산, 음수면 감산 (강하게 빠지는 종목은 빈집이라도 스코어 낮춤).
        ret5d = c.get("ret5d")
        if ret5d is not None:
            score += max(-15, min(20, ret5d * 1.5))
        return score

    enriched_candidates.sort(key=_candidate_score, reverse=True)

    # ─────────────────────────────────────────
    # Step 7c: 주도섹터 거래대금 톱5 — 빈집 필터와 별개로 외인+기관 동행 매수 주도주
    # 매수 후보(빈집 전략)에는 institutionNet5d<0 필터로 빠지지만, 삼전·하닉처럼
    # 외인·기관이 폭풍 매수 중인 거래대금 1위급 주도주를 별도 섹션으로 노출.
    # ─────────────────────────────────────────
    leading_value_top: list[dict] = []
    if not vacancy_df.empty and leading_sectors:
        import math
        pool = vacancy_df[vacancy_df["sector"].isin(leading_sectors)]
        pool = pool[pool["tradingValue5dAvg"].notna()]
        pool = pool.sort_values("tradingValue5dAvg", ascending=False).head(5)
        pool_dicts = pool.to_dict("records")
        # vacancyPercentile 계산용 전 유니버스 점수 — Step 7 의 if 블록 안에서만
        # 정의되어 여기서는 재사용 불가. 같은 방식으로 다시 계산.
        all_scores_lvt = [
            float(s) for s in vacancy_df["vacancyScore"].tolist()
            if s is not None and not (isinstance(s, float) and math.isnan(s))
        ]
        print(f"\n[Step 7c] 주도섹터 거래대금 톱5 enrichment (차트+빈집게이지) {len(pool_dicts)}개")
        try:
            leading_value_top = enrich_with_chart_and_buyzone(
                pool_dicts,
                all_vacancy_scores=all_scores_lvt,
                sleep_sec=0.0,
                progress_every=5,
            )
        except Exception as e:
            print(f"  [!] leading_value enrichment 실패: {e}")
            leading_value_top = pool_dicts

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
        "leadingValueTop": leading_value_top,
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
    # KOSDAQ 시총 200~300위에 텔레그램·외부 분석에서 자주 다루는 종목 (오이솔루션,
    # 인텍플러스, 네패스아크, 알멕, 브이엠, 한선엔지니어링, 와이지원, 아스플로,
    # 세미파이브, 싸이맥스, 나노, 세아메카닉스 등) 이 있어 300까지 확대.
    payload = build_flow_dashboard(top_n_kospi=300, top_n_kosdaq=300)
    save_flow_dashboard(payload)
