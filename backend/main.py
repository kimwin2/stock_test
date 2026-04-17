"""
메인 파이프라인
네이버 금융 뉴스 크롤링 → ChatGPT 테마 분석 → 종목 데이터 조회 → JSON 출력

사용법:
    python main.py              # 전체 파이프라인 실행
    python main.py --crawl-only # 크롤링만 실행
    python main.py --skip-crawl # 크롤링 건너뛰고 저장된 기사 사용
"""

import json
import os
import sys
import io
import argparse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

try:
    from crawler import crawl_naver_finance_news_with_fallback, save_articles, load_articles
    from analyzer import analyze_themes, save_analysis, load_analysis
    from stock_data import get_stock_details_for_themes
except ModuleNotFoundError:
    from .crawler import crawl_naver_finance_news_with_fallback, save_articles, load_articles
    from .analyzer import analyze_themes, save_analysis, load_analysis
    from .stock_data import get_stock_details_for_themes


BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
OUTPUT_FILE = os.path.join(PROJECT_DIR, "frontend", "dashboard_data.json")


def run_pipeline(skip_crawl: bool = False, crawl_only: bool = False, skip_analysis: bool = False):
    """
    전체 파이프라인을 실행합니다.

    Args:
        skip_crawl: True이면 크롤링을 건너뛰고 저장된 기사 사용
        crawl_only: True이면 크롤링만 실행
        skip_analysis: True이면 분석을 건너뛰고 저장된 분석 결과 사용
    """
    print("=" * 60)
    print(">>> Stock Backend Pipeline Start")
    print(f"   시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ─────────────────────────────────────────────
    # Step 1: 뉴스 크롤링
    # ─────────────────────────────────────────────
    if skip_crawl:
        print("\n[Step 1] 크롤링 건너뛰 (저장된 기사 사용)")
        articles = load_articles()
        print(f"   로드된 기사: {len(articles)}개")
    else:
        print("\n[Step 1] 네이버 금융 뉴스 크롤링")
        articles = crawl_naver_finance_news_with_fallback(200)

        save_articles(articles)

    if not articles:
        print("\n[X] 기사를 수집하지 못했습니다. 프로그램을 종료합니다.")
        sys.exit(1)

    if crawl_only:
        print(f"\n[OK] 크롤링 완료: {len(articles)}개 기사")
        return

    # ─────────────────────────────────────────────
    # Step 2: ChatGPT API 테마 분석
    # ─────────────────────────────────────────────
    if skip_analysis:
        print("\n[Step 2] 분석 건너뛰 (저장된 분석 결과 사용)")
        analysis = load_analysis()
    else:
        print("\n[Step 2] ChatGPT API 테마 분석")
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        analysis = analyze_themes(articles, date_str)
        save_analysis(analysis)

    themes = analysis.get("themes", [])
    if not themes:
        print("\n[X] 테마를 추출하지 못했습니다. 프로그램을 종료합니다.")
        sys.exit(1)

    # ─────────────────────────────────────────────
    # Step 3: 종목 데이터 조회
    # ─────────────────────────────────────────────
    print("\n[Step 3] 테마별 종목 데이터 조회")
    completed_themes = get_stock_details_for_themes(themes)

    # ─────────────────────────────────────────────
    # Step 4: 최종 JSON 조립 및 저장
    # ─────────────────────────────────────────────
    print("\n[Step 4] 최종 JSON 조립")
    dashboard_data = {
        "updatedAt": datetime.now(KST).isoformat(),
        "antwinnerSignals": analysis.get("antwinnerSignals", []),
        "youtubeSignals": analysis.get("youtubeSignals", []),
        "telegramSignals": analysis.get("telegramSignals", []),
        "priceSignalCandidates": analysis.get("priceSignalCandidates", []),
        "themes": completed_themes,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 최종 데이터를 {OUTPUT_FILE}에 저장했습니다.")

    # 결과 요약
    print("\n" + "=" * 60)
    print("=== Pipeline Result Summary ===")
    print("=" * 60)
    print(f"   수집 기사: {len(articles)}개")
    print(f"   추출 테마: {len(completed_themes)}개")
    for theme in completed_themes:
        stock_count = len(theme.get("stocks", []))
        print(f"     • {theme['themeName']} ({theme['totalVolume']}) - {stock_count}개 종목")
        for stock in theme.get("stocks", []):
            marker = "[*]" if stock.get("isTop") else "   "
            print(f"       {marker} {stock['name']}: {stock['price']:,}원 ({stock['changeRate']:+.2f}%) {stock['volume']}")
    print(f"\n   출력 파일: {OUTPUT_FILE}")
    print("=" * 60)

    return dashboard_data


def main():
    parser = argparse.ArgumentParser(description="Stock 백엔드 파이프라인")
    parser.add_argument("--skip-crawl", action="store_true", help="크롤링 건너뛰기 (저장된 기사 사용)")
    parser.add_argument("--crawl-only", action="store_true", help="크롤링만 실행")
    parser.add_argument("--skip-analysis", action="store_true", help="분석 건너뛰기 (저장된 분석 결과 사용)")
    args = parser.parse_args()

    run_pipeline(
        skip_crawl=args.skip_crawl,
        crawl_only=args.crawl_only,
        skip_analysis=args.skip_analysis,
    )


if __name__ == "__main__":
    main()
