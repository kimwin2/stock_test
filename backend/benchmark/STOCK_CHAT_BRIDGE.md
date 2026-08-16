# 레퍼런스 다리 — stock_chat → 대조 잡

`daily-benchmark.yml` 이 매일 저녁 도는데, GitHub Actions 에는 stock_chat
데이터가 없다. 레퍼런스 `data/` 는 평문이라 커밋하지 않는 정책이기 때문이다.
이 문서는 그 다리를 놓는 절차다.

**경계는 그대로다.** 올리는 건 대조 잡이 읽을 정답지일 뿐이고, 제품(S3
산출물 `dashboard_data.json` / `flow_dashboard.json`, 사용자 화면) 어디에도
들어가지 않는다. 버킷 안에서도 `reference/` 로 완전히 분리한다.

## 1. stock_chat 에 추가할 워크플로

수집(`daily.yml`)이 끝난 뒤 그날 JSON 하나만 올린다.

```yaml
name: Publish Daily Reference

on:
  workflow_run:
    workflows: ["Daily"]        # stock_chat 의 기존 수집 워크플로 이름에 맞출 것
    types: [completed]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  publish:
    # 수집이 실패한 날 옛 파일을 다시 올리면 대조가 낡은 정답지로 채점한다.
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ap-northeast-2

      - name: Upload today's reference
        shell: bash
        run: |
          set -euo pipefail
          DATE="$(TZ=Asia/Seoul date +%F)"
          SRC="data/daily/${DATE}.json"
          if [ ! -f "$SRC" ]; then
            echo "::warning::$SRC 없음 — 오늘 수집분이 없다. 올리지 않는다."
            exit 0
          fi
          aws s3 cp "$SRC" "s3://stock-dashboard-data/reference/daily/${DATE}.json" \
            --content-type application/json
          echo "uploaded ${DATE}"
```

> `data/daily/` 가 CI 러너에 남지 않는 구조라면(수집을 로컬에서만 돌린다면)
> 이 워크플로 대신 수집 스크립트 끝에 같은 `aws s3 cp` 한 줄을 붙이면 된다.

## 2. 이 레포(stock_test)에 시크릿 추가

Settings → Secrets and variables → Actions → New repository secret

| 이름 | 값 |
|---|---|
| `REFERENCE_DAILY_URL` | `https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/reference/daily` |

`daily_check.py` 가 `<base>/YYYY-MM-DD.json` 으로 받는다. 시크릿이 없으면
'레퍼런스 없음' 리포트를 내고 잡은 통과한다(실패로 두면 매일 빨간 X 만 쌓인다).

## 3. 확인

```bash
# 올라갔는지
curl -sI https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/reference/daily/$(TZ=Asia/Seoul date +%F).json

# 대조를 수동으로 한 번
# Actions → Daily Benchmark (관심종목 대조) → Run workflow
```

Job Summary 에 관문별 표가 나오면 다리가 붙은 것이다.

## 주의

- **버킷 공개 범위.** `stock-dashboard-data` 는 프론트가 읽어야 해서 객체가
  공개다. `reference/` 도 같은 성질이 되므로, 레퍼런스 원문 문장이 그대로
  담긴 필드가 있다면 올리기 전에 `tickers`/`sectors`/`cash` 만 남기고 깎을 것.
  대조에 필요한 건 그 셋뿐이다(`reference.load_reference` 가 읽는 키).
- **수집이 조용히 멈춘다.** 초대 링크 만료가 반복된다. 위 워크플로는 수집
  성공일 때만 올리므로, 링크가 죽으면 새 파일이 안 올라오고 대조 리포트에
  '레퍼런스 없음' 이 뜬다 — 그게 알람 역할을 한다.
