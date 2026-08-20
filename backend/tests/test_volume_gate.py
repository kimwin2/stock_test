"""거래대금 읽기·게이트 회귀 검증 (네트워크 스텁).

2026-08-20 실사고 재현: SK하이닉스 40조 자사주 중개 소식에 SK증권(2,810원)이
상한가를 갔고 테마 헤드라인도 그 종목을 가리키는데 카드에 안 잡혔다.
거래대금 크롤이 실패하자 `price × 100만` = 28.1억을 지어냈고, 유동성 게이트
30억에 걸려 매번 조용히 탈락했다.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import stock_data as sd
import theme_stocks as ts

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (기대 {want!r})"))
    if not ok:
        fails.append(name)


# ── 실제 네이버 시세 페이지의 셀 구조 ──────────────────────────────────
def sise_html(shares: str, value: str, extra_cells: str = "") -> str:
    return f"""<html><body>
<table class="no_info"><tbody>
<tr><td class="first"><span class="sptxt sp_txt1">전일</span>
      <em class="no_up"><span class="blind">2,165</span></em></td>
    <td><span class="sptxt sp_txt2">고가</span>
      <em class="no_up"><span class="blind">2,810</span></em></td>
    <td class="last"><span class="sptxt sp_txt3">거래량</span>
      <em class="no_up"><span class="blind">{shares}</span></em></td></tr>
<tr><td class="first"><span class="sptxt sp_txt4">시가</span>
      <em class="no_up"><span class="blind">2,300</span></em></td>
    <td><span class="sptxt sp_txt5">저가</span>
      <em class="no_dn"><span class="blind">2,250</span></em></td>
    <td class="last"><span class="sptxt sp_txt10">거래대금</span>
      <em class="no_up"><span class="blind">{value}</span></em>
      <span class="tah">백만</span></td></tr>
