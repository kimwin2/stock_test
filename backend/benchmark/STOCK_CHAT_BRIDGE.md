# 레퍼런스 다리 — stock_chat → 대조 잡

`daily-benchmark.yml` 이 매일 저녁 도는데, GitHub Actions 에는 stock_chat
데이터가 없다. 레퍼런스 `data/` 는 평문이라 커밋하지 않는 정책이기 때문이다.
이 문서는 그 다리를 놓는 절차다.

**경계는 그대로다.** 가져오는 건 대조 잡이 읽을 정답지뿐이고, 제품(S3
산출물 `dashboard_data.json` / `flow_dashboard.json`, 사용자 화면) 어디에도
들어가지 않는다.

## 결론부터 — 시크릿 하나면 붙는다

stock_chat 은 **이미 매시간 그 데이터를 공개 주소로 내보내고 있다.**
GitHub Pages 의 `data/core.enc` 다. AES-256-GCM 으로 암호화돼 있어서 레포가
public 이어도 평문 노출이 없고, 그 안의 `days[]` 가 우리가 필요한 전부다
(`tickers` / `sectors` / `cash`). 즉 **stock_chat 쪽은 건드릴 게 없다.**

Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
|---|---|
| `SHARE_PASSPHRASE` | stock_chat 레포 시크릿의 `SHARE_PASSPHRASE` 와 **같은 값** |

베이스 주소가 `https://kimwin2.github.io/stock_chat` 가 아니라면 Variables 에
`STOCK_CHAT_BUNDLE_URL` 을 추가한다. 시크릿이 없으면 '레퍼런스 없음' 리포트를
내고 잡은 통과한다(실패로 두면 매일 빨간 X 만 쌓이고 아무도 안 본다).

### 확인

```bash
# 로컬에서 한 번 (stock_chat 체크아웃 없이)
cd backend
export SHARE_PASSPHRASE=...
REFERENCE_DAILY_DIR=/nonexistent python -m benchmark.daily_check --days 5

# 번들만 펼쳐 보기
python -m benchmark.stock_chat_bundle --out /tmp/ref && ls /tmp/ref
```

Job Summary 에 관문별 표가 나오면 다리가 붙은 것이다.

## 무엇이 새고 무엇이 안 새는가

`core.enc` 에는 채널 요약 문장·원문 인용·stock_chat 의 **Gemini 키와 GitHub
토큰**까지 들어있다. `stock_chat_bundle._slim()` 이 화이트리스트
(`date`/`stance`/`cash.kr`/`sectors`/`tickers`/`message_count`)로만 통과시키고
나머지는 디스크에 쓰지 않는다. 블랙리스트로 두면 저쪽이 필드를 추가할 때마다
하나씩 새므로 화이트리스트여야 한다. 필드를 늘릴 일이 생기면 이 경계부터 볼 것.

## 왜 S3 경유를 안 쓰는가 (이전 계획)

원래 계획은 stock_chat 에 워크플로를 하나 더 달아 그날 `data/daily/*.json` 을
`s3://stock-dashboard-data/reference/daily/` 로 올리는 것이었다. 되긴 하지만
비용이 더 든다:

- stock_chat 에 AWS 자격증명 시크릿을 새로 심어야 한다 (권한 표면이 늘어난다)
- 그 버킷은 프론트가 읽어야 해서 객체가 **공개**다. 레퍼런스 원문이 담긴 필드를
  깎아 올리지 않으면 그대로 노출된다
- 파이프라인이 하나 더 늘어 조용히 멈출 자리가 하나 더 생긴다

번들 경로는 그 셋이 전부 없다. 이미 도는 배포를 읽기만 한다.

`REFERENCE_DAILY_URL` 시크릿 경로는 **그대로 남겨뒀다** — 레퍼런스를 어딘가로
따로 실어 나를 일이 생기면 쓰면 된다. 번들 경로가 붙어 있으면 쓰이지 않는다.

## 주의

- **번들 포맷이 바뀌면 멈춘다.** `manifest.json` 의 `kdf`/`cipher` 를 확인하고
  다르면 예외를 던진다. 조용히 쓰레기를 만들지 않기 위해서다. stock_chat 의
  `pipeline/bundle.py` 를 고쳤다면 여기 상수(`EXPECT_*`)도 같이 본다.
- **공유 암호를 바꾸면 양쪽을 같이 바꿔야 한다.** stock_chat 시크릿만 바꾸면
  다음 날부터 '복호화 실패' 로 대조가 조용히 빈다.
- **수집이 조용히 멈춘다.** 초대 링크 만료가 반복된다. 리포트는 번들 생성
  시각을 같이 찍고, 6시간 이상 묵었으면 경고한다 — 데이터 나이만 보면
  수집기가 오늘 죽어도 3일간 조용하다(2026-08-10 실사고).
- **요약 대기 중인 날은 건너뛴다.** 오늘 글은 올라왔는데 요약이 아직 없으면
  번들에 `pending: true` 인 빈 껍데기가 들어간다. 그대로 채점하면
  "관심종목 0개 · 적중 0%" 라는 멀쩡해 보이는 거짓 리포트가 된다.

---

## 실측 확인 (2026-08-20)

양쪽 끝을 stock_chat 원본 코드와 대조했다. **계약은 전부 맞고, 빠진 건 시크릿 하나뿐이다.**

| 항목 | stock_chat (`pipeline/bundle.py`) | 이 레포 (`stock_chat_bundle.py`) | |
|---|---|---|---|
| 경로 | `WEB_DATA_DIR` = `web/data/` → Pages 루트 | `<base>/data/manifest.json`, `data/core.enc` | ✅ |
| KDF | PBKDF2-HMAC-SHA256, **600,000회**, salt 16B | 동일 | ✅ |
| 암호 | AES-256-GCM, 출력 `iv(12) ‖ ct+tag` | `IV_BYTES = 12` | ✅ |
| 평문 | gzip(JSON) | gunzip | ✅ |
| 일자 | `core["days"]`, `summary.archive_days` 기본 **400일** | `--days` 로 소급 대조 가능 | ✅ |
| 필드 | `date`/`stance`/`cash`/`tickers`/`sectors`/`message_count` | `KEEP_KEYS` 화이트리스트와 정확히 일치 | ✅ |

수집도 살아 있다 — `hourly.yml` 이 30분 간격(KST 05~24시)으로 돌고 최근 실행이 전부 성공이다.

> **`core["secrets"]` 에 `gemini_key`·`gh_token` 이 들어 있는 것을 원본에서 재확인했다.**
> `_slim()` 의 화이트리스트를 블랙리스트로 바꾸면 그 순간 새어 나간다. 저쪽이
> 필드를 추가할 때마다 샌다는 뜻이라, 이건 취향 문제가 아니다.

### 남은 한 칸

`stock_test` 레포에 **`SHARE_PASSPHRASE` 시크릿이 없다.** 실측 로그:

```
SHARE_PASSPHRASE:
STOCK_CHAT_BUNDLE_URL:
REFERENCE_DAILY_URL:
```

→ Settings → Secrets and variables → Actions → New repository secret
→ 이름 `SHARE_PASSPHRASE`, 값은 **stock_chat 레포에 넣어둔 것과 같은 값**.

넣고 나면 `Daily Benchmark` 를 `workflow_dispatch` 로 한 번 돌려 확인한다
(`days` 를 10 이상으로 주면 과거 날짜까지 한 번에 소급 대조된다).
