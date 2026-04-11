# 프론트엔드 개발 요청서 - Stock 주식 테마 대시보드

## 프로젝트 개요

실시간 주식 테마 분석 대시보드 **"Stock Premium"**의 프론트엔드를 개발해야 합니다.
백엔드에서 생성한 `dashboard_data.json` 파일을 읽어서 화면에 렌더링하는 단일 페이지 웹앱입니다.

> **핵심 목표**: 첨부된 스크린샷(`stock-screenshot.png`)과 **동일한 디자인**을, `dashboard_data.json` 데이터를 이용해 구현하는 것.

---

## 참고 파일 경로

| 파일 | 경로 | 설명 |
|------|------|------|
| **디자인 스크린샷** | `stock-screenshot.png` | 구현 대상 UI (반드시 이 이미지를 보고 동일하게 구현) |
| **프론트엔드 스펙** | `frontend.md` | 컴포넌트 상세 요구사항 |
| **데이터 소스** | `dashboard_data.json` | 백엔드가 생성하는 실제 JSON 데이터 |
| **연결 아키텍처** | `connect.md` | 추후 AWS 배포 시 연결 구조 (참고용) |

---

## 데이터 구조 (`dashboard_data.json`)

```json
{
  "updatedAt": "2026-04-11T00:03:29.846145",
  "themes": [
    {
      "themeName": "중동전쟁",
      "totalVolume": "12,885억",
      "headline": "휴전 기대감에 코스피 상승, 외국인 매수세 유입",
      "headlineUrl": "https://finance.naver.com/news/...",
      "stocks": [
        {
          "name": "대한광통신",
          "price": 17770,
          "time": "16:10",
          "changeRate": 25.32,
          "volume": "178억",
          "isTop": true,
          "barData": {
            "minMaxRange": [0, 100],
            "currentRange": [22, 89],
            "baseline": 0
          }
        },
        {
          "name": "GS건설",
          "price": 37650,
          "time": "16:10",
          "changeRate": 5.91,
          "volume": "377억",
          "isTop": false,
          "barData": {
            "minMaxRange": [0, 100],
            "currentRange": [26, 88],
            "baseline": 0
          }
        }
      ]
    }
  ]
}
```

### 필드 설명

| 필드 | 타입 | 설명 |
|------|------|------|
| `updatedAt` | string | 데이터 갱신 시각 (ISO 8601) |
| `themeName` | string | 테마명 (카드 헤더 좌측에 표시) |
| `totalVolume` | string | 테마 전체 거래대금 (카드 헤더 우측, 빨간색) |
| `headline` | string | 테마 관련 핵심 뉴스 한줄 요약 |
| `headlineUrl` | string | **headline 클릭 시 이동할 뉴스 링크 URL** |
| `name` | string | 종목명 |
| `price` | number | 현재가 (원 단위 정수) |
| `time` | string | 거래 시간 (예: "16:10") |
| `changeRate` | number | 등락률 (%, 양수=상승, 음수=하락) |
| `volume` | string | 거래대금 (예: "178억") |
| `isTop` | boolean | true인 종목은 1위 → 노란 배경 하이라이트 |
| `barData.minMaxRange` | [0, 100] | 고정값, 전체 범위 |
| `barData.currentRange` | [number, number] | 시가~현재가 위치 (0~100 스케일) |
| `barData.baseline` | number | 전일종가 기준선 위치 (0~100 스케일) |

---

## UI 컴포넌트 상세

### 1. Top Header
- 좌측: **"Stock"** (굵은 청록색) + **"Premium"** (주황색) 로고
- 중앙: `updatedAt`에서 파싱한 날짜/시간 표시 (예: "04-11(금) 00:03")
- 우측: 아이콘 3개 (뉴스, 히스토리, 캘린더) - 현재는 장식용
- 하단: 검색창 ("종목, 테마명을 입력하세요.") - 현재는 장식용

