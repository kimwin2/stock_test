# stock_test — 프로젝트 가이드 (AI 에이전트용)

이 파일은 Claude Code 등 AI 에이전트가 이 레포에서 작업할 때 따라야 하는 규칙을 정리한다.

## 프로젝트 개요

한국 주식 단타 트레이더용 대시보드. 세 개의 탭:

1. **급등·테마** — 네이버 금융 뉴스 + 다중 시그널 → Gemini 테마 분석 → 종목 시세
2. **수급·주도** — Fear & Greed 오실레이터, 주도 ETF Mansfield RS, 수급 빈집, 거래대금 강도 (TI), 외인/기관 섹터별 매수, 매수 후보 차트
3. **AI 브리핑** — 자체 시그널(F&G/주도섹터/수급/빈집) 변화 + DART 공시 이벤트를 Gemini 가 서술형 브리핑으로 작성 (`backend/briefing/`). 예측·조언 문장 금지, 데이터 서술만. DART_API_KEY 없으면 공시 섹션 스킵, LLM 실패 시 규칙 기반 fallback. S3 데이터에 briefing 키가 없으면 프론트는 `briefing_sample.json` 으로 샘플 미리보기 표시.

**스택**: Python(AWS Lambda) + vanilla JS(GitHub Pages) · 데이터: Naver mobile API, FinanceDataReader, Telegram(Telethon), Gemini (OpenAI SDK + Gemini OpenAI-호환 endpoint, model `gemini-2.5-flash-lite`)

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
sam build && sam deploy --parameter-overrides "GeminiApiKey=AIza..."

# 텔레그램 채널 덤프 + 분석 (개인 분석 도구)
cd backend && python -m telegram.fetch_dump --channel "https://t.me/+..." --limit 1000 --out telegram/dev/<name>_raw.json
cd backend && python -m telegram.analyze_dump --in telegram/dev/<name>_raw.json
```

## 참고 채널 대조 (backend/benchmark/)

수급·시황 탭이 숙련 트레이더의 판단에 얼마나 수렴하는지 **측정**하는 개발용 도구.

- **경계**: 레퍼런스의 문장·수치를 제품에 실어 나르지 않는다. Lambda 배포·S3 산출물·
  사용자 화면 어디에도 들어가지 않는다. 유료 상품이라 외부 코멘트에 의존할 수 없고,
  의존하면 그 채널이 멈추는 순간 제품도 멈춘다. 정답지(평가용)로만 쓴다.
- **개선 경로**: 대조에서 나온 격차는 항상 **우리 계산 로직**에 반영한다.
  `사전 누락`(SECTOR_RULES 에 그 섹터 자체가 없음) → 코드 결함,
  `순위 누락`(사전엔 있는데 주도섹터로 안 뽑힘) → 임계값 문제. 처방이 다르다.
- 하루치 노이즈로 로직을 흔들지 않도록 **2일 이상 반복된 격차만** 조치 후보로 낸다.

```bash
cd backend
python -m benchmark.compare --date 2026-08-01
python -m benchmark.compare --all --flow-dir <일별 스냅샷 디렉터리>
```

레퍼런스 경로는 `REFERENCE_DAILY_DIR` 환경변수(기본 `~/repo/stock_chat/data/daily`).
flow 산출물은 매 실행마다 `s3://<bucket>/history/flow/YYYY-MM-DD.json` 로 스냅샷이
남으므로, 같은 날짜끼리 대조하려면 이걸 받아서 `--flow-dir` 로 준다. 날짜가 어긋나면
도구가 경고한다 (다른 날 시장을 견주면 섹터 비교가 성립하지 않는다).

## 알려진 이슈 / 메모

- KOSDAQ 시총 200위 밖 종목 (심텍, 코리아써키트 등)은 매수 후보에 안 잡힘. 필요 시 `flow_signals/universe.py` 의 `EXPLICIT_SECTOR` 에 추가.
- `pykrx` 는 KRX 로그인 요구로 종목별 투자자 데이터 못 가져옴. `Naver mobile API` (`/api/stock/{code}/trend`, 10일치) 로 대체.
- 일부 ETF 코드(`471490`, `421970`, `381190`, `117710`)는 FDR 데이터 부재 — 무시하고 진행.

### 매수 후보 선정 규율 (2026-08 개편)

후보 품질 저하(미달 종목 유입)를 막기 위한 하드 규칙. 완화 시 후보가 다시 오염되므로 신중히.

