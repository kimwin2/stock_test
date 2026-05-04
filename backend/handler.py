"""
AWS Lambda Handler for Stock Dashboard Backend Pipeline

POST /run (수동 실행) → 이 함수 실행 → S3에 dashboard_data.json 업로드

환경변수:
    - OPENAI_API_KEY: OpenAI API 키
    - S3_BUCKET_NAME: S3 버킷 이름 (예: stock-dashboard-data)
    - S3_KEY: S3 객체 키 (기본값: dashboard_data.json)
"""

import json
import math
import os
import sys
import io
import traceback
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

import boto3

# Lambda 환경에서 UTF-8 강제
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 모듈 임포트 — flow_signals 는 mode=flow 호출 시점에만 lazy import.
# 이유: pandas/FinanceDataReader 등 무거운 의존성이 theme 모드 cold start 까지
# 끌고 들어와 import 실패 시 theme 파이프라인까지 죽이는 것을 방지.
try:
    from crawler import crawl_naver_finance_news_with_fallback
    from analyzer import analyze_themes
    from price_signals.collector import collect_price_theme_signals
    from price_signals.store import save_price_signal_payload
    from stock_data import get_stock_details_for_themes
except ModuleNotFoundError:
    from .crawler import crawl_naver_finance_news_with_fallback
    from .analyzer import analyze_themes
    from .price_signals.collector import collect_price_theme_signals
    from .price_signals.store import save_price_signal_payload
    from .stock_data import get_stock_details_for_themes


def _import_flow_pipeline():
    """flow_signals.pipeline.build_flow_dashboard 지연 임포트."""
    try:
        from flow_signals.pipeline import build_flow_dashboard
    except ModuleNotFoundError:
        from .flow_signals.pipeline import build_flow_dashboard
    return build_flow_dashboard


