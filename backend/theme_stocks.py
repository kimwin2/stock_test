"""테마별 종목 선정 — 증거 기반.

기존 방식의 문제:
    LLM 이 종목명 6개를 자유 텍스트로 생성 → 이름으로 코드 검색 → 해석된
    순서대로 앞 4개. 등락률·거래대금이 선정에 전혀 개입하지 않아, 카드에
    오르는 건 "오늘 실제로 오른 종목"이 아니라 "LLM 이 대장주라고 짐작한
    종목"이었다. 이름을 환각하면 조용히 탈락해 카드가 2종목만 남기도 했다.

지금 방식:
    1) 여러 시그널 소스의 합집합으로 후보 풀 구성 (출처를 태그로 보존)
    2) 실측 시세로 하드 게이트 (환각·동전주·저유동성·급락 컷)
    3) 교차 확인 · 거래대금 · 등락률 점수로 정렬 → 상위 N
    4) 유효 종목이 부족한 테마는 드롭, 종목이 크게 겹치는 테마는 병합

핵심 아이디어는 수급 탭에서 이미 검증된 패턴이다 — 점수와 함께 근거
문자열을 남겨 "왜 이 종목이 뽑혔는지" 추적 가능하게 한다.
"""

from __future__ import annotations

import math
import re

# ── 출처 ──────────────────────────────────────────
# 실측(measured) = 그 시각 실제 시세·거래대금에 근거한 소스.
# 나머지는 선행·정성 시그널이라 단독으로는 근거가 약하다.
SRC_ANTWINNER = "개미승리"
SRC_PRICE = "급등클러스터"
SRC_INFOSTOCK = "인포스탁"
SRC_WOWNET = "와우넷"
SRC_YOUTUBE = "유튜브"
SRC_TELEGRAM = "텔레그램"
SRC_LLM = "뉴스분석"

MEASURED_SOURCES = {SRC_ANTWINNER, SRC_PRICE}

# ── 게이트 기준 ────────────────────────────────────
MIN_PRICE = 1500                      # 동전주 제외 (기존 규칙 유지)
# 거래대금 30억 — 테마 대표주라면 그날 돈이 실제로 몰려야 한다.
# 10억대는 단타로 들어가고 나올 수 있는 유동성이 아니라 대표주로 부적합하다.
MIN_TRADING_VALUE = 3_000_000_000
MIN_CHANGE_RATE = -3.0                # 크게 빠진 종목은 테마 대표로 부적합
LIMIT_UP_RATE = 29.0                  # 상한가 판정

MAX_STOCKS_PER_THEME = 4
MIN_STOCKS_PER_THEME = 2              # 이보다 적으면 테마 자체를 드롭
# 테마 생존 조건 — 선정 종목 중 최소 하나는 의미 있게 올라야 한다.
# '급등·테마' 탭인데 전 종목이 마이너스인 테마는 오늘의 주도 테마가 아니다.
THEME_MIN_LEAD_RATE = 3.0
# 완화해도 **움직이지 않은 테마는 테마가 아니다.**
#
# 0.0 은 완화가 아니라 게이트를 없앤 것이었다. 조건이 `lead_rate < 0.0` 이라
# 전 종목이 0.0% 여도 통과한다. 정규장이 열리기 전(08:00~09:00)에는 네이버가
# 전일 종가를 그대로 주므로 **매 평일 아침 이 구간이 반드시 발생**한다.
# 실측 2026-08-19 08:32: 테마 4개 전부 `gateRelaxed: true`, 전 종목 0.0%,
# 그중 하나가 '하락장'(코데즈컴바인·양지사)이었다.
# 0.5% 는 "실제로 움직였다"의 최소선이다. 프리마켓 시세(nxt_quotes)가 붙으면
# 진짜로 오른 종목은 여유롭게 넘고, 체결이 없어 0% 인 날은 여기서 막힌다.
RELAXED_LEAD_RATE = 0.5
MAX_POOL_PER_THEME = 12               # 시세 조회 예산 상한

