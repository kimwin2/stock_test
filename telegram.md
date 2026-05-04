# Telegram Integration Plan

`@faststocknews` 채널을 기존 백엔드 파이프라인에 선행 시그널 소스로 추가하기 위한 설계 문서입니다.  
이번 단계의 목표는 "텔레그램 메시지를 5분 단위로 수집하고, 기존 GPT 분석 컨텍스트에 병렬로 넣을 수 있는 구조를 준비하는 것"입니다.

## 1. 목표

- 네이버 뉴스보다 빠른 장중 텔레그램 시그널을 수집한다.
- 기존 `news -> analyzer -> stock_data -> dashboard_data.json` 구조는 유지한다.
- 실시간 상주 서버는 두지 않고, 스케줄 기반 서버리스 방식으로 운영한다.
- 1차 대상 채널은 `@faststocknews` 한 곳만 사용한다.
- 운영 시간은 `KST 기준 08:00~18:00`으로 제한한다.

## 2. 수집 방식

### 왜 Bot API가 아니라 Telethon인가

- 외부 공개 채널을 안정적으로 읽고 과거 메시지를 가져오려면 Telegram Bot API보다 MTProto 클라이언트 방식이 유리하다.
- 따라서 v1은 `Telethon + Telegram user session(StringSession)` 구조로 간다.
- 인증은 수집 전용 텔레그램 계정 1개로 수행한다.

### 메시지를 어떻게 가져올지

- 대상 채널: `@faststocknews`
- 수집 주기: `5분`
- 수집 범위: 마지막으로 처리한 `message_id` 이후의 새 메시지
- 백필 기준: 첫 실행 또는 상태 유실 시 최근 `60분` 메시지만 복구
- 수집 대상 필드
  - 메시지 ID
  - 채널 username
  - 게시 시각
  - 본문 텍스트
  - 캡션 텍스트
  - 조회수
  - 전달수
  - 메시지 링크

### v1에서 하지 않을 것

- 다중 채널 동시 수집
- 이미지 OCR
- 음성/영상 파싱
- 초단위 스트리밍
- 텔레그램만으로 테마를 확정하는 규칙 엔진

## 3. 서버 운영 방식

### 권장 구조

기존 `stock-pipeline` Lambda는 유지하고, 텔레그램 전용 수집 Lambda를 별도로 추가한다.

```text
EventBridge Scheduler (5분)
  -> telegram-signal-collector Lambda
  -> S3에 최신 텔레그램 시그널 저장

EventBridge Rule/Scheduler (기존 10분)
  -> stock-pipeline Lambda
  -> 뉴스 + 유튜브 + 텔레그램 시그널을 함께 GPT에 전달
  -> dashboard_data.json 생성
```

### 왜 별도 Lambda로 분리하는가

- 텔레그램 수집 실패가 메인 대시보드 생성 실패로 바로 전이되지 않는다.
- 수집 주기와 대시보드 생성 주기를 독립적으로 운영할 수 있다.
- 텔레그램 인증과 수집 로직을 메인 파이프라인에서 분리할 수 있다.

### 스케줄 정책

- 수집 주기: `5분`
- 운영 시간: `매 영업일 KST 08:00~18:00`
- 기본 수집은 `08:00~17:55` 5분 간격으로 돌리고, `18:00`은 별도 1회 스케줄로 마감 수집한다.

### AWS 스케줄 권장안

현재 `template.yaml`은 `Schedule` 기반 EventBridge Rule을 사용 중이다.  
텔레그램 수집기는 시간대 제어가 필요하므로 `ScheduleV2` 기반 EventBridge Scheduler로 추가하는 것을 권장한다.

- 이유 1: `Asia/Seoul` timezone을 직접 지정할 수 있다.
- 이유 2: UTC 환산 cron을 쪼개지 않아도 된다.
- 이유 3: 수집 전용 스케줄을 메인 파이프라인과 분리하기 쉽다.

권장 스케줄 표현:

