# 백엔드 파이프라인 문서

단타 트레이딩을 위한 주식 테마 분석 백엔드.  
뉴스 크롤링 → 다중 시그널 수집 → GPT 테마 분석 → 종목 데이터 조회 → JSON 출력.

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py (파이프라인)                        │
│                                                                 │
│  Step 1. 뉴스 크롤링 ─────────── crawler.py + daum_crawler.py     │
│                                                                 │
│  Step 2. 시그널 수집 + GPT 분석 ── analyzer.py                     │
│          ├── 🏆 개미승리 시그널 ── antwinner/collector.py           │
│          ├── 📺 유튜브 시그널 ──── youtube_signals.py              │
│          ├── 📡 텔레그램 시그널 ── telegram/collector.py            │
│          ├── 📈 가격 기반 시그널 ── price_signals/cluster.py        │
│          └── 🤖 Gemini API ──── gemini-2.5-flash-lite                    │
│                                                                 │
│  Step 3. 종목 데이터 조회 ─────── stock_data.py                    │
│                                                                 │
│  Step 4. JSON 조립 ────────────→ frontend/dashboard_data.json    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 디렉터리 구조

```
backend/
├── main.py                  # 메인 파이프라인 (로컬 실행)
├── handler.py               # AWS Lambda 핸들러
├── analyzer.py              # GPT 테마 분석 + 시그널 통합
├── crawler.py               # 네이버 금융 뉴스 크롤러
├── daum_crawler.py          # 다음 금융 뉴스 크롤러
├── stock_data.py            # 종목 실시간 데이터 조회
├── youtube_signals.py       # 유튜브 시그널 수집
├── requirements.txt         # 의존성 패키지
│
├── antwinner/               # 개미승리 테마 시그널 모듈
│   ├── __init__.py
│   ├── collector.py         #   API 수집 (상위 10개 테마)
│   ├── store.py             #   로컬 JSON 저장/로드
│   └── dev/
│       └── antwinner_themes.json  # 캐시 데이터
│
├── telegram/                # 텔레그램 시그널 모듈
│   ├── __init__.py
│   ├── client.py            #   Telethon 클라이언트
│   ├── collector.py         #   메시지 수집
│   ├── scoring.py           #   시그널 점수화
│   ├── models.py            #   데이터 모델
│   ├── store.py             #   S3/로컬 저장
│   └── dev/
│       └── telegram_signals.json
│
├── price_signals/           # 가격 기반 테마 시그널 모듈
│   ├── __init__.py
│   ├── collector.py         #   급등주 수집
│   ├── cluster.py           #   종목 군집화 → 테마 후보
│   ├── taxonomy.py          #   테마 분류 규칙
│   ├── models.py            #   데이터 모델
│   ├── store.py             #   로컬 JSON 저장
│   └── dev/
│       └── price_theme_signals.json
│
└── (output files)
    ├── crawled_articles.json      # 크롤링된 기사
    └── theme_analysis.json        # GPT 분석 결과
```

---

## 파이프라인 실행

### 로컬 실행
```bash
cd backend

# 전체 파이프라인
python3 main.py

# 크롤링만
python3 main.py --crawl-only

# 크롤링 건너뛰기 (저장된 기사 사용)
python3 main.py --skip-crawl

# 분석 건너뛰기 (저장된 분석 결과 사용)
python3 main.py --skip-analysis
```

### Lambda 실행
- `handler.py`의 `lambda_handler()`가 EventBridge 또는 수동 호출로 트리거
- 결과를 S3(`dashboard_data.json`)에 업로드

---

## Step 1: 뉴스 크롤링 (`crawler.py`)

| 소스 | 설명 |
|------|------|
| 네이버 메인뉴스 | `finance.naver.com/news/mainnews.naver` — 주요 증권 뉴스 |
| 네이버 속보 | `finance.naver.com/news/news_list.naver` — 증권 속보, 시황, 공시 |
| 다음 뉴스 | `daum_crawler.py` — 다음 금융 뉴스 (보조) |

- 목표: **200개** 기사 수집
- 메인뉴스 우선 → 부족하면 속보/아카이브로 보충
- URL 기준 중복 제거

---

## Step 2: 시그널 수집 + GPT 분석 (`analyzer.py`)

### 시그널 소스 (가중치 높은 순)

