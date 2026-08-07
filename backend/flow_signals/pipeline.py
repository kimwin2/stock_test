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
from .supply_vacancy import (
    collect_universe_vacancy,
    rank_vacancy_by_sector,
    enrich_with_chart_and_buyzone,
    build_universe_metadata,
)
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
        # 금융 — 증권/보험/은행 분리 매핑.
        # "배당" ETF 는 여러 섹터의 고배당주 묶음이라 섹터 시그널이 아니므로 매핑하지 않는다.
        # (기존에는 배당 ETF 가 RS 상위에 들면 금융 전체가 주도섹터가 됐다.)
        ("증권", "증권"),
        ("KRX 증권", "증권"),
        ("보험", "보험"),
        ("은행", "은행"),
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
    # [수정 이력] 기존에는 set → sorted() 로 반환해 섹터 순서가 '가나다순' 이었다.
    # 그런데 후보 점수 로직은 leading_sectors.index(sector) 로 1·2·3위에
    # +40/+32/+24 를 준다. 즉 RS 가 가장 강한 섹터가 아니라 이름이 먼저 오는
    # 섹터가 최고 가산점을 받고 있었다 (실측: 반도체 RS 92.8 이 5위 +14,
    # 2차전지 RS 31.5 가 1위 +40). RS 강도 순으로 정렬해 순위를 의미 있게 만든다.
    sector_rs: dict[str, float] = {}
    for etf in leading_etfs:
        name = etf.get("name") or ""
        try:
            rs = float(etf.get("rsNorm") or 0)
        except (TypeError, ValueError):
            rs = 0.0
        for kw, sector in sector_map:
            if kw in name and sector != "기타":
                if rs > sector_rs.get(sector, float("-inf")):
                    sector_rs[sector] = rs
    return [s for s, _ in sorted(sector_rs.items(), key=lambda kv: kv[1], reverse=True)]


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
    #
    # [수정 이력] 기존 로직은 "절대 금액 top8 중 amount>0" 이면 무조건 추가해서
    # 주도 섹터가 13개(전체 분류의 절반 이상)까지 불어났고, 그 결과 "주도섹터 교집합"
    # 필터가 사실상 아무것도 걸러내지 못했다. 특히 시총이 큰 금융은 순매수가
    # 시총의 0.002% (정규화 강도 0.23) 에 불과한데도 절대금액만으로 상시 진입했다.
    #
    # 신규 규칙:
    #   1) 절대금액이 아니라 시총 정규화 강도(섹터 시총 대비 5일 순매수, 만분율)로 판정
    #   2) FLOW_STRENGTH_MIN 이상만 후보 (미미한 매수는 주도가 아님)
    #   3) 정규화 강도 상위 FLOW_SECTOR_TOP_N 개만 채택
    #   4) ETF RS 기반 섹터를 우선하고, 최종 개수를 MAX_LEADING_SECTORS 로 제한
    FLOW_STRENGTH_MIN = 5.0      # 만분율. 섹터 시총의 0.05% 이상 순매수
    FLOW_SECTOR_TOP_N = 4
    MAX_LEADING_SECTORS = 7

    flow_ranked: dict[str, float] = {}
    for kind in ("organ", "foreigner"):
        for entry in (sector_flows.get(kind) or []):
            strength = entry.get("strength")
            if strength is None or strength < FLOW_STRENGTH_MIN:
                continue
            sector = entry["sector"]
            # 외인/기관 중 더 강한 쪽 값을 그 섹터의 대표 강도로 사용
            if strength > flow_ranked.get(sector, 0.0):
                flow_ranked[sector] = strength

    leading_sectors_flow = [
        s for s, _ in sorted(flow_ranked.items(), key=lambda kv: kv[1], reverse=True)
    ][:FLOW_SECTOR_TOP_N]
    print(
        f"   수급 강도 기반 섹터(정규화 {FLOW_STRENGTH_MIN}+ 상위 {FLOW_SECTOR_TOP_N}): "
        f"{[(s, round(flow_ranked[s], 1)) for s in leading_sectors_flow] or '(없음)'}"
    )

    for sector in leading_sectors_flow:
        if sector not in leading_sectors:
            leading_sectors.append(sector)

    # ETF RS 섹터를 앞에 두고 전체 개수 제한 — 주도 섹터가 많아질수록 필터 의미가 사라진다.
    dropped_sectors = leading_sectors[MAX_LEADING_SECTORS:]
    leading_sectors = leading_sectors[:MAX_LEADING_SECTORS]
    if dropped_sectors:
        print(f"   상한({MAX_LEADING_SECTORS}) 초과로 제외된 섹터: {dropped_sectors}")
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

    # ─────────────────────────────────────────
    # 매수 후보 점수 — 참고 자료 최근 20일(2026-05-07 ~ 05-27) 발언 빈도 기반 가중치
    #
    # 빈도 분석 결과 (텔레그램 dump 1,456개 메시지):
    #   주도업종      220회  →  최우선 (1·2·3위 가중치 강화)
    #   신고가        114회  →  "52주 신고가 + 주도업종 + 거래대금 강도 하단" 3교집합
    #   오실레이터    130회  →  수급 빈집 (음수 osc) 깊이
    #   10일선/추세    64회  →  "10일 이평선 이탈해서 정리" — 강한 매도 시그널
    #   거래대금 강도  36회  →  하단(낮은 쪽) 진입을 선호. 과열 회피.
    #
    # 가장 자주 반복된 매수 조건 (5/18~5/27 사이만 7회):
    #   "52주 신고가 AND 주도업종 AND 거래대금 강도 하단"
    #   "주도업종 + 컨센상향 + 수급 빈집"  ← 컨센상향은 무료 데이터 부재
    #   "주도업종이면서 수급 빈집"
    #
    # 가중치 조정 방향:
    #   주도섹터 1·2·3위 +35/+28/+22 → +40/+32/+24 (강화: "주도업종만 한다")
    #   수급 빈집 max +30 → +35 (강화)
    #   신고가 (52주=250d 신고가) +22 → +28 (강화: "52주 신고가" 7회 반복)
    #   거래대금 강도 하단 (tvr<1.0) — 신규 가산 +12 ("거래대금 강도 하단" 7회 명시)
    #   매수권 진입 +10 → +6 (축소)
    #   외국인 5d 매수 universe 랭킹 — 유지
    #
    # [추세 살아있음 보정] — "추세 살아있는 종목 더 넣고싶다" 요청 반영.
    #   추세추종 강화: ↑10MA +18→+24, ↑20MA +6→+10
    #   신규: ↑50MA +8, 정배열(MA10>20>50) +12, MA10 우상향 +8
    #   모멘텀 복원: 5d ±8 → ±14, 신규 20d 추세 +14/-8 (상승은 크게, 하락은 작게 감점)
    #   10일선 이탈 -22 유지 (추세 깨진 종목은 강하게 배제)
    #
    # 참고: 컨센서스 상향 / 추정이익 가속화는 FnGuide 유료 데이터 필요 — 본 함수 미반영.
    # ─────────────────────────────────────────

    # 외국인 5일 순매수 절대금액 universe 랭킹 — "외국인 매수대금 순위" 추종.
    foreigner_buy_rank: dict[str, int] = {}
    if not vacancy_df.empty and "foreignerNet5d" in vacancy_df.columns:
        fn_pos = (
            vacancy_df[vacancy_df["foreignerNet5d"] > 0]
            .sort_values("foreignerNet5d", ascending=False)
        )
        for rk, (_, row) in enumerate(fn_pos.iterrows(), start=1):
            foreigner_buy_rank[row["code"]] = rk

    def _candidate_score_with_reasons(c: dict) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        # 1) 주도섹터 — "주도업종만 매수대상" (220회 언급, 최우선)
        sector = c.get("sector")
        if leading_sectors and sector:
            try:
                rank = leading_sectors.index(sector)
            except ValueError:
                rank = -1
            if rank == 0:
                score += 40; reasons.append(f"★주도섹터 1위({sector}) +40")
            elif rank == 1:
                score += 32; reasons.append(f"★주도섹터 2위({sector}) +32")
            elif rank == 2:
                score += 24; reasons.append(f"★주도섹터 3위({sector}) +24")
            elif 0 <= rank < 5:
                score += 14; reasons.append(f"주도섹터 {rank+1}위({sector}) +14")

        # 2) 수급 오실레이터 빈집 — "주도업종 + 수급 빈집" 7회 반복
        osc = c.get("oscLast")
        osc_pct = c.get("oscPercentile")
        if osc is not None and osc < 0:
            # historical percentile 낮을수록 (자기 분포 하위) 깊은 빈집.
            # 50→0점, 0→35점 (선형). pct None 이면 50 으로 처리.
            depth = max(0.0, min(35.0, (50 - (osc_pct if osc_pct is not None else 50)) * 0.7))
            score += depth
            tag = "깊은 빈집" if depth >= 25 else "빈집"
            reasons.append(f"{tag} osc={osc:.5f} pct={osc_pct} +{depth:.0f}")
        elif osc is None:
            # osc 없을 때 fallback: vacancyScore (시총표준화 모멘텀)
            v = c.get("vacancyScore") or 0
            if v < 0:
                fb = max(0.0, min(25.0, -v * 14000))
                score += fb
                reasons.append(f"빈집(모멘텀 fallback) +{fb:.0f}")

        # 3) 추세추종 — "추세가 살아있는" 종목 우대 (강화).
        #    10일선 위/이탈 + 20·50일선 정배열 + MA10 우상향 기울기.
        #    빈집(수급 일시 이탈)이라도 가격 추세가 살아있는 종목 = 이상적 매수타점.
        if c.get("aboveMA10"):
            score += 24; reasons.append("↑10MA +24")
        else:
            score -= 22; reasons.append("10MA 이탈 -22")
        if c.get("aboveMA20"):
            score += 10; reasons.append("↑20MA +10")
        if c.get("aboveMA50"):
            score += 8; reasons.append("↑50MA +8")
        if c.get("alignedMA"):
            score += 12; reasons.append("정배열(10>20>50) +12")
        if c.get("ma10Rising"):
            score += 8; reasons.append("10MA 우상향 +8")

        # 4) 신고가 — "52주 신고가" 7회 반복. 250일 신고가 ≈ 52주 신고가.
        if c.get("newHigh250d"):
            score += 28; reasons.append("52주 신고가 +28")
        elif c.get("newHigh50d"):
            score += 14; reasons.append("50d 신고가 +14")

        # 250일 고점 근접도 — 신고가는 아니지만 근접한 종목도 가산.
        # 0.90→0, 1.0→12 (선형, 0.85→0 보다 타이트하게)
        if c.get("max250d") and c.get("close"):
            ratio = c["close"] / c["max250d"]
            near = min(12.0, max(0.0, (ratio - 0.90) / 0.10 * 12))
            if near >= 2:
                score += near
                reasons.append(f"52주 고점근접 {ratio*100:.0f}% +{near:.0f}")

        # 5) 거래대금 강도 하단 (NEW) — "거래대금 강도 하단" 7회 명시.
        # tradingValueRatio (5d평균/20d평균) < 1.0 = 관심 감소 = TI 하단.
        # 신고가 근접인데 거래대금이 빠진 상태 = 바닥에서 매수 진입 (참고 자료 핵심 매수타점).
        tvr = c.get("tradingValueRatio")
        if tvr is not None:
            if tvr < 0.7:
                score += 14; reasons.append(f"거래대금 강도 깊은 하단 ×{tvr:.2f} +14")
            elif tvr < 1.0:
                score += 10; reasons.append(f"거래대금 강도 하단 ×{tvr:.2f} +10")
            elif tvr > 2.5:
                score -= 14; reasons.append(f"거래대금 과열 ×{tvr:.2f} -14")
            elif tvr > 1.8:
                score -= 7; reasons.append(f"거래대금 다소과열 ×{tvr:.2f} -7")

        # 6) 모멘텀 — 추세 살아있음 우대로 가중치 복원/확대.
        #    5d(단기) ±14 + 20d(추세 방향) +14/-8 (상승 추세는 크게, 하락은 작게 감점).
        ret5d = c.get("ret5d")
        if ret5d is not None:
            mom = max(-14.0, min(14.0, ret5d * 1.2))
            score += mom
            sign = "+" if mom >= 0 else ""
            reasons.append(f"5d {ret5d:+.1f}% {sign}{mom:.0f}")
        ret20d = c.get("ret20d")
        if ret20d is not None:
            if ret20d > 0:
                mom20 = min(14.0, ret20d * 0.6)
            else:
                mom20 = max(-8.0, ret20d * 0.4)
            score += mom20
            sign = "+" if mom20 >= 0 else ""
            reasons.append(f"20d 추세 {ret20d:+.1f}% {sign}{mom20:.0f}")

        # 7) 매수권 진입 — 보조.
        if c.get("buyZone", {}).get("inBuyZone"):
            score += 6; reasons.append("매수권 진입 +6")

        # 8) "지금" 비어있는 상태 — 매도 연속 일수 (수급 빈집 지속 가산).
        streak = c.get("currentVacancyDays") or 0
        if streak >= 1:
            cv = min(8.0, streak * 3)
            score += cv
            reasons.append(f"매도 {streak}일 연속 +{cv:.0f}")

        # 9) 외국인 5d 순매수 universe 랭킹 — "외국인 매수대금 순위" 추종.
        fn5 = c.get("foreignerNet5d") or 0
        fn_rank = foreigner_buy_rank.get(c.get("code"))
        if fn5 > 0 and fn_rank is not None:
            if fn_rank <= 30:
                score += 12; reasons.append(f"외인 5d 매수 #{fn_rank} (+{fn5/1e8:.0f}억) +12")
            elif fn_rank <= 100:
                score += 8;  reasons.append(f"외인 5d 매수 #{fn_rank} (+{fn5/1e8:.0f}억) +8")
            elif fn_rank <= 300:
                score += 4;  reasons.append(f"외인 5d 매수 #{fn_rank} (+{fn5/1e8:.0f}억) +4")

        return round(score, 1), reasons

    # 점수 + 근거를 각 후보 dict 에 부착
    for c in enriched_candidates:
        s, r = _candidate_score_with_reasons(c)
        c["taerinScore"] = s
        c["taerinReasons"] = r

    enriched_candidates.sort(key=lambda c: c.get("taerinScore", 0), reverse=True)

    # ─────────────────────────────────────────
    # Step 7a: 하드 필터 — 점수만으로는 걸러지지 않던 미달 종목 배제
    #
    # [수정 이력] 기존에는 추세·빈집 조건이 전부 '점수 가감' 이었고 최종 30개를
    # 커트라인 없이 상위 30개로 잘랐다. 그래서 통과 풀이 얇은 날에는
    #   - 10일선을 이탈해 추세가 깨진 종목 (-22점을 받고도 상위 30위 안)
    #   - 수급 오실레이터가 양수라 화면에 '정상/찼음' 으로 표시되는 종목
    # 이 그대로 매수 후보로 노출됐다 (실측: 30개 중 각각 4개 / 3개).
    #
    # 하드 조건:
    #   1) aboveMA10 — "추세가 살아있는" 종목만. 10일선 이탈은 강한 매도 시그널.
    #   2) oscLast < 0 — 빈집의 정식 정의(수급 오실레이터 음수). osc 계산이
    #      불가한 종목은 1차 스크리닝 지표로 대체 판정.
    #   3) MIN_CANDIDATE_SCORE — 최소 점수 커트라인.
    # 조건이 과해 후보가 MIN_CANDIDATES 미만이면 점수 커트라인만 완화한다
    # (추세·빈집은 전략의 정의 자체라 완화하지 않는다).
    MIN_CANDIDATE_SCORE = 45.0
    MIN_CANDIDATES = 8

    def _is_vacant(c: dict) -> bool:
        osc = c.get("oscLast")
        if osc is not None:
            return osc < 0
        # osc 없으면 1차 스크리닝(시총 표준화 모멘텀 + 5일 순매도) 으로 판정
        return (c.get("vacancyScore") or 0) < 0 and (c.get("institutionNet5d") or 0) < 0

    # 필터 이전 풀은 매도 시그널(Step 9) 산출에 쓴다. 하드 필터로 10MA 이탈
    # 종목을 빼버리면 "신고가 후 음전 + 10MA 이탈" 조건이 영원히 성립하지 않는다.
    pre_filter_candidates = list(enriched_candidates)

    total_before = len(enriched_candidates)
    trend_ok = [c for c in enriched_candidates if c.get("aboveMA10")]
    dropped_trend = total_before - len(trend_ok)
    vacant_ok = [c for c in trend_ok if _is_vacant(c)]
    dropped_vacancy = len(trend_ok) - len(vacant_ok)
    scored_ok = [c for c in vacant_ok if c.get("taerinScore", 0) >= MIN_CANDIDATE_SCORE]
    dropped_score = len(vacant_ok) - len(scored_ok)

    score_relaxed = False
    if len(scored_ok) < MIN_CANDIDATES and vacant_ok:
        # 점수 커트라인만 완화 (추세·빈집 조건은 유지)
        scored_ok = vacant_ok[:MIN_CANDIDATES]
        score_relaxed = True

    # 섹터 편중 방지 — 한 섹터가 후보 목록을 점유하면 사용자에겐 분산이 사라진다.
    # (실측: 증권 ETF 강세일 때 후보 15개 중 7개가 증권사)
    # 이미 점수순 정렬돼 있으므로 섹터별 상위 MAX_PER_SECTOR 개만 남긴다.
    MAX_PER_SECTOR = 4
    per_sector: dict[str, int] = {}
    diversified: list[dict] = []
    for c in scored_ok:
        sec = c.get("sector") or "기타"
        if per_sector.get(sec, 0) >= MAX_PER_SECTOR:
            continue
        per_sector[sec] = per_sector.get(sec, 0) + 1
        diversified.append(c)
    dropped_concentration = len(scored_ok) - len(diversified)
    scored_ok = diversified

    print(
        f"\n[Step 7a] 하드 필터: {total_before}개 → {len(scored_ok)}개 "
        f"(10MA 이탈 -{dropped_trend}, 빈집 아님 -{dropped_vacancy}, "
        f"{MIN_CANDIDATE_SCORE}점 미만 -{dropped_score}, "
        f"섹터 편중 -{dropped_concentration}"
        f"{', 점수 커트라인 완화 적용' if score_relaxed else ''})"
    )
    candidate_filter_stats = {
        "beforeFilter": total_before,
        "afterFilter": len(scored_ok),
        "droppedByTrend": dropped_trend,
        "droppedByVacancy": dropped_vacancy,
        "droppedByScore": dropped_score,
        "droppedByConcentration": dropped_concentration,
        "minScore": MIN_CANDIDATE_SCORE,
        "maxPerSector": MAX_PER_SECTOR,
        "scoreCutoffRelaxed": score_relaxed,
    }
    enriched_candidates = scored_ok

    # ─────────────────────────────────────────
    # Step 7c: 주도섹터 거래대금 톱10 — 빈집 필터와 별개로 외인+기관 동행 매수 주도주
    # 매수 후보(빈집 전략)에는 institutionNet5d<0 필터로 빠지지만, 삼전·하닉처럼
    # 외인·기관이 폭풍 매수 중인 거래대금 1위급 주도주를 별도 섹션으로 노출.
    # ─────────────────────────────────────────
    leading_value_top: list[dict] = []
    if not vacancy_df.empty and leading_sectors:
        import math
        pool = vacancy_df[vacancy_df["sector"].isin(leading_sectors)]
        pool = pool[pool["tradingValue5dAvg"].notna()]
        pool = pool.sort_values("tradingValue5dAvg", ascending=False).head(10)
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
    for c in pre_filter_candidates:
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
    # Step 9b: 유니버스 전 종목 검색용 metadata (이름↔코드 매핑)
    #   검색바에서 universe 600 어떤 종목이든 자동완성 가능하게 한다.
    #   chart history 는 무거워 매수 후보 40 종목만 — 나머지는 chart API 가
    #   on-demand 로 캔들차트 fetch.
    # ─────────────────────────────────────────
    print(f"\n[Step 9b] 유니버스 metadata (검색용)")
    try:
        universe_metadata = build_universe_metadata(universe)
        print(f"   metadata: {len(universe_metadata)}개")
    except Exception as e:
        print(f"  [!] universe metadata 실패: {e}")
        universe_metadata = []

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
        "candidateFilterStats": candidate_filter_stats,
        "leadingValueTop": leading_value_top,
        "sectorFlows": sector_flows,
        "sectorMovers": sector_movers,
        "newHighs": new_highs,
        "tradingIntensity": ti_results,
        "exitSignals": exit_signals[:15],
        "universeMetadata": universe_metadata,
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
    # 로컬 실행 시 .env 로드 (KRX_ID/KRX_PW → F&G 실데이터). Lambda 는 환경변수 직접 주입.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    except ImportError:
        pass
    # KOSDAQ 시총 200~300위에 텔레그램·외부 분석에서 자주 다루는 종목 (오이솔루션,
    # 인텍플러스, 네패스아크, 알멕, 브이엠, 한선엔지니어링, 와이지원, 아스플로,
    # 세미파이브, 싸이맥스, 나노, 세아메카닉스 등) 이 있어 300까지 확대.
    payload = build_flow_dashboard(top_n_kospi=300, top_n_kosdaq=300)

    # AI 데이터 브리핑 — 로컬 실행 시 직전 결과는 기존 로컬 JSON 사용
    try:
        try:
            from briefing.generator import attach_briefing
        except ModuleNotFoundError:
            from ..briefing.generator import attach_briefing
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prev_path = os.path.join(os.path.dirname(backend_dir), "frontend", "flow_dashboard.json")
        previous = None
        if os.path.exists(prev_path):
            with open(prev_path, encoding="utf-8") as f:
                previous = json.load(f)
        attach_briefing(payload, previous_payload=previous)
    except Exception as e:
        print(f"  [!] briefing 스킵: {e}")

    save_flow_dashboard(payload)
