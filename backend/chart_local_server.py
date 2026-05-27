"""로컬 개발용 HTTP 서버 — Lambda chart_handler 와 동일 로직을 :8081 에 노출.

사용:
    cd backend && python3 chart_local_server.py
    → http://127.0.0.1:8081/chart?code=005930&timeframe=day

frontend/chart.js 가 localhost 호스트일 때 이 주소로 요청. 프로덕션은 Lambda URL.

기본적으로 MemoryCacheBackend 사용 (S3 자격 없는 환경에서 동작 보장).
S3 캐시를 로컬에서도 쓰려면 AWS_PROFILE 설정 후 CHART_CACHE_BACKEND=s3 으로 실행.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 로컬 dev 기본값
os.environ.setdefault("CHART_CACHE_BACKEND", "memory")

from chart_handler import lambda_handler


class _Handler(BaseHTTPRequestHandler):
    def _serve(self, method: str) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/chart", "/"):
            self.send_response(404)
            self.end_headers()
            return

        # query: parse_qs returns lists → 첫 값만
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        event = {
            "requestContext": {"http": {"method": method}},
            "queryStringParameters": qs,
        }
        result = lambda_handler(event, None)
        status = result.get("statusCode", 200)
        headers = result.get("headers", {})
        body = result.get("body", "")

        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body.encode("utf-8") if isinstance(body, str) else body)

    def do_GET(self):  # noqa: N802
        self._serve("GET")

    def do_OPTIONS(self):  # noqa: N802
        self._serve("OPTIONS")

    def log_message(self, fmt: str, *args) -> None:  # 짧은 로그
        print(f"  [chart-dev] {self.address_string()} - {fmt % args}")


def main() -> None:
    host = os.environ.get("CHART_DEV_HOST", "127.0.0.1")
    port = int(os.environ.get("CHART_DEV_PORT", "8081"))
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"[chart-dev] listening on http://{host}:{port}")
    print(f"[chart-dev] backend = {os.environ.get('CHART_CACHE_BACKEND')}")
    print(f"[chart-dev] try: curl 'http://{host}:{port}/chart?code=005930&timeframe=day&count=10'")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[chart-dev] stopping")
        server.server_close()


if __name__ == "__main__":
    main()
