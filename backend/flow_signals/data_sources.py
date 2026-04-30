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
