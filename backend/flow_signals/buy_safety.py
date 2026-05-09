"""매수 안전성 5단계 판단 — 참고 자료 4월 25일 영상 로직 기반.

핵심 인용 (etc_source/tr_father_20260430/exel_logic.txt):
- "오실레이터가 과열권이라서 일단은 10% 가까이 현금을 챙긴다. 이 구간에서는 신용 쓰지 말라"
- "오실레이터가 떨어졌어요. 그럼에도 불구하고 코스피가 10일평선이나 5일평선을 깨지 않는 경우
   현금 비중은 10% 미만으로 유지하면서 시장에 순응한다"
- "오실레이터가 초기에 떨어질 때는 버티다가 한 1주 지나고 나서는 후두둑 떨어지기 시작"
- "시장이 삐걱거린다고 생각하면 강제로 현금 비중을 30% 만든다"
- "바닥에서 턴할 때 그때 주도 종목군이 또 바뀌기도 한다"

5단계 + 점수 (0~100):
  매수위험   ( 0~19) — peak/turn-down 직후, 현금 30%+, 신용 금지
  매수조심   (20~39) — osc 떨어지는 중 + 가격 균열, 현금 20-30%
  중간       (40~59) — osc 0 근처 + 가격 버팀, 현금 10-20%
  매수가능   (60~79) — osc 양수 추세 + 가격 5/10일선 위, 현금 5-10%
  매수권장   (80~100) — osc 바닥 turn up + 가격 회복, 현금 0-5%
"""

from __future__ import annotations

import pandas as pd

from .data_sources import fetch_index_ohlcv


STAGES = [
    {"key": "danger",  "label": "매수위험", "emoji": "🔴", "min": 0,  "max": 19,  "cash": "30%+",   "credit": "절대 금지"},
    {"key": "caution", "label": "매수조심", "emoji": "🟠", "min": 20, "max": 39,  "cash": "20-30%", "credit": "금지"},
    {"key": "neutral", "label": "중간",     "emoji": "🟡", "min": 40, "max": 59,  "cash": "10-20%", "credit": "자제"},
    {"key": "ok",      "label": "매수가능", "emoji": "🟢", "min": 60, "max": 79,  "cash": "5-10%",  "credit": "선별 사용"},
    {"key": "buy",     "label": "매수권장", "emoji": "🟦", "min": 80, "max": 100, "cash": "0-5%",   "credit": "사용 가능"},
]


def _stage_for(score: int) -> dict:
    for s in STAGES:
        if s["min"] <= score <= s["max"]:
            return s
    return STAGES[-1] if score > 100 else STAGES[0]


