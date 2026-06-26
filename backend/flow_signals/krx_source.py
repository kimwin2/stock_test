"""KRX 정보데이터시스템(data.krx.co.kr) 실데이터 소스 — F&G 오실레이터 원본 입력용.

태린이아빠 원본 F&G 의 진짜 입력(proxy 아님)을 KRX MDC 에서 직접 받는다.
KRX MDC 는 회원 로그인을 강제하므로 KRX_ID / KRX_PW (.env) 필요.
검증: 이 소스로 재현한 오실레이터 vs 엑셀(원본 진짜값) 상관 r=0.94 (119일 겹침).

| feature   | 소스                                                   |
|-----------|--------------------------------------------------------|
| VKOSPI    | MDCSTAT00301 indIdx=1, indIdx2=300 (날짜범위 1콜)       |
| PutCall   | KOSPI200 옵션(MDCSTAT12501,KRDRVOPK2I) 총 콜/풋 거래량  |
|           |   → 5일 MA 비율 (엑셀 "ATM"=전체거래량 5dMA 임을 확정)  |
| BondDiff  | KTB10(KRDRVFUBMA) − KTB5(KRDRVFUBM5) 최근월물 선물종가  |

옵션/국채선물은 per-date 호출이라 무겁다 → cache(JSON) 에 일자별 저장하고
매 실행은 결측일(보통 오늘 1일)만 증분 fetch. 280일 콜드 백필은 ~15분이라
Lambda(15분 한도) 안에서 못 도므로 cache 를 미리 시드해 두고 증분만 돌린다.
"""
from __future__ import annotations

import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

GET = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}
OPT_PROD = "KRDRVOPK2I"   # KOSPI200 옵션
KTB5_PROD = "KRDRVFUBM5"  # 5년국채선물
KTB10_PROD = "KRDRVFUBMA"  # 10년국채선물
VKOSPI_BLD = "dbms/MDC/STAT/standard/MDCSTAT00301"
DERIV_BLD = "dbms/MDC/STAT/standard/MDCSTAT12501"

_OPT_PAT = re.compile(r"코스피200\s+([CP])\s+(\d{6})")
_FUT_PAT = re.compile(r"F\s+(\d{6})")
_SESSION: requests.Session | None = None

DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev", "krx_cache.json")


# ---------------- KRX 로그인 (vendored — pykrx 의존성 제거) ----------------
# KRX MDC 는 회원 로그인 세션(JSESSIONID)을 요구한다. 아래는 data.krx.co.kr
# 로그인 흐름을 그대로 옮긴 것 (구 pykrx fork auth.py 동등). Lambda 패키지에
# pykrx 를 넣지 않기 위해 내장한다.
_UA_LOGIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
_LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
_LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"


def login_krx(login_id: str, login_pw: str, session: requests.Session) -> bool:
    """data.krx.co.kr 로그인 → 세션에 JSESSIONID 부착. 성공 시 True."""
    session.get(_LOGIN_PAGE, headers={"User-Agent": _UA_LOGIN}, timeout=15)
    session.get(_LOGIN_JSP, headers={"User-Agent": _UA_LOGIN, "Referer": _LOGIN_PAGE}, timeout=15)
    payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
               "mbrId": login_id, "pw": login_pw}
    headers = {"User-Agent": _UA_LOGIN, "Referer": _LOGIN_PAGE}
    resp = session.post(_LOGIN_URL, data=payload, headers=headers, timeout=15)
    data = resp.json()
    code = data.get("_error_code", "")
    if code == "CD011":  # 중복 로그인 → 기존 세션 무시하고 재전송
        payload["skipDup"] = "Y"
        resp = session.post(_LOGIN_URL, data=payload, headers=headers, timeout=15)
        code = resp.json().get("_error_code", "")
    if code == "CD010":
        raise RuntimeError("KRX 비밀번호 변경 필요 (CD010)")
    return code == "CD001"  # CD001 = 정상


# ---------------- 세션 / MDC ----------------
def _session() -> requests.Session:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    krx_id, krx_pw = os.getenv("KRX_ID"), os.getenv("KRX_PW")
    if not (krx_id and krx_pw):
        raise RuntimeError("KRX_ID/KRX_PW 미설정 — KRX 실데이터 사용 불가")
    last = None
    for attempt in range(5):
        try:
            s = requests.Session()
            if login_krx(krx_id, krx_pw, s):
                s.get("https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd", headers=HDR, timeout=15)
                _SESSION = s
                return _SESSION
        except Exception as e:  # noqa: BLE001 (KRX 간헐적 빈 응답)
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"KRX 로그인 실패 (5회): {last}")


def _mdc(bld: str, **params) -> list[dict]:
    data = {"bld": bld, "locale": "ko_KR", "csvxls_isNo": "false", **params}
    last = None
    for attempt in range(4):
        try:
            r = _session().post(GET, data=data, headers=HDR, timeout=20)
            if r.status_code == 200 and r.text.strip().startswith(("{", "[")):
                return r.json().get("output", [])
            last = f"status={r.status_code} body={r.text[:30]!r}"
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"MDC {bld} 실패: {last}")


def _num(x) -> float:
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


# ---------------- feature fetchers ----------------
def fetch_vkospi(strt: str, end: str) -> pd.Series:
    """V-KOSPI200 변동성지수 일별 (YYYYMMDD). 날짜범위 1콜."""
    out = _mdc(VKOSPI_BLD, indIdx="1", indIdx2="300", strtDd=strt, endDd=end, share="1", money="1")
    rec = {row["TRD_DD"].replace("/", ""): _num(row["CLSPRC_IDX"]) for row in out}
    s = pd.Series(rec, name="vkospi")
    s.index = pd.to_datetime(s.index, format="%Y%m%d")
    return s.sort_index()


