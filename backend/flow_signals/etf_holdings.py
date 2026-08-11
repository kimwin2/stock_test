"""테마 ETF 의 실제 편입비중 — 포착 경로의 빈 칸을 메운다.

화면의 근거 체인은 `주도 ETF(RS) › 섹터 › 종목` 인데, 마지막 화살표를 지금까지
**우리 섹터 사전이 대신 주장**하고 있었다. "그 ETF 가 강하고, 이 종목은 같은
섹터니까, 기관 자금이 이 종목에도 올 것이다" — 중간 고리가 추정이다.

이 전략의 원리 자체가 "ETF 로 들어온 자금이 리밸런싱 때 구성종목을 사 올린다"
이므로, **그 ETF 가 이 종목을 실제로 몇 % 담고 있는지**가 근거의 핵심이다.
담고 있지 않다면 같은 섹터라는 사실만으로는 자금이 올 이유가 없다.

**집계 범위는 테마 ETF 전체다.** 처음엔 '주도 ETF(RS≥70)' 로만 좁혔는데,
RS 70 을 넘는 ETF 는 하루에 0~3개뿐이라 후보 9개 중 편입 0개가 나왔다
(실측 2026-08-10). 사실이긴 하나 화면에 아무것도 못 띄우면 근거가 아니다.
대신 담은 ETF 가 **주도 ETF 인지**를 따로 표시해 강한 근거와 약한 근거를 나눈다.

다음 리밸런싱 D-N 은 넣지 않았다. 지수 정기변경일은 산출기관·지수마다 다르고
공개 일정이 일정하지 않아, 추정해서 날짜로 보여주면 없는 정밀도를 만든다.
"""

from __future__ import annotations

import time

from .data_sources import fetch_etf_pdf

# PDF 를 받을 ETF 상한. 테마 ETF 풀 자체가 40개 미만이라 사실상 전체를 받는다.
# 페이지당 ~55KB · 0.3초 → 전체 15초 안쪽. (전체 파이프라인 335초)
MAX_PDF_ETFS = 40

# 시장 전체 지수·팩터 ETF. RS 벤치마크로 풀에 들어있을 뿐 테마가 아니다.
# 편입비중 집계에서 빼지 않으면 "신세계 — KODEX 200 에 0.08% 편입" 같은 줄이
# 나오는데, 이건 테마 근거가 아니라 시가총액을 다시 말하는 것이다.
# (실측: 이걸 안 걸렀을 때 후보 9개 중 3개의 근거가 KODEX 200/코스닥150 뿐이었다.)
BROAD_MARKET_ETFS = {
    "069500",  # KODEX 200
    "229200",  # KODEX 코스닥150
    "278530",  # KODEX 200TR
    "279530",  # KODEX 고배당주 (팩터)
}


def build_holdings_index(
    etfs: list[dict],
    leading_codes: set[str] | None = None,
    sleep_sec: float = 0.1,
) -> dict:
    """테마 ETF PDF → 종목명 역인덱스.

    Args:
        etfs: {code, name, rsNorm} 목록 (RS 계산을 마친 테마 ETF 전체)
        leading_codes: 그중 주도(RS≥70)로 뽑힌 ETF 코드 — 근거 강도 구분용

    Returns:
        {
          "byName": {종목명: [{"etf", "code", "weight", "rsNorm", "leading"}, ...]},
          "etfCount": PDF 를 실제로 받아온 ETF 수,
          "asOf": 구성종목 기준일 (가장 최근),
        }
    """
    leading_codes = leading_codes or set()
    by_name: dict[str, list[dict]] = {}
    as_of: str | None = None
    fetched = 0

    for etf in etfs[:MAX_PDF_ETFS]:
        code = (etf.get("code") or "").strip()
        label = (etf.get("name") or code or "").strip()
        if not code or code in BROAD_MARKET_ETFS:
            continue
        pdf = fetch_etf_pdf(code)
        holdings = pdf.get("holdings") or []
        if not holdings:
            # 상장폐지·WISEfn 미제공 등. 근거 보강용이라 조용히 건너뛴다.
            continue
        fetched += 1
        if pdf.get("asOf") and (as_of is None or pdf["asOf"] > as_of):
            as_of = pdf["asOf"]
        rs = etf.get("rsNorm")
        for h in holdings:
            by_name.setdefault(h["name"], []).append({
                "etf": label,
                "code": code,
                "weight": round(h["weight"], 2),
                "rsNorm": rs,
                "leading": code in leading_codes,
            })
        if sleep_sec:
            time.sleep(sleep_sec)

    # 주도 ETF 를 먼저, 그다음 편입비중 큰 순. 화면은 상위 1~2개만 쓴다.
    for entries in by_name.values():
        entries.sort(key=lambda e: (e["leading"], e["weight"]), reverse=True)

    return {"byName": by_name, "etfCount": fetched, "asOf": as_of}


def holdings_for(index: dict, name: str) -> dict | None:
    """종목 1개의 테마 ETF 편입 요약. 담은 ETF 가 없으면 None.

    None 과 '0개' 를 구분한다. 어떤 테마 ETF 도 담고 있지 않다는 사실 자체가
    정보라서, 화면이 편입/미편입을 구분해 보여줄 수 있어야 한다.
    """
    entries = (index.get("byName") or {}).get((name or "").strip())
    if not entries:
        return None
    return {
        "count": len(entries),
        "totalWeight": round(sum(e["weight"] for e in entries), 2),
        "leadingCount": sum(1 for e in entries if e["leading"]),
        "top": entries[:2],
        "asOf": index.get("asOf"),
    }
