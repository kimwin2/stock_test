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
DEFAULT_BRIEFING_MODEL = "gemini-3.5-flash-lite"

DISCLAIMER = (
    "본 브리핑은 공개 데이터(자체 산출 시그널·금감원 DART 공시)의 요약이며, "
    "특정 종목의 매수·매도 추천이나 투자 조언이 아닙니다. 투자 판단과 책임은 이용자 본인에게 있습니다."
)

BRIEFING_SYSTEM_PROMPT = """당신은 한국 주식시장 데이터 브리핑 작성자입니다.
제공되는 JSON 은 자체 계산된 수급 시그널(공포·탐욕 지수, 주도섹터, 외인/기관 수급, 수급 빈집 오실레이터)과
금감원 DART 공시 이벤트입니다. 이 데이터만 근거로 아침 브리핑을 한국어로 작성하세요.

절대 규칙 (위반 금지):
1. 예측 금지 — "오를 것", "상승이 예상", "유망", "주목해야" 같은 전망·권유 표현 금지.
2. 조언 금지 — "매수 추천", "비중 확대 권장" 등 투자 판단을 대신하는 문장 금지.
3. 제공된 JSON 에 없는 사실을 만들어내지 마세요. 수치는 JSON 그대로 인용하세요.
4. 일어난 변화를 담백하게 서술하세요. (예: "외국인은 5일 연속 반도체를 순매수했다",
   "공포·탐욕 지수는 62에서 71로 올라 과열 구간에 진입했다")
5. 공시는 사실만 요약 (예: "OO는 유상증자 결정을 공시했다"). 호재/악재 단정 금지.

작성 원칙 (이게 품질을 가른다):
6. **지표를 나열하지 말고 연결하세요.** 읽는 사람은 숫자를 이미 화면에서 봅니다.
   글의 값어치는 "여러 지표가 지금 무엇을 함께 말하고 있는가" 에 있습니다.
   나쁜 예: "공포·탐욕 지수는 41.6이다. 주도섹터는 A, B다."  ← 화면 복창
   좋은 예: "쏠림은 6개월 최저 구간인데 5일째 올라오고 있고, 그 사이 주도섹터에서
            반도체가 빠지고 소비재가 들어왔다 — 주도주가 교체되는 구간이다."
7. **오늘의 핵심은 '변화' 입니다.** 수준(level)보다 방향(전환·진입·이탈)을 앞세우세요.
   변화가 없으면 "변화 없이 이어지고 있다" 고 그대로 쓰세요. 억지로 만들지 마세요.
8. marketDifficulty 는 이 브리핑의 뼈대입니다. band 와 direction 을 반드시 해석에
   포함하세요. 정의: 쏠림이 높거나 올라가면 일부 업종만 살아남아 종목 고르기가
   어려운 장이고, 낮거나 내려가면 업종 간 편차가 좁아 순환매가 도는 장입니다.
   (이건 예측이 아니라 지표의 정의입니다. 그대로 서술하세요.)
9. 화면에 이미 있는 숫자를 반복하기보다, 지표 사이의 관계를 말하세요.
   headline 은 오늘 시장의 상태를 한 문장으로 규정하는 문장이어야 합니다.
   "지수 41.6 기록" 같은 수치 복창은 headline 으로 실패입니다.

출력은 JSON 만:
{
  "headline": "오늘 시장 상태를 규정하는 한 줄 (45자 이내, 수치 나열 금지)",
  "sections": [
    {"title": "오늘 장", "body": "장 난이도(쏠림 band/direction) + 공포·탐욕 방향을 엮어 지금이 어떤 국면인지 (2~3문장)"},
    {"title": "무엇이 바뀌었나", "body": "주도섹터 진입/이탈, 빈집 존 전환 등 직전 대비 변화. 없으면 없다고 (2~4문장)"},
    {"title": "돈의 방향", "body": "외인·기관 섹터 수급이 주도섹터와 맞는지 어긋나는지 (2~3문장)"},
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


def _crowding_state(crowding: dict | None) -> dict:
    """업종 쏠림 → 장 난이도 (백분위 + 방향).

    절대 임계값을 쓰지 않는 이유: 우리 지수와 참고 자료의 지수는 스케일이
    다르다(우리 13~80, 저쪽 -0.3~0.3). 숫자를 옮겨 적으면 틀린다. 백분위는
    스케일이 달라도 옮겨진다 — 참고 자료도 "0.3 넘으면 90% 구간" 이라고
    백분위로 말한다. 프론트(briefing.js)와 정의를 반드시 일치시킬 것.

    방향 정의(참고 자료 40일치에서 반복 확인):
      쏠림 상승 = 일부 업종만 생존 = 매매 어려움
      쏠림 하락 = 업종 간 편차 축소 = 매매 편함(순환매)
    """
    hist = [h.get("crowding") for h in ((crowding or {}).get("history") or [])
            if h and h.get("crowding") is not None]
    if len(hist) < 20:
        return {"available": False, "signal": (crowding or {}).get("signal")}

    last = hist[-1]
    ranked = sorted(hist)
    below = sum(1 for x in ranked if x <= last)
    pct = round(below / len(ranked) * 100)
    if pct >= 90:
        band = "극단 쏠림"
    elif pct >= 70:
        band = "어려운 장"
    elif pct >= 30:
        band = "보통"
    else:
        band = "편한 장"

    # 방향 창은 3거래일. 참고 자료는 전환을 하루이틀 만에 읽는다
    # ("업종쏠림지수 당일 반등 = 일부만 생존 = 어려운 시장 시작").
    # 5거래일로 잡으면 되돌아선 것을 놓친다 — 실측(2026-08-10): 13.5→16.18 로
    # 이틀 만에 튀어 올랐는데 5일 창은 여전히 '내려가는 중' 이라고 답했다.
    back = hist[max(0, len(hist) - 4)]
    span = (max(hist) - min(hist)) or 1
    delta = last - back
    if delta > span * 0.02:
        direction = "올라가는 중"
    elif delta < -span * 0.02:
        direction = "내려가는 중"
    else:
        direction = "옆걸음"

    return {
        "available": True,
        "value": round(last, 2),
        "percentile": pct,
        "topPct": 100 - pct,
        "band": band,
        "direction": direction,
        "delta3d": round(delta, 2),
    }


def build_signal_facts(payload: dict, previous: dict | None) -> dict:
    """현재/직전 payload → 브리핑 근거 사실(JSON 직렬화 가능) 정리."""
    prev = previous or {}

    fg_now = _fg(payload.get("marketSentiment"), "kospi")
    fg_prev = _fg(prev.get("marketSentiment"), "kospi")
    fg_kosdaq = _fg(payload.get("marketSentiment"), "kosdaq")

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
            "score": c.get("flowScore"),
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
    crowd_state = _crowding_state(crowding)

    return {
        "date": datetime.now(KST).strftime("%Y-%m-%d"),
        "fearGreed": {
            "kospi": fg_now,
            "kospiPrev": fg_prev,
            "kospiDelta": round(fg_now - fg_prev, 1) if (fg_now is not None and fg_prev is not None) else None,
            "kosdaq": fg_kosdaq,
        },
        # 장 난이도 — 이 제품이 '오늘' 탭에서 답하는 질문. 절대값이 아니라
        # 자기 이력 대비 백분위와 방향으로 준다 (프론트 판정과 같은 정의).
        "marketDifficulty": crowd_state,
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
    diff = facts.get("marketDifficulty") or {}
    sectors = facts.get("leadingSectors") or {}
    flows = facts.get("sectorFlows5d") or {}

    # 시장 온도
    parts = []
    if fg.get("kospi") is not None:
        if fg.get("kospiDelta") is not None and abs(fg["kospiDelta"]) >= 0.05:
            direction = "올라" if fg["kospiDelta"] > 0 else "내려"
            parts.append(f"코스피 공포·탐욕 지수는 {fg['kospiPrev']}에서 {fg['kospi']}로 {direction} 있습니다.")
        else:
            parts.append(f"코스피 공포·탐욕 지수는 {fg['kospi']} 입니다.")
    if fg.get("kosdaq") is not None:
        parts.append(f"KOSDAQ 은 {fg['kosdaq']} 입니다.")
    if diff.get("band"):
        parts.append(
            f"업종 쏠림은 최근 6개월 분포에서 하위 {diff.get('percentile')}% 자리로 "
            f"'{diff['band']}' 구간이며 {diff.get('direction') or '옆걸음'} 입니다."
        )
    temp_body = " ".join(parts) or "시장 심리 데이터가 없습니다."

    # 무엇이 바뀌었나 — 이 탭의 핵심. 수준이 아니라 변화를 앞세운다.
    parts = []
    now_sectors = sectors.get("now") or []
    if sectors.get("added"):
        parts.append(f"주도 업종에 {', '.join(sectors['added'])} 이(가) 새로 들어왔습니다.")
    if sectors.get("removed"):
        parts.append(f"{', '.join(sectors['removed'])} 은(는) 주도 업종에서 빠졌습니다.")
    for t in (facts.get("vacancyZoneTransitions") or [])[:3]:
        if t.get("name"):
            parts.append(f"{t['name']} 은(는) '{t['from']}' 에서 '{t['to']}' 존으로 옮겨갔습니다.")
    if not parts:
        # 변화가 없으면 없다고 쓴다. 억지로 만들면 매일 같은 글이 된다.
        parts.append("직전 갱신 대비 주도 업종과 빈집 존에 바뀐 것은 없습니다.")
        if now_sectors:
            parts.append(f"주도 업종은 {', '.join(now_sectors[:5])} 그대로입니다.")
    ec = facts.get("exitSignalCount")
    if ec:
        parts.append(f"신고가 후 음전+10일선 이탈 매도 시그널은 {ec}건입니다.")
    flow_body = " ".join(parts)

    # 돈의 방향 — 외인·기관 수급이 주도 업종과 맞물리는지
    parts = []
    ftop = flows.get("foreignerTop") or []
    otop = flows.get("organTop") or []
    if ftop:
        parts.append(f"외국인은 최근 5일 {ftop[0]['sector']}({ftop[0]['amountEok']:,}억)을 가장 많이 사들였습니다.")
    if otop:
        parts.append(f"기관은 {otop[0]['sector']}({otop[0]['amountEok']:,}억)이 1위입니다.")
    lead_set = set(now_sectors)
    aligned = [e["sector"] for e in (ftop[:3] + otop[:3]) if e["sector"] in lead_set]
    if aligned:
        parts.append(f"이 가운데 {', '.join(dict.fromkeys(aligned))} 은(는) 주도 업종과 겹칩니다.")
    elif ftop or otop:
        parts.append("수급 상위 업종과 주도 업종이 겹치지 않습니다.")
    vac_body = " ".join(parts) or "수급 데이터가 없습니다."

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
        elif disclosures.get("reason") == "fetch_failed":
            parts.append("이번 회차에는 공시 데이터를 가져오지 못했습니다 (다음 갱신 때 재시도).")
        else:
            parts.append("공시 데이터 미연결 상태입니다 (DART API 키 설정 시 표시).")
    dart_body = " ".join(parts)

    # 헤드라인 — 수치 복창 대신 '지금 어떤 국면인가' 를 규정한다.
    # 이것도 LLM 프롬프트와 같은 우선순위를 따른다: 변화 > 난이도 > 수준.
    if sectors.get("added") and sectors.get("removed"):
        headline = f"주도 업종 교체 — {sectors['removed'][0]} 빠지고 {sectors['added'][0]} 진입"
    elif sectors.get("added"):
        headline = f"주도 업종에 {sectors['added'][0]} 새로 진입"
    elif diff.get("available") and diff.get("direction") != "옆걸음":
        headline = f"업종 쏠림 {diff['direction']} — {diff['band']} 구간"
    elif diff.get("available"):
        headline = f"{diff['band']} 구간에서 변화 없이 이어지는 중"
    elif now_sectors:
        headline = f"주도 업종 {now_sectors[0]} 중심의 수급 지속"
    else:
        headline = "오늘의 수급 데이터 브리핑"

    return headline, [
        {"title": "오늘 장", "body": temp_body},
        {"title": "무엇이 바뀌었나", "body": flow_body},
        {"title": "돈의 방향", "body": vac_body},
        {"title": "공시 체크", "body": dart_body},
    ]


# ─────────────────────────────────────────
# LLM 생성
# ─────────────────────────────────────────
def _generate_with_llm(
    facts: dict, disclosures: dict, model: str
) -> tuple[tuple[str, list[dict]] | None, str | None]:
    """Returns (result, error). error 는 payload 에 실어 CloudWatch 없이도 원인을 본다."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return (None, "GEMINI_API_KEY 미설정")
    try:
        from openai import OpenAI
    except ImportError:
        return (None, "openai 패키지 없음")

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
            return (None, f"LLM 응답에 headline/sections 없음 (raw {content[:120]!r})")
        return ((headline, sections), None)
    except Exception as e:
        print(f"  [!] 브리핑 LLM 생성 실패 — fallback 사용: {e}")
        return (None, f"{type(e).__name__}: {str(e)[:300]}")


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
    if use_llm:
        llm_result, llm_error = _generate_with_llm(facts, disclosures, model)
    else:
        llm_result, llm_error = None, "use_llm=False"

    if llm_result:
        headline, sections = llm_result
        source = "llm"
    else:
        headline, sections = _fallback_sections(facts, disclosures)
        source = "fallback"
        print(f"  [i] 브리핑 fallback 사용 — 사유: {llm_error}")

    return {
        "generatedAt": datetime.now(KST).isoformat(),
        "date": facts.get("date"),
        "source": source,
        "model": model if source == "llm" else None,
        # fallback 으로 떨어진 이유를 payload 에 남긴다 — CloudWatch 없이 원인 파악.
        "llmError": llm_error,
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
