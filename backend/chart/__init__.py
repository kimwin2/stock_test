"""Chart data module — Naver fchart fetch + cache + API.

Phase 1 (이 모듈):
    REST 엔드포인트가 종목 차트(분/일/주봉)를 요청 시점에 Naver 에서 fetch.
    S3 에 TTL 기반 캐시. 같은 종목·timeframe 으로 다수 사용자 요청이 오면
    캐시에서 즉시 응답.

Phase 2 (확장):
    동일한 API 응답 shape 을 WebSocket snapshot/update 으로 그대로 전달.
    CacheBackend 인터페이스를 S3 → Redis 로 교체하면 fan-out 워커 구조로
    이행 가능. naver.py 의 fetch 로직은 그대로 재사용.
"""

from .naver import fetch_candles, TIMEFRAMES
from .cache import CacheBackend, S3CacheBackend, MemoryCacheBackend
from .service import get_chart

__all__ = [
    "fetch_candles",
    "TIMEFRAMES",
    "CacheBackend",
    "S3CacheBackend",
    "MemoryCacheBackend",
    "get_chart",
]
