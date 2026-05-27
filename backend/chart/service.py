"""Chart service — cache + Naver fetch + TTL 정책.

이 모듈이 Phase 1 의 핵심. Lambda 핸들러와 로컬 dev 서버 양쪽에서 동일하게 사용.

TTL 정책:
    - 분봉 (minute):    30초    (장중에만 의미. 장외엔 어차피 변하지 않음)
    - 일봉 (day):       장중 60초, 장외 6시간
    - 주봉 (week):      장중 5분, 장외 12시간
    - 월봉 (month):     24시간

장중 = KST 09:00~15:30 평일.

Phase 2 진입 시 변경점:
    - CacheBackend 를 RedisCacheBackend 로 swap (default_backend() 수정).
    - 백엔드 워커가 인기 종목 분봉을 push 로 갱신 (이 서비스는 폴링형 그대로 두고
      별도 워커 추가). 그 경우 이 함수의 TTL 은 한결 짧아질 수 있음.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional

from .naver import fetch_candles, TIMEFRAMES
from .cache import CacheBackend, default_backend

KST = timezone(timedelta(hours=9))

# 장중 시간 (KST). 09:00 시가, 15:30 종가 (동시호가 별도지만 일단 간단히).
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 30)


def _is_market_open(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(KST)
    if now.weekday() >= 5:  # 토(5)/일(6)
        return False
    t = now.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def _ttl_seconds(timeframe: str) -> int:
    """현재 시각 기준 timeframe 별 캐시 신선도(초)."""
    market_open = _is_market_open()
    if timeframe == "minute":
        return 30 if market_open else 3600  # 장외엔 1시간(데이터 변화 없음)
    if timeframe == "day":
        return 60 if market_open else 6 * 3600
    if timeframe == "week":
        return 5 * 60 if market_open else 12 * 3600
    if timeframe == "month":
        return 24 * 3600
    return 60


def _is_fresh(cached_at_iso: str, ttl_seconds: int) -> bool:
    try:
        cached_at = datetime.fromisoformat(cached_at_iso)
    except ValueError:
        return False
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=KST)
    age = (datetime.now(KST) - cached_at).total_seconds()
    return age <= ttl_seconds


# 모듈 레벨 singleton — Lambda warm 컨테이너 재사용
_backend_singleton: Optional[CacheBackend] = None


def _get_backend() -> CacheBackend:
    global _backend_singleton
    if _backend_singleton is None:
        _backend_singleton = default_backend()
    return _backend_singleton


def get_chart(
    code: str,
    timeframe: str = "day",
    count: Optional[int] = None,
    force_refresh: bool = False,
) -> dict:
    """캐시 우선 조회, miss/만료 시 Naver fetch.

    Returns 응답 형태 (Phase 2 WebSocket snapshot 과 동일 shape):
        {
            "code": "005930",
            "name": "삼성전자",
            "timeframe": "day",
            "candles": [{t, o, h, l, c, v}, ...],
            "updatedAt": ISO8601-str,    # 캐시 fresh fetch 시각
            "fromCache": bool,           # 디버그용
            "ttlSeconds": int,           # 이 응답이 유효한 잔여 시간 힌트
        }
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe!r}")

    backend = _get_backend()
    cache_key = f"{code}/{timeframe}"
    ttl = _ttl_seconds(timeframe)

    if not force_refresh:
        cached = backend.get(cache_key)
        if cached and _is_fresh(cached["cached_at"], ttl):
            data = cached["data"]
            return {
                **data,
                "updatedAt": cached["cached_at"],
                "fromCache": True,
                "ttlSeconds": ttl,
            }

    # cache miss or stale → fetch fresh
    fresh = fetch_candles(code=code, timeframe=timeframe, count=count)
    backend.set(cache_key, fresh)
    return {
        **fresh,
        "updatedAt": datetime.now(KST).isoformat(),
        "fromCache": False,
        "ttlSeconds": ttl,
    }
