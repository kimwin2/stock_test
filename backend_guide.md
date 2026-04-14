# Backend Guide

이 문서는 현재 프로젝트의 백엔드 구성과 각 모듈의 역할만 빠르게 파악하기 위한 요약본입니다.

## 백엔드가 하는 일

백엔드는 매 실행마다 아래 흐름으로 대시보드 데이터를 만듭니다.

1. 네이버 금융 뉴스 약 200건을 수집합니다.
2. 수집한 기사와 유튜브 보조 시그널을 바탕으로 ChatGPT가 당일 핵심 테마 7개를 추출합니다.
3. 각 테마의 관련 종목 시세를 조회해 프런트에서 바로 쓸 수 있는 형태로 가공합니다.
4. 최종 결과를 JSON으로 저장하거나 S3에 업로드합니다.

## 구성과 역할

| 경로 | 역할 |
|------|------|
| `backend/main.py` | 로컬 실행 진입점입니다. 크롤링, 분석, 시세 조회를 순서대로 실행하고 `frontend/dashboard_data.json`을 생성합니다. |
| `backend/handler.py` | AWS Lambda 진입점입니다. `main.py`와 같은 파이프라인을 돌린 뒤 결과를 S3에 업로드합니다. |
| `backend/crawler.py` | 네이버 금융 뉴스를 수집합니다. 메인 뉴스가 부족하면 뉴스 리스트로 보충합니다. 기사 캐시는 `crawled_articles.json`에 저장합니다. |
| `backend/analyzer.py` | 기사 목록을 `gpt-4o-mini`에 보내 테마 7개를 추출합니다. 대표 기사 URL을 붙이고 분석 캐시는 `theme_analysis.json`에 저장합니다. |
| `backend/stock_data.py` | 종목명을 종목코드로 찾고, 네이버 모바일/데스크톱 소스에서 시세를 조회해 테마별 종목 리스트를 완성합니다. |
| `backend/youtube_signals.py` | `심플 관심종목 TV` 채널에서 `내일 관심테마!`, `당일 관심테마!` 영상을 읽어 분석 보조 시그널로 제공합니다. |
| `backend/daum_crawler.py` | 다음 금융 뉴스 검색 보조 유틸입니다. 현재 기본 파이프라인의 핵심 경로는 아니지만 크롤링 보완용으로 남아 있습니다. |
| `backend/crawled_articles.json` | 최근 크롤링 결과 캐시입니다. `--skip-crawl` 실행 시 재사용합니다. |
| `backend/theme_analysis.json` | 최근 분석 결과 캐시입니다. `--skip-analysis` 실행 시 재사용합니다. |

## 기본 데이터 흐름

```text
Naver Finance News
  -> crawler.py
  -> analyzer.py
  -> stock_data.py
  -> dashboard_data.json
```

실제 기본 실행 경로는 `main.py`와 `handler.py` 모두 `crawl_naver_finance_news_with_fallback(200)`를 사용합니다.

## 단계별 책임

### 1. 뉴스 수집

`crawler.py`가 기사 리스트를 `title`, `summary`, `url`, `date`, `source` 형태로 정리합니다.  
중복 URL은 제거하고, 로컬 실행 시 결과를 `backend/crawled_articles.json`에 저장합니다.

### 2. 테마 분석

`analyzer.py`는 기사 목록과 유튜브 시그널을 프롬프트로 구성해 OpenAI API를 호출합니다.  
결과는 아래와 같은 테마 구조로 정리됩니다.

```json
{
  "themeName": "광통신",
  "headline": "광통신주 강세",
  "representativeArticleIndex": 3,
  "relatedStocks": ["대한광통신", "옵티시스", "쏠리드"],
  "reasoning": "뉴스와 외부 시그널이 겹친 핵심 테마"
}
```

분석 후 `representativeArticleIndex`를 실제 기사 URL에 매핑해 `headlineUrl`도 붙입니다.

### 3. 종목 시세 조회

`stock_data.py`는 먼저 종목코드를 찾고, 그다음 현재가와 등락률, 거래대금, 미니 차트용 `barData`를 만듭니다.

- 종목코드 검색: 하드코딩 맵 우선, 없으면 네이버 검색 API/HTML fallback
- 시세 조회: 네이버 모바일 API 우선, 실패 시 데스크톱 HTML fallback
- 테마별 출력: 조회 성공한 종목 중 최대 4개를 등락률 기준으로 정렬

### 4. 최종 출력

`main.py`는 결과를 `frontend/dashboard_data.json`에 저장합니다.  
`handler.py`는 같은 구조의 JSON을 S3에 업로드합니다.

최종 JSON의 핵심 필드는 아래 3개입니다.

```json
{
  "updatedAt": "2026-04-14T09:00:00",
  "youtubeSignals": [],
  "themes": []
}
```

## 실행 방식

### 로컬

```powershell
cd backend
pip install -r requirements.txt
python main.py
```

자주 쓰는 옵션:

- `python main.py --crawl-only`
- `python main.py --skip-crawl`
- `python main.py --skip-crawl --skip-analysis`

### AWS Lambda

`handler.py`가 EventBridge 또는 수동 호출로 실행됩니다.  
필수 환경변수는 아래와 같습니다.

- `OPENAI_API_KEY`
- `S3_BUCKET_NAME`
- `S3_KEY`

## 한 줄 정리

이 백엔드는 `뉴스 수집 -> GPT 테마 분석 -> 종목 시세 조회 -> 대시보드 JSON 생성`을 담당하는 파이프라인입니다.