def evaluate_market_safety(symbol: str, label: str, sentiment: dict) -> dict:
    """KOSPI/KOSDAQ 한 시장의 매수 안전성 점수 + 단계 + 근거.

    Parameters
    ----------
    symbol : str  — 'KS11' or 'KQ11'
    label  : str  — 'KOSPI' or 'KOSDAQ'
    sentiment : dict  — build_index_sentiment() 결과 (history, oscillator 등 포함)
    """
    if "history" not in sentiment or not sentiment["history"]:
        return {"label": label, "error": "no sentiment history"}

    df = fetch_index_ohlcv(symbol, days=60)
    if df.empty or len(df) < 11:
        return {"label": label, "error": "insufficient OHLCV"}

    df["ma5"] = df["Close"].rolling(5).mean()
    df["ma10"] = df["Close"].rolling(10).mean()
    last = df.iloc[-1]
    price = float(last["Close"])
    ma5 = float(last["ma5"]) if pd.notna(last["ma5"]) else None
    ma10 = float(last["ma10"]) if pd.notna(last["ma10"]) else None
    above_ma5 = ma5 is not None and price > ma5
    above_ma10 = ma10 is not None and price > ma10

    hist = sentiment["history"]
    osc_series = [p["oscillator"] for p in hist[-20:] if p.get("oscillator") is not None]
    if len(osc_series) < 6:
        return {"label": label, "error": "insufficient oscillator history"}

    osc_now = float(sentiment.get("oscillator") or osc_series[-1])
    osc_5d_ago = osc_series[-6]
    osc_5d_chg = osc_now - osc_5d_ago
    osc_recent = osc_series[-15:] if len(osc_series) >= 15 else osc_series
    osc_peak = max(osc_recent)
    osc_peak_idx = len(osc_recent) - 1 - osc_recent[::-1].index(osc_peak)
    days_since_peak = len(osc_recent) - 1 - osc_peak_idx

    score = 50  # baseline
    rationale: list[dict] = []

    # 1) Oscillator 절대 위치
    if osc_now > 0.025:
        score -= 30
        rationale.append({"weight": -30, "msg": f"osc 과열권 ({osc_now:+.4f} > +0.025): 신용 금지, 현금 확보"})
    elif osc_now > 0.005:
        score += 15
        rationale.append({"weight": +15, "msg": f"osc 양수 추세권 ({osc_now:+.4f}): 시장 순응 구간"})
    elif osc_now > -0.005:
        score += 0
        rationale.append({"weight": 0, "msg": f"osc zero 근처 ({osc_now:+.4f}): 방향성 모호"})
    elif osc_now > -0.025:
        score -= 20
        rationale.append({"weight": -20, "msg": f"osc 음수권 ({osc_now:+.4f}): 떨어지는 중 위험"})
    else:
        score += 25
        rationale.append({"weight": +25, "msg": f"osc 공포 깊음 ({osc_now:+.4f} < -0.025): 역발상 매수 시점"})

    # 2) Oscillator 추세 (가장 중요한 신호)
    if osc_5d_chg > 0.003:
        score += 25
        rationale.append({"weight": +25, "msg": f"osc turn up ({osc_5d_chg:+.4f} / 5일): 바닥 → 회복 신호"})
    elif osc_5d_chg > 0:
        score += 10
        rationale.append({"weight": +10, "msg": f"osc 소폭 회복 ({osc_5d_chg:+.4f}): 약한 양호"})
    elif osc_5d_chg > -0.003:
        score -= 5
        rationale.append({"weight": -5, "msg": f"osc 정체 ({osc_5d_chg:+.4f}): 변화 작음"})
    else:
        score -= 25
        rationale.append({"weight": -25, "msg": f'osc turn down ({osc_5d_chg:+.4f}): "1주 후 후두둑" 위험'})

    # 3) 가격 vs 이평선 (5/10일선 깸 여부)
    if above_ma5 and above_ma10:
        score += 10
        rationale.append({"weight": +10, "msg": f"가격 5/10일선 위 ({price:.2f}): 추세 살아있음"})
    elif above_ma10:
        score += 0
        rationale.append({"weight": 0, "msg": f"가격 5일선 깸 / 10일선 위: 약한 균열"})
    else:
        score -= 15
        rationale.append({"weight": -15, "msg": f"가격 10일선 깸 ({price:.2f} < {ma10:.2f}): 후두둑 트리거"})

    # 4) Peak 후 위험 시간 (1주 이내)
    if osc_peak > 0.025 and 0 < days_since_peak < 7:
        score -= 10
        rationale.append({"weight": -10, "msg": f"peak ({osc_peak:+.3f}) 후 {days_since_peak}일 — 1주 후 후두둑 경계 구간"})

    score = max(0, min(100, score))
    stage = _stage_for(score)

    return {
        "label": label,
        "score": score,
        "stage": stage["key"],
        "stageLabel": stage["label"],
        "stageEmoji": stage["emoji"],
        "stageIndex": STAGES.index(stage),  # 0~4
        "totalStages": len(STAGES),
        "cashRecommend": stage["cash"],
        "creditRecommend": stage["credit"],
        "rationale": rationale,
        "metrics": {
            "price": round(price, 2),
            "ma5": round(ma5, 2) if ma5 else None,
            "ma10": round(ma10, 2) if ma10 else None,
            "oscillator": round(osc_now, 4),
            "osc5dChange": round(osc_5d_chg, 4),
            "oscPeakRecent": round(osc_peak, 4),
            "daysSincePeak": days_since_peak,
        },
    }


def build_buy_safety(market_sentiment: dict) -> dict:
    """KOSPI/KOSDAQ 매수 안전성 통합 결과.

    Parameters
    ----------
    market_sentiment : dict
        build_market_sentiment() 결과 (kospi/kosdaq 키 포함)
    """
    out: dict = {"stages": STAGES}
    for key, symbol in [("kospi", "KS11"), ("kosdaq", "KQ11")]:
        sent = market_sentiment.get(key)
        if not sent or "error" in sent:
            out[key] = {"error": sent.get("error", "missing") if sent else "missing"}
            continue
        try:
            out[key] = evaluate_market_safety(symbol, sent.get("label", key.upper()), sent)
        except Exception as e:
            out[key] = {"error": str(e)}
    return out
