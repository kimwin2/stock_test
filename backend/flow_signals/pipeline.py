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
from .etf_holdings import build_holdings_index, holdings_for
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


# 주도 "테마" 로 취급하지 않는 섹터.
# 이 프로그램의 전략은 "당일 가장 강한 테마 + 추세 생존 + 수급 빈집" 인데,
# 보험·은행은 금리·밸류업 재료로 장기 RS 는 올라와도 단타 테마처럼 움직이지 않는다.
# 실제로 KODEX 보험(rsNorm 76.5)이 주도섹터가 되면서 롯데손해보험·동양생명·
# DB손해보험·서울보증보험이 매수 후보 11개 중 4개를 차지했다 — 사용자가 처음부터
# 지적한 바로 그 종목들이다. RS 규칙상 '틀린' 건 아니지만 전략의 의도와 어긋나므로
# 주도 섹터 후보에서 제외한다. (증권은 거래대금 테마로 움직여 유지)
# 주도 '테마' 로 보지 않는 섹터. 수급 집계에는 포함하되 주도섹터 후보에서만 뺀다.
# 금리·밸류업으로 장기 RS 는 올라오지만 단타 테마로 움직이지 않는다.
# 지주는 업종이 아니라 지배구조 형태라 애초에 테마가 될 수 없다.
NON_THEME_SECTORS = {"보험", "은행", "지주"}


def _resolve_leading_sectors_from_etfs(
    leading_etfs: list[dict],
) -> tuple[list[str], dict[str, dict]]:
    """주도 ETF 라벨에서 우리 sector taxonomy 라벨 추출.

    Returns: (RS 강도순 섹터 리스트, 섹터별 출처 dict)
    출처는 "이 종목이 왜 뽑혔나"를 ETF → 섹터 → 종목으로 되짚기 위한 근거다.
    """
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
        # 코스닥 IT — 반도체 비중 큼. 상장 목록 표기는 'TIGER 코스닥150IT'(공백 없음)
        # 이라 공백 있는 키만 두면 매칭에 실패해 게임/IT 로 흘러간다.
        ("코스닥150 IT", "반도체"),
        ("코스닥150IT", "반도체"),
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
        # "AI전력..." 계열은 전력기기 ETF 다. 아래 ("AI", ...) 보다 반드시 위 —
        # 밑에 두면 'KODEX AI전력핵심설비' 가 AI/반도체팹리스로 둔갑한다.
        ("AI전력", "전력기기"),
        ("전력기기", "전력기기"),
        ("AI", "AI/반도체팹리스"),
        # 방산/조선/중공업
        ("방산", "방산"),
        ("조선", "조선"),
        ("중공업", "조선"),
        # 전력 인프라
        ("전력", "전력기기"),
        ("산업재", "전력기기"),
        ("원전", "원전"),
        ("원자력", "원전"),   # 'HANARO 원자력iSelect' 는 "원전" 키워드에 안 걸린다
        ("SMR", "원전"),
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
    sector_src: dict[str, str] = {}      # 섹터 → 그 섹터를 만든 ETF 이름
    for etf in leading_etfs:
        name = etf.get("name") or ""
        try:
            rs = float(etf.get("rsNorm") or 0)
        except (TypeError, ValueError):
            rs = 0.0
        # [중요] 첫 매칭에서 멈춘다. break 가 없으면 ETF 하나가 여러 섹터를 만든다.
        #   실제 사고: "TIGER 글로벌AI인프라" 가 "AI"→AI/반도체팹리스 와
        #   "인프라"→건설/인프라 에 동시 매칭되어, 건설/인프라 가 RS 78.9 짜리
        #   3위 주도섹터로 둔갑 → 포스코인터내셔널·대우건설·동원시스템즈가
        #   매수 후보 1·2·3위를 차지했다.
        # sector_map 은 specific → general 순으로 정렬돼 있으므로 첫 매칭이 정답이다.
        for kw, sector in sector_map:
            if kw in name:
                if sector != "기타" and rs > sector_rs.get(sector, float("-inf")):
                    sector_rs[sector] = rs
                    sector_src[sector] = name
                break

    # 방어적 섹터는 주도 테마에서 제외 — 아래 NON_THEME_SECTORS 주석 참고.
    ordered = [
        s for s, _ in sorted(sector_rs.items(), key=lambda kv: kv[1], reverse=True)
        if s not in NON_THEME_SECTORS
    ]
    # 근거 체인용 출처. "이 종목이 왜 뽑혔나" 를 ETF → 섹터 → 종목으로 되짚으려면
    # 어느 ETF 가 그 섹터를 주도로 만들었는지를 버리면 안 된다.
    sources = {
        s: {"via": "etf", "etf": sector_src.get(s), "rsNorm": round(sector_rs[s], 1)}
        for s in ordered
    }
    return ordered, sources


