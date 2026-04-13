# Backend Code Guide

Stock Dashboard 백엔드 파이프라인의 코드 구조, 데이터 흐름, 실행 방법을 정리한 문서입니다.

---

## 1. 전체 구조

```
backend/
├── main.py              # 로컬 실행 진입점 (CLI 옵션 지원)
├── handler.py           # AWS Lambda 핸들러 (클라우드 실행 진입점)
├── crawler.py           # Step 1 - 네이버 금융 뉴스 크롤링
├── analyzer.py          # Step 2 - ChatGPT API 테마 분석
├── stock_data.py        # Step 3 - 종목코드 검색 + 시세 조회
├── requirements.txt     # Python 의존성 패키지
├── .env                 # 환경변수 (OPENAI_API_KEY) - gitignore됨
├── .env.example         # 환경변수 예시
├── crawled_articles.json    # [캐시] 크롤링 결과
└── theme_analysis.json      # [캐시] ChatGPT 분석 결과
```

---

## 2. 데이터 파이프라인 흐름

4단계 순차 실행 파이프라인입니다. 각 단계는 독립적으로도 실행 가능합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Step 1: crawler.py                                                 │
│  네이버 금융 뉴스 200개 크롤링                                       │
│  → crawled_articles.json 저장                                       │
│                                                                     │
│         ↓ articles (list[dict])                                     │
│                                                                     │
│  Step 2: analyzer.py                                                │
│  ChatGPT API (gpt-4o-mini) 호출                                     │
│  기사 200개를 보내서 주도 테마 7개 추출                                │
│  → theme_analysis.json 저장                                         │
│                                                                     │
│         ↓ themes (list[dict])                                       │
│                                                                     │
│  Step 3: stock_data.py                                              │
│  테마별 관련종목 6개 중 시세조회 가능한 4개 선별                        │
│  종목코드 검색 → 네이버 모바일 증권 API로 현재가/등락률/거래대금 조회    │
│                                                                     │
│         ↓ completed_themes (list[dict])                             │
│                                                                     │
│  Step 4: main.py / handler.py                                       │
│  최종 dashboard_data.json 조립                                       │
│  (로컬) → frontend/dashboard_data.json 파일로 저장                   │
│  (AWS)  → S3 stock-dashboard-data 버킷에 업로드                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 모듈별 상세 설명

### 3-1. crawler.py — 뉴스 크롤링

네이버 금융(`finance.naver.com`) 뉴스 기사를 수집합니다.

#### 크롤링 소스 (우선순위)

| 순위 | 소스 | URL | 함수 |
|:---:|------|-----|------|
| 1 | 메인뉴스 | `/news/mainnews.naver` | `crawl_single_page()` |
| 2 | 속보 뉴스 | `/news/news_list.naver` (모드: LSS2D) | `crawl_news_flash()` |
| 3 | 시장 뉴스 | `/news/news_list.naver` (여러 섹션) | `crawl_market_news_list()` |

메인뉴스에서 우선 수집하고, 100개 미만이면 속보/시장 뉴스로 보충합니다.

#### 크롤링 데이터 구조

```json
{
  "title": "중동 사태에 자재 비상 '공사 지연' 혼란",
  "summary": "중동 분쟁으로 인한 원자재 수급 차질이...",
  "url": "https://finance.naver.com/news/news_read.naver?article_id=...",
  "date": "2026-04-11 09:30",
  "source": "매일경제"
}
```

#### HTML 파싱 셀렉터

```python
# 메인뉴스 기사 리스트
soup.select("ul.newsList li")          # 1차 시도
soup.select("div.mainNewsList li")     # 2차 대체
soup.select("li.block1")              # 3차 대체

# 기사 내부 요소
item.select_one("dd.articleSubject a") # 제목 + URL
item.select_one("dd.articleSummary")   # 요약
item.select_one("span.wdate")         # 날짜
item.select_one("span.press")         # 출처
```

#### 주요 함수