| 순위 | 시그널 | 소스 | 마커 | 설명 |
|------|--------|------|------|------|
| 🥇 | **개미승리** | `antwinner/` | `●` | 실제 장중 등락률·거래대금 기반 상위 10개 테마 + 종목 |
| 🥈 | **유튜브** | `youtube_signals.py` | `◆` | 심플 관심종목TV의 내일/당일 관심테마 |
| 🥉 | **텔레그램** | `telegram/` | — | @faststocknews 채널 실시간 메시지 |
| 4 | **가격 기반** | `price_signals/` | — | 급등주 군집화로 포착한 테마 후보 |
| 5 | **뉴스 키워드** | `crawler.py` | `★` | 특징주/강세/상한가 키워드 포함 기사 |

### 개미승리 시그널 (`antwinner/`)

- **API**: `https://antwinner.com/api/all-themes`
- **수집**: 평균 등락률 기준 **상위 10개 테마**, 테마당 등락률 상위 **6개 종목**
- **데이터**: 테마명, 평균등락률, 상승비율, 종목(등락률·현재가·거래대금)
- **가중치**: 프롬프트 최상단 배치 + 시스템 프롬프트에서 "최고 신뢰 시그널" 명시
- **폴백**: API 실패 시 로컬 캐시(`dev/antwinner_themes.json`) 사용

### GPT 분석

- **모델**: `gemini-2.5-flash-lite` (temperature=0.3)
- **입력**: 뉴스 기사 + 5개 시그널 소스를 하나의 프롬프트로 조립
- **출력**: 7개 테마 (테마명, 헤드라인, 관련종목 6개, 선정이유)
- **후처리**:
  - 가격 기반 시그널의 강한 후보가 누락되면 약한 테마를 교체
  - 종목 겹침이 큰 테마 자동 병합
  - 병합 후 슬롯 부족하면 가격 후보로 보강

### 기사 정렬 우선순위 (프롬프트 내)

```
1. ● 개미승리 상위 테마 관련 기사
2. ◆ 유튜브 시그널 관련 기사
3. ★ 특징주/급등 키워드 기사
4. 일반 기사
```

---

## Step 3: 종목 데이터 조회 (`stock_data.py`)

- GPT가 추출한 테마별 관련종목(최대 6개 중 4개)의 실시간 데이터 조회
- **데이터 소스**: 네이버 모바일 증권 API (`m.stock.naver.com`) → 데스크탑 HTML 파싱 폴백
- **조회 항목**: 현재가, 등락률, 거래대금, 시가/고가/저가, Range Bar 데이터
- **종목코드 검색**: 하드코딩 매핑 → 네이버 모바일 API → 네이버 통합검색

---

## Step 4: JSON 출력

최종 결과는 `frontend/dashboard_data.json`에 저장됩니다.

```json
{
  "updatedAt": "2026-04-16T23:00:00",
  "antwinnerSignals": [...],
  "youtubeSignals": [...],
  "telegramSignals": [...],
  "priceSignalCandidates": [...],
  "themes": [
    {
      "themeName": "유리기판",
      "totalVolume": "5,372억",
      "headline": "핵심 뉴스 한줄 요약",
      "headlineUrl": "https://...",
      "stocks": [
        {
          "name": "한빛레이저",
          "price": 6860,
          "changeRate": 29.92,
          "volume": "1,626억",
          "isTop": true,
          "barData": { "minMaxRange": [0, 100], "currentRange": [30, 95], "baseline": 25 }
        }
      ]
    }
  ]
}
```

---

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `GEMINI_API_KEY` | Gemini API 키 (Google AI Studio) | (필수) |
| `S3_BUCKET_NAME` | S3 버킷 이름 | `stock-dashboard-data` |
| `S3_KEY` | S3 객체 키 | `dashboard_data.json` |
| `TG_CHANNEL_USERNAME` | 텔레그램 채널 | `@faststocknews` |
| `TG_LOOKBACK_MINUTES` | 텔레그램 수집 범위(분) | `180` |

---

## 의존성

```
requests>=2.31.0
beautifulsoup4>=4.12.0
openai>=1.0.0
python-dotenv>=1.0.0
lxml>=4.9.0
boto3>=1.34.0
yt-dlp>=2025.10.14
telethon>=1.36.0
```