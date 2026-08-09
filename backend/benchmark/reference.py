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
}

# 개별 테마가 아니라 분류 체계 상위어. 매매 대상이 아니므로 대조에서 제외한다.
# 이런 단어를 '사전 누락' 으로 세면 SECTOR_RULES 에 잡동사니를 추가하게 된다.
UMBRELLA_TERMS = {"산업재", "경기소비재", "필수소비재", "소재", "IT", "성장주", "가치주", "대형주", "중소형주"}


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
    sectors_raw = [
        s for s in (raw.get("sectors") or [])
        if s and re.sub(r"\s+", "", s) not in UMBRELLA_TERMS
    ]
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


def available_dates() -> list[str]:
    d = daily_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
