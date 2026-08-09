"""수급 빈집 (Supply Vacancy) — 핵심 지표.

원리 (참고 자료 엑셀/오실레이터 시트 기준):
- "수급 오실레이터(MACD Histogram) 가 음수" 인 상태를 빈집이라 부른다.
- 오실레이터 정의:
    ratio   = (외인+기관 5일 누적 순매수) / 시가총액   ← 시기외(시총 표준화)
    EMA12   = ratio 의 12일 EMA (α = 2/13)
    EMA26   = ratio 의 26일 EMA (α = 2/27)
    MACD    = EMA12 - EMA26
    Signal  = EMA9 of MACD
    Osc     = MACD - Signal   (= MACD Histogram)
  Osc < 0 → 빈집.  주도섹터/추세추종/EPS 상향과 교집합으로 매수 후보.

본 모듈은 두 단계로 동작한다:
1. Universe 전체에 대한 1차 스크리닝 (compute_vacancy_score / collect_universe_vacancy):
   trend 데이터(10일치)만으로 계산 가능한 보조 지표를 제공.
   - flowStrength5d  = (외+기 5일 누적) / 시가총액   ← 시총 표준화 강도 (xlsm 시기외)
   - flowStrength20d = (외+기 20일 누적) / 시가총액
   - vacancyScore   = 시총 표준화된 모멘텀 (5일 강도 - 20일 환산 baseline) — universe 정렬용
   - currentVacancyDays / currentlyVacant — 일별 부호 기반 현재성

2. 후보 종목 enrich (enrich_with_chart_and_buyzone):
   60일 수급 시계열을 fetch 해 정식 수급 오실레이터(osc) 를 계산.
   oscLast 부호가 빈집 zone 판정의 1차 기준 (참고 자료 정의).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import numpy as np
import pandas as pd

from .data_sources import fetch_naver_investor_trend, parse_investor_trend, fetch_stock_ohlcv, fetch_naver_pc_frgn
from .buy_zones import compute_buy_zone


def compute_vacancy_score(trend: pd.DataFrame, market_cap: float | None = None) -> dict | None:
    """투자자 트렌드 (날짜 정렬) → 빈집 1차 지표.

    market_cap 이 주어지면 시총 표준화된 강도/모멘텀 을 계산해 universe 비교에 사용한다.
    빈집이 클수록 (음수일수록) "수급이 빠진" 상태.
    Returns: dict with vacancyScore, flowStrength5d/20d, foreignerNet5d, organNet5d, etc.
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

    # 시총 표준화 강도 (xlsm 시기외 컬럼과 동일 단위) — universe 비교의 핵심 키
    if market_cap and market_cap > 0:
        strength_5d = inst5 / float(market_cap)
        strength_20d = inst_n / float(market_cap)
        baseline_5d = inst_per_5d_baseline / float(market_cap)
        # 정규화된 모멘텀: 시총 대비 5일 강도가 20일 baseline 보다 얼마나 빠졌나
        vacancy = strength_5d - baseline_5d
    else:
        strength_5d = None
        strength_20d = None
        # market_cap 없을 때만 fallback: 절대 모멘텀 (이전 정의)
        vacancy = inst5 - inst_per_5d_baseline

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

    # ── 참고 자료 관점: "지금" 비어있는지 강조하기 위한 현재성 지표
    # currentVacancyDays  : 가장 최근부터 외인+기관 일별 net 이 음수인 연속 일수
    #                      (오늘부터 며칠째 매도 중)
    # last3DaysSellCount  : 최근 3거래일 중 매도(음수) 일수 (0~3)
    # currentlyVacant     : True if 어제·오늘 둘 다 net 음수 (현재 매도 우위 상태)
    daily_amounts = [d["instAmount"] for d in daily_flow]
    streak = 0
    for amt in reversed(daily_amounts):
        if amt < 0:
            streak += 1
        else:
            break
    last3 = daily_amounts[-3:] if len(daily_amounts) >= 3 else daily_amounts
    sell_count_3 = sum(1 for a in last3 if a < 0)
    currently_vacant = bool(len(daily_amounts) >= 2 and daily_amounts[-1] < 0 and daily_amounts[-2] < 0)

    return {
        # vacancyScore: market_cap 주어진 경우 시총표준화 모멘텀(차원 무차원), 아니면 원화 모멘텀
        "vacancyScore": round(vacancy, 8) if (market_cap and market_cap > 0) else round(vacancy, 0),
        "flowStrength5d": round(strength_5d, 8) if strength_5d is not None else None,
        "flowStrength20d": round(strength_20d, 8) if strength_20d is not None else None,
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
        "currentVacancyDays": int(streak),
        "last3DaysSellCount": int(sell_count_3),
        "currentlyVacant": currently_vacant,
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
        market_cap = float(row["marketCap"]) if row.get("marketCap") is not None else None
        try:
            raw = fetch_naver_investor_trend(code)
            trend = parse_investor_trend(raw)
            score = compute_vacancy_score(trend, market_cap=market_cap)
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
    """percentile → zone 라벨 (fallback — osc 데이터 없을 때만)."""
    if percentile < 25:
        return "빈집"
    if percentile > 75:
        return "찼음"
    return "정상"


def _zone_from_osc(osc_series: list[float]) -> tuple[str, float | None]:
    """수급 오실레이터(MACD Histogram) 의 마지막 값과 자기 종목 historical 분포로 zone 결정.

    참고 자료 정의:
      - osc < 0  →  "빈집"  (수급이 빠져나간 상태, 추세 안에서 눌림목 공략 후보)
      - osc 의 historical 상위 25% 초과 → "찼음" (xlsm 수급오실레이터 시트 점선 기준)
      - 그 외 → "정상"
    Returns: (zone_label, historical_percentile_of_last_osc)
    """
    if not osc_series:
        return ("정상", None)
    last = osc_series[-1]
    sorted_vals = sorted(osc_series)
    less_than = sum(1 for v in sorted_vals if v < last)
    pct = round(less_than / len(sorted_vals) * 100, 1)

    if last < 0:
        return ("빈집", pct)
    if pct > 75:
        return ("찼음", pct)
    return ("정상", pct)


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
        ma10_series = df["Close"].rolling(10).mean() if len(df) >= 10 else None
        ma20_series = df["Close"].rolling(20).mean() if len(df) >= 20 else None
        ma50_series = df["Close"].rolling(50).mean() if len(df) >= 50 else None
        ma10 = float(ma10_series.iloc[-1]) if ma10_series is not None else None
        ma20 = float(ma20_series.iloc[-1]) if ma20_series is not None else None
        ma50 = float(ma50_series.iloc[-1]) if ma50_series is not None else None

        # 추세 살아있음 판정용 — MA10 기울기(우상향) + 정배열(MA10>MA20>MA50)
        ma10_rising = (
            ma10_series is not None and len(ma10_series.dropna()) >= 6
            and float(ma10_series.iloc[-1]) > float(ma10_series.iloc[-6])
        )
        aligned_ma = (
            ma10 is not None and ma20 is not None and ma50 is not None
            and ma10 > ma20 > ma50
        )

        last_close = float(df["Close"].iloc[-1])
        last_high = float(df["High"].iloc[-1])
        max_50 = float(df["High"].tail(50).max())
        max_250 = float(df["High"].tail(min(250, len(df))).max())

        ret5d = None
        if len(df) > 6:
            ret5d = round((last_close / float(df["Close"].iloc[-6]) - 1) * 100, 2)
        ret20d = None
        if len(df) > 21:
            ret20d = round((last_close / float(df["Close"].iloc[-21]) - 1) * 100, 2)

        buy_zone = compute_buy_zone(df)

        # 수급 percentile (전 유니버스 vacancyScore 기준)
        vac_score = item.get("vacancyScore")
        if vac_score is not None and scores:
            percentile = _compute_percentile(float(vac_score), scores)
            zone = _vacancy_zone(percentile)
        else:
            percentile = None
            zone = None

        # 60일 시가총액 시계열 (close × 발행주식수 추정).
        # 발행주식수 = 현재 marketCap / 현재 close (유증·분할 무시 근사).
        market_cap_now = item.get("marketCap")
        cap_history_won: list[float] = []
        if market_cap_now and last_close > 0:
            shares = float(market_cap_now) / float(last_close)
            cap_history_won = [round(float(c) * shares, 0) for c in price_hist]

        # 수급 오실레이터 시계열 — 참고 자료 xlsm 과 동일한 로직:
        #   ratio   = (외인 + 기관) 5일누적 순매수 / 시가총액   ← xlsm '시기외'
        #   EMA12   = ratio 의 12일 EMA (α = 2/13)
        #   EMA26   = ratio 의 26일 EMA (α = 2/27)
        #   MACD    = EMA12 − EMA26
        #   Signal  = EMA9 of MACD (α = 2/10)
        #   Osc     = MACD − Signal   (= MACD Histogram)
        # 수급 시계열은 Naver PC frgn 페이지에서 받음 (페이지당 ~20 거래일).
        # pages=4 → ~80 거래일, 5일 rolling 후 ~76 포인트. EMA26(α=2/27) 의
        # 초기값 시딩 영향은 (25/27)^76 ≈ 0.3% 로 충분히 수렴한다.
        supply_osc_series: list[dict] = []
        long_flow: pd.DataFrame | None = None
        try:
            long_flow = fetch_naver_pc_frgn(code, pages=4, sleep=0.12)
        except Exception as e:
            print(f"  [!] {code} 60일 수급 fetch 실패: {e}")
            long_flow = None

        if long_flow is not None and not long_flow.empty and cap_history_won:
            cap_by_date = {d: c for d, c in zip(date_hist, cap_history_won)}
            # xlsm 의 '5일누적 외+기' = 직전 5거래일 합산. 첫 4일은 5일치가
            # 모이지 않아 의미 없으므로 min_periods=5 로 NaN 처리하고 제외.
            inst_5d = long_flow["institutional_amount"].rolling(5, min_periods=5).sum()
            ratio_series: list[tuple[str, float]] = []
            for (_, row), s5 in zip(long_flow.iterrows(), inst_5d):
                if pd.isna(s5):
                    continue
                d = row["date"].strftime("%Y-%m-%d")
                cap_d = cap_by_date.get(d) or market_cap_now
                if cap_d and cap_d > 0:
                    ratio_series.append((d, float(s5) / cap_d))
            if ratio_series:
                vals = [r for _, r in ratio_series]

                def _ema(values: list[float], alpha: float) -> list[float]:
                    out: list[float] = []
                    s: float | None = None
                    for v in values:
                        s = v if s is None else alpha * v + (1 - alpha) * s
                        out.append(s)
                    return out

                ema12 = _ema(vals, 2 / 13)
                ema26 = _ema(vals, 2 / 27)
                macd = [ema12[i] - ema26[i] for i in range(len(vals))]
                signal = _ema(macd, 2 / 10)
                osc = [macd[i] - signal[i] for i in range(len(vals))]
                for (d, r), o in zip(ratio_series, osc):
                    supply_osc_series.append({
                        "date": d,
                        "ratio": r,
                        "osc": o,
                    })

        # 수급 오실레이터 기반 zone 재정의 — 참고 자료 기준이 1차.
        # osc 시계열이 있으면 그 부호로 빈집/정상/찼음 결정 (universe percentile 무시).
        osc_last = None
        ratio_last = None
        osc_pct = None
        if supply_osc_series:
            osc_last = supply_osc_series[-1]["osc"]
            ratio_last = supply_osc_series[-1]["ratio"]
            zone_osc, osc_pct = _zone_from_osc([p["osc"] for p in supply_osc_series])
            zone = zone_osc

        enriched = {
            **item,
            "priceHistory60d": price_hist,
            "dateHistory60d": date_hist,
            "capHistory60d": cap_history_won,
            "supplyOscHistory": supply_osc_series,
            "oscLast": osc_last,
            "oscPercentile": osc_pct,
            "ratioLast": ratio_last,
            "ma10": round(ma10, 0) if ma10 is not None else None,
            "ma20": round(ma20, 0) if ma20 is not None else None,
            "ma50": round(ma50, 0) if ma50 is not None else None,
            "newHigh50d": bool(last_high >= max_50 * 0.999),
            "newHigh250d": bool(last_high >= max_250 * 0.999),
            "ret5d": ret5d,
            "ret20d": ret20d,
            "max250d": round(max_250, 0),
            "buyZone": buy_zone,
            "aboveMA10": bool(ma10 is not None and last_close >= ma10),
            "aboveMA20": bool(ma20 is not None and last_close >= ma20),
            "aboveMA50": bool(ma50 is not None and last_close >= ma50),
            "ma10Rising": bool(ma10_rising),
            "alignedMA": bool(aligned_ma),
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


# ─────────────────────────────────────────────────────────────
# Universe chart bundle — 검색용 lightweight chart.
#   목적: 종목 검색에서 universe 600 전 종목을 다룰 수 있도록.
#   포함: priceHistory60d, capHistory60d, dateHistory60d, ma10, ret5d
#   생략: supplyOscHistory, vacancyPercentile (osc 데이터는 무거워 후보 40개만)
# ─────────────────────────────────────────────────────────────
def _build_one_lightweight_chart(
    code: str,
    name: str,
    sector: str,
    market: str,
    market_cap: float | None,
) -> dict | None:
    try:
        df = fetch_stock_ohlcv(code, days=300)
    except Exception:
        return None
    if df.empty or len(df) < 10:
        return None

    recent = df.tail(60).copy()
    price_hist = [round(float(v), 0) for v in recent["Close"]]
    date_hist = [d.strftime("%Y-%m-%d") for d in recent.index]

    last_close = float(df["Close"].iloc[-1])
    ma10 = float(df["Close"].rolling(10).mean().iloc[-1]) if len(df) >= 10 else None

    ret5d = None
    if len(df) > 6:
        ret5d = round((last_close / float(df["Close"].iloc[-6]) - 1) * 100, 2)

    cap_history_won: list[float] = []
    if market_cap and last_close > 0:
        shares = float(market_cap) / float(last_close)
        cap_history_won = [round(float(c) * shares, 0) for c in price_hist]

    return {
        "code": code,
        "name": name,
        "sector": sector,
        "market": market,
        "marketCap": int(market_cap) if market_cap else None,
        "close": round(last_close, 0),
        "priceHistory60d": price_hist,
        "capHistory60d": cap_history_won,
        "dateHistory60d": date_hist,
        "ma10": round(ma10, 0) if ma10 is not None else None,
        "ret5d": ret5d,
    }


def build_universe_metadata(universe: pd.DataFrame) -> list[dict]:
    """유니버스 전 종목의 검색용 metadata (code, name, sector, market, marketCap).

    차트 데이터 없이 종목명 ↔ 종목코드 매핑만 필요할 때 사용 (검색바 자동완성).
    FDR 호출 없음 — 즉시 반환.
    """
    out: list[dict] = []
    for _, row in universe.iterrows():
        code = row.get("code")
        if not code:
            continue
        market_cap = row.get("marketCap")
        out.append({
            "code": code,
            "name": row.get("name", ""),
            "sector": row.get("sector", ""),
            "market": row.get("market", ""),
            "marketCap": int(market_cap) if market_cap is not None else None,
        })
    return out


def build_universe_chart_bundle(
    universe: pd.DataFrame,
    exclude_codes: Iterable[str] | None = None,
    max_workers: int = 12,
    progress_every: int = 100,
) -> list[dict]:
    """유니버스 전 종목의 lightweight chart bundle (FDR 만 사용, osc 없음).

    이미 full enrichment 가 적용된 코드는 exclude_codes 로 제외해 중복 페이로드 방지.
    """
    exclude = set(exclude_codes or [])
    tasks: list[tuple] = []
    for _, row in universe.iterrows():
        code = row["code"]
        if code in exclude:
            continue
        market_cap = float(row["marketCap"]) if row.get("marketCap") is not None else None
        tasks.append(
            (code, row.get("name", ""), row.get("sector", ""), row.get("market", ""), market_cap)
        )

    bundles: list[dict] = []
    total = len(tasks)
    completed = 0
    if total == 0:
        return bundles

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_build_one_lightweight_chart, *t): t[0] for t in tasks}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                bundles.append(res)
            completed += 1
            if progress_every and completed % progress_every == 0:
                print(f"  [.] chart bundle {completed}/{total}")

    return bundles