# 선정 종목 전부가 LLM 지목뿐인 테마 — 근거가 문장 하나다. 실측 시그널이 하나도
# 없으므로 "오늘 이 테마가 실제로 뛰었다"는 증거가 화면 안에 존재하지 않는다.
# 2026-08-11: '조선기자재'(지역난방공사 +4.31 / 산일전기 +0.49 / 일진전기 -1.28)와
# '게임'(NC +4.93 / 컴투스 +3.19 / 데브시스터즈 -2.00)이 대장주 1종목의 +3% 만으로
# 통과했다. 종목 구성도 테마명과 달랐다(조선기자재인데 전부 전력기기·집단에너지).
# 이런 테마는 대장주가 누가 봐도 급등이어야만 살린다.
UNBACKED_THEME_MIN_LEAD_RATE = 10.0
UNBACKED_RELAXED_LEAD_RATE = 5.0

THEME_MERGE_OVERLAP = 0.6             # 이름이 달라도 종목이 이만큼 겹치면 같은 테마
# 이름에 같은 섹터어가 있고 대표 종목도 이만큼 겹치면 같은 테마로 본다.
# 두 근거를 모두 요구하는 이유: 어느 한쪽만으로는 오판한다. '2차전지 소재'와
# '반도체 소재'는 낱말을 공유하지만 다른 테마고, 대형주 몇 개가 겹치는 서로 다른
# 테마도 흔하다. 임계값 하나를 내려서 맞추면 그날 숫자에만 맞는 조정이 된다.
THEME_MERGE_TOKEN_OVERLAP = 0.3


# 지수 추종 상품(ETF/ETN/레버리지/인버스). 지수 급등일엔 이들이 급등주 상위를
# 점령해 "코스닥150 레버리지 상품" 같은 무의미한 테마가 만들어진다 (2026-08-10).
# 개별 기업이 아니므로 테마 대시보드에서는 어떤 소스로 들어와도 제외한다.
_INDEX_PRODUCT_BRANDS = (
    "KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "ARIRANG", "HANARO", "SOL",
    "KIWOOM", "PLUS", "UNICORN", "TIMEFOLIO", "WOORI", "FOCUS", "MASTER",
    "KOSEF", "1Q", "BNK", "HK", "ITF", "DAISHIN343", "N2", "QV", "메리츠",
)
# "합성" 은 동남합성 같은 실제 기업이 있어 넣지 않는다 (합성 ETF 는 ETF 로 잡힘).
_INDEX_PRODUCT_KEYWORDS = ("ETN", "ETF", "레버리지", "인버스", "선물")


def is_index_product(name: str) -> bool:
    """지수·파생 추종 상품인가. 종목명 기반 판별."""
    n = (name or "").strip()
    if not n:
        return False
    upper = n.upper()
    if any(kw in upper for kw in _INDEX_PRODUCT_KEYWORDS):
        return True
    head = upper.split(" ", 1)[0]
    return head in _INDEX_PRODUCT_BRANDS and " " in upper


def _norm_name(name: str) -> str:
    """종목명 정규화 — 소스마다 표기가 미묘하게 다르다."""
    return re.sub(r"\s+", "", (name or "")).strip()