| 함수 | 설명 | 반환 |
|------|------|------|
| `crawl_naver_finance_news(200)` | 전체 크롤링 실행 (중복 제거 포함) | `list[dict]` |
| `crawl_single_page(page)` | 메인뉴스 1페이지 크롤링 | `list[dict]` |
| `crawl_news_flash(page)` | 속보 뉴스 1페이지 크롤링 | `list[dict]` |
| `crawl_market_news_list(count)` | 시장 뉴스 멀티 섹션 크롤링 | `list[dict]` |
| `save_articles(articles)` | `crawled_articles.json`에 저장 | 파일 경로 |
| `load_articles()` | 저장된 기사 로드 | `list[dict]` |

---

### 3-2. analyzer.py — ChatGPT 테마 분석

크롤링된 기사 200개를 ChatGPT API에 보내서 당일 주도 테마 7개를 추출합니다.

#### API 호출 설정

| 항목 | 값 |
|------|-----|
| 모델 | `gpt-4o-mini` |
| Temperature | `0.3` (일관성 높게) |
| Max Tokens | `3000` |
| 응답 형식 | `json_object` (JSON 모드 강제) |

#### 프롬프트 구조

```
[시스템 프롬프트]
  "당신은 한국 주식시장 전문 애널리스트입니다. 단타 트레이딩에 특화..."

[유저 프롬프트]
  "아래는 오늘(2026-04-11) 수집된 증권 뉴스 기사 200개입니다..."
  + 전체 기사 목록 (번호. [제목] 요약 형태)
```

#### ChatGPT 응답 구조 (테마 1개)

```json
{
  "themeName": "중동사태",
  "headline": "중동 사태에 자재 비상 '공사 지연' 혼란",
  "representativeArticleIndex": 13,
  "relatedStocks": ["현대건설", "삼성물산", "GS건설", "대우건설", "포스코", "SK건설"],
  "reasoning": "중동 사태로 인한 자재 공급 차질과..."
}
```

#### 후처리: 대표 기사 URL 매핑

`representativeArticleIndex` 값으로 원본 기사 리스트에서 URL을 찾아 `headlineUrl` 필드를 추가합니다.

```python
# analyzer.py 154~160행
article_idx = theme.get("representativeArticleIndex", 1)
if isinstance(article_idx, int) and 1 <= article_idx <= len(articles):
    theme["headlineUrl"] = articles[article_idx - 1].get("url", "")
```

#### 주요 함수

| 함수 | 설명 | 반환 |
|------|------|------|
| `analyze_themes(articles, date_str)` | ChatGPT 테마 분석 | `{"themes": [...]}` |
| `format_articles_for_prompt(articles)` | 기사를 프롬프트용 텍스트로 변환 | `str` |
| `save_analysis(analysis)` | `theme_analysis.json`에 저장 | 파일 경로 |
| `load_analysis()` | 저장된 분석 결과 로드 | `dict` |

---

### 3-3. stock_data.py — 종목 시세 조회

테마에서 추출된 종목명(`relatedStocks`)을 실제 시세 데이터로 변환합니다.

#### 종목코드 검색 (3단계 fallback)

| 순위 | 방법 | 함수 |
|:---:|------|------|
| 1 | 하드코딩 매핑 (`STOCK_CODE_MAP`, 약 100개) | `search_stock_code()` |
| 2 | 네이버 모바일 증권 API 검색 | `search_stock_code_online()` |
| 3 | 네이버 통합검색 HTML 파싱 | `search_stock_code_online()` |

`STOCK_CODE_MAP`에 없는 종목은 API로 검색 후 자동으로 런타임 캐싱됩니다.

#### 시세 데이터 조회 (2단계 fallback)

| 순위 | 방법 | 함수 |
|:---:|------|------|
| 1 | 네이버 모바일 증권 API (`m.stock.naver.com/api/stock/{code}/basic`) | `get_stock_detail_mobile()` |
| 2 | 네이버 데스크탑 HTML 파싱 (`finance.naver.com/item/main.naver`) | `get_stock_detail_desktop()` |

#### 시세 데이터 구조 (조회 결과)