- `cron(0/5 8-17 ? * MON-FRI *)`
- `cron(0 18 ? * MON-FRI *)`
- `ScheduleExpressionTimezone: Asia/Seoul`

참고: AWS SAM의 `ScheduleV2`는 EventBridge Scheduler 리소스를 생성하고 timezone 지정이 가능하다.  
출처:
- https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-schedulev2.html
- https://docs.aws.amazon.com/eventbridge/latest/userguide/using-eventbridge-scheduler.html

## 4. 코드 구조 초안

```text
backend/
├── main.py
├── handler.py
├── analyzer.py
├── stock_data.py
├── youtube_signals.py
└── telegram/
    ├── __init__.py               # 텔레그램 패키지 진입점
    ├── bootstrap.py              # 로컬 1회용 세션 발급 도구
    ├── client.py                 # Telethon 클라이언트 생성/인증
    ├── collector.py              # 채널 메시지 수집 및 정규화
    ├── scoring.py                # 메시지 점수화 규칙
    ├── store.py                  # S3/로컬 JSON 저장소 추상화
    ├── models.py                 # 텔레그램 메시지/상태 스키마 정의
    ├── handler.py                # 텔레그램 전용 Lambda 핸들러
    └── dev/
        ├── telegram_state.json   # 로컬 개발용 상태 파일
        └── telegram_signals.json # 로컬 개발용 최신 시그널 파일
```

### 각 파일 역할

#### `backend/telegram/bootstrap.py`

- 로컬 1회 실행용
- 전화번호, 인증 코드, 2FA를 통해 `StringSession` 생성
- 생성된 세션 문자열을 콘솔로 출력
- 배포 대상은 아니고 운영 준비용 도구다

#### `backend/telegram/client.py`

- `Telethon` 클라이언트 생성
- 환경변수 기반 인증 설정 로드
- `StringSession`으로 로그인된 세션 구성
- 채널 resolve에 필요한 공통 연결 로직 제공

#### `backend/telegram/collector.py`

- `@faststocknews` 채널 resolve
- 마지막 수집 상태를 읽고 신규 메시지만 fetch
- 메시지 본문/캡션 정규화
- `STOCK_CODE_MAP` 기반 종목명 매칭
- 점수화 전 입력 데이터 생성
- GPT 입력에 맞는 요약용 구조 반환

#### `backend/telegram/scoring.py`

- 메시지 랭킹 규칙 담당
- 최근성, 종목명 포함 여부, 키워드, 조회수, 전달수를 반영
- GPT에 넘길 상위 메시지 선별 기준 제공

#### `backend/telegram/store.py`

- 텔레그램 수집 상태 저장
- 최신 시그널 저장
- 로컬에서는 JSON 파일, Lambda에서는 S3를 사용하는 인터페이스 담당
- 책임
  - `load_telegram_state`
  - `save_telegram_state`
  - `load_telegram_signals`
  - `save_telegram_signals`

#### `backend/telegram/models.py`

- 텔레그램 메시지, 상태, GPT 입력용 구조를 dataclass 또는 TypedDict로 정의
- 수집기와 메인 파이프라인 사이의 데이터 계약 역할

#### `backend/telegram/handler.py`

- 텔레그램 전용 Lambda 진입점
- 실행 시간 가드
- `collector.py` 호출
- 결과를 S3에 저장
- 간단한 실행 메트릭 반환

#### `backend/analyzer.py`

- 텔레그램 시그널 읽기 함수 추가
- `format_telegram_signals_for_prompt()` 추가
- `backend.telegram.store`에서 최신 텔레그램 시그널 로드
- 뉴스/유튜브/텔레그램을 하나의 컨텍스트로 조합
- 프롬프트 상에서 텔레그램은 "실시간 선행 시그널"로 가중치를 부여

#### `backend/main.py`

- 로컬 테스트 시 `backend/telegram/dev/telegram_signals.json` 또는 S3 최신 시그널을 읽을 수 있게 조정
- 최종 JSON에 `telegramSignals` 필드 포함

#### `backend/handler.py`

