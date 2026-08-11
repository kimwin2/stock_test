"""외부 데이터 소스 래퍼.

- FinanceDataReader: 코스피/코스닥 지수, 종목/ETF OHLCV, 종목 리스팅
- Naver Mobile API: 종목별 외국인/기관/개인 순매수 (최근 10일)
- Naver search: 종목명 ↔ 종목코드
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pandas as pd
import requests

try:
    import FinanceDataReader as fdr
except ImportError:  # pragma: no cover
    fdr = None


KST = timezone(timedelta(hours=9))

NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://m.stock.naver.com/",
}


def _kst_today() -> datetime:
    return datetime.now(KST)


def _to_int_signed(s: str | int | float | None) -> int:
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).replace(",", "").replace("+", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _to_int_plain(s: str | int | float | None) -> int:
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def fetch_index_ohlcv(symbol: str = "KS11", days: int = 400) -> pd.DataFrame:
    """KS11=KOSPI, KQ11=KOSDAQ. 종가/시고저/거래량 일봉 반환."""
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    end = _kst_today()
    start = end - timedelta(days=days)
    df = fdr.DataReader(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    df.index = pd.to_datetime(df.index)
    return df


def fetch_stock_ohlcv(code: str, days: int = 300) -> pd.DataFrame:
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    end = _kst_today()
    start = end - timedelta(days=days)
    df = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    df.index = pd.to_datetime(df.index)
    return df


def fetch_kospi_listing() -> pd.DataFrame:
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    df = fdr.StockListing("KOSPI")
    return df


def fetch_kosdaq_listing() -> pd.DataFrame:
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    df = fdr.StockListing("KOSDAQ")
    return df


def fetch_etf_listing() -> pd.DataFrame:
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    df = fdr.StockListing("ETF/KR")
    return df


# 국채선물 추종 ETF — 참고 자료 F&G 의 BondDiff (10년−5년) 입력용.
# 주의: TIGER 국채선물10년 (305080) 은 같은 1년 동안 +6.7% 인데
# KODEX 국채선물10년 (365780) / KOSEF 국고채10년 (148070) 은 -6.5% 로
# 다른 10년물 추종 ETF 와 정반대 → 305080 은 신뢰 불가.
# 참고 자료 의 "10년국채선물지수" -8.8% 와 가장 가까운 KODEX 365780 사용.
KTB_ETF_5Y = "453850"   # KODEX 국채선물5년
KTB_ETF_10Y = "365780"  # KODEX 국채선물10년


def fetch_ktb_futures_pair(days: int = 400) -> pd.DataFrame:
    """5년/10년 국채선물 추종 ETF 의 종가 시계열 한 프레임에 합쳐 반환.

    Returns DataFrame indexed by date with columns: ktb5y, ktb10y.
    한쪽 ETF 결측 시 빈 DataFrame.
    """
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    end = _kst_today()
    start = end - timedelta(days=days)
    try:
        d5 = fdr.DataReader(KTB_ETF_5Y, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        d10 = fdr.DataReader(KTB_ETF_10Y, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except Exception:
        return pd.DataFrame()
    if d5.empty or d10.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "ktb5y": d5["Close"],
        "ktb10y": d10["Close"],
    })
    out.index = pd.to_datetime(out.index)
    return out


# 인버스/정방향 ETF 거래대금 비율 — 옵션 PutCall ATM 의 무료 proxy.
# bear ETF 매수 거래대금이 늘면 fear, bull ETF 매수가 늘면 greed.
PROXY_ETFS = {
    "KOSPI": {"bear": "114800", "bull": "069500"},   # KODEX 인버스 / KODEX 200
    "KOSDAQ": {"bear": "251340", "bull": "233740"},  # KODEX 코스닥150선물인버스 / KODEX 코스닥150레버리지
}


def fetch_putcall_proxy_ratio(market: str = "KOSPI", days: int = 400) -> pd.Series:
    """인버스 거래대금 / 정방향 거래대금 비율. 높을수록 fear (PutCall ratio 와 유사).

    Returns Series indexed by date. 빈 데이터일 시 빈 Series.
    """
    if fdr is None:
        raise RuntimeError("FinanceDataReader 가 설치돼 있지 않습니다.")
    pair = PROXY_ETFS.get(market.upper())
    if not pair:
        return pd.Series(dtype="float64")
    end = _kst_today()
    start = end - timedelta(days=days)
    try:
        bear = fdr.DataReader(pair["bear"], start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        bull = fdr.DataReader(pair["bull"], start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except Exception:
        return pd.Series(dtype="float64")
    if bear.empty or bull.empty:
        return pd.Series(dtype="float64")
    bear_value = (bear["Close"] * bear["Volume"]).rename("bear_value")
    bull_value = (bull["Close"] * bull["Volume"]).rename("bull_value")
    df = pd.concat([bear_value, bull_value], axis=1).dropna()
    df = df[df["bull_value"] > 0]
    ratio = df["bear_value"] / df["bull_value"]
    ratio.index = pd.to_datetime(ratio.index)
    return ratio


def fetch_naver_investor_trend(code: str, retries: int = 2, timeout: int = 6) -> list[dict]:
    """https://m.stock.naver.com/api/stock/{code}/trend
    최근 10거래일의 외국인/기관/개인 순매수 (단위: 주식 수)."""
    url = f"https://m.stock.naver.com/api/stock/{code}/trend"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=NAVER_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json() or []
        except Exception as e:
            last_err = e
        time.sleep(0.4 * (attempt + 1))
    if last_err:
        raise RuntimeError(f"Naver investor trend 실패 ({code}): {last_err}")
    return []


def fetch_naver_pc_frgn(code: str, pages: int = 6, sleep: float = 0.25, timeout: int = 8) -> pd.DataFrame:
    """https://finance.naver.com/item/frgn.naver?code=...&page=N

    페이지당 약 10 거래일치. pages=6 이면 ~60일치.
    Returns DataFrame with columns: date, close, volume, organ_qty, foreigner_qty,
                                     foreigner_hold_qty, foreigner_hold_ratio.
    단위: qty=주식수, close=원, volume=주식수, foreigner_hold_ratio=문자열 "49.43%".
    """
    from io import StringIO

    url = "https://finance.naver.com/item/frgn.naver"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.naver.com/",
    }
    rows: list[dict] = []
    seen_dates: set[str] = set()
    for page in range(1, pages + 1):
        r = requests.get(url, params={"code": code, "page": page}, headers=headers, timeout=timeout)
        if r.status_code != 200:
            break
        r.encoding = "euc-kr"
        try:
            tables = pd.read_html(StringIO(r.text))
        except ValueError:
            break
        # frgn 페이지의 일자별 매매 테이블은 9 컬럼 (multi-level header 포함)
        target = None
        for t in tables:
            if t.shape[1] == 9 and t.shape[0] >= 5:
                target = t
                break
        if target is None:
            break
        # multi-level → flat
        target.columns = [
            "date", "close", "diff", "rate", "volume",
            "organ_qty", "foreigner_qty", "foreigner_hold_qty", "foreigner_hold_ratio",
        ]
        df = target.dropna(subset=["date", "close"]).copy()
        if df.empty:
            break
        added = False
        for _, row in df.iterrows():
            d = str(row["date"]).strip()
            if d in seen_dates:
                continue
            seen_dates.add(d)
            try:
                rows.append({
                    "date": datetime.strptime(d, "%Y.%m.%d"),
                    "close": int(row["close"]) if pd.notna(row["close"]) else None,
                    "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                    "organ_qty": int(row["organ_qty"]) if pd.notna(row["organ_qty"]) else 0,
                    "foreigner_qty": int(row["foreigner_qty"]) if pd.notna(row["foreigner_qty"]) else 0,
                    "foreigner_hold_qty": int(row["foreigner_hold_qty"]) if pd.notna(row["foreigner_hold_qty"]) else None,
                    "foreigner_hold_ratio": str(row["foreigner_hold_ratio"]).strip() if pd.notna(row["foreigner_hold_ratio"]) else None,
                })
                added = True
            except (ValueError, TypeError):
                continue
        if not added:
            break
        if sleep:
            time.sleep(sleep)
    if not rows:
        return pd.DataFrame()
    df_out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    # 금액 환산 (주식수 × 종가)
    df_out["foreigner_amount"] = df_out["foreigner_qty"] * df_out["close"]
    df_out["organ_amount"] = df_out["organ_qty"] * df_out["close"]
    df_out["institutional_amount"] = df_out["foreigner_amount"] + df_out["organ_amount"]
    return df_out


def parse_investor_trend(rows: list[dict]) -> pd.DataFrame:
    """Naver trend → DataFrame (단위: 주식수, 순매수금액은 종가 곱해서 별도 계산)."""
    if not rows:
        return pd.DataFrame()
    records = []
    for entry in rows:
        try:
            bizdate = datetime.strptime(entry["bizdate"], "%Y%m%d")
        except (KeyError, ValueError):
            continue
        records.append(
            {
                "date": bizdate,
                "code": entry.get("itemCode"),
                "close": _to_int_plain(entry.get("closePrice")),
                "foreigner_qty": _to_int_signed(entry.get("foreignerPureBuyQuant")),
                "organ_qty": _to_int_signed(entry.get("organPureBuyQuant")),
                "individual_qty": _to_int_signed(entry.get("individualPureBuyQuant")),
                "foreigner_hold_ratio": entry.get("foreignerHoldRatio"),
                "volume": _to_int_plain(entry.get("accumulatedTradingVolume")),
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    # 순매수 금액(원) — 종가 × 주식수
    df["foreigner_amount"] = df["foreigner_qty"] * df["close"]
    df["organ_amount"] = df["organ_qty"] * df["close"]
    df["institutional_amount"] = df["foreigner_amount"] + df["organ_amount"]
    df["individual_amount"] = df["individual_qty"] * df["close"]
    return df


def fetch_investor_flow_for_codes(
    codes: Iterable[str],
    sleep_sec: float = 0.15,
    on_error: str = "skip",  # "skip" | "raise"
) -> dict[str, pd.DataFrame]:
    """여러 종목의 투자자 순매수 데이터 일괄 수집."""
    out: dict[str, pd.DataFrame] = {}
    for i, code in enumerate(codes):
        try:
            rows = fetch_naver_investor_trend(code)
            df = parse_investor_trend(rows)
            if not df.empty:
                out[code] = df
        except Exception as e:
            if on_error == "raise":
                raise
            print(f"  [!] {code} 투자자 데이터 실패: {e}")
        if sleep_sec:
            time.sleep(sleep_sec)
    return out


# ── ETF 구성종목(PDF) ────────────────────────────────────────────────
# "주도 ETF 가 강하다 → 그래서 이 종목이다" 라는 주장은, 그 ETF 가 실제로 그
# 종목을 담고 있어야 성립한다. 지금까지 그 연결은 우리 섹터 사전이 대신하고
# 있었다(같은 섹터니까 담겨 있을 것이다). 실제 편입비중으로 대체한다.
#
# 출처: WISEfn ETF 페이지가 CU당 구성종목 전체를 페이지 안에 JSON 으로 심어
# 내려준다. 로그인·API 키가 필요 없고 새 의존성도 없다 (KRX MDC 의 PDF 화면은
# 회원 로그인을 요구하고, 로그인해도 이 화면은 빈 결과를 준다).
ETF_PDF_URL = "https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={code}"
_CU_DATA_PAT = re.compile(r"var\s+CU_data\s*=\s*(\{.*?\});", re.S)


def fetch_etf_pdf(code: str, timeout: int = 10) -> dict:
    """ETF 1종의 구성종목. {"asOf": "YYYY-MM-DD", "holdings": [{name, weight}]}.

    실패하면 빈 결과를 준다 — 근거 보강용이라 없다고 파이프라인이 멈추면 안 된다.
    '원화현금' 같은 비종목 항목은 걷어낸다.
    """
    import json as _json
    try:
        r = requests.get(ETF_PDF_URL.format(code=code), headers=NAVER_HEADERS, timeout=timeout)
        m = _CU_DATA_PAT.search(r.text)
        if not m:
            return {"asOf": None, "holdings": []}
        rows = (_json.loads(m.group(1)) or {}).get("grid_data") or []
    except Exception as e:
        print(f"  [!] ETF {code} PDF 실패: {e}")
        return {"asOf": None, "holdings": []}

    holdings, as_of = [], None
    for row in rows:
        name = (row.get("STK_NM_KOR") or "").strip()
        weight = row.get("ETF_WEIGHT")
        if not name or weight is None:
            continue
        if name in ("원화현금", "원화예금", "설정현금액", "현금"):
            continue
        as_of = as_of or row.get("TRD_DT")
        holdings.append({"name": name, "weight": float(weight)})
    return {"asOf": as_of, "holdings": holdings}
