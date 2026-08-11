"""ETF 기반 주도 업종/테마 추출.

퀀트 분석 로직:
1. Mansfield RS (60/120/250일) 평균 → 0~100 정규화 → 70 이상 = 주도
2. 변동성 조정 모멘텀 (3/6/12개월 수익률 / 표준편차의 평균)
3. Sortino (다운사이드 변동성 대비 수익률)
4. 10/20/50일 이동평균선 위 종가만 통과

본 구현은 위 셋 중 (1) Mansfield RS + (2) 변동성 조정 모멘텀만.
ETF 풀은 시가총액 상위 + 테마성 ETF 선별.
"""

from __future__ import annotations

import time
import numpy as np
import pandas as pd

from .data_sources import fetch_etf_listing, fetch_stock_ohlcv, fetch_index_ohlcv


# 주요 테마/업종 ETF 핸드픽 (티커 → 라벨)
#
# [2026-08-10 전수 정정] 46개 중 25개의 코드가 라벨과 다른 ETF를 가리키고 있었다.
# RS 는 **코드의 실제 주가**로 계산하고 섹터 매핑은 **라벨의 키워드**로 하기 때문에,
# 둘이 어긋나면 "엉뚱한 ETF 의 강도로 엉뚱한 섹터가 주도로 뽑힌다".
#   실측: 479850 은 'TIGER 글로벌AI인프라' 가 아니라 'HANARO K-뷰티' 였다.
#         K-뷰티 ETF 가 강한 날 AI/반도체팹리스 가 주도섹터 2위로 올라갔다.
#         266390 은 'KODEX 2차전지산업' 이 아니라 'KODEX 경기소비재' 였다
#         (그래서 신세계가 '2차전지 ETF 편입종목' 으로 잡혔고, 그 덕에 발견했다).
# 운용사가 코드를 재활용·상장폐지하므로 이 목록은 **썩는다**. 아래 검증 가드가
# 매 실행마다 상장 목록과 대조해 라벨을 실제 이름으로 덮고 어긋남을 출력한다.
THEME_ETFS = {
    # 지수
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "278530": "KODEX 200TR",
    # 반도체
    "091160": "KODEX 반도체",
    "091230": "TIGER 반도체",
    "396500": "TIGER 반도체TOP10",
    "139260": "TIGER 200 IT",
    "261060": "TIGER 코스닥150 IT",   # 232080 은 'TIGER 코스닥150'(IT 아님) 이었다
    "395160": "KODEX AI반도체TOP2플러스",
    "455850": "SOL AI반도체소부장",
    # 2차전지
    "305720": "KODEX 2차전지산업",     # 266390(=KODEX 경기소비재) 제거
    "364980": "TIGER 2차전지TOP10",    # 라벨이 'TIGER KRX BBIG K-뉴딜' 로 틀려 있었다
    # 자동차/소비재
    "091180": "KODEX 자동차",          # 098560 은 상장 목록에 없는 죽은 코드였다
    "228790": "TIGER 화장품",          # 228810(=TIGER 미디어컨텐츠) 제거
    "227550": "TIGER 200 산업재",      # 139290 은 'TIGER 200 경기소비재' 였다
    # 화학/철강/건설
    "117460": "KODEX 에너지화학",
    "117680": "KODEX 철강",
    "139220": "TIGER 200 건설",
    # 조선/방산/우주
    "139230": "TIGER 200 중공업",      # 139250(=TIGER 200 에너지화학) 제거
    "0115D0": "KODEX 조선TOP10",       # 117700 은 'KODEX 건설' 이었다
    "449450": "PLUS K방산",            # 456600(=TIME 글로벌AI인공지능액티브) 제거
    "463250": "TIGER K방산&우주",
    "421320": "PLUS 우주항공",
    # 전력/원전 — 주도섹터로 자주 지목되는데 ETF 가 하나도 없었다
    "487240": "KODEX AI전력핵심설비",
    "0117V0": "TIGER 코리아AI전력기기TOP3플러스",
    "434730": "HANARO 원자력iSelect",
    "0098F0": "KODEX 원자력SMR",
    # 로봇 ETF — 이전에 454910(=두산로보틱스 개별 종목)이 잘못 등록돼 있어 RS 가
    # 음수로 잡혀서 로봇 섹터가 leading 에 못 들어갔음. 정정.
    "445290": "KODEX 로봇액티브",
    # 게임/소프트웨어
    "300950": "KODEX 게임산업",        # 449180 은 'KODEX 미국S&P500(H)' 였다
    "157490": "TIGER 소프트웨어",
    # 바이오/헬스케어
    "244580": "KODEX 바이오",
    "227540": "TIGER 200 헬스케어",    # 227560 은 'TIGER 200 생활소비재' 였다
    # 금융 — 5/4·5/6 참고 자료 분석에서 "수급+시세 교집합 핵심"으로 강조됨.
    # 외국인 통합계좌 규제 완화·IBKR 한국주식 직거래 오픈 등 구조적 모멘텀 반영.
    "102970": "KODEX 증권",            # 102780 은 'KODEX 삼성그룹' 이었다
    "140700": "KODEX 보험",
    "091170": "KODEX 은행",
    "139270": "TIGER 200 금융",
    "279530": "KODEX 고배당주",
}

