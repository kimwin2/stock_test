# 베타 v0.1 배포 체크리스트 — 경로 C (공개 + 사용량 측정)

> **결정**: 로그인/회원가입 없이 **공개**로 열고, 분석툴로 사용량만 측정한다.
> 배포 형태: **PWA(웹 코어) → Tauri(데스크탑 exe) + Capacitor(모바일 앱)**.
> 회원가입을 안 하므로 개인정보 수집이 없고 PIPA 부담이 최소화된다.

상태 표기: `[ ]` 미착수 · `[~]` 진행중 · `[x]` 완료

---

## 0. 공통 기반 (어떤 타겟이든 필수)

- [x] **데이터 URL 원격 고정** — 호스트명 추측 제거. 패키징(exe/앱)에서 hostname 이
      `github.io` 가 아니어서 번들된 옛 JSON 을 보던 버그 수정.
      `frontend/app.js`, `frontend/flow.js` → 기본 S3, 로컬 개발만 `?local=1`.
- [ ] **차트 API URL 주입 확인** — `index.html` 의 `window.STOCK_CHART_API_URL` 이
      패키징 빌드에도 포함되는지 (현재 인라인 `<script>` 라 OK). 도메인 바뀌면 이 한 줄만 수정.

## 1. 🔴 법적/고지 (주식 서비스라 출시 전 필수)

- [ ] **면책고지(disclaimer)** 전 화면 노출 — "투자자문 아님 / 종목은 정보 제공일 뿐 /
      투자 판단·손실 책임은 이용자 본인" . 최초 진입 시 모달 1회 + 푸터 상시.
- [ ] **유사투자자문 규제 확인** — 불특정 다수 대상 종목 제공은 자본시장법상
      유사투자자문업 신고 대상이 될 수 있음. 베타 범위(초대/공개)·표현 수위 점검.
- [ ] **데이터 소스 ToS** — Naver mobile API, FinanceDataReader, Telegram(faststocknews)
      데이터를 외부 서비스로 재배포해도 되는지 확인. 개인 도구 ≠ 공개 서비스.
- [ ] **개인정보처리방침(최소판)** — 회원 미수집이라도 분석툴이 식별자(쿠키/디바이스ID)를
      쓰면 고지 의무 발생. 스토어 등록(Capacitor)에도 개인정보처리방침 URL 필수.

## 2. 🟡 비용·남용 방어 (트래픽 들어오면 바로 터짐)

- [ ] **차트 Lambda 보호** — `ChartApiFunction` 이 `AuthType: NONE` + CORS `*` (`template.yaml:179`).
      누가 루프 돌리면 Lambda+Naver fetch 비용 폭증. CloudFront 캐싱 또는 Lambda 동시성 제한(Reserved Concurrency) + 쓰로틀.
- [ ] **AWS Budget 알람** — 월 한도($5 등) 초과 시 이메일. Gemini/Lambda/S3 egress 폭주 조기 감지.
- [ ] **S3 egress 모니터링** — 공개 버킷이라 데이터 JSON 직접 핫링크 가능. CloudFront 앞단 권장.

## 3. 🟢 관측 (= 베타의 핵심 목적: 누가/얼마나 쓰나)

- [ ] **분석툴 1개** — GA4 또는 Plausible. 웹/PWA/Tauri/Capacitor 전부에서 동작.
      `platform`(web/desktop/android/ios) 커스텀 차원으로 타겟별 사용량 구분.
- [ ] **핵심 이벤트 정의** — 탭 전환(테마/수급), 종목 클릭, 차트 열람, 재방문.
- [ ] **에러 추적** — Sentry(무료 티어) 또는 최소한 console 에러 → 분석 이벤트.
- [ ] **피드백 채널** — 채널톡/구글폼/이메일 링크 1개. 베타는 피드백이 산출물.

## 4. 📦 패키징

### 4-1. PWA 코어 (모든 타겟의 공통 베이스)
- [ ] `manifest.webmanifest` (이름/아이콘/테마색/`display: standalone`)
- [ ] 서비스워커 — 정적 자산 캐시 + 오프라인 폴백 (데이터는 항상 네트워크)
- [ ] 앱 아이콘 세트 (192/512 + maskable)

### 4-2. 데스크탑 exe — Tauri
- [ ] Tauri 프로젝트 셸 (`frontend/` 를 `distDir` 로) + 윈도우 설정
- [ ] 빌드 산출물: Windows `.exe`/`.msi` (필요 시 mac `.dmg`)
- [ ] 외부 도메인 허용 목록(S3/차트 Lambda) CSP/allowlist 설정

### 4-3. 모바일 앱 — Capacitor
- [ ] Capacitor 래핑 (Android 우선) + WebView 설정
- [ ] 스토어 메타(아이콘/스크린샷/개인정보처리방침 URL/면책)
- [ ] 금융 카테고리 심사 대비 — 면책·출처 표기

## 5. 🚀 릴리스

- [ ] 커스텀 도메인 + HTTPS (GitHub Pages, PWA 설치용 안정 URL)
- [ ] 버전 표기 `v0.1.0-beta` (화면 푸터 + 빌드 메타)
- [ ] 베타 배포 채널 정리 (웹 링크 / exe 다운로드 / 스토어 또는 APK)

---

## 권장 진행 순서

1. **0번 + 1번 면책고지** — 어떤 형태로 나가든 막아야 할 최소 안전선
2. **3번 분석툴** — 측정 없는 베타는 의미 없음 (경로 C의 본질)
3. **2번 비용 알람** — 트래픽 받기 전에
4. **4번 PWA → Tauri → Capacitor** 순서로 패키징
5. **5번 릴리스**