def build_flow_dashboard(
    top_n_kospi: int = 400,
    top_n_kosdaq: int = 200,
    # 5일 순매도 선필터를 걷어내면서 풀을 넓혔다. 선필터가 하던 일을 정식
    # 오실레이터(osc<0)가 대신하려면 그 오실레이터를 계산할 종목 수가 있어야 한다.
    # 종목당 60일 수급 시계열 fetch 라 비용이 선형으로 는다 — Lambda Timeout 600s
    # 대비 실측 247s(80종목) 였으므로 120 까지가 안전선이다. 더 늘리려면 실측 먼저.
    new_high_candidates_only: int = 120,
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

    leading_sectors_etf, sector_sources = _resolve_leading_sectors_from_etfs(leading.get("leading", []))
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
    FLOW_STRENGTH_MIN = 5.0      # 만분율. 섹터 시총의 0.05% 이상 순매수 (기관 기준)
    FLOW_AMOUNT_MIN = 300 * 1e8  # 300억. 정규화만 쓰면 초소형 섹터가 상위를 독식한다.
    # 주도 '섹터' 라면 폭이 있어야 한다. 종목 3개짜리 묶음이 움직인 건 섹터
    # 테마가 아니라 개별 종목 움직임이고, 시총이 작아 정규화 강도만 비정상적으로
    # 높게 나온다. 섹터 분류를 잘게 쪼갠 뒤 항공(3종목)이 강도 43.3 으로 1위에
    # 올라 진짜 주도섹터를 밀어내고 매수 후보를 39개→21개로 깎았다.
    # 종목 수가 이보다 적으면 섹터라 부를 수 없다 (개별 종목 움직임).
    #
    # [수정 이력 2026-08-11] 3 → 5. 폭 가중(아래)이 절벽 없이 강등해 주긴 하지만,
    # 정확히 3종목짜리 묶음이 계속 상위로 올라와 자리를 차지했다. 실측 그날
    # 항공(3종목)·해운(3종목)이 주도섹터 7자리 중 2개를 먹고 그날의 진짜
    # 주도였던 반도체장비를 상한 밖으로 밀어냈다. 5로 올리면 신재생(5)·
    # 통신장비(6)·원전(6) 같은 실제 소형 테마는 살고 저 둘만 걸러진다.
    FLOW_SECTOR_MIN_MEMBERS = 5
    # 폭 가중 — 정규화 강도는 시총이 작을수록 커지므로, 소수 종목 섹터가
    # 상위를 독식한다. 하드 컷오프로 자르면 경계값에서 절벽이 생겨
    # 정상 테마(신재생 5종목)까지 잘리거나, 반대로 통과시키면 대형 섹터
    # (반도체장비 39종목·2,492억)가 밀려난다. 둘 다 실측으로 겪었다.
    # 종목 수가 이 값에 이를 때까지 선형으로 가중해 절벽 없이 처리한다.
    FLOW_SECTOR_BREADTH_FULL = 10
    # [수정 이력 2026-08-11] 축별 상한 4 / 2 → 6 / 3.
    # 세 축을 번갈아 뽑도록 바꾼 뒤로는 총 개수를 MAX_LEADING_SECTORS 가 통제한다.
    # 축별 상한까지 빡빡하게 두면 전체 상한을 못 채우고 자리가 빈 채로 끝난다 —
    # 실측 그날 7자리 중 5개만 채워졌고, 그 바람에 두 축 모두에서 간발로 밀린
    # 바이오(기관 13.8·거래대금 5.0)가 탈락했다. 참고 채널이 그날 하루 종일
    # 주도업종으로 부른 제약바이오이고, 그들이 편입한 파마리서치가 여기 있다.
    FLOW_SECTOR_TOP_N = 6
    MAX_LEADING_SECTORS = 7

    # [주의] 절대금액만 보면 시총 큰 섹터가, 정규화 강도만 보면 시총 작은 섹터가
    # 독식한다. 두 조건을 모두 요구해 양쪽 편향을 막는다.
    # 금액 하한은 초소형 섹터의 미미한 유입이 큰 강도로 증폭되는 것을 막는 안전장치다
    # (예: 시총 1천억 섹터에 20억만 들어와도 강도 200이 나온다).
    # 참고로 정상 신호는 이 하한을 문제없이 통과한다 — 실측(2026-08-07):
    #   연료전지/수소 353억(시총 1.9조, 강도 181), 원전 400억(4.4조, 90),
    #   신재생 484억(6.7조, 72), 반도체장비 5,185억(75.7조, 68).
    # 즉 소형 테마 섹터가 상위에 오는 것 자체는 과대평가가 아니라 실제 수급 집중이다.
    #
    # [수정 이력 2026-08-11] 기존에는 외인/기관 중 **더 강한 쪽**을 대표값으로 썼다
    # (max). 그러면 한 주체만 사고 다른 주체는 파는 섹터가 1위로 올라온다.
    # 실측: 그날 수급축 1위가 `유통/음식료` 강도 36.0 이었는데 외국인 단독
    # 신호였고, 기관 쪽에는 상위에 흔적조차 없었다. 그 결과 후보 4자리를
    # 신세계·이마트·하림지주(내수 유통)가 채웠다 — 정작 그날 시장이 본 '소비재'는
    # 화장품·음식료 수출이었고, 저 셋은 참고 채널 어느 리스트에도 없었다.
    #
    # 해법은 '외인과 기관 둘 다' 를 요구하는 게 아니라 **기관을 기준으로 삼는**
    # 것이다. 교집합(양쪽 모두 순매수)을 하드 조건으로 걸어봤더니 실측에서
    # 통과 섹터가 3개로 줄고 그날의 진짜 1위(반도체장비)까지 탈락했다 —
    # 외국인이 삼성전자 하나를 사려고 나머지 전 섹터를 파는 날이 흔하기 때문에
    # 외국인 부호를 거부권으로 쓰면 축이 통째로 죽는다.
    #
    # 국내 섹터 로테이션을 실제로 만드는 주체는 기관이다. 참고 채널도 업종
    # 순위를 사모/투신/연금(= 전부 기관 내부 주체)의 시총대비·금액대비로 뽑고,
    # 외국인은 마지막에 평균에 섞는 보조 입력으로만 쓴다.
    # 그래서 순위는 기관 강도로 매기고, 외국인은 sources 에 병기만 한다.
    #
    # 실측(2026-08-11) — 기관 기준으로 바꾸자 순위가 이렇게 정리됐다:
    #   반도체장비 20.4 / 화장품·소비재 18.0 / 2차전지 17.6 / 건설·인프라 17.4
    #   / 화학 14.6 / 바이오 13.8 / 전력기기 9.9
    # 참고 채널의 그날 결론(반도체 소부장·소비재·바이오·데이터센터)과 거의 겹친다.
    # 반대로 외인 단독 신호였던 유통/음식료(외인 36.0·기관 -45.7)·항공·해운은
    # 전부 사라졌다 — 이들이 그날 우리 후보를 오염시킨 장본인이었다.
    #
    # 반드시 잘리지 않은 전체 표(bySector)를 쓴다 — 주체별 head(15) 리스트를
    # 쓰면 기관 상위 15 밖으로 밀린 섹터가 통째로 사라진다.
    by_sector = sector_flows.get("bySector") or {}

    flow_ranked: dict[str, float] = {}
    flow_debug: list[tuple[str, float, float, float]] = []
    for sector, e in by_sector.items():
        members = e.get("stockCount") or 0
        if members < FLOW_SECTOR_MIN_MEMBERS:
            continue
        o_str = e.get("organStrength")
        if o_str is None or o_str < FLOW_STRENGTH_MIN:
            continue
        # 금액 하한도 기관 기준으로 본다. 합계로 보면 외국인 매도가 기관 매수를
        # 상쇄해, 기관이 2,538억을 담은 반도체장비가 -1,921억으로 뒤집힌다.
        if (e.get("organAmount") or 0) < FLOW_AMOUNT_MIN:
            continue
        weighted = o_str * min(1.0, members / FLOW_SECTOR_BREADTH_FULL)
        flow_ranked[sector] = weighted
        flow_debug.append((sector, weighted, o_str, e.get("foreignerStrength") or 0.0))

    for sector, w, o_str, f_str in sorted(flow_debug, key=lambda x: -x[1])[:8]:
        print(f"       {sector:<14} 폭가중 {w:6.1f} (기관 {o_str:6.1f} / 외인 {f_str:6.1f})")

    leading_sectors_flow = [
        s for s, _ in sorted(flow_ranked.items(), key=lambda kv: kv[1], reverse=True)
        if s not in NON_THEME_SECTORS
    ][:FLOW_SECTOR_TOP_N]
    print(
        f"   수급 강도 기반 섹터(폭가중 상위 {FLOW_SECTOR_TOP_N}, 괄호는 폭가중 점수): "
        f"{[(s, round(flow_ranked[s], 1)) for s in leading_sectors_flow] or '(없음)'}"
    )

    # ── 거래대금 쏠림 기반 주도섹터 (세 번째 축) ────────────────────────────
    #
    # 숙련 트레이더가 주도업종을 고를 때 가장 먼저 보는 축이 '거래대금 쏠림'
    # 인데 우리에겐 그 축이 아예 없었다. 우리가 쓰던 둘은 이렇게 기운다:
    #   - ETF RS: 6개월 상대강도라 느리다. 오늘 돈이 몰린 곳을 못 잡는다.
    #   - 수급 강도: 5일 순매수 ÷ 시총이라 시총 작은 섹터로 기운다.
    # 그래서 거래대금이 압도적인 섹터가 주도에서 통째로 빠진다.
    # 실측(2026-08-10): 주도섹터 4개에 반도체가 없었다. 삼성전자·SK하이닉스가
    # 시장 거래대금을 지배하는 날에도 '유통/음식료'(시총 41조)가 1위였다.
    #
    # 지표 = 섹터 거래대금 점유율 × 증가율(5일평균 ÷ 20일평균).
    # 점유율만 보면 반도체가 상시 1위로 고정돼 신호가 죽고, 증가율만 보면
    # 거래대금이 원래 적던 소형 섹터가 튄다. 곱해야 "원래 큰데 지금 더
    # 몰리는" 섹터가 남는다.
    TURNOVER_TOP_N = 3   # 축별 상한 완화 근거는 FLOW_SECTOR_TOP_N 주석 참고
    TURNOVER_SHARE_MIN = 0.03   # 시장 거래대금의 3% 미만은 '쏠림' 이라 할 수 없다
    turnover_ranked: dict[str, float] = {}
    try:
        if not vacancy_df.empty and "tradingValue5dAvg" in vacancy_df.columns:
            tv = vacancy_df[vacancy_df["tradingValue5dAvg"].notna()].copy()
            total_tv = float(tv["tradingValue5dAvg"].sum()) or 1.0
            grouped = tv.groupby("sector").agg(
                value=("tradingValue5dAvg", "sum"),
                ratio=("tradingValueRatio", "median"),
                members=("tradingValue5dAvg", "size"),
            )
            for sector, row in grouped.iterrows():
                if sector in NON_THEME_SECTORS or sector == "기타":
                    continue
                if int(row["members"]) < FLOW_SECTOR_MIN_MEMBERS:
                    continue
                share = float(row["value"]) / total_tv
                if share < TURNOVER_SHARE_MIN:
                    continue
                # 증가율이 없으면 중립(1.0). 없는 값을 벌점으로 쓰면 안 된다.
                ratio = 1.0 if pd.isna(row["ratio"]) else float(row["ratio"])
                turnover_ranked[str(sector)] = share * 100 * ratio
        leading_sectors_turnover = [
            s for s, _ in sorted(turnover_ranked.items(), key=lambda kv: kv[1], reverse=True)
        ][:TURNOVER_TOP_N]
        # 채택분만 찍으면 "왜 저 섹터가 안 들어왔나" 를 다음 사람이 못 따진다.
        # 바로 아래 순위까지 남겨야 임계값 조정 근거가 생긴다.
        _tv_all = sorted(turnover_ranked.items(), key=lambda kv: kv[1], reverse=True)[:6]
        print(
            f"   거래대금 쏠림 기반 섹터(채택 상위 {TURNOVER_TOP_N}, 점유율%×증가율): "
            f"{[(s, round(turnover_ranked[s], 1)) for s in leading_sectors_turnover] or '(없음)'}"
        )
        print(f"       (참고 상위 6: {[(s, round(v, 1)) for s, v in _tv_all]})")
    except Exception as e:
        print(f"  [!] 거래대금 쏠림 계산 실패(건너뜀): {e}")
        leading_sectors_turnover = []

    # ── 세 축 병합 — 축을 이어붙이지 않고 **번갈아** 뽑는다 ──────────────────
    #
    # [수정 이력 2026-08-11] 기존에는 ETF → 수급 → 거래대금 순으로 이어붙인 뒤
    # 뒤에서 잘랐다. 그러면 상한에 걸릴 때 **항상 거래대금 축부터** 죽는다.
    # 실측 그날 ETF 축이 4개를 만들자 거래대금 축의 반도체장비(그날 시장이
    # 하루 종일 밀던 코스닥 소부장)가 상한 밖으로 밀려 통째로 사라졌다.
    # 축 순서는 신호의 세기와 아무 상관이 없는데 그게 우선순위가 되어 있었다.
    #
    # 각 축의 1순위 → 각 축의 2순위 → … 순으로 번갈아 채운다. 어느 축도
    # 굶지 않고, 상한에 걸려 잘리는 건 모든 축의 하위 순위가 된다.
    axes = [
        ("etf", list(leading_sectors_etf)),
        ("flow", list(leading_sectors_flow)),
        ("turnover", list(leading_sectors_turnover)),
    ]
    leading_sectors = []
    for depth in range(max((len(v) for _, v in axes), default=0)):
        for via, sectors in axes:
            if depth >= len(sectors):
                continue
            sector = sectors[depth]
            if sector in leading_sectors:
                continue
            leading_sectors.append(sector)
            if via == "flow":
                sector_sources[sector] = {
                    "via": "flow",
                    "strength": round(flow_ranked.get(sector, 0.0), 1),
                }
            elif via == "turnover":
                sector_sources[sector] = {
                    "via": "turnover",
                    "sharePct": round(turnover_ranked.get(sector, 0.0), 1),
                }
            # etf 축 출처는 _resolve_leading_sectors_from_etfs 가 이미 채웠다.

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
        # [수정 이력 2026-08-11] 여기에 `institutionNet5d < 0` (5일 순매도) 선필터가
        # 있었다. 이건 우리가 내세운 빈집 정의와 다른 조건이고, 정식 오실레이터를
        # 계산하기도 전에 후보를 잘라내고 있었다.
        #
        # 오실레이터는 MACD 히스토그램이다 — 순매수의 '부호'가 아니라 '감속'을 잰다.
        # 5일 순매수가 플러스여도 20일 평균 페이스보다 느려졌으면 osc 는 음수다.
        # 그게 히스토그램을 쓰는 이유 전부인데, 선필터가 그 경우를 통째로 지웠다.
        #
        # 실측(2026-08-11): 참고 채널이 "수급 빈집" 이라 지목한 5종목 중 3개
        # (파마리서치 +519억, 에이피알 +136억, 코스맥스 +141억)가 5일 순매수
        # 플러스였다. 그중 파마리서치는 vacancyScore 가 -6.3e-4 (감속)로 우리
        # 정렬 기준상 명백한 빈집인데도 이 한 줄에 걸려 사라졌고, 그날 그들이
        # 실제로 편입한 종목이었다.
        #
        # 이제 주도섹터 교집합만 걸고, 빈집 판정은 Step 7a 의 `osc < 0` 에 맡긴다.
        # 정렬 키인 vacancyScore(5일 강도 - 20일 환산 baseline) 자체가 이미 감속
        # 지표라 순매도 종목이 자연히 앞으로 온다 — 하드컷이 필요 없다.
        filtered = vacancy_df
        # 1) 주도 섹터 매칭
        if leading_sectors:
            filtered = filtered[filtered["sector"].isin(leading_sectors)]
        # 2) 가장 빈집 (vacancyScore 가장 작은 음수) 순 정렬 + head
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
        c["flowScore"] = s
        c["flowReasons"] = r

    enriched_candidates.sort(key=lambda c: c.get("flowScore", 0), reverse=True)

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
    scored_ok = [c for c in vacant_ok if c.get("flowScore", 0) >= MIN_CANDIDATE_SCORE]
    dropped_score = len(vacant_ok) - len(scored_ok)

    score_relaxed = False
    if len(scored_ok) < MIN_CANDIDATES and vacant_ok:
        # 점수 커트라인만 완화 (추세·빈집 조건은 유지)
        scored_ok = vacant_ok[:MIN_CANDIDATES]
        score_relaxed = True

    # 섹터 편중 방지 — 한 섹터가 후보 목록을 점유하면 사용자에겐 분산이 사라진다.
    # (실측: 증권 ETF 강세일 때 후보 15개 중 7개가 증권사)
    # 이미 점수순 정렬돼 있으므로 섹터별 상위 MAX_PER_SECTOR 개만 남긴다.
    #
    # [수정 이력 2026-08-11] 전 섹터 일괄 4개였다. 그런데 이 규칙이 그날 탈락의
    # 절대다수를 만들었다 — 78개 중 32개가 여기서 잘렸고(추세 5 / 빈집 17 /
    # 점수 1 전부 합친 것보다 많다), 잘린 32개 중 18개가 반도체장비였다.
    # 하필 그날 시장이 하루 종일 밀던 테마가 코스닥 반도체 소부장이었고,
    # 참고 채널 리스트와 겹친 종목 19개 중 12개가 이 규칙 하나로 사라졌다.
    #
    # "분산" 은 수단이지 목적이 아니다. 주도섹터를 뽑아놓고 그 1위 섹터를
    # 가장 세게 자르면 전략 자체가 뒤집힌다. 상위 주도섹터일수록 자리를
    # 더 준다 — 대신 하위 섹터는 그대로 4개로 묶어 편중은 계속 막는다.
    MAX_PER_SECTOR = 4
    MAX_PER_LEADING_SECTOR = 7   # 주도섹터 상위 3개에 적용
    LEADING_SLOT_RANK = 3

    def _sector_cap(sec: str) -> int:
        try:
            rank = leading_sectors.index(sec)
        except (ValueError, AttributeError):
            return MAX_PER_SECTOR
        return MAX_PER_LEADING_SECTOR if rank < LEADING_SLOT_RANK else MAX_PER_SECTOR

    per_sector: dict[str, int] = {}
    diversified: list[dict] = []
    overflow: list[dict] = []
    for c in scored_ok:
        sec = c.get("sector") or "기타"
        if per_sector.get(sec, 0) >= _sector_cap(sec):
            # 조건은 다 통과했는데 섹터 상한에만 걸린 종목이다. 버리면
            # "우리가 그 종목을 못 봤다" 로 오해된다. 따로 담아 화면에 남긴다.
            overflow.append({
                "code": c.get("code"), "name": c.get("name"), "sector": sec,
                "flowScore": c.get("flowScore"), "ret5d": c.get("ret5d"),
                "oscPercentile": c.get("oscPercentile"), "vacancyZone": c.get("vacancyZone"),
            })
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
        "maxPerLeadingSector": MAX_PER_LEADING_SECTOR,
        "scoreCutoffRelaxed": score_relaxed,
    }
    enriched_candidates = scored_ok

    # ─────────────────────────────────────────
    # Step 7b: 주도 ETF 실제 편입비중 — 포착 경로의 마지막 화살표를 근거로 바꾼다
    #
    # 지금까지 `주도 ETF › 섹터 › 종목` 의 마지막 연결은 우리 섹터 사전이
    # 대신 주장하고 있었다("같은 섹터니까 담겨 있을 것이다"). 이 전략의 원리가
    # "ETF 로 들어온 자금이 구성종목을 사 올린다" 인 이상, 그 ETF 가 이 종목을
    # 실제로 담고 있는지가 근거의 핵심이다. 담고 있지 않다면 같은 섹터라는
    # 사실만으로는 자금이 올 이유가 없다.
    #
    # 실패해도 파이프라인은 계속 간다 — 근거 '보강' 이지 후보 선정 조건이 아니다.
    # (선정 조건으로 삼으면 WISEfn 이 하루 죽을 때 후보가 통째로 비어버린다.)
    # ─────────────────────────────────────────
    print("\n[Step 7b] 테마 ETF 편입비중")
    etf_holdings_index: dict = {"byName": {}, "etfCount": 0, "asOf": None}
    try:
        _leading_codes = {e.get("code") for e in (leading.get("leading") or []) if e.get("code")}
        etf_holdings_index = build_holdings_index(
            leading.get("all") or [], leading_codes=_leading_codes)
        print(f"   PDF 확보 ETF {etf_holdings_index['etfCount']}개 · "
              f"기준일 {etf_holdings_index.get('asOf') or '-'}")
    except Exception as e:
        print(f"  [!] 실패: {e}")

    if etf_holdings_index.get("etfCount"):
        # 미편입(None)도 그대로 둔다. '어떤 테마 ETF 도 안 담은 종목' 이라는
        # 사실이 정보라서, 화면이 편입/미편입을 구분해 보여줄 수 있어야 한다.
        for c in enriched_candidates:
            c["etfHoldings"] = holdings_for(etf_holdings_index, c.get("name"))
        held = sum(1 for c in enriched_candidates if c.get("etfHoldings"))
        lead_held = sum(1 for c in enriched_candidates
                        if (c.get("etfHoldings") or {}).get("leadingCount"))
        print(f"   후보 {len(enriched_candidates)}개 중 테마 ETF 편입 {held}개 "
              f"(그중 주도 ETF 편입 {lead_held}개)")

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
        # 섹터별 '왜 주도인가' 출처 — ETF(RS) 인지 수급 강도인지. 근거 체인 렌더용.
        "leadingSectorSources": {s: sector_sources.get(s) for s in leading_sectors if sector_sources.get(s)},
        "supplyVacancy": vacancy_result,
        "buyCandidates": enriched_candidates[:30],
        "candidateFilterStats": candidate_filter_stats,
        "leadingValueTop": leading_value_top,
        "sectorFlows": sector_flows,
        "sectorMovers": sector_movers,
        "newHighs": new_highs,
        "tradingIntensity": ti_results,
        "exitSignals": exit_signals[:15],
        # 조건은 통과했으나 섹터 상한(4개)에만 걸린 종목. 숨기면 "못 봤다"로 오해된다.
        "overflowCandidates": overflow,
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
    # [주의] 여기서 buyCandidates 가 아니라 Step 6 의 leadingTop(= ETF 축 섹터만
    # 본 빈집 랭킹)을 찍고 있었다. 이름은 '매수후보' 인데 숫자는 전혀 다른 목록이라
    # 실행 로그만 보면 후보가 29개인 날에 4개로 보인다. 실제 후보 수를 찍는다.
    print(f"   매수후보(하드필터 통과): {len(enriched_candidates)}"
          f"  (섹터상한 초과 대기 {len(overflow)})")
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
