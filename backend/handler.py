"""
AWS Lambda Handler for Stock Dashboard Backend Pipeline

POST /run (수동 실행) → 이 함수 실행 → S3에 dashboard_data.json 업로드

환경변수:
    - OPENAI_API_KEY: OpenAI API 키
    - S3_BUCKET_NAME: S3 버킷 이름 (예: stock-dashboard-data)
    - S3_KEY: S3 객체 키 (기본값: dashboard_data.json)
"""

import json
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

# 모듈 임포트
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

    json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

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


def lambda_handler(event, context):
    """
    AWS Lambda 메인 핸들러.
    EventBridge 또는 수동 호출로 실행됩니다.
    """
    print("=" * 60)
    print(">>> Stock Lambda Pipeline Start")
    print(f"    시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"    이벤트: {json.dumps(event, default=str)[:200]}")
    print("=" * 60)

    bucket = os.environ.get("S3_BUCKET_NAME", "stock-dashboard-data")
    s3_key = os.environ.get("S3_KEY", "dashboard_data.json")

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
        analysis = analyze_themes(articles, date_str)
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
