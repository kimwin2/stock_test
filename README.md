# 🎯 Stock Premium - 주식 테마 대시보드

실시간 주식 테마 분석 대시보드. 네이버 금융 뉴스를 크롤링하고, ChatGPT로 오늘의 주도 테마를 분석하여 대장주를 보여줍니다.

## 아키텍처

```
[EventBridge 10분] → [Lambda Python] → [S3 JSON]
                                            ↑
                   [GitHub Pages 웹사이트] → fetch()
```

| 구성요소 | 서비스 | 역할 |
|---------|--------|------|
| 백엔드 | AWS Lambda | 뉴스 크롤링 + GPT 분석 + 종목 데이터 |
| 스케줄러 | EventBridge | 10분마다 Lambda 트리거 |
| 데이터 저장 | S3 | dashboard_data.json 저장 |
| 프론트엔드 | GitHub Pages | 정적 웹사이트 호스팅 |
| CI/CD | GitHub Actions | 자동 배포 |

## 프로젝트 구조

```
├── .github/workflows/
│   ├── deploy-lambda.yml       # Lambda 자동 배포
│   └── deploy-frontend.yml     # GitHub Pages 자동 배포
├── backend/
│   ├── handler.py              # Lambda 진입점
│   ├── main.py                 # 로컬 파이프라인
│   ├── crawler.py              # 네이버 뉴스 크롤러
│   ├── analyzer.py             # ChatGPT 테마 분석
│   ├── stock_data.py           # 종목 데이터 조회
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html              # 대시보드 메인 페이지
│   ├── app.js                  # 데이터 로드 및 렌더링
│   ├── style.css               # 스타일
│   └── dashboard_data.json     # 로컬 테스트 데이터
├── template.yaml               # AWS SAM 인프라 정의
├── samconfig.toml              # SAM 배포 설정
└── README.md
```

## 🚀 빠른 시작

### 1. 로컬 실행 (백엔드)

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 OPENAI_API_KEY 설정

python main.py              # 전체 파이프라인
python main.py --skip-crawl # 저장된 기사로 분석만
```

### 2. 로컬 실행 (프론트엔드)

```bash
cd frontend
python -m http.server 8080
# http://localhost:8080 접속
```

### 3. AWS 배포

자세한 배포 가이드는 [deploy.md](deploy.md)를 참고하세요.

```bash
# AWS CLI 설정
aws configure

# SAM 배포
sam build
sam deploy --parameter-overrides "OpenAIApiKey=sk-your-key"
```

### 4. GitHub Secrets 설정

리포지토리 Settings → Secrets and variables → Actions에 추가:

| Secret | 설명 |
|--------|------|
| `AWS_ACCESS_KEY_ID` | AWS IAM 액세스 키 |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM 시크릿 키 |
| `OPENAI_API_KEY` | OpenAI API 키 |

### 5. GitHub Pages 활성화

리포지토리 Settings → Pages → Source → **GitHub Actions** 선택

## 📡 데이터 흐름

1. **EventBridge** 10분마다 Lambda 트리거
2. **Lambda**가 네이버 뉴스 크롤링 (200개)
3. **ChatGPT API**로 주도 테마 7개 추출
4. 각 테마별 **대장주 4개** 실시간 시세 조회
5. `dashboard_data.json`을 **S3**에 업로드
6. **GitHub Pages** 웹사이트에서 S3 JSON을 fetch하여 렌더링

## 💰 비용

| 서비스 | 프리티어 | 예상 비용 |
|--------|---------|----------|
| Lambda | 월 100만건 무료 | $0 (10분×6×24=~4,300건/월) |
| S3 | 5GB 무료 | $0 |
| EventBridge | 무료 | $0 |
| GitHub Pages | 무료 | $0 |
| OpenAI API | - | ~$1/월 (gpt-4o-mini) |

## 라이선스

MIT