# 해외 ETF 는 전부 뺐다. 한국 섹터로 매핑될 수 없어 sector_map 이 '기타' 로
# 버리는데, RS 상위는 차지해 leading 목록에서 국내 테마를 밀어냈고 (실측:
# 2026-08-10 주도 ETF 1위가 미국 S&P500 이었다) 편입비중 조회 비용만 늘렸다.


def resolve_theme_etfs(verbose: bool = True) -> dict[str, str]:
    """THEME_ETFS 를 상장 목록의 **실제 이름**으로 덮는다. 목록에 없으면 버린다.

    핸드픽 목록은 썩는다 — 운용사가 코드를 재활용하고 ETF 를 상장폐지한다.
    그런데 RS 는 코드로, 섹터 매핑은 이름으로 하므로 어긋남이 조용히 가짜
    주도섹터를 만든다. 매 실행 상장 목록을 정답으로 삼아 이름을 맞추고,
    어긋난 것은 눈에 띄게 출력해 다음 사람이 목록을 고칠 수 있게 한다.
    """
    try:
        df = fetch_etf_listing()
    except Exception as e:
        if verbose:
            print(f"  [!] ETF 상장 목록 조회 실패 — 핸드픽 라벨 그대로 사용: {e}")
        return dict(THEME_ETFS)

    name_col = next((c for c in ["Name", "Symbol Name", "종목명"] if c in df.columns), None)
    code_col = next((c for c in ["Symbol", "Code", "종목코드"] if c in df.columns), None)
    if not name_col or not code_col:
        return dict(THEME_ETFS)
    listed = {str(c).zfill(6): str(n) for c, n in zip(df[code_col], df[name_col])}

    resolved: dict[str, str] = {}
    drifted, missing = [], []
    for code, label in THEME_ETFS.items():
        actual = listed.get(code)
        if actual is None:
            missing.append(f"{code}({label})")
            continue
        if actual.replace(" ", "") != label.replace(" ", ""):
            drifted.append(f"{code}: 기대 '{label}' → 실제 '{actual}'")
        resolved[code] = actual
    if verbose and drifted:
        print(f"  [!] ETF 라벨 불일치 {len(drifted)}건 — 실제 이름으로 덮었습니다. "
              f"THEME_ETFS 를 고치세요:")
        for d in drifted:
            print(f"        {d}")
    if verbose and missing:
        print(f"  [!] 상장 목록에 없는 ETF {len(missing)}건 — 제외: {', '.join(missing)}")
    return resolved


def _mansfield_rs(etf: pd.Series, benchmark: pd.Series, ma_period: int) -> pd.Series:
    relative = etf / benchmark
    ma = relative.rolling(window=ma_period, min_periods=ma_period).mean()
    return ((relative / ma) - 1) * 100


def _normalize_to_100(x: float, scale: float = 12.0) -> float:
    return float(100 * (1 / (1 + np.exp(-x / scale))))


def _vol_adjusted_momentum(prices: pd.Series, windows=(63, 126, 252)) -> float:
    rets = prices.pct_change(fill_method=None)
    scores = []
    for w in windows:
        if len(rets.dropna()) < w * 0.5:
            continue
        seg = rets.tail(w)
        mean_r = seg.mean(skipna=True)
        std_r = seg.std(skipna=True)
        if std_r and std_r > 0:
            scores.append(mean_r / std_r)
    if not scores:
        return float("nan")
    return float(np.mean(scores))