- Lambda 메인 파이프라인 실행 시 `backend.telegram.store`를 통해 S3의 최신 텔레그램 시그널을 읽어 GPT 분석에 전달
- 최종 `dashboard_data.json`에도 `telegramSignals`를 포함

## 5. 데이터 스키마 초안

### 5-1. 텔레그램 원시/정규화 메시지 스키마

파일 예시: `signals/telegram_faststocknews_latest.json`  
로컬 개발용 경로 예시: `backend/telegram/dev/telegram_signals.json`

```json
{
  "channel": "@faststocknews",
  "collectedAt": "2026-04-14T08:35:00+09:00",
  "windowMinutes": 60,
  "lastMessageId": 18342,
  "items": [
    {
      "messageId": 18342,
      "postedAt": "2026-04-14T08:33:12+09:00",
      "text": "[속보] 스페이스X 관련주 강세...",
      "views": 12450,
      "forwards": 182,
      "url": "https://t.me/faststocknews/18342",
      "matchedStocks": ["AP위성", "켄코아에어로스페이스", "쎄트렉아이"],
      "keywords": ["스페이스X", "우주항공", "강세"],
      "score": 0.91
    }
  ]
}
```

필드 의미:

- `channel`: 수집 채널 식별자
- `collectedAt`: 수집 실행 시각
- `windowMinutes`: 장애 복구 시 되돌아보는 최대 시간 창
- `lastMessageId`: 다음 수집의 커서
- `items[]`: GPT에 전달 가능한 최근 메시지 목록
- `score`: 최근성, 종목명 포함 여부, 키워드, 조회/전달 수를 반영한 내부 랭킹 점수

### 5-2. 수집 상태 스키마

파일 예시: `signals/telegram_faststocknews_state.json`  
로컬 개발용 경로 예시: `backend/telegram/dev/telegram_state.json`

```json
{
  "channel": "@faststocknews",
  "lastMessageId": 18342,
  "lastCollectedAt": "2026-04-14T08:35:00+09:00",
  "lastSuccessAt": "2026-04-14T08:35:02+09:00",
  "consecutiveFailures": 0
}
```

필드 의미:

- `lastMessageId`: 마지막으로 저장 완료한 메시지 ID
- `lastCollectedAt`: 수집 시도 시각
- `lastSuccessAt`: 성공 저장 시각
- `consecutiveFailures`: 장애 모니터링용 카운터

### 5-3. GPT 입력용 텔레그램 시그널 스키마

`analyzer.py`에는 전체 메시지를 그대로 넣지 않고, 상위 메시지만 요약해 전달한다.

```json
[
  {
    "postedAt": "2026-04-14T08:33:12+09:00",
    "text": "[속보] 스페이스X 관련주 강세...",
    "matchedStocks": ["AP위성", "켄코아에어로스페이스", "쎄트렉아이"],
    "keywords": ["스페이스X", "우주항공"],
    "score": 0.91
  }
]
```

### 5-4. 최종 대시보드 JSON 확장안

기존 `dashboard_data.json`에 텔레그램 필드를 추가한다.

```json
{
  "updatedAt": "2026-04-14T08:40:00+09:00",
  "youtubeSignals": [],
  "telegramSignals": [
    {
      "postedAt": "2026-04-14T08:33:12+09:00",
      "text": "[속보] 스페이스X 관련주 강세...",
      "matchedStocks": ["AP위성", "켄코아에어로스페이스", "쎄트렉아이"],
      "keywords": ["스페이스X", "우주항공"],
      "score": 0.91
    }
  ],
  "themes": []
}
```

## 6. 환경변수 초안

### 텔레그램 수집기용

- `TG_API_ID`
- `TG_API_HASH`
- `TG_STRING_SESSION`
- `TG_CHANNEL_USERNAME=@faststocknews`
- `TG_LOOKBACK_MINUTES=180`
- `TG_MAX_ITEMS=20`
- `TG_MIN_SCORE=0.35`

### 기존 메인 파이프라인용