- **섹터 분류**: `금융` 을 `증권`/`은행`/`보험` 으로 분리. 하나로 묶으면 증권 ETF 강세에
  보험사(롯데손해보험 등)까지 주도섹터로 딸려 들어온다. `"지주"` 키워드는 세아베스틸지주(철강)를
  오분류해 `"금융지주"` 로 좁혔다.
- **주도 섹터 상한**: ETF RS 기반 + 수급 강도 기반 합쳐 최대 7개(`MAX_LEADING_SECTORS`).
  수급 섹터는 **절대 금액이 아니라 시총 정규화 강도**(만분율, `FLOW_STRENGTH_MIN=5.0`) 상위 4개만.
  절대 금액 기준이던 기존 로직은 주도섹터를 13개까지 불려 필터를 무력화했다.
- **ETF→섹터 순서는 RS 강도순**. 점수 로직이 `leading_sectors.index()` 로 1·2·3위에
  +40/+32/+24 를 주므로, 가나다순이면 가산점이 이름 순서로 배분된다(과거 실제 버그).
- **하드 필터**(`Step 7a`): `aboveMA10`(추세 생존) ∩ `oscLast < 0`(빈집 정식 정의) ∩
  점수 45점 이상 ∩ 섹터당 최대 4종목. 후보가 8개 미만이면 **점수 커트라인만** 완화한다
  (추세·빈집은 전략의 정의라 완화 금지).
- `exitSignals` 는 하드 필터 **이전** 풀(`pre_filter_candidates`)에서 산출한다. 필터 후 풀로
  계산하면 "10MA 이탈" 조건이 영원히 성립하지 않는다.

### 섹터 분류 — 가장 조용하고 큰 결함 (2026-08-09)

`classify_sector` 는 **회사명에 업종 키워드가 있을 때만** 동작한다. 한국 기업명에는
업종이 잘 안 들어가서, 이 방식만으로는 절반이 '기타'로 빠진다.

- 실측: 유니버스 550종목 중 327개(59%), **시총 기준 15.6%** 가 미분류였다.
  LG에너지솔루션(84조)이 2차전지가 아니었고 두산에너빌리티(49조)가 원전이 아니었다.
- 섹터 수급은 종목을 섹터별로 **합산**해 구한다. 대장주가 빠지면 그 섹터 합계가
  통째로 어긋나고, 주도섹터 판정과 후보 필터가 전부 그 위에 서 있다.
- 조치: 시총 상위 미분류를 `EXPLICIT_SECTOR_ADDITIONS` 에 코드로 직접 지정.
  현재 시총 기준 미분류 3.2%.
- **새 종목이 시총 상위로 올라오면 이 목록도 늘려야 한다.** 정기 점검 필요.

**섹터를 잘게 쪼개면 반대 방향 함정이 열린다.** 정규화 강도(시총 대비)는 시총이
작을수록 커지므로, 소형 섹터가 상위를 독식한다. 실측: 항공(3종목)이 강도 43.3 으로
1위에 올라 반도체장비·원전을 밀어내고 후보를 39→21개로 깎았다.
→ `FLOW_SECTOR_MIN_MEMBERS=6`. 3종목이 움직인 건 섹터 테마가 아니라 개별 종목이다.

**어설프게 묶느니 '기타'로 두는 편이 낫다.** 업종이 다른 종목을 한 버킷에 넣으면
(강원랜드+농심+CJ대한통운) 없던 섹터 강도가 만들어져 가짜 주도섹터가 된다.

### 주도섹터 판정 함정 (2026-08-07 실사고)

- **ETF 키워드 매칭은 첫 매칭에서 `break`**. 없으면 ETF 하나가 여러 섹터를 만든다.
  실제: `TIGER 글로벌AI인프라` 가 `"AI"` 와 `"인프라"` 에 동시 매칭돼 건설/인프라가
  RS 78.9 짜리 3위 주도섹터로 둔갑 → 포스코인터내셔널·대우건설·동원시스템즈가
  매수 후보 1·2·3위를 차지했다.
- **`NON_THEME_SECTORS = {보험, 은행}`**. 금리·밸류업으로 장기 RS 는 올라오지만
  단타 테마로 움직이지 않는다. KODEX 보험(RS 76.5)이 주도섹터가 되자 후보 11개 중
  4개가 보험사(롯데손해보험 등)로 채워졌다. 증권은 거래대금 테마라 유지.
- **수급 섹터는 강도와 절대금액을 모두 요구**(`FLOW_AMOUNT_MIN=300억`).
  절대금액만 보면 대형주 섹터가, 정규화 강도만 보면 초소형 섹터가 독식한다.