def compute_etf_rs(
    sleep_sec: float = 0.05,
    benchmark: str = "KS11",
) -> pd.DataFrame:
    benchmark_df = fetch_index_ohlcv(benchmark, days=400)
    if benchmark_df.empty:
        raise RuntimeError("벤치마크 데이터 가져오기 실패")
    bench_close = benchmark_df["Close"]

    rows: list[dict] = []
    # 핸드픽 라벨이 아니라 상장 목록의 실제 이름을 쓴다. 섹터 매핑이 이름
    # 키워드로 이뤄지므로, 여기서 어긋나면 가짜 주도섹터가 만들어진다.
    for code, label in resolve_theme_etfs().items():
        try:
            df = fetch_stock_ohlcv(code, days=400)
        except Exception as e:
            print(f"  [!] ETF {code} 실패: {e}")
            continue
        if df.empty or len(df) < 60:
            continue

        close = df["Close"].dropna()
        common = close.index.intersection(bench_close.index)
        etf_aligned = close.loc[common]
        bench_aligned = bench_close.loc[common]

        rs_values = []
        for w in [60, 120, 250]:
            if len(etf_aligned) < w:
                continue
            rs = _mansfield_rs(etf_aligned, bench_aligned, ma_period=w).dropna()
            if not rs.empty:
                rs_values.append(rs.iloc[-1])

        if not rs_values:
            continue
        rs_avg_raw = float(np.mean(rs_values))
        rs_avg_norm = _normalize_to_100(rs_avg_raw)

        vam = _vol_adjusted_momentum(etf_aligned)

        # 단기 축 — 6개월 RS 는 구조적으로 느려서 '오늘 도는 돈' 을 못 잡는다.
        # 숙련 트레이더가 주도업종을 고를 때 실제로 보는 건 섹터지수의
        # 10주(=50거래일)·10일 이평 상회 여부다. 그 두 축을 그대로 만든다.
        # 실측(2026-08-11): 전력기기·원전 ETF 가 rsNorm 49.5 / 34.4 라 장기
        # 기준으로는 한참 아래인데, 그날 시장의 주도업종은 데이터센터·전력이었다.
        ma10d = float(etf_aligned.tail(10).mean()) if len(etf_aligned) >= 10 else None
        ma10w = float(etf_aligned.tail(50).mean()) if len(etf_aligned) >= 50 else None
        last_close = float(etf_aligned.iloc[-1])
        # 단기 Mansfield RS (20일) — 이평 상회만 보면 시장 전체가 오르는 날
        # 전부 참이 된다. 벤치마크 대비 상대강도를 같이 요구한다.
        rs_short = None
        if len(etf_aligned) >= 25:
            rs20 = _mansfield_rs(etf_aligned, bench_aligned, ma_period=20).dropna()
            if not rs20.empty:
                rs_short = float(rs20.iloc[-1])

        rows.append(
            {
                "code": code,
                "name": label,
                "close": round(float(etf_aligned.iloc[-1]), 2),
                "rsAvg": round(rs_avg_raw, 2),
                "rsNorm": round(rs_avg_norm, 1),
                "aboveMA10d": bool(ma10d is not None and last_close > ma10d),
                "aboveMA10w": bool(ma10w is not None and last_close > ma10w),
                "rsShort": round(rs_short, 2) if rs_short is not None else None,
                "volAdjMomentum": round(vam, 4) if pd.notna(vam) else None,
                "ret1m": round(float(etf_aligned.pct_change(20).iloc[-1] * 100), 2)
                    if len(etf_aligned) > 20 else None,
                "ret3m": round(float(etf_aligned.pct_change(60).iloc[-1] * 100), 2)
                    if len(etf_aligned) > 60 else None,
            }
        )
        if sleep_sec:
            time.sleep(sleep_sec)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values("rsNorm", ascending=False).reset_index(drop=True)
    return df


def build_leading_sectors(top_n: int = 10) -> dict:
    df = compute_etf_rs()
    if df.empty:
        return {"items": [], "top": [], "leading": []}

    # leading 정의 (셋 중 하나라도 만족):
    #   1) rsNorm >= 70  — 장기 인덱스 대비 강세 (Mansfield RS)
    #   2) ret1m >= 20 & volAdjMomentum >= 0.10  — 단기 모멘텀 강세
    # 단기 조건을 추가한 이유: KODEX 로봇액티브 처럼 IPO 후 급락 종목이 ETF
    # 비중 높아 장기 RS 는 낮지만 단기엔 폭등하는 케이스 (분석가들이 주목하는
    # "거래대금 상위 + 빈집" 패턴) 를 놓치지 않기 위함.
    #
    # [시도했다가 되돌린 것 — 2026-08-11]
    # "10주·10일 이평 동시 상회" 를 세 번째 leading 조건으로 넣어봤다.
    # 참고 채널이 주도업종을 그 기준으로 고르기 때문인데, 실측 결과 실패했다:
    #   - 목표였던 전력·원전 ETF 는 3개월 -36% 낙폭이라 50일선 아래였고 여전히 탈락
    #   - 대신 철강·게임·에너지화학이 무더기로 통과해 주도섹터 7자리를 채웠고,
    #     그날의 진짜 주도(반도체장비)가 상한 밖으로 밀려났다
    # 원인은 RS 창 길이가 아니라 **바스켓 구성**이었다. 그들의 '데이터센터' 는
    # LS·대한전선·일진전기 같은 개별 강세주 묶음이고, 우리 전력 ETF 는 낙폭
    # 과대주까지 담은 지수다. ETF 축으로는 재현할 수 없는 차이다.
    # → 아래 aboveMA10w/aboveMA10d/rsShort 는 관측용으로 남기되 판정에는 안 쓴다.
    long_term = df["rsNorm"] >= 70
    short_term = (df["ret1m"] >= 20) & (df["volAdjMomentum"] >= 0.10)
    leading = df[long_term | short_term].copy()

    return {
        "all": df.to_dict("records"),
        "top": df.head(top_n).to_dict("records"),
        "leading": leading.to_dict("records"),
        "leadingCount": len(leading),
    }