</tbody></table>
{extra_cells}
</body></html>"""


# 호가·일별시세 등 페이지 뒤쪽에 널려 있는 <td><span>숫자 셀.
# 예전 정규식(DOTALL)은 '거래대금' 을 찾은 뒤 문서 끝까지 훑어 이걸 물어왔다.
TRAILING = """<table class="type2"><tbody>
<tr><td><span class="tah p11">164,800</span></td></tr>
<tr><td><span class="tah p11">164,500</span></td></tr>
</tbody></table>"""


print("\n[1] 거래대금 셀을 정확히 읽는다 — SK증권 281억")
# 1,000만주 × 2,810원 = 281억 = 28,100백만원 (거래대금 셀과 거래량 셀이 서로를 설명한다)
check("SK증권", sd._volume_from_html(sise_html("10,000,000", "28,100"), 2810), 28_100_000_000)

print("\n[2] 라벨 뒤 좁은 창 밖으로 새지 않는다 — 신영증권 '1,648억' 사고")
# 실거래 7억(7,000주 × 164,200원 ≈ 1,149백만). 뒤쪽 호가 164,800 을 읽으면
# 1,648억이 되어 실제의 140배가 된다.
got = sd._volume_from_html(sise_html("7,000", "1,149", TRAILING), 164200)
check("신영증권", got, 1_149_000_000)

print("\n[3] 거래대금 셀이 깨져도 거래량×현재가로 대체한다 (지어내기 아님)")
broken = sise_html("1,000,000", "28,100").replace(
    '<span class="sptxt sp_txt10">거래대금</span>\n      <em class="no_up"><span class="blind">28,100</span></em>', "")
check("거래대금 셀 소실", sd._volume_from_html(broken, 2810), 2_810_000_000)

print("\n[4] 자릿수가 어긋난 거래대금은 버리고 거래량×현재가를 쓴다")
# 파서가 호가(164,800백만 = 1,648억)를 물어온 경우 — 실측 추정의 140배
check("자릿수 오독", sd._volume_from_html(sise_html("7,000", "164,800"), 164200), 1_149_400_000)

print("\n[5] 아무것도 못 읽으면 0 = '모른다'. 가격으로 지어내지 않는다")
check("빈 페이지", sd._volume_from_html("<html><body>없음</body></html>", 2810), 0)

print("\n[6] basic API 에 거래대금이 있으면 크롤 없이 쓴다")
check("accumulatedTradingValue",
      sd._volume_from_basic({"accumulatedTradingValue": "28,100,000,000"}, 2810), 28_100_000_000)
check("거래량만 있을 때",
      sd._volume_from_basic({"accumulatedTradingVolume": "1,000,000"}, 2810), 2_810_000_000)
check("둘 다 없을 때", sd._volume_from_basic({"closePrice": "2,810"}, 2810), 0)

print("\n[7] 게이트: 모르는 거래대금은 illiquid 가 아니라 noVolume")
sk = {"name": "SK증권", "price": 2810, "changeRate": 29.79, "volumeRaw": 0, "volumeUnknown": True}
check("noVolume", ts.passes_gate(dict(sk)), "noVolume")
check("회로 차단 시 통과", ts.passes_gate(dict(sk), allow_unknown_volume=True), None)
check("실제 저유동성은 illiquid",
      ts.passes_gate({"name": "잡주", "price": 2810, "changeRate": 5.0, "volumeRaw": 1_000_000_000}),
      "illiquid")

print("\n[8] 실측 시그널(개미승리) 거래대금으로 보정 → SK증권이 살아난다")
cand = ts.Candidate("SK증권")
cand.add(ts.SRC_ANTWINNER, "001510")
cand.measured_value = ts._parse_eok("281억")
check("_parse_eok('281억')", cand.measured_value, 2.81e10)
fixed = ts.reconcile_volume(dict(sk), cand)
check("보정된 거래대금", fixed["volumeRaw"], 2.81e10)
check("보정 후 게이트 통과", ts.passes_gate(fixed), None)

print("\n[9] 크롤이 실측과 자릿수로 어긋나면 실측을 믿는다 — 신영증권")
sy = ts.Candidate("신영증권")
sy.add(ts.SRC_ANTWINNER, "001720")
sy.measured_value = ts._parse_eok("7억")
bad = {"name": "신영증권", "price": 164200, "changeRate": 4.85, "volumeRaw": 164_800_000_000}
fixed = ts.reconcile_volume(bad, sy)
check("보정된 거래대금", fixed["volumeRaw"], 7e8)
check("보정 후 illiquid", ts.passes_gate(fixed), "illiquid")

print("\n[10] 정상 범위면 크롤 값을 그대로 둔다 (실측 스냅샷 시차 흡수)")
ss = ts.Candidate("삼성증권")
ss.add(ts.SRC_ANTWINNER, "016360")
ss.measured_value = ts._parse_eok("676억")
same = {"name": "삼성증권", "price": 93300, "changeRate": 9.76, "volumeRaw": 93_200_000_000}
check("보정 안 함", ts.reconcile_volume(same, ss) is same, True)

print("\n[11] 예전 동작 재현 — 이 값이었으면 SK증권은 탈락했다")
old_guess = 2810 * 1_000_000
check("가격 추정치", old_guess, 2_810_000_000)
check("예전 판정",
      ts.passes_gate({"name": "SK증권", "price": 2810, "changeRate": 29.79, "volumeRaw": old_guess}),
      "illiquid")
check("_parse_eok('1조 2000억')", ts._parse_eok("1조 2000억"), 1.2e12)

print("\n[12] 2026-08-20 '증권' 테마 전체 재생 — 실제 선정 코드에 그대로 흘린다")

# 그날 개미승리가 실측으로 들고 온 값 (S3 payload 원본)
ANT = {"thema": "증권", "companies": [
    {"stockname": "SK증권",    "stock_code": "001510", "current_price": "2,810",   "fluctuation": "29.79%", "volume": "281억"},
    {"stockname": "삼성증권",   "stock_code": "016360", "current_price": "93,300",  "fluctuation": "9.76%",  "volume": "676억"},
    {"stockname": "상상인증권", "stock_code": "001290", "current_price": "1,012",   "fluctuation": "8.93%",  "volume": "29억"},
    {"stockname": "한화투자증권","stock_code": "003530", "current_price": "4,670",   "fluctuation": "6.26%",  "volume": "60억"},
    {"stockname": "신영증권",   "stock_code": "001720", "current_price": "164,200", "fluctuation": "4.85%",  "volume": "7억"},
    {"stockname": "미래에셋증권","stock_code": "006800", "current_price": "36,250",  "fluctuation": "4.47%",  "volume": "297억"},
]}
THEME = {"themeName": "증권",
         "headline": "SK하이닉스 40조 자사주 중개 소식에…SK증권 '상한가'",
         "relatedStocks": ["SK증권", "삼성증권", "신영증권", "미래에셋증권", "한화투자증권"]}

# 그날 크롤이 실제로 돌려준 값. SK증권은 못 읽었고(0), 신영증권은 호가
# 164,800 을 백만원 단위 거래대금으로 오독해 실제의 235배가 됐다.
CRAWL = {"001510": 0, "016360": 93_200_000_000, "001290": 2_900_000_000,
         "003530": 6_000_000_000, "001720": 164_800_000_000, "006800": 29_700_000_000}
META = {c["stock_code"]: (c["stockname"], int(c["current_price"].replace(",", "")),
                          float(c["fluctuation"].rstrip("%"))) for c in ANT["companies"]}


def replay(legacy: bool):
    """legacy=True 면 수정 전 동작(가격으로 거래대금 지어내기)을 재현한다."""
    selected = []
    for cand in ts.build_candidates(THEME, {"antwinnerSignals": [ANT]}):
        if cand.code not in META:
            continue
        name, price, rate = META[cand.code]
        vol = CRAWL[cand.code]
        if legacy and vol == 0:
            vol = price * (200000 if price >= 100000 else 500000 if price >= 10000 else 1000000)
        detail = {"code": cand.code, "name": name, "price": price, "changeRate": rate,
                  "volumeRaw": vol, "volumeUnknown": vol <= 0}
        if not legacy:
            detail = ts.reconcile_volume(detail, cand)
        if ts.passes_gate(detail):
            continue
        selected.append((ts.score_candidate(cand, detail)[0], name))
    selected.sort(reverse=True)
    return [n for _, n in selected[:ts.MAX_STOCKS_PER_THEME]]


before, after = replay(legacy=True), replay(legacy=False)
print(f"     수정 전: {before}")
print(f"     수정 후: {after}")
check("수정 전에는 SK증권이 없었다", "SK증권" in before, False)
check("수정 후 SK증권이 잡힌다", "SK증권" in after, True)
check("SK증권이 대장주(1위)", after[0], "SK증권")
# 실거래 7억짜리가 '1,648억' 으로 카드에 올라 있던 것도 같이 사라진다
check("가짜 거래대금으로 올라온 종목 제거", "신영증권" in after, False)

print("\n" + ("=" * 52))
print("모두 통과" if not fails else f"실패 {len(fails)}건: {fails}")
sys.exit(1 if fails else 0)
