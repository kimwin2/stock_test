"""
ChatGPT API를 이용한 뉴스 테마 분석 모듈
- 크롤링된 기사들을 분석하여 테마 7개를 추출합니다.
- 각 테마별 요약과 관련 종목을 반환합니다.
"""
from __future__ import annotations

import sys
import io
import json
import os
from openai import OpenAI
from dotenv import load_dotenv

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()


def get_openai_client() -> OpenAI:
    """OpenAI 클라이언트를 생성합니다."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY 환경변수가 설정되지 않았습니다.\n"
            ".env 파일에 OPENAI_API_KEY=sk-... 형태로 설정해주세요."
        )
    return OpenAI(api_key=api_key)


SYSTEM_PROMPT = """당신은 한국 주식시장 전문 애널리스트입니다. 단타 트레이딩에 특화되어 있으며, 
뉴스를 분석하여 당일 주도 테마를 정확히 파악하는 능력이 뛰어납니다.

오늘 수집된 증권 뉴스 기사들을 분석하여 다음을 수행하세요:

1. **테마 7개 추출**: 오늘 가장 주목받는 투자 테마 7개를 선정합니다.
   - 기사에서 반복적으로 언급되는 섹터/이슈를 테마로 선정
   - 거래대금이 몰릴 만한 핫한 테마를 우선 선정
   
2. **각 테마별 정보**:
   - themeName: 테마명 (간결하게, 예: "광통신", "반도체소부장", "중동전쟁", "방산")
   - headline: 테마 관련 핵심 뉴스 한줄 요약 (기사 제목 스타일, 50자 이내)
   - representativeArticleIndex: 이 테마를 가장 잘 대표하는 기사 번호 (1부터 시작하는 정수)
   - relatedStocks: 해당 테마의 대장주 후보 종목명 6개 (한국 상장종목만, 정확한 종목명)
   - reasoning: 이 테마를 선정한 이유 (1-2문장)

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요."""

USER_PROMPT_TEMPLATE = """아래는 오늘({date}) 수집된 증권 뉴스 기사 {count}개입니다. 
분석하여 오늘의 주도 테마 7개를 추출해주세요.

=== 뉴스 기사 목록 ===
{articles_text}

=== 응답 형식 (JSON) ===
{{
  "themes": [
    {{
      "themeName": "테마명",
      "headline": "핵심 뉴스 한줄 요약",
      "representativeArticleIndex": 1,
      "relatedStocks": ["종목1", "종목2", "종목3", "종목4", "종목5", "종목6"],
      "reasoning": "선정 이유"
    }}
  ]
}}"""


def format_articles_for_prompt(articles: list[dict]) -> str:
    """기사 리스트를 프롬프트에 넣을 텍스트로 변환합니다."""
    lines = []
    for i, article in enumerate(articles, 1):
        title = article.get("title", "").strip()
        summary = article.get("summary", "").strip()
        if summary:
            lines.append(f"{i}. [{title}] {summary}")
        else:
            lines.append(f"{i}. {title}")
    return "\n".join(lines)


def analyze_themes(articles: list[dict], date_str: str = None) -> dict:
    """
    ChatGPT API를 사용하여 기사들에서 테마를 추출합니다.

    Args:
        articles: 크롤링된 기사 리스트
        date_str: 날짜 문자열 (예: "2026-04-10")

    Returns:
        {
            "themes": [
                {
                    "themeName": str,
                    "headline": str,
                    "relatedStocks": [str, ...],
                    "reasoning": str
                }, ...
            ]
        }
    """
    if not date_str:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")

    client = get_openai_client()

    articles_text = format_articles_for_prompt(articles)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        count=len(articles),
        articles_text=articles_text,
    )

    print(f"[INFO] ChatGPT API 호출 중... (기사 {len(articles)}개 분석)")
    print(f"  [>] 프롬프트 길이: {len(user_prompt):,}자")

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # 일관성 높게
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        result_text = response.choices[0].message.content
        print(f"  [OK] ChatGPT 응답 수신 완료")
        print(f"  [>] 토큰 사용: input={response.usage.prompt_tokens}, output={response.usage.completion_tokens}")

        result = json.loads(result_text)

        # 검증: themes 키 존재 및 5개인지
        if "themes" not in result:
            raise ValueError("응답에 'themes' 키가 없습니다.")

        themes = result["themes"]
        if len(themes) < 7:
            print(f"  [!] 테마가 {len(themes)}개만 추출되었습니다 (목표: 7개)")

        # 각 테마 검증 및 대표 기사 URL 매핑
        for theme in themes:
            if "themeName" not in theme:
                raise ValueError(f"테마에 'themeName'이 없습니다: {theme}")
            if "relatedStocks" not in theme or len(theme["relatedStocks"]) < 4:
                print(f"  [!] 테마 '{theme.get('themeName')}'의 관련 종목이 부족합니다.")

            # 대표 기사 URL 매핑
            article_idx = theme.get("representativeArticleIndex", 1)
            if isinstance(article_idx, int) and 1 <= article_idx <= len(articles):
                theme["headlineUrl"] = articles[article_idx - 1].get("url", "")
            else:
                # 못 찾으면 첫 번째 기사 URL
                theme["headlineUrl"] = articles[0].get("url", "") if articles else ""

        print(f"\n[INFO] 추출된 테마:")
        for i, theme in enumerate(themes, 1):
            stocks_str = ", ".join(theme.get("relatedStocks", [])[:4])
            print(f"  {i}. {theme['themeName']}: {theme.get('headline', '')} → [{stocks_str}]")

        return result

    except json.JSONDecodeError as e:
        print(f"[ERROR] ChatGPT 응답 JSON 파싱 실패: {e}")
        print(f"  원본 응답: {result_text[:500]}")
        raise
    except Exception as e:
        print(f"[ERROR] ChatGPT API 호출 실패: {e}")
        raise


def save_analysis(analysis: dict, filepath: str = None) -> str:
    """분석 결과를 JSON 파일로 저장합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "theme_analysis.json")

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 테마 분석 결과를 {filepath}에 저장했습니다.")
    return filepath


def load_analysis(filepath: str = None) -> dict:
    """저장된 분석 결과를 로드합니다."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "theme_analysis.json")

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # 테스트: 저장된 기사 파일이 있으면 분석
    from crawler import load_articles
    articles = load_articles()
    if articles:
        result = analyze_themes(articles)
        save_analysis(result)
    else:
        print("크롤링된 기사가 없습니다. 먼저 crawler.py를 실행하세요.")
