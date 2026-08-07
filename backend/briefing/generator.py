"""AI 데이터 브리핑 생성.

입력: flow_dashboard payload (현재 + 직전 실행분) + DART 공시 이벤트
출력: payload["briefing"] — 서술형 시황 브리핑 (LLM 또는 규칙 기반 fallback)

규칙:
- 예측/조언/매수·매도 권유 문장 금지. 데이터 변화 사실만 서술.
- LLM 실패 시에도 브리핑은 항상 생성 (fallback).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .dart_source import collect_disclosure_events

KST = timezone(timedelta(hours=9))

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_BRIEFING_MODEL = "gemini-2.5-flash-lite"

DISCLAIMER = (
    "본 브리핑은 공개 데이터(자체 산출 시그널·금감원 DART 공시)의 요약이며, "
    "특정 종목의 매수·매도 추천이나 투자 조언이 아닙니다. 투자 판단과 책임은 이용자 본인에게 있습니다."
)

BRIEFING_SYSTEM_PROMPT = """당신은 한국 주식시장 데이터 브리핑 작성자입니다.
제공되는 JSON 은 자체 계산된 수급 시그널(Fear&Greed, 주도섹터, 외인/기관 수급, 수급 빈집 오실레이터)과
금감원 DART 공시 이벤트입니다. 이 데이터만 근거로 아침 브리핑을 한국어로 작성하세요.

절대 규칙 (위반 금지):
1. 예측 금지 — "오를 것", "상승이 예상", "유망", "주목해야" 같은 전망·권유 표현 금지.
2. 조언 금지 — "매수 추천", "비중 확대 권장" 등 투자 판단을 대신하는 문장 금지.
3. 제공된 JSON 에 없는 사실을 만들어내지 마세요. 수치는 JSON 그대로 인용하세요.
4. 일어난 변화를 담백하게 서술하세요. (예: "외국인은 5일 연속 반도체를 순매수했다",
   "Fear&Greed 는 62에서 71로 올라 과열 구간에 진입했다")
5. 공시는 사실만 요약 (예: "OO는 유상증자 결정을 공시했다"). 호재/악재 단정 금지.