def _sanitize_for_json(obj):
    """Infinity / NaN 을 null 로 변환 (브라우저 JSON.parse 호환)."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _fetch_existing_dashboard(bucket: str, key: str) -> dict | None:
    """S3 에서 기존 dashboard_data.json 을 조회. 없거나 파싱 실패 시 None."""
    try:
        s3 = boto3.client("s3")
        resp = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"  [!] 기존 dashboard 조회 실패: {e}")
        return None


def _is_openai_quota_error(exc: BaseException) -> bool:
    """OpenAI quota/rate 한도 에러 여부."""
    try:
        from openai import APIStatusError, RateLimitError
    except Exception:
        return False
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIStatusError) and getattr(exc, "status_code", None) in (429, 402):
        return True
    msg = str(exc).lower()
    return "insufficient_quota" in msg or "exceeded your current quota" in msg


def upload_to_s3(data: dict, bucket: str, key: str) -> str:
    """
    dashboard_data.json을 S3에 업로드합니다.

    Args:
        data: 대시보드 데이터 딕셔너리
        bucket: S3 버킷 이름
        key: S3 객체 키

    Returns:
        S3 URL 문자열
    """
    s3 = boto3.client("s3")

    sanitized = _sanitize_for_json(data)
    json_bytes = json.dumps(
        sanitized, ensure_ascii=False, indent=2, allow_nan=False
    ).encode("utf-8")

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json_bytes,
        ContentType="application/json; charset=utf-8",
        CacheControl="max-age=300",  # 5분 캐시
    )

    url = f"https://{bucket}.s3.ap-northeast-2.amazonaws.com/{key}"
    print(f"[S3] 업로드 완료: {url}")
    return url


def _run_flow_pipeline(bucket: str) -> dict:
    """수급/주도 파이프라인. EventBridge에서 mode=flow 로 호출."""
    flow_key = os.environ.get("FLOW_S3_KEY", "flow_dashboard.json")
    top_kospi = int(os.environ.get("FLOW_TOP_KOSPI", "300"))
    top_kosdaq = int(os.environ.get("FLOW_TOP_KOSDAQ", "150"))

    build_flow_dashboard = _import_flow_pipeline()
    payload = build_flow_dashboard(top_n_kospi=top_kospi, top_n_kosdaq=top_kosdaq)
    flow_url = upload_to_s3(payload, bucket, flow_key)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "flow pipeline 성공",
                "leadingSectors": payload.get("leadingSectorLabels"),
                "vacancyAnalyzed": payload.get("vacancyAnalyzed"),
                "elapsedSeconds": payload.get("elapsedSeconds"),
                "s3_url": flow_url,
                "updatedAt": payload["updatedAt"],
            },
            ensure_ascii=False,
        ),
    }


def lambda_handler(event, context):
    """
    AWS Lambda 메인 핸들러.
    EventBridge 또는 수동 호출로 실행됩니다.

    event.mode:
        - "themes" (기본): 뉴스/테마 파이프라인 → dashboard_data.json
        - "flow": 수급/주도 파이프라인 → flow_dashboard.json
    """
    print("=" * 60)
    print(">>> Stock Lambda Pipeline Start")
    print(f"    시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"    이벤트: {json.dumps(event, default=str)[:200]}")
    print("=" * 60)

    bucket = os.environ.get("S3_BUCKET_NAME", "stock-dashboard-data")
    s3_key = os.environ.get("S3_KEY", "dashboard_data.json")
    mode = (event or {}).get("mode") or "themes"

    if mode == "flow":
        try:
            return _run_flow_pipeline(bucket)
        except Exception as e:
            error_msg = traceback.format_exc()
            print(f"[FATAL] flow pipeline 실패:\n{error_msg}")
            return {"statusCode": 500, "body": json.dumps({"error": str(e), "traceback": error_msg})}

    try:
        # ── Step 1: 뉴스 크롤링 ──
        print("\n[Step 1] 네이버 금융 뉴스 크롤링")
        articles = crawl_naver_finance_news_with_fallback(200)

        if not articles:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "기사를 수집하지 못했습니다."})
            }

        print(f"  [OK] 수집된 기사: {len(articles)}개")

        # ── Step 2: 가격 기반 테마 시그널 수집 ──
        print("\n[Step 2] 가격 기반 테마 시그널 수집")
        try:
            price_signal_payload = collect_price_theme_signals(articles=articles)
            price_signal_target = save_price_signal_payload(price_signal_payload)
            print(
                "  [OK] 가격 기반 테마 시그널 저장: "
                f"{price_signal_target} (후보 {len(price_signal_payload.get('candidates', []))}개)"
            )
        except Exception as e:
            print(f"  [!] 가격 기반 테마 시그널 수집 실패: {e}")

        # ── Step 3: ChatGPT 테마 분석 ──
        print("\n[Step 3] ChatGPT API 테마 분석")
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        try:
            analysis = analyze_themes(articles, date_str)
        except Exception as e:
            if _is_openai_quota_error(e):
                print(f"  [!] OpenAI 한도 초과 — 기존 dashboard 보존하고 updatedAt 만 갱신: {e}")
                existing = _fetch_existing_dashboard(bucket, s3_key) or {}
                degraded = dict(existing)
                degraded["updatedAt"] = datetime.now(KST).isoformat()
                degraded["themesError"] = "OpenAI quota exceeded — themes not refreshed (billing 확인 필요)"
                degraded["themesErrorDetail"] = str(e)[:500]
                upload_to_s3(degraded, bucket, s3_key)
                return {
                    "statusCode": 200,
                    "body": json.dumps({
                        "message": "themes 갱신 스킵 (OpenAI 한도) — 기존 데이터 보존",
                        "themesError": degraded["themesError"],
                        "updatedAt": degraded["updatedAt"],
                    }, ensure_ascii=False),
                }
            raise

        themes = analysis.get("themes", [])

        if not themes:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "테마를 추출하지 못했습니다."})
            }

        # ── Step 4: 종목 데이터 조회 ──
        print("\n[Step 4] 테마별 종목 데이터 조회")
        completed_themes = get_stock_details_for_themes(themes)

        # ── Step 5: JSON 조립 및 S3 업로드 ──
        print("\n[Step 5] JSON 조립 및 S3 업로드")
        dashboard_data = {
            "updatedAt": datetime.now(KST).isoformat(),
            "antwinnerSignals": analysis.get("antwinnerSignals", []),
            "infostockSignals": analysis.get("infostockSignals", []),
            "youtubeSignals": analysis.get("youtubeSignals", []),
            "wownetSignals": analysis.get("wownetSignals", []),
            "telegramSignals": analysis.get("telegramSignals", []),
            "priceSignalCandidates": analysis.get("priceSignalCandidates", []),
            "themes": completed_themes,
        }

        s3_url = upload_to_s3(dashboard_data, bucket, s3_key)

        # 결과 요약
        theme_count = len(completed_themes)
        stock_count = sum(len(t.get("stocks", [])) for t in completed_themes)

        summary = {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Pipeline 성공",
                "articles": len(articles),
                "themes": theme_count,
                "stocks": stock_count,
                "s3_url": s3_url,
                "updatedAt": dashboard_data["updatedAt"],
            }, ensure_ascii=False)
        }

        print(f"\n[OK] Pipeline 완료: {theme_count}개 테마, {stock_count}개 종목")
        return summary

    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"\n[FATAL] Pipeline 실패:\n{error_msg}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "traceback": error_msg,
            })
        }


# 로컬 테스트용
if __name__ == "__main__":
    # 로컬에서 테스트할 때는 .env 파일의 환경변수 사용
    from dotenv import load_dotenv
    load_dotenv()

    result = lambda_handler({"source": "local-test"}, None)
    print("\n=== Lambda Response ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
