"""AWS Lambda handler — 차트 데이터 REST API.

GET /chart?code=005930&timeframe=day&count=200

API Gateway (REST/HTTP) 또는 Lambda Function URL 양쪽에서 호출 가능.

Event shape:
    - Function URL / API Gateway v2: queryStringParameters, requestContext.http.method
    - API Gateway v1 (REST): queryStringParameters, httpMethod

Response (statusCode/body/headers) 형식은 둘 다 호환.

Phase 2 시:
    - 같은 HTTP 엔드포인트는 lightweight snapshot polling 으로 유지 (구형 클라이언트 backup).
    - 새 WebSocket 엔드포인트를 별도 추가 (ECS Fargate 위에서 운영).
    - 두 엔드포인트가 같은 chart.service.get_chart() 를 공유 → 일관성 확보.
"""

from __future__ import annotations

import json
import os
import traceback
from typing import Any

from chart.service import get_chart
from chart.naver import TIMEFRAMES

# ── CORS ─────────────────────────────────────
# GitHub Pages (https://*.github.io) 와 로컬 dev (http://localhost:*) 에서 호출.
# 환경변수로 좁힐 수 있게 함. 기본은 *.
ALLOWED_ORIGIN = os.environ.get("CHART_CORS_ORIGIN", "*")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "3600",
}


def _resp(status: int, body: Any, *, extra_headers: dict | None = None) -> dict:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "max-age=15",  # 브라우저 측 짧은 캐시 (분봉 30s TTL 보다 짧음)
        **CORS_HEADERS,
    }
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _get_method(event: dict) -> str:
    """v1/v2 양쪽 지원."""
    if "requestContext" in event and isinstance(event["requestContext"], dict):
        http = event["requestContext"].get("http")
        if isinstance(http, dict) and "method" in http:
            return str(http["method"]).upper()
    return str(event.get("httpMethod", "GET")).upper()


def _get_query(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def lambda_handler(event, context):
    """Function URL / API Gateway entry point."""
    try:
        method = _get_method(event)

        if method == "OPTIONS":
            # Preflight
            return _resp(200, {"ok": True})

        if method != "GET":
            return _resp(405, {"error": f"Method {method} not allowed"})

        params = _get_query(event)
        code = (params.get("code") or "").strip()
        timeframe = (params.get("timeframe") or "day").strip().lower()
        count_raw = params.get("count")

        if not code:
            return _resp(400, {"error": "Missing required parameter: code"})

        # Validation 은 service/naver 가 하지만, 메시지 사용자친화 위해 미리 검사.
        if timeframe not in TIMEFRAMES:
            return _resp(400, {
                "error": f"Invalid timeframe: {timeframe!r}",
                "allowed": list(TIMEFRAMES.keys()),
            })

        count = None
        if count_raw:
            try:
                count = int(count_raw)
            except ValueError:
                return _resp(400, {"error": f"Invalid count: {count_raw!r}"})

        try:
            payload = get_chart(code=code, timeframe=timeframe, count=count)
        except ValueError as ve:
            return _resp(400, {"error": str(ve)})
        except RuntimeError as re_:
            # Naver fetch 실패 — 일시적 문제일 수 있으니 502 로
            return _resp(502, {"error": str(re_)})

        return _resp(200, payload)

    except Exception as e:
        print(f"[FATAL] chart_handler: {traceback.format_exc()}")
        return _resp(500, {"error": "Internal error", "detail": str(e)[:200]})


# 로컬 단독 테스트
if __name__ == "__main__":
    mock_event = {
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": {"code": "005930", "timeframe": "day", "count": "5"},
    }
    os.environ.setdefault("CHART_CACHE_BACKEND", "memory")
    result = lambda_handler(mock_event, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
