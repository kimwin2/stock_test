# stock_test — 프로젝트 가이드 (AI 에이전트용)

이 파일은 Claude Code 등 AI 에이전트가 이 레포에서 작업할 때 따라야 하는 규칙을 정리한다.

## 프로젝트 개요

한국 주식 단타 트레이더용 대시보드. 두 개의 탭:

1. **급등·테마** — 네이버 금융 뉴스 + 다중 시그널 → GPT 테마 분석 → 종목 시세
2. **수급·주도** — Pier&Grid 오실레이터, 주도 ETF Mansfield RS, 수급 빈집, 거래대금 강도 (TI), 외인/기관 섹터별 매수, 매수 후보 차트

**스택**: Python(AWS Lambda) + vanilla JS(GitHub Pages) · 데이터: Naver mobile API, FinanceDataReader, Telegram(Telethon), OpenAI

**배포**:
- Lambda: 평일 8~16시 10분 간격(theme), 평일 8~20시 정각(flow)
- S3: `stock-dashboard-data` 버킷에 `dashboard_data.json` / `flow_dashboard.json`
- GitHub Pages: `frontend/` 정적 호스팅

## gstack 스킬 정책

이 레포는 gstack 5개 스킬만 사용한다. 다른 gstack 스킬은 `.claude/skills/gstack/` 에 물리적으로 존재하지만 **호출하지 않는다**. 초기 단계에서 노이즈를 줄이기 위함.

### 활성 스킬 5개

| Skill | 사용 시점 | 트리거 키워드 |
|---|---|---|
| **`/office-hours`** | 새 기능/탭/제품 결정 단계 — 페르소나·시점·결정 명확화 | "brainstorm this", "이거 해야 할까", "정보 어떻게 보여줄까" |
| **`/investigate`** | 버그 발생 시 — 임의 수정 금지, 근본 원인부터 | "debug this", "왜 안돼", "이전엔 됐는데" |
| **`/review`** | 커밋/푸시 직전 — 프로덕션 깨질 패턴 검사 | "review this", "코드 리뷰", "푸시 전 검토" |
| **`/qa`** | UI/기능 동작 검증 — 실 브라우저로 클릭하며 확인 | "qa", "test this site", "브라우저로 돌려봐" |
| **`/careful`** | 파괴적 명령 실행 직전 — 재확인 강제 | `rm -rf`, `git push --force`, `DROP TABLE` 등 자동 트리거 |

### 사용 안 하는 스킬 (참고용)

`.claude/skills/gstack/` 에 같이 들어있지만 **이 레포에서는 호출하지 말 것**:

- 계획류: `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/plan-devex-review`, `/autoplan` — 솔로 단계라 과함
- 출시류: `/ship`, `/land-and-deploy`, `/canary`, `/landing-report`, `/document-release` — PR 워크플로 아님 (main 직 푸시)
- 디자인 자동화: `/design-consultation`, `/design-shotgun`, `/design-html`, `/design-review`, `/devex-review` — 화면은 직접 결정
- 보조: `/codex`, `/cso`, `/health`, `/benchmark`, `/benchmark-models`, `/retro`, `/learn`, `/context-save`, `/context-restore`, `/skillify`, `/make-pdf`, `/setup-deploy`, `/setup-gbrain`, `/setup-browser-cookies`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/qa-only`, `/browse`, `/open-gstack-browser`, `/connect-chrome`, `/pair-agent`

→ 향후 필요 시점이 명확해지면 위 표에 옮기고 사용. 지금은 5개로 한정.

### gstack 인프라 메모

- **Bun 미설치 상태**. `gstack/setup` 스크립트는 Bun 필수라 미실행. `bin/*` 스크립트는 모두 bash이므로 그대로 작동.
- **`/qa` 의 browse 바이너리**는 빌드 안 됨. `/qa` 호출 시 Playwright(파이썬) 폴백으로 진행하거나, 필요하면 그때 Bun 설치 후 `cd .claude/skills/gstack && ./setup` 실행.
- skill 자동 업데이트 체크: `~/.gstack/sessions/` 에 세션 파일 생성하지만 무해.

## 코드 작업 규칙

### 파이썬 (backend/)

- 신규 모듈은 `backend/<domain>/` 패키지로 묶는다 (`flow_signals/`, `price_signals/`, `telegram/`, `antwinner/`, `infostock/` 패턴 따라).
- 외부 API 호출은 `data_sources.py` 또는 `collector.py` 에 격리.
- Lambda 진입은 `handler.py` 에 통합. `event.mode` 로 분기 (theme / flow / 새 mode 추가 가능).
- 의존성 추가 시 `backend/requirements.txt` + Lambda 패키지 크기 확인 필수 (250MB unzipped 한도).

### 프론트 (frontend/)

- vanilla JS, no framework. 차트는 vanilla SVG 로 작성 (외부 라이브러리 추가 금지).
- 데이터 fetch URL 자동 분기: `github.io` 호스트면 S3, 로컬이면 같은 디렉토리 JSON.
- 새 탭 추가 시 `index.html` 의 탭 nav + 패널 + 별도 `*.js` 모듈로.

### 비밀

- `.env` (`backend/.env`, `.env`) 는 .gitignore 됨. 신규 키 추가 시 `backend/.env.example` 도 업데이트.
- `TG_STRING_SESSION` 같은 장기 자격증명은 절대 커밋 금지.

### Git

- main 직 푸시 워크플로. PR 도입 전. 그러나 **force-push 는 명시 허락 없이 금지**.
- 커밋 메시지: 한글 가능. `feat:` / `fix:` / `chore:` prefix 권장하지만 강제 아님.
- 외부 자료(`etc_source/`)는 .gitignore 됨. 절대 커밋 금지.

## 실행 명령 모음

```bash
# 백엔드 로컬 실행 (theme)
cd backend && python main.py

# 백엔드 로컬 실행 (flow)
cd backend && python -m flow_signals.pipeline

# 프론트 로컬 서버
cd frontend && python -m http.server 8080 --bind 127.0.0.1

# Lambda 배포
sam build && sam deploy --parameter-overrides "OpenAIApiKey=sk-..."

# 텔레그램 채널 덤프 + 분석 (개인 분석 도구)
cd backend && python -m telegram.fetch_dump --channel "https://t.me/+..." --limit 1000 --out telegram/dev/<name>_raw.json
cd backend && python -m telegram.analyze_dump --in telegram/dev/<name>_raw.json
```

## 알려진 이슈 / 메모

- KOSDAQ 시총 200위 밖 종목 (심텍, 코리아써키트 등)은 매수 후보에 안 잡힘. 필요 시 `flow_signals/universe.py` 의 `EXPLICIT_SECTOR` 에 추가.
- `pykrx` 는 KRX 로그인 요구로 종목별 투자자 데이터 못 가져옴. `Naver mobile API` (`/api/stock/{code}/trend`, 10일치) 로 대체.
- 일부 ETF 코드(`471490`, `421970`, `381190`, `117710`)는 FDR 데이터 부재 — 무시하고 진행.