- `GEMINI_API_KEY`
- `S3_BUCKET_NAME`
- `S3_KEY`
- `TELEGRAM_SIGNAL_S3_KEY=signals/telegram_faststocknews_latest.json`
- `TELEGRAM_STATE_S3_KEY=signals/telegram_faststocknews_state.json`

## 7. 메시지 처리 규칙 초안

### 수집 후 정규화

- 본문이 없고 캡션만 있으면 캡션을 사용한다.
- 줄바꿈과 공백은 단일 공백으로 정리한다.
- URL만 있는 메시지는 우선순위를 낮춘다.
- 너무 짧은 메시지나 광고성 문구는 제외한다.

### 점수화

- 최근 15분 메시지 가산점
- 종목명 매칭 시 가산점
- `속보`, `특징주`, `강세`, `수주`, `급등`, `상한가` 포함 시 가산점
- 조회수/전달수는 보조 점수로만 반영

### GPT 전달 기준

- 최근 60분 메시지 중 상위 10~15개만 전달
- 동일 키워드 반복 메시지는 묶거나 하위 점수 항목 제거
- 종목명이 전혀 없는 메시지는 우선순위를 낮춘다

## 8. 분석 프롬프트 반영 방식

`analyzer.py`에는 아래 의미의 새 컨텍스트 블록을 추가한다.

```text
=== 실시간 텔레그램 시그널 ===
- 08:33 | 스페이스X 관련주 강세 | 종목: AP위성, 켄코아에어로스페이스, 쎄트렉아이
- 08:31 | 세종시 관련 정책 기대감 | 종목: 유라테크, 프럼파스트
```

GPT 지침은 아래 방향으로 둔다.

- 텔레그램 시그널은 장중 선행 시그널로 간주한다.
- 뉴스와 텔레그램이 겹치면 높은 우선순위로 반영한다.
- 텔레그램만 있고 뉴스가 약한 경우에도 "초기 형성 테마" 후보로 반영한다.
- 다만 근거가 약한 단일 메시지는 과도하게 확대 해석하지 않는다.

## 9. 운영/장애 대응

### 장애 허용 원칙

- 텔레그램 수집 실패가 메인 파이프라인 전체 실패로 이어지지 않게 한다.
- 메인 파이프라인은 텔레그램 시그널이 없으면 빈 배열로 계속 동작한다.

### 최소 모니터링 항목

- 마지막 수집 성공 시각
- 최근 실패 횟수
- 최근 저장 메시지 수
- 마지막 `message_id`

### 복구 전략

- 세션 만료 시 `backend/telegram/bootstrap.py`로 새 `StringSession` 재발급
- 상태 파일 유실 시 최근 60분만 재수집
- 중복 메시지는 `messageId` 기준으로 제거

## 10. 구현 순서

1. `backend/telegram/bootstrap.py`로 수집 전용 세션 발급
2. `backend/telegram/client.py`와 `backend/telegram/collector.py`에서 단일 채널 수집 로직 구현
3. `backend/telegram/store.py`와 `backend/telegram/models.py`로 상태/시그널 저장 구조 분리
4. `backend/telegram/handler.py` + `ScheduleV2` 스케줄 추가
5. `analyzer.py`에 텔레그램 컨텍스트 주입
6. `main.py`, `handler.py`의 최종 JSON에 `telegramSignals` 추가
7. 프런트는 우선 숨기거나 디버그용으로만 노출

## 11. 이번 단계에서의 결정 사항

- 채널은 `@faststocknews` 하나만 사용한다.
- 수집 주기는 `5분`으로 한다.
- 운영 시간은 `KST 08:00~18:00`으로 하고, `18:00`은 별도 1회 스케줄로 수집한다.
- 수집기는 별도 Lambda로 분리한다.
- 인증은 `Telethon + StringSession` 방식으로 한다.
- 저장소는 현재 아키텍처와 맞춰 `S3 + JSON` 중심으로 간다.
- 기존 메인 백엔드 파이프라인은 유지하고, 텔레그램은 병렬 시그널 소스로만 추가한다.
