# 베타 v0.1 배포 체크리스트 — 경로 C (공개 + 사용량 측정)

> **결정**: 로그인/회원가입 없이 **공개**로 열고, 필요해지면 사용량만 측정한다.
> 회원 미수집이라 PIPA 부담 최소. 배포 형태는 추후 PWA→Tauri(exe)→Capacitor(앱).

상태: `[ ]` 미착수 · `[x]` 완료

---

## ✅ v0.1 필수 — 이것만 하면 띄운다 (3개)

- [x] **데이터 URL 원격 고정** — 패키징 시 죽은 데이터 뜨던 버그 수정.
      `frontend/app.js`, `frontend/flow.js` 기본 S3, 로컬 개발만 `?local=1`.
- [x] **면책고지** — "투자자문 아님 / 판단·손실 책임은 본인". 최초 1회 동의 모달
      + 하단 상시 한 줄. `frontend/index.html` + `style.css`.
- [ ] **AWS Budget 알람** — 코드 아님, 콘솔 5분. 차트 Lambda(무인증) 남용 시 요금폭탄 방어.
      ```
      AWS 콘솔 → Billing → Budgets → Create budget
      → Monthly cost budget, 한도 $5(예) → 80%/100% 도달 시 이메일 알림
      ```

→ 위 3개 끝나면 **링크 공유로 베타 오픈 가능.**

---

## ⏭️ 나중 — 필요해질 때 (v0.1 관문 아님)

- [ ] **분석툴** — 사용량 궁금해지면 GA4 또는 Plausible 한 줄 삽입.
      (Plausible = 쿠키리스라 개인정보 부담 적음)
- [ ] **에러 추적 / 피드백 채널** — Sentry, 구글폼/이메일 링크.
- [ ] **차트 Lambda 강화** — 트래픽 늘면 CloudFront 캐싱 또는 Reserved Concurrency 제한.
- [ ] **커스텀 도메인 + HTTPS** — PWA 설치/스토어용 안정 URL.
- [ ] **개인정보처리방침** — 분석 식별자 수집하거나 스토어 등록 시 필요해짐.
- [ ] **데이터 소스 ToS 확인** — Naver/FDR/Telegram 외부 재배포 가능 여부.
- [ ] **유사투자자문 규제 확인** — 공개 범위·표현 수위가 커지면 점검.

## 📦 패키징 (별개 작업 — 앱/exe 만들 준비되면)

- [ ] **PWA 코어** — manifest + 서비스워커 + 아이콘. (Tauri/Capacitor 공통 베이스)
- [ ] **Tauri** — 데스크탑 exe (~5MB)
- [ ] **Capacitor** — 모바일 앱 (Android 우선) + 스토어 메타