출력은 JSON 만:
{
  "headline": "오늘 데이터의 핵심 한 줄 (40자 이내, 서술형)",
  "sections": [
    {"title": "시장 온도", "body": "F&G/현금비중 변화 서술 (2~3문장)"},
    {"title": "수급 흐름", "body": "주도섹터/외인·기관 섹터 흐름 변화 서술 (2~4문장)"},
    {"title": "빈집 시그널", "body": "빈집 후보/존 전환 서술 (2~3문장)"},
    {"title": "공시 체크", "body": "후보·유니버스 종목 주요 공시 서술 (2~4문장, 공시 없으면 '특이 공시 없음' 1문장)"}
  ]
}"""


def _fg(sentiment: dict | None, market: str) -> float | None:
    try:
        v = (sentiment or {}).get(market, {}).get("fearGreed")
        return round(float(v), 1) if v is not None else None
    except (TypeError, ValueError):
        return None


def _zone_map(candidates: list[dict] | None) -> dict[str, str]:
    out = {}
    for c in candidates or []:
        code = c.get("code")
        zone = c.get("vacancyZone")
        if code and zone:
            out[code] = zone
    return out


def build_signal_facts(payload: dict, previous: dict | None) -> dict:
    """현재/직전 payload → 브리핑 근거 사실(JSON 직렬화 가능) 정리."""
    prev = previous or {}

    fg_now = _fg(payload.get("marketSentiment"), "kospi")
    fg_prev = _fg(prev.get("marketSentiment"), "kospi")
    fg_kosdaq = _fg(payload.get("marketSentiment"), "kosdaq")

    cash_now = (payload.get("cashRecommendation") or {}).get("cashPct")
    cash_prev = (prev.get("cashRecommendation") or {}).get("cashPct")
    cash_level = (payload.get("cashRecommendation") or {}).get("level")

    sectors_now = payload.get("leadingSectorLabels") or []
    sectors_prev = prev.get("leadingSectorLabels") or []
    # 직전 데이터가 없으면 "전부 신규 진입" 오서술이 되므로 델타 생략
    if sectors_prev:
        sectors_added = [s for s in sectors_now if s not in sectors_prev]
        sectors_removed = [s for s in sectors_prev if s not in sectors_now]
    else:
        sectors_added = []
        sectors_removed = []

    flows = payload.get("sectorFlows") or {}
    top_foreign = [
        {"sector": e["sector"], "amountEok": round(e["amount"] / 1e8)}
        for e in (flows.get("foreigner") or [])[:5]
    ]
    top_organ = [
        {"sector": e["sector"], "amountEok": round(e["amount"] / 1e8)}
        for e in (flows.get("organ") or [])[:5]
    ]

    candidates = payload.get("buyCandidates") or []
    top_candidates = [
        {
            "name": c.get("name"),
            "sector": c.get("sector"),
            "score": c.get("taerinScore"),
            "vacancyZone": c.get("vacancyZone"),
            "newHigh250d": bool(c.get("newHigh250d")),
        }
        for c in candidates[:5]
    ]

    # 빈집 존 전환 — 직전 실행의 후보와 code 매칭
    zones_now = _zone_map(candidates)
    zones_prev = _zone_map(prev.get("buyCandidates"))
    transitions = []
    name_by_code = {c.get("code"): c.get("name") for c in candidates}
    for code, z_now in zones_now.items():
        z_prev = zones_prev.get(code)
        if z_prev and z_prev != z_now:
            transitions.append({"name": name_by_code.get(code), "from": z_prev, "to": z_now})

    crowding = payload.get("crowding") or {}

    return {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "fearGreed": {
            "kospi": fg_now,
            "kospiPrev": fg_prev,
            "kospiDelta": round(fg_now - fg_prev, 1) if (fg_now is not None and fg_prev is not None) else None,
            "kosdaq": fg_kosdaq,
        },
        "cashRecommendation": {"nowPct": cash_now, "prevPct": cash_prev, "level": cash_level},
        "crowdingSignal": crowding.get("signal"),
        "leadingSectors": {"now": sectors_now[:8], "added": sectors_added, "removed": sectors_removed},
        "sectorFlows5d": {"foreignerTop": top_foreign, "organTop": top_organ},
        "buyCandidatesTop": top_candidates,
        "vacancyZoneTransitions": transitions[:8],
        "exitSignalCount": len(payload.get("exitSignals") or []),
        "vacancyAnalyzed": payload.get("vacancyAnalyzed"),
    }


# ─────────────────────────────────────────
# 규칙 기반 fallback — LLM 없이도 항상 브리핑 생성
# ─────────────────────────────────────────
def _fallback_sections(facts: dict, disclosures: dict) -> tuple[str, list[dict]]:
    fg = facts.get("fearGreed") or {}
    cash = facts.get("cashRecommendation") or {}
    sectors = facts.get("leadingSectors") or {}
    flows = facts.get("sectorFlows5d") or {}

    # 시장 온도
    parts = []
    if fg.get("kospi") is not None:
        if fg.get("kospiDelta") is not None and abs(fg["kospiDelta"]) >= 0.05:
            direction = "올라" if fg["kospiDelta"] > 0 else "내려"
            parts.append(f"KOSPI Fear&Greed 는 {fg['kospiPrev']}에서 {fg['kospi']}로 {direction} 있습니다.")
        else:
            parts.append(f"KOSPI Fear&Greed 는 {fg['kospi']} 입니다.")
    if fg.get("kosdaq") is not None:
        parts.append(f"KOSDAQ 은 {fg['kosdaq']} 입니다.")
    if cash.get("nowPct") is not None:
        if cash.get("prevPct") is not None and cash["prevPct"] != cash["nowPct"]:
            parts.append(f"권고 현금 비중은 {cash['prevPct']}% → {cash['nowPct']}% 로 조정됐습니다 ({cash.get('level') or '-'}).")
        else:
            parts.append(f"권고 현금 비중은 {cash['nowPct']}% ({cash.get('level') or '-'}) 입니다.")
    temp_body = " ".join(parts) or "시장 심리 데이터가 없습니다."

    # 수급 흐름
    parts = []
    now_sectors = sectors.get("now") or []
    if now_sectors:
        parts.append(f"현재 주도 섹터는 {', '.join(now_sectors[:5])} 입니다.")
    if sectors.get("added"):
        parts.append(f"직전 대비 {', '.join(sectors['added'])} 이(가) 새로 진입했습니다.")
    if sectors.get("removed"):
        parts.append(f"{', '.join(sectors['removed'])} 은(는) 주도 섹터에서 빠졌습니다.")
    ftop = flows.get("foreignerTop") or []
    otop = flows.get("organTop") or []
    if ftop:
        parts.append(f"외국인 5일 순매수 1위 섹터는 {ftop[0]['sector']}({ftop[0]['amountEok']:,}억) 입니다.")
    if otop:
        parts.append(f"기관 1위는 {otop[0]['sector']}({otop[0]['amountEok']:,}억) 입니다.")
    flow_body = " ".join(parts) or "수급 흐름 데이터가 없습니다."

    # 빈집 시그널
    parts = []
    cands = facts.get("buyCandidatesTop") or []
    if cands:
        names = ", ".join(f"{c['name']}({c['sector']})" for c in cands[:3] if c.get("name"))
        parts.append(f"빈집 스크리닝 상위는 {names} 입니다.")
    for t in (facts.get("vacancyZoneTransitions") or [])[:3]:
        if t.get("name"):
            parts.append(f"{t['name']} 은(는) '{t['from']}' 에서 '{t['to']}' 존으로 전환됐습니다.")
    ec = facts.get("exitSignalCount")
    if ec:
        parts.append(f"신고가 후 음전+10일선 이탈 매도 시그널은 {ec}건입니다.")
    vac_body = " ".join(parts) or "빈집 시그널 데이터가 없습니다."

    # 공시 체크
    parts = []
    cand_events = disclosures.get("candidateEvents") or []
    uni_events = disclosures.get("universeEvents") or []
    for e in cand_events[:4]:
        parts.append(f"후보 종목 {e['name']} 은(는) '{e['category']}' 공시를 냈습니다.")
    if not cand_events and uni_events:
        for e in uni_events[:3]:
            parts.append(f"{e['name']} 이(가) '{e['category']}' 공시를 냈습니다.")
    if not parts:
        if disclosures.get("available"):
            parts.append("후보·유니버스 종목에 특이 공시가 없습니다.")
        else:
            parts.append("공시 데이터 미연결 상태입니다 (DART API 키 설정 시 표시).")
    dart_body = " ".join(parts)

    # 헤드라인
    if fg.get("kospiDelta") is not None and abs(fg["kospiDelta"]) >= 3:
        headline = f"F&G {fg['kospiPrev']}→{fg['kospi']}, 시장 온도 변화 확대"
    elif sectors.get("added"):
        headline = f"주도 섹터에 {sectors['added'][0]} 신규 진입"
    elif now_sectors:
        headline = f"주도 섹터 {now_sectors[0]} 중심의 수급 지속"
    else:
        headline = "오늘의 수급 데이터 브리핑"

    return headline, [
        {"title": "시장 온도", "body": temp_body},
        {"title": "수급 흐름", "body": flow_body},
        {"title": "빈집 시그널", "body": vac_body},
        {"title": "공시 체크", "body": dart_body},
    ]


# ─────────────────────────────────────────
# LLM 생성
# ─────────────────────────────────────────
def _generate_with_llm(facts: dict, disclosures: dict, model: str) -> tuple[str, list[dict]] | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    dart_slim = {
        "available": disclosures.get("available"),
        "candidateEvents": [
            {k: e[k] for k in ("name", "category", "title", "date")}
            for e in (disclosures.get("candidateEvents") or [])[:10]
        ],
        "universeEvents": [
            {k: e[k] for k in ("name", "category", "title", "date")}
            for e in (disclosures.get("universeEvents") or [])[:10]
        ],
    }
    user_prompt = (
        "다음 데이터로 브리핑을 작성하세요.\n\n"
        f"## 시그널 데이터\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        f"## DART 공시 이벤트\n{json.dumps(dart_slim, ensure_ascii=False)}"
    )

    try:
        client = OpenAI(api_key=api_key, base_url=GEMINI_OPENAI_BASE_URL)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=1500,
        )
        content = resp.choices[0].message.content or ""
        data = json.loads(content)
        headline = str(data.get("headline") or "").strip()
        sections = [
            {"title": str(s.get("title") or "").strip(), "body": str(s.get("body") or "").strip()}
            for s in (data.get("sections") or [])
            if s.get("body")
        ]
        if not headline or not sections:
            return None
        return headline, sections
    except Exception as e:
        print(f"  [!] 브리핑 LLM 생성 실패 — fallback 사용: {e}")
        return None


# ─────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────
def build_briefing(
    payload: dict,
    previous_payload: dict | None = None,
    dart_api_key: str | None = None,
    use_llm: bool = True,
) -> dict:
    """flow payload → briefing dict. 실패 요소가 있어도 항상 dict 반환."""
    facts = build_signal_facts(payload, previous_payload)

    universe_codes = {
        m.get("code") for m in (payload.get("universeMetadata") or []) if m.get("code")
    }
    candidate_codes = {
        c.get("code") for c in (payload.get("buyCandidates") or []) if c.get("code")
    }
    disclosures = collect_disclosure_events(
        universe_codes=universe_codes,
        candidate_codes=candidate_codes,
        api_key=dart_api_key,
    )
    if disclosures.get("available"):
        n = len(disclosures.get("candidateEvents") or []) + len(disclosures.get("universeEvents") or [])
        print(f"  [OK] DART 공시 이벤트 {n}건 (후보 {len(disclosures.get('candidateEvents') or [])}건)")
    else:
        print(f"  [i] DART 공시 스킵: {disclosures.get('error')}")

    model = (os.environ.get("BRIEFING_MODEL") or DEFAULT_BRIEFING_MODEL).strip()
    llm_result = _generate_with_llm(facts, disclosures, model) if use_llm else None
    if llm_result:
        headline, sections = llm_result
        source = "llm"
    else:
        headline, sections = _fallback_sections(facts, disclosures)
        source = "fallback"

    return {
        "generatedAt": datetime.now(KST).isoformat(),
        "date": facts.get("date"),
        "source": source,
        "model": model if source == "llm" else None,
        "headline": headline,
        "sections": sections,
        "signalFacts": facts,
        "disclosures": disclosures,
        "disclaimer": DISCLAIMER,
    }


def attach_briefing(
    payload: dict,
    previous_payload: dict | None = None,
    dart_api_key: str | None = None,
    use_llm: bool = True,
) -> dict:
    """payload 에 briefing 키를 부착. 브리핑 생성 실패가 파이프라인을 죽이지 않게 방어."""
    try:
        payload["briefing"] = build_briefing(
            payload,
            previous_payload=previous_payload,
            dart_api_key=dart_api_key,
            use_llm=use_llm,
        )
    except Exception as e:
        import traceback
        print(f"  [!] briefing 생성 실패 (파이프라인 계속): {e}")
        traceback.print_exc()
        payload["briefing"] = None
    return payload
