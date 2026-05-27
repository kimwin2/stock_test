"""Cache backend abstraction for chart data.

Phase 1: S3 backend.
Phase 2 마이그레이션: 같은 인터페이스로 RedisCacheBackend 만 추가하면 됨.
service.get_chart() 의 코드는 그대로 둠.

각 캐시 값은 {data: dict, cached_at: ISO8601-str} 형태로 보관.
TTL 판정은 호출자(service.py)가 cached_at + 자체 정책으로 수행 — 즉
백엔드는 "마지막에 언제 썼는지" 만 알려주고, "신선한가?" 는 service 가 판단.
이렇게 분리해야 분봉/일봉 별로 TTL 을 다르게 적용 가능.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional

KST = timezone(timedelta(hours=9))


class CacheBackend(ABC):
    """공통 인터페이스. 구현체는 S3/Memory/Redis 등."""

    @abstractmethod
    def get(self, key: str) -> Optional[dict]:
        """캐시 hit 시 {"data": ..., "cached_at": ISO8601-str} 반환.
        miss 또는 손상된 데이터는 None."""

    @abstractmethod
    def set(self, key: str, data: dict) -> None:
        """캐시에 저장. cached_at 은 지금 시각으로 자동 기록."""


class MemoryCacheBackend(CacheBackend):
    """프로세스 메모리 내 단순 dict. 로컬 dev 와 Lambda warm 동안만 유효."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            return dict(entry)  # shallow copy

    def set(self, key: str, data: dict) -> None:
        with self._lock:
            self._store[key] = {
                "data": data,
                "cached_at": datetime.now(KST).isoformat(),
            }


class S3CacheBackend(CacheBackend):
    """S3 객체 단위 캐시.

    key → s3://{bucket}/{prefix}{key}.json
    cached_at 은 객체 본문에 함께 저장 (S3 LastModified 는 region/clock skew
    이슈 있어 자체 timestamp 사용).
    """

    def __init__(self, bucket: str, prefix: str = "chart/") -> None:
        import boto3  # lazy import (로컬 dev 에서 boto3 미설치여도 Memory 만 쓰면 됨)

        self._s3 = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix.lstrip("/").rstrip("/") + "/"

    def _object_key(self, key: str) -> str:
        # 키에 '/' 가 들어가도 OK (예: "005930/day"). 안전 문자만 허용.
        safe = key.replace("..", "").strip("/")
        return f"{self._prefix}{safe}.json"

    def get(self, key: str) -> Optional[dict]:
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._object_key(key))
            body = obj["Body"].read().decode("utf-8")
            payload = json.loads(body)
            # 형식 검증
            if not isinstance(payload, dict) or "data" not in payload or "cached_at" not in payload:
                return None
            return payload
        except self._s3.exceptions.NoSuchKey:
            return None
        except Exception as e:
            # 캐시 미스 처럼 처리 — 에러는 호출자가 fresh fetch 로 우회
            print(f"  [cache] S3 get failed for {key}: {e}")
            return None

    def set(self, key: str, data: dict) -> None:
        payload = {
            "data": data,
            "cached_at": datetime.now(KST).isoformat(),
        }
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self._object_key(key),
                Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json; charset=utf-8",
                # CloudFront/브라우저 캐시는 짧게 — 신선도 판단은 우리가 함
                CacheControl="max-age=30",
            )
        except Exception as e:
            # 캐시 쓰기 실패는 치명적이지 않음. 다음 요청에서 다시 시도.
            print(f"  [cache] S3 put failed for {key}: {e}")


def default_backend() -> CacheBackend:
    """환경변수로 backend 선택.

    CHART_CACHE_BACKEND=s3 (기본): S3CacheBackend, bucket=S3_BUCKET_NAME
    CHART_CACHE_BACKEND=memory: MemoryCacheBackend (로컬 dev)
    """
    backend = os.environ.get("CHART_CACHE_BACKEND", "s3").lower()
    if backend == "memory":
        return MemoryCacheBackend()
    if backend == "s3":
        bucket = os.environ.get("S3_BUCKET_NAME", "stock-dashboard-data")
        prefix = os.environ.get("CHART_CACHE_PREFIX", "chart/")
        return S3CacheBackend(bucket=bucket, prefix=prefix)
    raise ValueError(f"Unknown CHART_CACHE_BACKEND: {backend!r}")