```json
{
  "code": "006360",
  "name": "GS건설",
  "price": 37650,
  "changeRate": 5.91,
  "changeAmount": 2100,
  "prevClose": 35550,
  "open": 36390,
  "high": 37860,
  "low": 35970,
  "volumeRaw": 37700000000,
  "volume": "377억",
  "time": "16:10"
}
```

#### Range Bar 계산 (`calculate_bar_data`)

프론트엔드의 미니 차트 바에 사용되는 데이터입니다.

```
저가(low) ───────────────────── 고가(high)
  ■■■■■■■■ [시가 → 현재가] ■■■■■■
       ↑ baseline(전일종가)

→ 모든 값을 0~100 스케일로 변환
```

```json
{
  "minMaxRange": [0, 100],
  "currentRange": [22, 89],
  "baseline": 0
}
```

#### 테마 조립 로직 (`get_stock_details_for_themes`)

1. 테마별 `relatedStocks` 6개 중 시세 조회 성공한 **4개만** 채택
2. **등락률 내림차순** 정렬
3. 1위 종목에 `isTop: true` 표시
4. 테마별 `totalVolume` = 포함된 4개 종목 거래대금 합계

---

### 3-4. handler.py — AWS Lambda 핸들러

`main.py`와 동일한 파이프라인을 AWS Lambda 환경에서 실행합니다.

| 차이점 | `main.py` (로컬) | `handler.py` (Lambda) |
|--------|------------------|----------------------|
| 환경변수 | `.env` 파일 (dotenv) | Lambda 환경변수 |
| 출력 | `frontend/dashboard_data.json` 파일 저장 | S3 버킷에 업로드 |
| 트리거 | CLI 수동 실행 | EventBridge 스케줄 / POST /run API |
| 기사 수 | 200개 | 200개 |

#### Lambda 환경변수

| 변수명 | 값 | 설명 |
|--------|-----|------|
| `OPENAI_API_KEY` | `sk-proj-...` | ChatGPT API 키 |
| `S3_BUCKET_NAME` | `stock-dashboard-data` | S3 버킷 이름 |
| `S3_KEY` | `dashboard_data.json` | S3 업로드 경로 |

#### S3 업로드 설정

```python
s3.put_object(
    ContentType="application/json; charset=utf-8",
    CacheControl="max-age=300",    # 5분 캐시
)
```

---

## 4. 최종 출력 JSON 스키마

프론트엔드(`app.js`)가 이 JSON을 파싱해서 대시보드를 렌더링합니다.

```json
{
  "updatedAt": "2026-04-11T01:12:09.394472",
  "themes": [
    {
      "themeName": "중동사태",
      "totalVolume": "5,446억",
      "headline": "중동 사태에 자재 비상 '공사 지연' 혼란",
      "headlineUrl": "https://finance.naver.com/news/...",
      "stocks": [
        {
          "name": "GS건설",
          "price": 37650,
          "time": "16:10",
          "changeRate": 5.91,
          "volume": "377억",
          "isTop": true,
          "barData": {
            "minMaxRange": [0, 100],
            "currentRange": [22, 89],
            "baseline": 0
          }
        }
      ]
    }
  ]
}
```

---

## 5. 실행 방법

### 5-1. 로컬 실행

```powershell
# 사전 준비
cd backend
pip install -r requirements.txt

# .env 파일 생성
echo OPENAI_API_KEY=sk-your-key > .env
```

```powershell
# 전체 파이프라인 실행 (크롤링 → 분석 → 시세 → JSON 저장)
python main.py

# 크롤링만 실행 (ChatGPT 비용 X)
python main.py --crawl-only

# 이전 크롤링 결과로 분석만 실행
python main.py --skip-crawl

# 이전 분석 결과로 시세만 갱신
python main.py --skip-crawl --skip-analysis
```

```powershell
# 개별 모듈 단독 실행
python crawler.py       # → crawled_articles.json 저장
python analyzer.py      # → theme_analysis.json 저장 (crawled_articles.json 필요)
python stock_data.py    # → 삼성전자 시세 테스트 출력
```

### 5-2. AWS Lambda (자동)