### 2. 테마 카드 (Theme Card)
- **둥근 모서리 흰색 카드** (카드 간 간격 적절히)
- **카드 헤더**: 청록색(`#00897B` 계열) 배경 + 흰색 테마명(좌측) + 빨간색 거래대금(우측)
- **서브 헤드라인**: 테마 아래 뉴스 요약 텍스트, **클릭 시 `headlineUrl`로 새 탭 열기** (`<a href="..." target="_blank">`)
- **종목 리스트**: 4개 종목 아이템

### 3. 주식 종목 아이템 (Stock Item) ⭐ 가장 중요
- `isTop: true`인 1위 종목은 **연한 노란색 배경** 하이라이트
- 레이아웃:
  ```
  [종목명]                    [↑등락률%]
  [현재가]  [시간]            [거래대금]
  [========= Range Bar =========]
  ```
- **등락률 색상**: 양수 → 빨간색(↑), 음수 → 파란색(↓), 0 → 검정
- **현재가 표시**: 천 단위 콤마 (예: 17,770)

### 4. 미니 차트 바 (Range Bar)
```
         baseline(전일종가)
              |
 ░░░░░░░░░░░░|░░░░░░░░░░░░░░    ← 회색 얇은 바 (저가~고가 전체범위, 0~100)
         ████████████            ← 빨간색 두꺼운 바 (시가~현재가, currentRange)
              |
```
- 회색 얇은 바: 전체 너비
- 빨간/파란 두꺼운 바: `currentRange[0]` ~ `currentRange[1]` 위치
  - 상승(changeRate > 0) → 빨간색
  - 하락(changeRate < 0) → 파란색
- 검은 세로선(tick): `baseline` 위치에 표시

### 5. 레이아웃
- **2단 그리드**: PC/태블릿에서 카드가 2열로 배치
- **1단**: 모바일에서는 1열
- 스크린샷 참고: 카드가 가로로 스크롤 가능한 형태도 고려

---

## 기술 요구사항

1. **HTML + CSS + JavaScript** 단일 페이지로 구현 (프레임워크 없이 가능)
2. `dashboard_data.json`을 `fetch()`로 로드
3. 반응형 디자인 (모바일 1열, PC 2열 이상)
4. `headline` 텍스트 클릭 → `headlineUrl` 링크로 새 탭 이동
5. 가격은 천 단위 콤마 포맷팅 (예: 1,027,000)
6. 파일 위치: `dashboard_data.json`은 프론트엔드와 같은 디렉터리(`front-end/`)에 위치

---

## 색상 팔레트 (스크린샷 기준)

| 용도 | 색상 |
|------|------|
| 앱 배경 | `#E8F5E9` (연한 민트/회색) |
| 카드 헤더 배경 | `#00897B` (청록색/틸) |
| 카드 본문 배경 | `#FFFFFF` |
| 거래대금 텍스트 | `#FF1744` (빨간색) |
| 상승 등락률 | `#FF1744` (빨간색) |
| 하락 등락률 | `#2962FF` (파란색) |
| 1위 종목 배경 | `#FFF9C4` (연한 노랑) |
| 상승 Range Bar | `#FF1744` (빨간색) |
| 하락 Range Bar | `#2962FF` (파란색) |
| 로고 "Stock" | `#00897B` (청록색) |
| 로고 "Premium" | `#FF9800` (주황색) |

---

## 주의사항

1. **스크린샷과 동일한 디자인**을 목표로 합니다. `stock-screenshot.png`를 반드시 보고 구현하세요.
2. `headlineUrl`이 있으면 headline을 `<a>` 태그로 감싸서 클릭 가능하게 만드세요.
3. `changeRate`가 음수인 종목은 파란색, 양수인 종목은 빨간색으로 표시합니다.
4. `isTop: true`인 종목만 노란 배경을 적용합니다.
5. 현재 데이터에는 테마 5개 × 종목 4개 = 총 20개 종목 아이템이 있습니다.
6. price가 0인 종목(예: 비상장)은 "-" 로 표시하거나 해당 아이템을 숨기세요.