def fetch_option_totals_day(dd: str) -> tuple[float, float]:
    """그 날 KOSPI200 옵션 총 콜/풋 거래량 (전 행사가·전월물 합)."""
    out = _mdc(DERIV_BLD, trdDd=dd, prodId=OPT_PROD)
    c = p = 0.0
    for row in out:
        m = _OPT_PAT.search(row["ISU_NM"])
        if not m:
            continue
        v = _num(row["ACC_TRDVOL"])
        if m.group(1) == "C":
            c += v
        else:
            p += v
    return c, p


def fetch_ktb_front_day(dd: str, prod: str) -> float:
    """그 날 최근월물(가장 가까운 만기) 국채선물 종가. SP(스프레드) 제외."""
    out = _mdc(DERIV_BLD, trdDd=dd, prodId=prod)
    best_exp, best_close = None, np.nan
    for row in out:
        m = _FUT_PAT.search(row["ISU_NM"])
        if not m:
            continue
        close = _num(row["TDD_CLSPRC"])
        if close <= 0:
            continue
        exp = m.group(1)
        if best_exp is None or exp < best_exp:
            best_exp, best_close = exp, close
    return best_close


# ---------------- cache + 조립 ----------------
# cache_path 가 s3://bucket/key 면 S3, 아니면 로컬 파일. (Lambda=S3, 로컬=파일)
def _parse_s3(path: str) -> tuple[str, str] | None:
    if path.startswith("s3://"):
        bucket, _, key = path[5:].partition("/")
        return bucket, key
    return None


def _empty_cache() -> dict:
    return {"opt": {}, "ktb5": {}, "ktb10": {}}


def _load_cache(path: str) -> dict:
    s3 = _parse_s3(path)
    try:
        if s3:
            import boto3
            obj = boto3.client("s3").get_object(Bucket=s3[0], Key=s3[1])
            c = json.loads(obj["Body"].read())
        elif os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
        else:
            return _empty_cache()
    except Exception as e:  # noqa: BLE001 (캐시 없음/접근불가 → 빈 캐시)
        print(f"  [i] KRX 캐시 로드 실패({e}) → 빈 캐시로 시작")
        return _empty_cache()
    for k in ("opt", "ktb5", "ktb10"):
        c.setdefault(k, {})
    return c


def _save_cache(path: str, c: dict) -> None:
    s3 = _parse_s3(path)
    body = json.dumps(c)
    if s3:
        import boto3
        boto3.client("s3").put_object(Bucket=s3[0], Key=s3[1],
                                      Body=body.encode("utf-8"), ContentType="application/json")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)


def build_daily_features(dates: list[str], cache_path: str = DEFAULT_CACHE,
                         max_fetch: int | None = None) -> pd.DataFrame:
    """dates(YYYYMMDD) → DataFrame[call_vol,put_vol,ktb5,ktb10] (cache 증분).

    max_fetch: 한 번에 신규 fetch 할 최대 일수 (Lambda 시간 보호; None=무제한).
    """
    c = _load_cache(cache_path)
    todo = [d for d in dates if d not in c["opt"] or d not in c["ktb5"] or d not in c["ktb10"]]
    if max_fetch is not None:
        todo = todo[-max_fetch:]
    for i, dd in enumerate(todo):
        try:
            if dd not in c["opt"]:
                c["opt"][dd] = fetch_option_totals_day(dd)
            if dd not in c["ktb5"]:
                c["ktb5"][dd] = fetch_ktb_front_day(dd, KTB5_PROD)
            if dd not in c["ktb10"]:
                c["ktb10"][dd] = fetch_ktb_front_day(dd, KTB10_PROD)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] KRX {dd} 실패: {str(e)[:60]}")
        if (i + 1) % 20 == 0:
            _save_cache(cache_path, c)
        time.sleep(0.15)
    _save_cache(cache_path, c)

    rows = []
    for dd in dates:
        if dd in c["opt"]:
            cv, pv = c["opt"][dd]
            rows.append({"date": pd.to_datetime(dd, format="%Y%m%d"), "call_vol": cv, "put_vol": pv,
                         "ktb5": c["ktb5"].get(dd, np.nan), "ktb10": c["ktb10"].get(dd, np.nan)})
    return pd.DataFrame(rows).set_index("date").sort_index() if rows else pd.DataFrame()


def fetch_real_fg_inputs(index_close: pd.Series, cache_path: str = DEFAULT_CACHE,
                         max_fetch: int | None = None) -> pd.DataFrame:
    """지수 종가(DatetimeIndex) → 실데이터 5-feature 입력 DataFrame.

    Returns columns: vkospi, call_vol, put_vol, ktb5, ktb10 (index 정렬·ffill).
    KRX 실패 시 RuntimeError → 호출측에서 proxy 폴백.
    """
    dates = [d.strftime("%Y%m%d") for d in index_close.index]
    strt, end = dates[0], dates[-1]
    vk = fetch_vkospi(strt, end)
    feat = build_daily_features(dates, cache_path=cache_path, max_fetch=max_fetch)
    df = pd.concat([vk, feat], axis=1)
    df = df.reindex(index_close.index)
    df[["vkospi", "ktb5", "ktb10"]] = df[["vkospi", "ktb5", "ktb10"]].ffill()
    return df
