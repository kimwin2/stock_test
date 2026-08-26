"""테마 정체성 회귀 검증 — 2026-08-20 'S7' 실사고.

인포스탁 장중 강세 3위 `S7(삼성전자/SK하이닉스 등)` 이 그대로 테마 카드가 됐다.
안에는 SK스퀘어(반도체 지주)·삼성생명(보험)·삼성전자(반도체)가 들어 있었고,
헤드라인은 "…SK스퀘어·삼성생명 급락" 인데 카드의 두 종목은 +11.85% / +7.61%
였다. 두 개의 별개 결함이다.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from antwinner.collector import is_non_theme_bucket, is_basket_label
import analyzer

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'OK ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" (기대 {want!r})"))
    if not ok:
        fails.append(name)


print("\n[1] 바스켓 라벨은 테마가 아니다 — 정의가 종목 나열이다")
check("S7(원본 이름)", is_non_theme_bucket("S7", "S7(삼성전자/SK하이닉스 등)"), True)
check("S7(괄호 떨어진 뒤)", is_non_theme_bucket("S7"), True)
check("K200", is_non_theme_bucket("K200"), True)

print("\n[2] 멀쩡한 테마명은 살아야 한다 — 여기서 과하면 진짜 테마가 죽는다")
for name, raw in [("귀금속", "귀금속(금/은)"), ("모더나", "모더나(MODERNA)"),
                  ("반도체 대표주", "반도체 대표주(생산)"), ("원전", "원전(SMR)"),
                  ("HBM3", ""), ("CXL2", ""), ("AI", ""), ("2차전지", ""),
                  ("스테이블코인", ""), ("증권", "")]:
    check(f"{name} 유지", is_non_theme_bucket(name, raw), False)

print("\n[3] 기존 장세 필터는 그대로 동작한다")
check("하락장", is_non_theme_bucket("하락장"), True)
check("낙폭과대", is_non_theme_bucket("낙폭과대"), True)

print("\n[4] 하락을 말하는 헤드라인은 '급등·테마' 카드의 근거가 아니다")
check("그날의 헤드라인",
      analyzer._is_bearish_headline("美 금리 상승·반도체주 약세…SK스퀘어·삼성생명 급락 - 머니투데이"),
      True)
check("[특징주] 급락", analyzer._is_bearish_headline("[특징주] 실적 부진에 A사 급락"), True)

print("\n[5] 상승어가 같이 있으면 정당한 근거다 — 하락어 하나로 자르면 안 된다")
check("코스피 하락에도 강세",
      analyzer._is_bearish_headline("코스피 하락에도 반도체株 강세"), False)
check("그날 증권 테마 헤드라인",
      analyzer._is_bearish_headline("SK하이닉스 40조 자사주 중개 소식에…SK증권 '상한가'"), False)
check("스테이블코인 헤드라인",
      analyzer._is_bearish_headline("[특징주] 트럼프, 가상자산 '클래리티법' 통과 촉구에 스테이블코인株 강세"),
      False)
check("방향어 없음", analyzer._is_bearish_headline("삼성전자, 3분기 실적 발표"), False)
# 거시 방향어를 떼는 규칙이 반대로 새지 않는지 — 종목이 오른 기사는 살아야 한다
check("환율 급등에 수출주 강세",
      analyzer._is_bearish_headline("환율 급등에 수출주 강세"), False)
check("금리 하락에 성장주 급등",
      analyzer._is_bearish_headline("금리 하락에 성장주 급등"), False)
check("금리 상승에 기술주 하락",
      analyzer._is_bearish_headline("美 금리 상승에 기술주 하락"), True)
check("코스피 급락에도 조선주 신고가",
      analyzer._is_bearish_headline("코스피 급락에도 조선주 신고가"), False)

print("\n[6] 로컬 기사 경로: 종목명이 겹쳐도 하락 기사는 확신 매칭이 안 된다")
theme = {"themeName": "S7", "relatedStocks": ["SK스퀘어", "삼성생명", "삼성전자"], "headline": ""}
bearish = {"title": "美 금리 상승·반도체주 약세…SK스퀘어·삼성생명 급락", "summary": "", "url": "", "date": ""}
scored = analyzer._score_article_relevance(theme, bearish, 1)
print(f"     점수 {scored['score']} · bearish={scored['bearish']}")
check("하락 표시", scored["bearish"], True)
check("확신 매칭 거부", analyzer._is_confident_article_match(scored), False)

print("\n[7] 폴백 헤드라인은 제목이 그 테마를 증거해야 붙는다")

# 2026-08-25 실사고: '화장품' 카드에 반도체 기사가 붙었다.
# 그 기사는 같은 회차에 '반도체' 카드의 헤드라인이기도 했다.
COSMETIC = {
    "themeName": "화장품",
    "relatedStocks": ["오가닉티코스메틱", "한국화장품제조", "제닉", "아로마티카"],
    "headline": "",
}
SEMI = "반도체株 쏠렸던 돈 어디 갔나 봤더니…한 달 새 68% 폭등 [종목+] - 한국경제"
check("그날의 오배정 헤드라인 거부", analyzer._headline_link_matches_theme(COSMETIC, SEMI), False)

# 살아야 하는 것들 — 여기서 과하면 멀쩡한 테마가 헤드라인을 통째로 잃는다
for title, want, why in [
    ("[특징주] 화장품株 일제히 강세…수출 호조", True, "테마명이 제목에"),
    ("한국화장품제조, 장중 16% 급등", True, "테마 종목이 제목에"),
    ("오가닉티코스메틱 상한가", True, "테마 종목이 제목에"),
    ("코스피 상승 마감…외국인 순매수", False, "테마도 종목도 없음"),
    ("삼성전자 3분기 실적 발표", False, "다른 업종"),
]:
    check(f"{why}: {title[:24]}", analyzer._headline_link_matches_theme(COSMETIC, title), want)

print("\n[8] 같은 기사가 두 테마의 헤드라인이 될 수 없다")
# 반도체 카드는 네이버 URL, 화장품 카드는 구글 RSS URL 이라 URL 로는 안 걸렸다.
used = set()
used.add(analyzer._title_key("반도체株 쏠렸던 돈 어디 갔나 봤더니…한 달 새 68% 폭등 [종목+]"))
check("URL 이 달라도 같은 제목이면 잠긴다",
      analyzer._title_key(SEMI) in used, True)
check("다른 기사는 안 잠긴다",
      analyzer._title_key("화장품株 일제히 강세") in used, False)

print("\n[9] 실측이 같은 이름의 명단을 갖고 있으면 그게 정답지다")

# 2026-08-26 실사고: '건설' 카드의 대장주가 한미글로벌(+20.09%)이었다.
# 개미승리는 이 종목을 '이란재건'·'우크라이나재건' 으로 분류하고 '건설'
# 버킷에는 넣지 않았다. 카드 이름은 '건설' 인데 구성은 '재건' 이었다.
import theme_stocks as ts

ANALYSIS = {"antwinnerSignals": [
    {"thema": "건설", "companies": [
        {"stockname": "현대건설", "fluctuation": "8.33%"},
        {"stockname": "GS건설", "fluctuation": "8.14%"},
        {"stockname": "HL D&I", "fluctuation": "4.80%"},
        {"stockname": "HDC", "fluctuation": "3.94%"},
        {"stockname": "일성건설", "fluctuation": "3.27%"},
        {"stockname": "동신건설", "fluctuation": "3.19%"},
    ]},
    {"thema": "이란재건", "companies": [
        {"stockname": "한미글로벌", "fluctuation": "20.09%"},
        {"stockname": "현대건설", "fluctuation": "8.33%"},
    ]},
    {"thema": "우크라이나재건", "companies": [
        {"stockname": "한미글로벌", "fluctuation": "20.09%"},
        {"stockname": "전진건설로봇", "fluctuation": "17.23%"},
    ]},
]}
CONSTRUCTION = {"themeName": "건설"}


def judge(name, rate, sources):
    cand = ts.Candidate(name)
    for src in sources:
        cand.add(src)
    return ts.offtheme_by_measured_roster(CONSTRUCTION, ANALYSIS, cand, rate)


check("한미글로벌(LLM 단독, 명단 밖, 컷 위) 제외",
      bool(judge("한미글로벌", 20.09, [ts.SRC_LLM])), True)
check("제외 사유에 실제 소속이 남는다",
      "이란재건" in (judge("한미글로벌", 20.09, [ts.SRC_LLM]) or ""), True)
check("전진건설로봇도 제외", bool(judge("전진건설로봇", 16.49, [ts.SRC_LLM])), True)

print("\n[10] 좁게 건다 — 이 셋 중 하나라도 어긋나면 심판하지 않는다")
check("실측 소스가 이 테마로 지목했으면 유지",
      judge("현대건설", 8.33, [ts.SRC_ANTWINNER, ts.SRC_LLM]), None)
check("명단에 있으면 유지", judge("동신건설", 3.19, [ts.SRC_LLM]), None)
# 명단은 등락률 상위 6개로 잘려 있다 — 컷 아래면 '없다' 가 '아니다' 를 뜻하지 않는다
check("컷(3.19%) 아래면 반증 불가", judge("어떤건설사", 2.0, [ts.SRC_LLM]), None)
# 이름이 정확히 같을 때만 그 명단을 정답지로 쓴다
check("이름이 다르면 심판 안 함 (반도체소부장 ↔ 반도체(전력))",
      ts.offtheme_by_measured_roster(
          {"themeName": "반도체소부장"},
          {"antwinnerSignals": [{"thema": "반도체(전력)", "companies": [
              {"stockname": "코스텍시스", "fluctuation": "14.50%"},
              {"stockname": "나노씨엠에스", "fluctuation": "13.07%"},
              {"stockname": "시지트로닉스", "fluctuation": "0.75%"}]}]},
          (lambda: (lambda c: (c.add(ts.SRC_LLM), c)[1])(ts.Candidate("티엘비")))(),
          0.9),
      None)
check("실측에 같은 이름 버킷이 없으면 심판 안 함",
      ts.offtheme_by_measured_roster({"themeName": "AI"}, ANALYSIS,
          (lambda: (lambda c: (c.add(ts.SRC_LLM), c)[1])(ts.Candidate("아무종목")))(), 30.0),
      None)

print("\n" + ("=" * 52))
print("모두 통과" if not fails else f"실패 {len(fails)}건: {fails}")
sys.exit(1 if fails else 0)
