"""참고 채널 일일 산출물 → 대조용 구조화 사실.

레퍼런스 저장소(별도 repo)의 daily/YYYY-MM-DD.json 에서 **수치와 목록만**
뽑는다. 서술 문장은 읽지 않는다 — 제품에 실어 나를 일이 없기 때문이다.

경로는 REFERENCE_DAILY_DIR 환경변수로 지정한다. 기본값은 옆 저장소.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_DAILY_DIR = Path.home() / "repo" / "stock_chat" / "data" / "daily"


def daily_dir() -> Path:
    return Path(os.environ.get("REFERENCE_DAILY_DIR") or DEFAULT_DAILY_DIR)


# 레퍼런스가 쓰는 섹터 표현 → 우리 SECTOR_RULES 이름.
# 우리 쪽 정규화 사전이지 레퍼런스의 자산이 아니다. 같은 산업을 가리키는
# 다른 이름을 억지로 '불일치'로 세지 않기 위한 최소한의 매핑.
SECTOR_ALIASES: dict[str, str] = {
    "변압기": "전력기기",
    "전선": "전력기기",
    "전력제어": "전력기기",
    "반도체소부장": "반도체장비",
    "소부장": "반도체장비",
    "후공정": "반도체장비",
    "패키징": "반도체장비",
    "바이오시밀러": "바이오",
    "제약": "바이오",
    "화장품": "화장품/소비재",
    "소비재": "화장품/소비재",
    "금융": "증권",          # 레퍼런스의 '금융'은 대개 증권/거래대금 테마
    "엔터": "미디어/엔터",
    "미디어": "미디어/엔터",
    "인터넷": "게임/IT",
    "게임": "게임/IT",
    "IT": "게임/IT",
    "건설": "건설/인프라",
    "인프라": "건설/인프라",
    "수소": "연료전지/수소",
    "태양광": "신재생",
    "풍력": "신재생",
    "우주": "우주항공",
    "항공우주": "우주항공",
    "AI": "AI/반도체팹리스",
    "팹리스": "AI/반도체팹리스",
    # 우리는 정유·석유를 '화학' 으로 분류한다. 표현이 다를 뿐 같은 대상이다.
    "에너지": "화학",
    "정유": "화학",
    "석유": "화학",
    # LNG 보냉재(한국카본·동성화인텍)는 조선 기자재 사이클을 탄다.
    "보냉재": "조선",
    # 우리는 변압기·전선·ESS·배전을 '전력기기' 한 섹터로 묶는다.
    # 레퍼런스는 이걸 '전력'·'ESS'·'데이터센터 인프라'로 나눠 부르는데,
    # 종목군은 같다(LS ELECTRIC·효성중공업·LS 등). 별칭이 없어 '사전 누락'으로
    # 잡히던 것을 정리한다 — 실제로 없는 섹터가 아니라 이름만 달랐다.
    "전력": "전력기기",
    "ESS": "전력기기",
    "에너지저장": "전력기기",
    "데이터센터": "전력기기",
    "데이터센터인프라": "전력기기",
    "배전": "전력기기",
    # 기판·부품 — 종목군이 우리 '반도체장비'(심텍·대덕전자·코리아써키트·엠케이전자)와
    # 같다. 이름만 다른데 '사전 누락' 으로 세면 없는 결함을 만들어낸다.
    "기판": "반도체장비",
    "PCB": "반도체장비",
    "패키지기판": "반도체장비",
    "리드프레임": "반도체장비",
    "장비": "반도체장비",
    "MLCC": "반도체",          # 삼성전기
    "메모리": "반도체",
    "파운드리": "반도체",        # DB하이텍
    "전력반도체": "반도체",      # "전력" 부분일치로 전력기기에 끌려가던 것 고정
    "연료전지": "연료전지/수소",
    "원자력": "원전",
    "항공방산": "방산",
    "음식료": "유통/음식료",
    "식품": "유통/음식료",
    "지주사": "지주",
    "친환경": "신재생",
    "신재생에너지": "신재생",
    # 레퍼런스가 국장 대응군으로 직접 지목한 이름이 현대오토에버·LG CNS·삼성SDS 다.
    # 우리는 그 종목들을 '게임/IT' 에 두고 있으므로 같은 대상이다.
    "소프트웨어": "게임/IT",
    "클라우드": "게임/IT",
    "SI": "게임/IT",
    "보안": "게임/IT",
    # 통신장비/광통신 — 이통3사(통신)와 다른 축이다. 레퍼런스도 '통신' 과 '네트워크' 를
    # 같은 날 따로 적는다. 계열로 묶지 않고 별도 섹터로 대응시킨다.
    "네트워크": "통신장비",
    "네트워킹": "통신장비",
    "광통신": "통신장비",
    "통신장비": "통신장비",
}

# 개별 테마가 아니라 분류 체계 상위어. 매매 대상이 아니므로 대조에서 제외한다.
# 이런 단어를 '사전 누락' 으로 세면 SECTOR_RULES 에 잡동사니를 추가하게 된다.
UMBRELLA_TERMS = {"산업재", "경기소비재", "필수소비재", "소재", "IT", "성장주", "가치주", "대형주", "중소형주"}

# 섹터가 아니라 여집합·부정 표현. 추출기가 "반도체 및 비반도체 차별화" 같은
# 리포트 문구에서 그대로 긁어온다. 이걸 '사전 누락' 으로 세면 SECTOR_RULES 에
# '비반도체' 를 추가하게 되는데, 그런 섹터는 존재할 수 없다.
NON_SECTOR_TERMS = {"비반도체", "비주도업종", "낙폭과대", "기타"}

# 한 항목에 두 섹터가 들어있는 표기 ("자동차 및 로봇", "PCB 및 기판").
# 쪼개지 않으면 둘 다 맞혀도 문자열이 달라 '사전 누락' 으로 잡힌다.
_COMPOUND_SPLIT = re.compile(r"\s*(?:및|and|·|,|＆|&)\s*")


# 같은 계열로 볼 섹터 묶음.
#
# 레퍼런스의 `sectors` 는 대화에서 뽑은 납작한 키워드 목록이라 세분이 없다.
# 예를 들어 그가 "반도체" 라고 쓴 날 본문은 대개 소부장·후공정 이야기인데,
# 우리는 그걸 '반도체장비' 로 더 좁게 분류한다. 문자열만 비교하면 정확히
# 맞힌 것을 '놓침' 으로 세어, 있지도 않은 결함을 만들어낸다.
# 정확 일치와 계열 일치를 함께 보고해 그 착시를 없앤다.
SECTOR_FAMILIES: list[set[str]] = [
    {"반도체", "반도체장비", "AI/반도체팹리스"},
    {"증권", "은행", "보험"},
    {"바이오", "헬스케어"},
    {"신재생", "연료전지/수소", "화학"},
    {"전력기기", "원전"},
    {"조선", "방산", "우주항공"},
    {"게임/IT", "미디어/엔터"},
]


def family_of(sector: str) -> set[str]:
    """그 섹터가 속한 계열 집합 (없으면 자기 자신만)."""
    for fam in SECTOR_FAMILIES:
        if sector in fam:
            return fam
    return {sector}


def split_compound(name: str) -> list[str]:
    """'자동차 및 로봇' → ['자동차', '로봇'], '네트워크(통신)' → ['네트워크'].

    괄호 보충설명과 접속사 나열을 풀지 않으면, 두 섹터를 다 맞힌 날도
    문자열이 달라 '사전 누락' 으로 집계된다.
    """
    base = re.sub(r"[（(].*?[)）]", "", name or "")
    parts = [p.strip() for p in _COMPOUND_SPLIT.split(base) if p.strip()]
    return parts or ([name.strip()] if (name or "").strip() else [])


def normalize_sector(name: str) -> str:
    """레퍼런스 섹터 표현 → 우리 섹터명. 매핑이 없으면 원문 유지."""
    s = re.sub(r"\s+", "", (name or "")).strip()
    if not s:
        return ""
    if s in SECTOR_ALIASES:
        return SECTOR_ALIASES[s]
    # 부분 일치 (긴 별칭 우선) — "반도체소부장(후공정)" 같은 표기 흡수
    for alias in sorted(SECTOR_ALIASES, key=len, reverse=True):
        if alias in s:
            return SECTOR_ALIASES[alias]
    return s


def load_reference(date_str: str) -> dict | None:
    """YYYY-MM-DD → 대조용 사실. 파일이 없으면 None."""
    path = daily_dir() / f"{date_str}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())

    kr_cash = ((raw.get("cash") or {}).get("kr") or {})
    sectors_raw: list[str] = []
    for entry in (raw.get("sectors") or []):
        for s in split_compound(entry):
            flat = re.sub(r"\s+", "", s)
            if not flat or flat in UMBRELLA_TERMS or flat in NON_SECTOR_TERMS:
                continue
            if s not in sectors_raw:
                sectors_raw.append(s)
    tickers = [t for t in (raw.get("tickers") or []) if t]

    return {
        "date": raw.get("date") or date_str,
        "stance": raw.get("stance"),
        # 현금비중 — 그의 최종 출력이자 우리 cashRecommendation 과 직접 비교 가능
        "cashStart": kr_cash.get("start"),
        "cashEnd": kr_cash.get("end"),
        # 언급 섹터 — 원문과 정규화본을 함께 보관 (사전 누락 진단에 원문이 필요)
        "sectorsRaw": sectors_raw,
        "sectors": [normalize_sector(s) for s in sectors_raw],
        "tickers": tickers,
        "messageCount": raw.get("message_count"),
    }


def staleness_days() -> int | None:
    """레퍼런스 최신 데이터가 며칠 묵었는지. 없으면 None.

    수집이 조용히 멈추면 낡은 데이터로 계속 대조하게 된다. 실제로 채널
    초대 링크가 만료돼 6일치를 날리고도 아무도 몰랐다. 대조할 때마다
    먼저 이걸 보고, 오래됐으면 결론을 내기 전에 수집부터 다시 돌린다.
    """
    dates = available_dates()
    if not dates:
        return None
    from datetime import date
    y, m, d = (int(x) for x in dates[-1].split("-"))
    return (date.today() - date(y, m, d)).days


def available_dates() -> list[str]:
    d = daily_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