def _norm_theme(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", (name or "")).lower()


# 섹터를 가리키지 않는 조사·수식어. 이것만 공유하는 건 같은 섹터라는 근거가 아니다.
_GENERIC_THEME_TOKENS = {"관련", "관련주", "테마", "종목", "그룹", "섹터", "기타", "강세", "수혜", "이슈"}


def _sector_tokens(name: str) -> set[str]:
    """테마명에서 섹터를 가리키는 낱말만 ('건설 및 토목 자재' → {건설, 토목, 자재}).

    `_theme_matches` 의 3글자 슬라이스는 '건설'·'조선'·'방산'처럼 두 글자짜리
    섹터어를 아예 만들지 못한다. 그래서 '반도체 A'와 '반도체 B'는 병합되는데
    '건설 A'와 '건설 B'는 영원히 안 되는 비대칭이 있었다 (2026-08-11).
    """
    parts = re.split(r"[^0-9a-zA-Z가-힣]+", name or "")
    return {p.lower() for p in parts if len(p) >= 2 and p.lower() not in _GENERIC_THEME_TOKENS}


def _theme_matches(a: str, b: str) -> bool:
    """테마명이 같은 것을 가리키는가 (부분 일치 + 토큰 겹침)."""
    na, nb = _norm_theme(a), _norm_theme(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    # 2글자 이상 공통 토큰이 있으면 같은 계열로 본다 ("태양광" ↔ "태양광에너지")
    return any(len(t) >= 2 and t in nb for t in (na[i:i + 3] for i in range(len(na) - 2)))


class Candidate:
    """테마 후보 종목 하나. 어떤 소스가 이 종목을 지목했는지 누적한다."""

    __slots__ = ("name", "sources", "code", "llm_rank", "measured_rate", "measured_value")

    def __init__(self, name: str):
        self.name = name
        self.sources: set[str] = set()
        self.code: str | None = None
        self.llm_rank: int | None = None
        self.measured_rate: float | None = None
        self.measured_value: float | None = None

    def add(self, source: str, code: str | None = None) -> None:
        self.sources.add(source)
        if code and not self.code:
            self.code = code


def _parse_pct(value) -> float | None:
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_eok(value) -> float | None:
    """'1823억' → 182_300_000_000. '1조 2000억' 도 읽는다."""
    s = str(value or "").replace(",", "").strip()
    m = re.match(r"^(?:([0-9.]+)\s*조)?\s*(?:([0-9.]+)\s*억)?", s)
    if not m or not (m.group(1) or m.group(2)):
        return None
    try:
        jo = float(m.group(1)) if m.group(1) else 0.0
        eok = float(m.group(2)) if m.group(2) else 0.0
    except ValueError:
        return None
    total = jo * 1e12 + eok * 1e8
    return total if total > 0 else None


def build_candidates(theme: dict, analysis: dict) -> list[Candidate]:
    """테마 하나에 대한 후보 풀 — 여러 소스의 합집합."""
    # 병합으로 흡수된 이름까지 같이 조회한다. 대표명만 보면 흡수된 테마를 지목했던
    # 실측 소스(개미승리·급등클러스터·인포스탁)의 태그가 통째로 사라져, 종목은
    # 그대로인데 근거만 LLM 단독으로 바뀐다 — 교차확인 점수와 `is_unbacked`
    # 판정이 동시에 틀어진다.
    theme_names = [theme.get("themeName", "")] + list(theme.get("mergedThemes") or [])

    def name_matches(other: str) -> bool:
        return any(_theme_matches(n, other) for n in theme_names if n)

    pool: dict[str, Candidate] = {}

    def get(name: str) -> Candidate | None:
        key = _norm_name(name)
        if not key:
            return None
        if key not in pool:
            pool[key] = Candidate(name.strip())
        return pool[key]

    # 1) LLM 이 지목한 종목 — 순서가 곧 대장주 지목 강도
    for rank, name in enumerate(theme.get("relatedStocks") or []):
        c = get(name)
        if c:
            c.add(SRC_LLM)
            if c.llm_rank is None:
                c.llm_rank = rank

    # 2) 개미승리 — 코드·등락률·거래대금까지 실측으로 들고 있는 최상급 소스
    for sig in analysis.get("antwinnerSignals") or []:
        if not name_matches(sig.get("thema", "")):
            continue
        for comp in sig.get("companies") or []:
            c = get(comp.get("stockname", ""))
            if not c:
                continue
            c.add(SRC_ANTWINNER, (comp.get("stock_code") or "").strip() or None)
            if c.measured_rate is None:
                c.measured_rate = _parse_pct(comp.get("fluctuation"))
                c.measured_value = _parse_eok(comp.get("volume"))

    # 3) 가격 기반 급등 클러스터 — 실제 시세로 묶인 종목군
    for cand in analysis.get("priceSignalCandidates") or []:
        if not name_matches(cand.get("themeName", "")):
            continue
        for name in cand.get("matchedStocks") or []:
            c = get(name)
            if c:
                c.add(SRC_PRICE)

    # 4) 인포스탁 장중 강세 테마
    for sig in analysis.get("infostockSignals") or []:
        if not name_matches(sig.get("themeName", "")):
            continue
        for name in (sig.get("matchedStocks") or []) + (sig.get("referenceStocks") or []):
            c = get(name)
            if c:
                c.add(SRC_INFOSTOCK)

    # 5) 유튜브 선행 시그널 (섹터 매칭)
    for sig in analysis.get("youtubeSignals") or []:
        if not any(name_matches(s) for s in (sig.get("sectors") or [])):
            continue
        for name in sig.get("stocks") or []:
            c = get(name)
            if c:
                c.add(SRC_YOUTUBE)

    # 6) 와우넷 특징주
    for sig in analysis.get("wownetSignals") or []:
        names = (sig.get("stocks") or []) + (sig.get("featuredStocks") or [])
        sectors = sig.get("sectors") or []
        if sectors and not any(name_matches(s) for s in sectors):
            continue
        for name in names:
            c = get(name)
            if c:
                c.add(SRC_WOWNET)

    # 7) 텔레그램 선행 시그널
    for sig in analysis.get("telegramSignals") or []:
        if not name_matches(sig.get("themeName", "") or sig.get("theme", "")):
            continue
        for name in sig.get("stocks") or []:
            c = get(name)
            if c:
                c.add(SRC_TELEGRAM)

    # 실측 소스가 지목한 종목을 먼저 조회하도록 정렬 (조회 예산이 유한하므로)
    def pool_priority(c: Candidate) -> tuple:
        return (
            0 if c.sources & MEASURED_SOURCES else 1,
            -len(c.sources),
            c.llm_rank if c.llm_rank is not None else 99,
        )

    return sorted(pool.values(), key=pool_priority)[:MAX_POOL_PER_THEME]


def is_unbacked(cands) -> bool:
    """이 종목들의 근거가 LLM 지목뿐인가.

    하나라도 외부 소스(개미승리·급등클러스터·인포스탁·와우넷·유튜브·텔레그램)가
    지목했다면 최소한 사람 또는 시세가 그 종목을 이 테마로 불렀다는 뜻이다.
    전부 `뉴스분석` 단독이면 테마 전체가 LLM 문장 위에만 서 있다.
    """
    return all(not (c.sources - {SRC_LLM}) for c in cands)


# 등락률과 거래대금의 배점. '급등·테마' 탭이므로 오른 폭이 유동성을 이겨야 한다.
# 이전 배점(등락 ×1.2, 거래대금 ×8 무제한)에서는 산일전기 +0.49%(1,848억)가 18.7점,
# 지역난방공사 +4.31%(751억)가 20.2점으로 사실상 동점이었다. 대형주가 거래대금으로
# 대장주 자리를 사던 구조라 상한을 둔다 (2026-08-11).
CHANGE_RATE_WEIGHT = 2.5
VALUE_SCORE_WEIGHT = 5.0
VALUE_SCORE_CAP = 12.0                # 1,000억 이상은 더 얹지 않는다


def score_candidate(cand: Candidate, detail: dict) -> tuple[float, list[str]]:
    """후보 하나의 점수와 근거. 높을수록 테마 대표주에 가깝다."""
    score = 0.0
    reasons: list[str] = []

    rate = float(detail.get("changeRate") or 0.0)
    pts = min(rate, 30.0) * CHANGE_RATE_WEIGHT
    score += pts
    reasons.append(f"등락 {rate:+.2f}% {pts:+.0f}")

    value = float(detail.get("volumeRaw") or 0.0)
    if value > 0:
        # 거래대금은 로그 스케일 — 10억 0점, 100억 +5, 1000억 이상 +10~12(상한)
        pts = min(max(0.0, math.log10(value / 1e9)) * VALUE_SCORE_WEIGHT, VALUE_SCORE_CAP)
        if pts > 0:
            score += pts
            reasons.append(f"거래대금 {value / 1e8:,.0f}억 +{pts:.0f}")

    # 교차 확인 — 서로 독립인 소스가 같은 종목을 지목할수록 신뢰도가 오른다.
    # 펀드매니저가 한 정보원만 믿지 않는 것과 같은 이유다.
    if len(cand.sources) >= 2:
        pts = (len(cand.sources) - 1) * 12.0
        score += pts
        reasons.append(f"교차확인 {len(cand.sources)}개({'·'.join(sorted(cand.sources))}) +{pts:.0f}")

    if cand.sources & MEASURED_SOURCES:
        score += 10.0
        reasons.append("실측 시그널 +10")

    if cand.llm_rank == 0:
        score += 6.0
        reasons.append("대장주 지목 +6")
    elif cand.llm_rank == 1:
        score += 3.0
        reasons.append("상위 지목 +3")

    if rate >= LIMIT_UP_RATE:
        score += 10.0
        reasons.append("상한가 +10")

    return (round(score, 1), reasons)


# 크롤 값과 실측 시그널 값이 이 배수 밖으로 벌어지면 크롤을 못 믿는다.
# 장중 몇 분 차이로 거래대금이 10배가 되지는 않는다.
VOLUME_TRUST_RATIO = 10.0


def reconcile_volume(detail: dict, cand: Candidate) -> dict:
    """시세 크롤의 거래대금을 실측 시그널 값으로 메우거나 바로잡는다.

    개미승리는 종목별 거래대금을 **실측으로 같이 들고 온다.** 그런데 우리는
    그 값을 정렬용으로만 쓰고, 게이트에는 따로 크롤한 값을 걸고 있었다.
    크롤은 두 가지로 틀린다 — 못 읽어서 0 이 되거나(SK증권), 엉뚱한 셀을 읽어
    가격을 거래대금으로 착각하거나(신영증권 실거래 7억 → 카드 '1,648억').
    이미 손에 든 실측값을 안 쓰면서 못 읽었다는 이유로 종목을 떨어뜨리는 건
    앞뒤가 안 맞는다.

    원본을 고치지 않고 사본을 돌려준다 — detail 은 종목 단위로 캐시되어
    여러 테마가 나눠 쓰기 때문이다.
    """
    measured = cand.measured_value
    if not measured or measured <= 0:
        return detail

    crawled = float(detail.get("volumeRaw") or 0)
    unknown = bool(detail.get("volumeUnknown")) or crawled <= 0
    conflict = (not unknown) and (
        crawled > measured * VOLUME_TRUST_RATIO or crawled * VOLUME_TRUST_RATIO < measured
    )
    if not (unknown or conflict):
        return detail

    fixed = dict(detail)
    fixed["volumeRaw"] = measured
    fixed["volumeUnknown"] = False
    fixed["volumeSource"] = "measured-signal"
    return fixed


# 완화 모드 — 시장이 조용해 통과 종목이 부족한 날, 탭을 비우느니 기준을 낮춘다.
# 동전주 컷(MIN_PRICE)만은 완화하지 않는다. 이건 품질이 아니라 안전 문제다.
RELAXED_TRADING_VALUE = 500_000_000
RELAXED_CHANGE_RATE = -10.0
MIN_THEMES = 3


def passes_gate(detail: dict, relaxed: bool = False,
                allow_unknown_volume: bool = False) -> str | None:
    """하드 게이트. 통과하면 None, 아니면 탈락 사유 키."""
    min_value = RELAXED_TRADING_VALUE if relaxed else MIN_TRADING_VALUE
    min_rate = RELAXED_CHANGE_RATE if relaxed else MIN_CHANGE_RATE
    if is_index_product(detail.get("name") or ""):
        return "indexProduct"
    if float(detail.get("price") or 0) < MIN_PRICE:
        return "penny"
    volume = float(detail.get("volumeRaw") or 0)
    unknown = bool(detail.get("volumeUnknown")) or volume <= 0
    if unknown and allow_unknown_volume:
        # 회로 차단 모드 — 유동성 판정 자체를 건너뛴다. 여기서 illiquid 로
        # 흘려보내면 게이트를 껐다는 말이 무색해진다 (volume 이 0 이므로 어떤
        # 하한을 걸어도 전부 탈락한다).
        pass
    elif unknown:
        # '거래대금 0원' 이 아니라 **못 읽었다**. 예전에는 이 자리를 가격으로
        # 지어낸 수(price × 100만)가 메웠고, 3,000원 미만 종목은 실제로 얼마가
        # 거래됐든 항상 30억 미만이 되어 조용히 탈락했다 (2026-08-20 SK증권:
        # 상한가 · 실거래 281억 · 헤드라인 주인공인데 카드에 없었다).
        # 모르면 모른다고 떨어뜨린다 — 통계에 이유가 남아야 다음에 보인다.
        return "noVolume"
    elif volume < min_value:
        return "illiquid"
    if float(detail.get("changeRate") or 0) < min_rate:
        return "falling"
    return None


def sector_mix(stocks: list[dict]) -> dict:
    """선정 종목들의 실제 업종 분포.

    LLM 이 붙인 테마명이 종목 구성과 맞는지 사후에 볼 수 있게 남긴다.
    실제로 급등클러스터가 묶은 동조 상한가 종목에 "2차전지 소재 및 장비"
    라는 이름이 붙었는데, 그 안에 건설기계 유통사와 피혁업체가 있었다.
    프롬프트가 금지해도 새어나가므로 최소한 드러나게는 해야 한다.

    분류가 '기타' 인 소형주가 많으면 판정 자체가 불가능하다. 그래서
    'unknown' 비율을 함께 담아, 근거 없이 단정하지 않도록 한다.
    """
    try:
        from flow_signals.universe import classify_sector
    except ImportError:
        from .flow_signals.universe import classify_sector

    counts: dict[str, int] = {}
    for s in stocks:
        sec = classify_sector(s.get("name") or "", s.get("code"))
        counts[sec] = counts.get(sec, 0) + 1
    known = {k: v for k, v in counts.items() if k != "기타"}
    total = len(stocks) or 1
    top = max(known.items(), key=lambda kv: kv[1]) if known else None
    return {
        "counts": counts,
        "dominant": top[0] if top else None,
        "dominantRatio": round(top[1] / total, 2) if top else 0.0,
        "unknownRatio": round(counts.get("기타", 0) / total, 2),
    }


def merge_similar_themes(themes: list[dict]) -> list[dict]:
    """유사 테마를 종목 선정 *전에* 병합.

    프롬프트의 '유사 테마 병합' 지시는 자주 새어나간다 ("태양광"과
    "태양광 에너지"가 같이 나오는 식). 이름 유사도와 지목 종목 겹침으로
    사후 보정한다.

    반드시 선정 전에 돌아야 한다. 선정 단계의 전역 종목 중복 제거가 먼저
    돌면 중복 테마는 서로 다른 종목을 받게 되어 겹침이 0 이 되고, 병합
    판정이 영원히 발동하지 않는다.
    """
    out: list[dict] = []
    for theme in themes:
        name = theme.get("themeName", "")
        stocks = {_norm_name(s) for s in (theme.get("relatedStocks") or []) if s}
        merged_into = None

        for kept in out:
            kept_name = kept.get("themeName", "")
            kept_stocks = {_norm_name(s) for s in (kept.get("relatedStocks") or []) if s}

            same_name = _theme_matches(name, kept_name)
            overlap = 0.0
            if stocks and kept_stocks:
                overlap = len(stocks & kept_stocks) / min(len(stocks), len(kept_stocks))
            shared_sector = _sector_tokens(name) & _sector_tokens(kept_name)

            if (same_name
                    or overlap >= THEME_MERGE_OVERLAP
                    or (shared_sector and overlap >= THEME_MERGE_TOKEN_OVERLAP)):
                merged_into = kept
                break

        if merged_into is None:
            out.append(dict(theme))
            continue

        # 이름이 더 짧고 일반적인 쪽을 대표명으로 (예: "태양광 에너지" → "태양광")
        if len(name) < len(merged_into.get("themeName", "")):
            merged_into["themeName"], name = name, merged_into["themeName"]
        merged_into.setdefault("mergedThemes", []).append(name)
        # 흡수된 테마가 들고 있던 종목코드 매핑은 살린다 (이름 검색보다 정확하다).
        codes = theme.get("_antwinner_stock_codes")
        if codes:
            merged_into.setdefault("_antwinner_stock_codes", {}).update(codes)
        # 후보 풀을 넓히기 위해 지목 종목은 순서를 지켜 합친다
        combined = list(merged_into.get("relatedStocks") or [])
        seen = {_norm_name(s) for s in combined}
        for s in theme.get("relatedStocks") or []:
            if _norm_name(s) not in seen:
                combined.append(s)
                seen.add(_norm_name(s))
        merged_into["relatedStocks"] = combined

    return out