| 항목 | 설정값 |
|------|--------|
| 스케줄 | 10분마다 (`rate(10 minutes)`) |
| 함수명 | `stock-pipeline` |
| 런타임 | Python 3.12 |
| 타임아웃 | 300초 (5분) |
| 메모리 | 512MB |
| 출력 | S3 `stock-dashboard-data/dashboard_data.json` |

### 5-3. AWS Lambda (수동)

```powershell
# 방법 1: API 엔드포인트 호출
curl -X POST https://<API_ID>.execute-api.ap-northeast-2.amazonaws.com/Prod/run

# 방법 2: AWS 콘솔
# Lambda > stock-pipeline > Test 버튼 > 빈 이벤트 {} 로 실행

# 방법 3: AWS CLI
aws lambda invoke --function-name stock-pipeline --region ap-northeast-2 output.json
```

---

## 6. 배포

### 자동 배포 (GitHub Actions)

`backend/` 또는 `template.yaml` 파일 변경 후 `git push`하면 자동 배포됩니다.

```
git push origin main
  → .github/workflows/deploy-lambda.yml 트리거
  → GitHub Actions에서 sam build && sam deploy 실행
  → AWS Lambda 함수 업데이트
```

필요한 GitHub Secrets:

| Secret | 용도 |
|--------|------|
| `AWS_ACCESS_KEY_ID` | AWS IAM 액세스 키 |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM 시크릿 키 |
| `OPENAI_API_KEY` | ChatGPT API 키 |

### 수동 배포 (SAM CLI)

```powershell
sam build
sam deploy --parameter-overrides "OpenAIApiKey=sk-..."
```

---

## 7. AWS 인프라 구성 (template.yaml)

```
 EventBridge ──(10분마다)──→ Lambda (stock-pipeline)
                                  │
 API Gateway ──(POST /run)──→ Lambda (stock-pipeline)
                                  │
                                  ↓
                              S3 Bucket (stock-dashboard-data)
                                  │
                                  ↓
                        dashboard_data.json (퍼블릭 읽기)
                                  │
                                  ↓
                        GitHub Pages (프론트엔드에서 fetch)
```

| 리소스 | 타입 | 이름 |
|--------|------|------|
| S3 Bucket | `AWS::S3::Bucket` | `stock-dashboard-data` |
| Lambda | `AWS::Serverless::Function` | `stock-pipeline` |
| EventBridge | Schedule 이벤트 | `stock-pipeline-schedule` |
| API Gateway | Api 이벤트 | `POST /run` |

---

## 8. 비용

| 항목 | 주 1회 실행 기준 | 비고 |
|------|-----------------|------|
| Lambda | 무료 | 프리티어 100만 요청/월 |
| ChatGPT API | ~$0.01/회 | gpt-4o-mini, ~3000 토큰 출력 |
| S3 | 무료 | 프리티어 5GB |
| API Gateway | 무료 | 프리티어 100만 요청/월 |
| **월 합계** | **~$0.05** | |

---

## 9. 트러블슈팅

### 크롤링 결과가 0개

네이버 금융 페이지 구조가 변경되었을 수 있습니다. `crawler.py`의 CSS 셀렉터(`ul.newsList li`, `dd.articleSubject a` 등)를 확인하세요.

### ChatGPT 응답 파싱 실패

`analyzer.py`에서 `response_format={"type": "json_object"}`를 사용하므로 JSON은 보장됩니다. 실패시 원본 응답이 콘솔에 출력됩니다.

### 종목코드를 찾을 수 없음

`stock_data.py`의 `STOCK_CODE_MAP`에 해당 종목이 없고, 네이버 API 검색도 실패한 경우입니다. 비상장/해외 종목이 나올 수 있으므로 (예: "두나무", "한국GM") 해당 종목은 스킵됩니다.

### Lambda 타임아웃

기본 5분(300초)으로 설정되어 있습니다. 크롤링 200개 + ChatGPT API + 종목 28개 시세 조회는 보통 2~3분 소요됩니다. CloudWatch Logs에서 확인:

```powershell
aws logs tail /aws/lambda/stock-pipeline --follow --region ap-northeast-2
```
