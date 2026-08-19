"""프리마켓 경로 + 게이트 수정 검증 (네트워크 스텁)."""
import os, sys, types
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import nxt_quotes, stock_data, theme_stocks as ts

KST = nxt_quotes.KST
PRE  = datetime(2026, 8, 19, 8, 32, tzinfo=KST)   # 프리마켓 창
OPEN = datetime(2026, 8, 19, 10, 0, tzinfo=KST)   # 정규장

# ── 시나리오별 가짜 시세 ────────────────────────────────────────────
UNIVERSE = {                       # code: (name, prevClose, 프리마켓 등락률)
    "047770": ("코데즈컴바인", 3510,  0.0),   # 프리마켓 체결 없음
    "030960": ("양지사",      4660,  0.0),
    "005930": ("삼성전자",  268500,  0.0),
    "042700": ("한미반도체", 95000,  0.0),
}
MOVERS = dict(UNIVERSE)
MOVERS["047770"] = ("코데즈컴바인", 3510, 12.5)   # 프리마켓에서 실제로 뜀
MOVERS["042700"] = ("한미반도체",  95000,  4.2)

def make_detail(table, code):
    if code not in table:
        return None
    name, prev, rate = table[code]
    price = round(prev * (1 + rate / 100))
    return {"code": code, "name": name, "price": price, "changeRate": rate,
            "changeAmount": price - prev, "prevClose": prev,
            "open": prev, "high": price, "low": prev,
            "volumeRaw": 50_000_000_000, "volume": "500억", "time": "08:32"}

def run(table, relaxed_allowed=True):
    """게이트만 떼어내 재현 — _select_theme_stocks 의 생존 판정과 동일한 식."""
    themes = [
        ("하락장",  ["047770", "030960"], False),   # 개미승리 근거 있음(unbacked=False)
        ("반도체",  ["005930", "042700"], False),
    ]
    def survive(codes, unbacked, relaxed):
        details = [make_detail(table, c) for c in codes]
        details = [d for d in details if d]
        if len(details) < (1 if relaxed else ts.MIN_STOCKS_PER_THEME):
            return False
        lead = max(d["changeRate"] for d in details)
        if unbacked:
            need = ts.UNBACKED_RELAXED_LEAD_RATE if relaxed else ts.UNBACKED_THEME_MIN_LEAD_RATE
        else:
            need = ts.RELAXED_LEAD_RATE if relaxed else ts.THEME_MIN_LEAD_RATE
        return lead >= need

    strict = [n for n, c, u in themes if survive(c, u, False)]
    if len(strict) >= ts.MIN_THEMES or not relaxed_allowed:
        return strict, False
    lax = [n for n, c, u in themes if survive(c, u, True)]
    return (lax, True) if len(lax) > len(strict) else (strict, False)

print("=== 1) 프리마켓 · NXT 시세 없음 (전 종목 0.0%) ===")
got, rel = run(UNIVERSE)
print(f"  통과 테마: {got or '없음'}   gateRelaxed={rel}")
print(f"  → 수정 전에는 RELAXED_LEAD_RATE=0.0 이라 {[n for n,_,_ in [('하락장',0,0),('반도체',0,0)]]} 전부 통과했다")
assert got == [], f"0% 인데 통과: {got}"
print("  OK — 움직이지 않은 테마는 이제 안 나간다\n")

print("=== 2) 프리마켓 · NXT 시세 있음 (실제 등락) ===")
got, rel = run(MOVERS)
print(f"  통과 테마: {got}   gateRelaxed={rel}")
assert "하락장" in got and "반도체" in got, got
print("  OK — 진짜 움직인 테마는 정상 통과 (테마명 필터는 별도 단계에서 적용)\n")

print("=== 3) 창 판정 · 네트워크 호출 여부 ===")
calls = []
def fake_get(url, **kw):
    calls.append(url)
    raise RuntimeError("네트워크 차단")
nxt_quotes.requests = types.SimpleNamespace(get=fake_get)
nxt_quotes.reset()
print(f"  정규장 10:00 → {nxt_quotes.fetch_premarket_quote('005930', now=OPEN)}  호출={len(calls)}건")
assert calls == [], "창 밖인데 네트워크를 탔다"
nxt_quotes.reset()
r = nxt_quotes.fetch_premarket_quote("005930", now=PRE)
print(f"  프리마켓 08:32 → {r}  호출={len(calls)}건 (후보 {len(nxt_quotes.CANDIDATES)}개 시도)")
assert r is None and len(calls) == len(nxt_quotes.CANDIDATES)
print("  OK — 전부 실패해도 None 을 돌려 기존 경로로 넘어간다\n")

print("=== 4) 회로 차단 (연속 실패 시 시도 중단) ===")
calls.clear(); nxt_quotes.reset()
for i in range(12):
    nxt_quotes.fetch_premarket_quote(f"00000{i}", now=PRE)
per = len(nxt_quotes.CANDIDATES)
print(f"  12종목 요청 → 실제 HTTP 시도 {len(calls)}건 "
      f"(차단 없으면 {12*per}건)")
assert len(calls) == nxt_quotes.FAIL_STREAK_LIMIT * per, len(calls)
print("  OK — 5회 실패 후 멈춘다. Lambda 타임아웃 위험 없음\n")

print("=== 5) get_stock_detail 폴백 (프리마켓 실패 → 정규장 경로) ===")
nxt_quotes.reset()
stock_data.reset_premarket_state()
stock_data.get_stock_detail_mobile = lambda c: {"code": c, "name": "폴백", "price": 1000,
    "changeRate": 5.0, "prevClose": 952, "volumeRaw": 5_000_000_000, "volume": "50억"}
d = stock_data.get_stock_detail("005930")
print(f"  결과: name={d['name']} rate={d['changeRate']} source={d.get('quoteSource','krx')}")
assert d["name"] == "폴백"
print(f"  premarket_hits={stock_data.premarket_hits()} (0 이어야 정상)")
assert stock_data.premarket_hits() == 0
print("  OK — 프리마켓이 통째로 죽어도 오늘과 같은 동작\n")

print("전부 통과")
