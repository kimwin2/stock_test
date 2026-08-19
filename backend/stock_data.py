"""
네이버 금융 종목 실시간 데이터 조회 모듈
- 종목명으로 종목코드를 검색합니다.
- 현재가, 등락률, 거래대금, 고/저/시가 등 실시간 데이터를 조회합니다.
"""
from __future__ import annotations

import sys
import io
import requests
from bs4 import BeautifulSoup
import json
import time
import re
import os
from typing import Optional, List, Dict
from datetime import datetime

try:
    import nxt_quotes
except ModuleNotFoundError:
    from . import nxt_quotes

# Windows cp949 콘솔 인코딩 문제 해결
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}


# 주요 한국 상장종목 코드 매핑 (자주 사용되는 종목)
STOCK_CODE_MAP = {
    # 대형주
    "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940", "현대차": "005380", "기아": "000270",
    "셀트리온": "068270", "KB금융": "105560", "신한지주": "055550",
    "POSCO홀딩스": "005490", "포스코홀딩스": "005490",
    "NAVER": "035420", "네이버": "035420", "카카오": "035720",
    "삼성SDI": "006400", "LG화학": "051910", "현대모비스": "012330",
    "삼성물산": "028260", "SK이노베이션": "096770", "LG전자": "066570",
    "한국전력": "015760", "SK텔레콤": "017670", "KT": "030200",
    "하나금융지주": "086790", "우리금융지주": "316140",
    
    # 반도체
    "한미반도체": "042700", "리노공업": "058470", "이오테크닉스": "039030",
    "하나마이크론": "067310", "원익IPS": "240810", "주성엔지니어링": "036930",
    "피에스케이": "319660", "티에스이": "131290", "넥스틴": "348210",
    "서진시스템": "178320", "삼성전기": "009150", "DB하이텍": "000990",
    "SK스퀘어": "402340", "코오롱인더": "120110", "삼화콘덴서": "001820",
    "세미파이브": "530017",
    
    # 광통신
    "대한광통신": "010170", "기산텔레콤": "092440", "오이솔루션": "138080",
    "쏠리드": "050890", "티엠씨": "950190", "LG이노텍": "011070",
    "옵티시스": "109080", "이노와이어리스": "073490", "넥스트칩": "405100",
    "남선알미늄": "008350", "코위버": "056360", "광전자": "017900",
    "머큐리": "100590", "빛과전자": "069540",
    
    # 건설
    "GS건설": "006360", "현대건설": "000720", "대우건설": "047040",
    "DL이앤씨": "375500", "삼성E&A": "028050", "한신공영": "004960",
    "대림산업": "000210", "HDC현대산업": "294870", "포스코건설": "034020",
    "대림건설": "001880", "전진건설로봇": "079900", "전진건설": "079900",
    
    # 화장품/K뷰티
    "아모레퍼시픽": "090430", "LG생활건강": "051900", "코스맥스": "192820",
    "한국콜마": "161890", "클리오": "237880", "에이블씨엔씨": "078520",
    "잇츠한불": "226320", "토니모리": "214420", "네오팜": "092730",
    "실리콘투": "257720", "브이티": "018290",
    
    # 방산
    "한화에어로스페이스": "012450", "LIG넥스원": "079550",
    "한화시스템": "272210", "현대로템": "064350",
    "풍산": "103140", "풍산홀딩스": "005810",
    "한화오션": "042660", "퍼스텍": "226340",
    
    # 에너지/전력
    "효성중공업": "298040", "LS일렉트릭": "010120", "두산에너빌리티": "034020",
    "한화솔루션": "009830", "씨에스윈드": "112610",
    "HD현대일렉트릭": "267260", "일진전기": "103590",
    "SK이터닉스": "475150", "신성이엔지": "011930",
    
    # 바이오
    "삼성바이오로직스": "207940", "셀트리온헬스케어": "091990",
    "SK바이오팜": "326030", "유한양행": "000100", "녹십자": "006280",
    "HLB": "028300", "에이치엘비": "028300", "알테오젠": "196170",
    "삼천당제약": "000250",
    
    # 기타
    "미래에셋증권": "006800", "삼성증권": "016360", "키움증권": "039490",
    "한국가스공사": "036460", "한국항공우주": "047810",
    "HD현대": "267250", "포스코퓨처엠": "003670",
    "카카오뱅크": "323410", "크래프톤": "259960",
    "엔씨소프트": "036570", "넷마블": "251270",
    "뉴엔AI": "405640", "다날": "064260", "엘앤에프": "066970",
    "위메이드": "112040", "위메이드플레이": "123420",
    "하나투어": "039130",
    "한화에어로": "012450",
    "SK": "034730",
    "SKC": "011790",
    "LG": "003550",
    "LG디스플레이": "034220",
    "한국타이어": "161390",
    "호텔신라": "008770",
    "CJ제일제당": "097950",
    "대한해운": "005880",
    "흥아해운": "003280",
    "넥스틸": "092790",
    "에코프로": "086520",
    "에코프로머티": "450080",
    "아이씨티케이": "456010",
    "엑스게이트": "356680",
    "케이씨에스": "115500",
    "아톤": "158430",
    "파수": "150900",

    # 개미승리 빈출 종목
    "한빛레이저": "452190", "필옵틱스": "161580", "켐트로닉스": "089010",
    "아모텍": "052710", "엔젯": "419080",
    "소프트캠프": "258790", "라온시큐어": "042510", "SGA솔루션즈": "184230",
    "케이사인": "192250", "핀텔": "291810", "드림시큐리티": "203650",
    "이노뎁": "303530", "에스피소프트": "443670", "에스넷": "038680",
    "오브젠": "417860", "셀바스AI": "108860", "피아이이": "452450",
    "코나아이": "052400", "웹케시": "053580", "쿠콘": "294570",
    "유라클": "088340",
    "비에이치": "090460", "덕산네오룩스": "213420", "자화전자": "033240",
    "에스켐": "475660",
}


def search_stock_code(stock_name: str) -> Optional[str]:
    """
    종목명으로 종목코드를 검색합니다.
    1순위: 하드코딩 매핑
    2순위: 네이버 증권 페이지 크롤링

    Args:
        stock_name: 종목명 (예: "삼성전자")

    Returns:
        종목코드 (예: "005930") 또는 None
    """
    # 1. 하드코딩 매핑에서 찾기
    if stock_name in STOCK_CODE_MAP:
        return STOCK_CODE_MAP[stock_name]

    # 우선주 표기 흡수 (예: "삼성전자우" → "삼성전자"). 접미사를 떼고 정확히 맞을 때만.
    #
    # 예전에는 name.startswith(stock_name) 도 허용했는데, 그러면 짧은 질의가
    # 더 긴 종목명을 가로챈다. 실제로 "메타"(해외 기업)가 "메타케어"로 해석돼
    # 엉뚱한 종목이 테마 카드에 올랐다. 부분 매칭은 방향을 한쪽으로 고정한다.
    for suffix in ("우B", "1우", "2우B", "우"):
        if stock_name.endswith(suffix):
            base = stock_name[: -len(suffix)]
            if base in STOCK_CODE_MAP:
                return STOCK_CODE_MAP[base]
            break

    # 2. 네이버 증권 검색 시도
    return search_stock_code_online(stock_name)


def search_stock_code_online(stock_name: str) -> Optional[str]:
    """네이버 증권에서 종목명 → 종목코드를 검색합니다."""
    # 방법 1: 네이버 증권 자동완성 API.
    # 기존에 쓰던 m.stock.naver.com/api/search/stocks 는 폐기되어 404 를 돌려줬고,
    # 그 탓에 OCI홀딩스·혜인 같은 실존 종목까지 "코드 없음"으로 탈락했다.
    try:
        resp = requests.get(
            "https://ac.stock.naver.com/ac",
            params={"q": stock_name, "target": "stock"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if resp.status_code == 200:
            items = [
                it for it in (resp.json().get("items") or [])
                if it.get("category") == "stock"
                and it.get("nationCode") == "KOR"
                and re.fullmatch(r"\d{6}", str(it.get("code") or ""))
            ]
            if items:
                # 이름이 정확히 일치할 때만 채택한다.
                #
                # 첫 항목으로 폴백하면 자동완성이 접두사로 찾아준 다른 회사를
                # 그대로 받아버린다. 실제로 "메타"(해외 기업)가 "메타케어"로
                # 해석됐다. 존재하지 않는 종목명(LLM 환각)이나 해외 기업은
                # 여기서 None 이 되는 게 맞다 — 틀린 종목을 넣는 것보다 낫다.
                target = re.sub(r"\s+", "", stock_name).lower()
                for it in items:
                    if re.sub(r"\s+", "", it.get("name", "")).lower() == target:
                        STOCK_CODE_MAP[stock_name] = it["code"]  # 캐싱
                        return it["code"]
    except Exception as e:
        print(f"  [!] 자동완성 검색 실패 ({stock_name}): {e}")

    # 방법 2: 네이버 통합검색에서 종목코드 추출.
    #
    # 마지막 정규식 code=(\d{6}) 는 페이지 어디에 있는 6자리든 물어온다.
    # 그래서 상장되지도 않은 이름(해외 기업 '애플' 등)에도 엉뚱한 코드가
    # 잡혔고, 페이지 구성에 따라 결과가 매번 달라졌다. 뽑은 코드가 정말
    # 그 종목인지 실제 종목명으로 확인한 뒤에만 채택한다.
    try:
        url = "https://search.naver.com/search.naver"
        params = {"query": f"{stock_name} 주가"}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            match = re.search(r'/item/main\.naver\?code=(\d{6})', resp.text)
            if not match:
                match = re.search(r'stockCode["\s:=]+(\d{6})', resp.text)
            if not match:
                match = re.search(r'code=(\d{6})', resp.text)
            if match:
                code = match.group(1)
                detail = get_stock_detail(code)
                found = re.sub(r"\s+", "", (detail or {}).get("name", "")).lower()
                if found and found == re.sub(r"\s+", "", stock_name).lower():
                    STOCK_CODE_MAP[stock_name] = code  # 캐싱
                    return code
                print(f"  [!] 통합검색 결과 불일치 ({stock_name} ≠ {(detail or {}).get('name')}) — 버림")
    except Exception as e:
        print(f"  [!] 통합검색 실패 ({stock_name}): {e}")

    print(f"  [!] {stock_name} 종목코드를 찾을 수 없습니다.")
    return None

# 이번 실행에서 프리마켓(NXT) 시세를 실제로 쓴 종목 수.
# 산출물에 그대로 실어 "이 화면이 정규장 시세인가 프리마켓 시세인가"를 드러낸다.
_premarket_hits = 0


def premarket_hits() -> int:
    return _premarket_hits


def reset_premarket_state() -> None:
    global _premarket_hits
    _premarket_hits = 0
    nxt_quotes.reset()


def _premarket_detail(stock_code: str) -> Optional[dict]:
    """08:00~09:00 구간의 넥스트레이드 프리마켓 시세.

    정규장이 열리기 전에는 네이버 basic API 가 전일 종가를 그대로 주므로
    전 종목 등락률이 0.0% 가 된다. 그 값으로는 테마 생존 게이트가 의미를
    잃는다 (자세한 경위는 nxt_quotes 모듈 docstring).

    **거래대금은 프리마켓 값을 쓰지 않는다.** 유동성 게이트(30억)는 "이 종목이
    단타로 드나들 수 있는가"를 묻는 것이고, 그건 종목의 성질이지 지금 이 순간의
    체결량이 아니다. 프리마켓 체결량으로 재면 멀쩡한 대형주까지 전부 illiquid 로
    떨어진다. 움직임은 프리마켓에서, 유동성은 직전 정규장에서 가져온다.
    """
    global _premarket_hits
    q = nxt_quotes.fetch_premarket_quote(stock_code)
    if not q:
        return None

    price = q["price"]
    change_price = q.get("changeAmount")
    change_rate = q["changeRate"]
    prev_close = q.get("prevClose") or (price - change_price if change_price is not None else price)
    if change_price is None:
        change_price = price - prev_close

    # OHLC 는 프리마켓 응답에 없다. 기존 모바일 경로와 같은 방식으로 추정한다
    # (당일 레인지 바 표시용이며 게이트 판정에는 쓰이지 않는다).
    abs_change = abs(change_price)
    if change_price > 0:
        open_price = price - int(abs_change * 0.6)
        high = price + int(abs_change * 0.1)
        low = open_price - int(abs_change * 0.2)
    elif change_price < 0:
        open_price = price + int(abs_change * 0.4)
        high = open_price + int(abs_change * 0.2)
        low = price - int(abs_change * 0.1)
    else:
        open_price = high = low = price

    volume_raw = get_volume_fast(stock_code) or q.get("volumeRaw") or 0
    if volume_raw == 0:
        volume_raw = price * (200000 if price >= 100000 else 500000 if price >= 10000 else 1000000)

    _premarket_hits += 1
    return {
        "code": stock_code,
        "name": q.get("name") or "",
        "price": price,
        "changeRate": change_rate,
        "changeAmount": change_price,
        "prevClose": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "volumeRaw": volume_raw,
        "volume": format_volume(volume_raw),
        "time": datetime.now(nxt_quotes.KST).strftime("%H:%M"),
        "quoteSource": "nxt-premarket",
    }


def get_stock_detail(stock_code: str) -> Optional[dict]:
    """
    네이버 금융에서 종목 상세 데이터를 조회합니다.
    0순위: 프리마켓(NXT) — 08:00~09:00 구간에서만, 못 믿을 값이면 건너뜀
    1순위: 네이버 모바일 증권 API (깔끔한 JSON)
    2순위: 데스크탑 페이지 HTML 파싱
    """
    # 방법 0: 프리마켓 — 창 밖이거나 실패하면 즉시 None 이라 아래로 흐른다.
    pre = _premarket_detail(stock_code)
    if pre:
        return pre

    # 방법 1: 네이버 모바일 증권 API
    detail = get_stock_detail_mobile(stock_code)
    if detail:
        return detail

    # 방법 2: 데스크탑 HTML 파싱
    return get_stock_detail_desktop(stock_code)


def get_volume_fast(stock_code: str) -> int:
    """네이버 시세 페이지에서 거래대금을 빠르게 가져옵니다."""
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={stock_code}"
        resp = requests.get(url, headers=HEADERS, timeout=3)
        if resp.status_code == 200:
            # 거래대금 백만원 단위로 표시됨
            match = re.search(r'거래대금.*?<td[^>]*>\s*<span[^>]*>([0-9,]+)</span>', resp.text, re.DOTALL)
            if match:
                return parse_number(match.group(1)) * 1_000_000
    except Exception:
        pass
    return 0


def get_stock_detail_mobile(stock_code: str) -> Optional[dict]:
    """네이버 모바일 증권 API로 종목 데이터를 조회합니다."""
    try:
        url = f"https://m.stock.naver.com/api/stock/{stock_code}/basic"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        }, timeout=5)

        if resp.status_code != 200:
            return None

        data = resp.json()

        price = int(data.get("closePrice", "0").replace(",", ""))
        change_price = int(data.get("compareToPreviousClosePrice", "0").replace(",", ""))
        change_rate = float(data.get("fluctuationsRatio", "0").replace(",", ""))
        prev_close = price - change_price

        # 거래시간
        time_str = ""
        local_traded_at = data.get("localTradedAt", "")
        if local_traded_at:
            match = re.search(r"(\d{2}):(\d{2})", local_traded_at)
            if match:
                time_str = f"{match.group(1)}:{match.group(2)}"

        # 시가/고가/저가 추정 (basic API에는 없으므로 등락률 기반 추정)
        # 상승종목: 시가 < 현재가, 하락종목: 시가 > 현재가
        abs_change = abs(change_price)
        if change_price > 0:
            open_price = price - int(abs_change * 0.6)
            high = price + int(abs_change * 0.1)
            low = open_price - int(abs_change * 0.2)
        elif change_price < 0:
            open_price = price + int(abs_change * 0.4)
            high = open_price + int(abs_change * 0.2)
            low = price - int(abs_change * 0.1)
        else:
            open_price = price
            high = price
            low = price

        # 거래대금: 네이버 시세 페이지에서 빠르게 가져오기
        volume_raw = get_volume_fast(stock_code)
        if volume_raw == 0:
            # 못 가져오면 가격 기반 대략 추정 (대형주/중형주/소형주)
            if price >= 100000:
                volume_raw = price * 200000  # 대형주 거래량 추정
            elif price >= 10000:
                volume_raw = price * 500000
            else:
                volume_raw = price * 1000000

        return {
            "code": stock_code,
            "name": data.get("stockName", ""),
            "price": price,
            "changeRate": change_rate,
            "changeAmount": change_price,
            "prevClose": prev_close,
            "open": open_price,
            "high": high,
            "low": low,
            "volumeRaw": volume_raw,
            "volume": format_volume(volume_raw),
            "time": time_str,
        }

    except Exception:
        return None


def get_stock_detail_desktop(stock_code: str) -> Optional[dict]:
    """네이버 금융 데스크탑 페이지에서 종목 상세 데이터를 조회합니다."""
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        result = {"code": stock_code}

        # 종목명
        name_tag = soup.select_one("div.wrap_company h2 a") or soup.select_one("div.wrap_company h2")
        if name_tag:
            result["name"] = name_tag.get_text(strip=True)
        else:
            result["name"] = ""

        # 현재가
        price_tag = soup.select_one("p.no_today span.blind")
        if price_tag:
            result["price"] = parse_number(price_tag.get_text(strip=True))
        else:
            result["price"] = 0

        # 전일 대비 등락
        change_tag = soup.select_one("p.no_exday span.blind")
        if change_tag:
            change_amount = parse_number(change_tag.get_text(strip=True))
        else:
            change_amount = 0

        # 등락 방향 (상승/하락)
        is_down = bool(soup.select_one("p.no_exday em.hv"))
        if is_down:
            change_amount = -change_amount

        result["changeAmount"] = change_amount

        # 등락률 계산
        prev_close = result["price"] - change_amount if result["price"] else 0
        if prev_close > 0:
            result["changeRate"] = round((change_amount / prev_close) * 100, 2)
        else:
            result["changeRate"] = 0.0
        result["prevClose"] = prev_close

        # 기본값 설정
        result["open"] = result["price"]
        result["high"] = result["price"]
        result["low"] = result["price"]

        # 시가, 고가, 저가 - 테이블에서 추출
        table = soup.select_one("table.no_info")
        if table:
            tds = table.select("td span.blind")
            if len(tds) >= 6:
                result["prevClose"] = parse_number(tds[0].get_text(strip=True))
                result["high"] = parse_number(tds[1].get_text(strip=True))
                result["open"] = parse_number(tds[3].get_text(strip=True))
                result["low"] = parse_number(tds[4].get_text(strip=True))

        # 거래대금
        volume_amount = extract_volume_amount(soup, stock_code)
        result["volumeRaw"] = volume_amount
        result["volume"] = format_volume(volume_amount)

        # 거래 시간
        time_tag = soup.select_one("em.date")
        if time_tag:
            time_text = time_tag.get_text(strip=True)
            match = re.search(r"(\d{2}):(\d{2})", time_text)
            if match:
                result["time"] = f"{match.group(1)}:{match.group(2)}"
            else:
                result["time"] = ""
        else:
            result["time"] = ""

        return result

    except Exception as e:
        print(f"  [X] 종목 데이터 조회 실패 ({stock_code}): {e}")
        return None


def extract_volume_amount(soup: BeautifulSoup, stock_code: str) -> int:
    """거래대금을 추출합니다 (원 단위)."""
    # 방법 1: 종목 페이지의 테이블에서 추출
    table = soup.select_one("table.no_info")
    if table:
        tds = table.select("td")
        for td in tds:
            text = td.get_text(strip=True)
            if "거래대금" in text:
                blind = td.select_one("span.blind")
                if blind:
                    return parse_number(blind.get_text(strip=True)) * 1_000_000  # 백만원 단위

    # 방법 2: 시세 API
    try:
        api_url = f"https://finance.naver.com/item/sise.naver?code={stock_code}"
        resp = requests.get(api_url, headers=HEADERS, timeout=5)
        soup2 = BeautifulSoup(resp.text, "lxml")
        # 거래대금 필드 찾기
        for td in soup2.select("td"):
            text = td.get_text()
            if "거래대금" in text:
                next_td = td.find_next_sibling("td")
                if next_td:
                    return parse_number(next_td.get_text(strip=True)) * 1_000_000
    except Exception:
        pass

    return 0


def parse_number(text: str) -> int:
    """문자열에서 숫자를 파싱합니다. 콤마, 공백 등 제거."""
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def format_volume(amount: int) -> str:
    """
    거래대금을 '억' 단위 문자열로 변환합니다.

    Args:
        amount: 원 단위 거래대금

    Returns:
        예: "24,680억", "170억"
    """
    if amount <= 0:
        return "0억"

    billions = amount / 100_000_000  # 억 단위
    if billions >= 1:
        return f"{billions:,.0f}억"
    else:
        millions = amount / 10_000  # 만 단위
        return f"{millions:,.0f}만"


def calculate_bar_data(open_price: int, high: int, low: int, current: int, prev_close: int) -> dict:
    """
    미니 차트 바(Range Bar) 데이터를 계산합니다.

    - minMaxRange: [0, 100] 전체 범위 (저가~고가)
    - currentRange: 시가~현재가 위치 (0~100 스케일)
    - baseline: 전일종가 위치 (0~100 스케일)
    """
    # 유효성 검증
    if high <= low or high == 0 or low == 0:
        return {
            "minMaxRange": [0, 100],
            "currentRange": [40, 60],
            "baseline": 50,
        }

    price_range = high - low

    def clamp(val):
        """0~100 범위로 클램핑"""
        return max(0, min(100, val))

    # 시가 위치 (0~100)
    open_pos = clamp(round(((open_price - low) / price_range) * 100))
    # 현재가 위치 (0~100)
    current_pos = clamp(round(((current - low) / price_range) * 100))
    # 전일종가 기준선
    baseline_pos = clamp(round(((prev_close - low) / price_range) * 100))

    # currentRange: 시가~현재가 (작은 값이 먼저)
    range_start = min(open_pos, current_pos)
    range_end = max(open_pos, current_pos)

    return {
        "minMaxRange": [0, 100],
        "currentRange": [range_start, range_end],
        "baseline": baseline_pos,
    }


def _dedupe_by_code(stocks: list[dict]) -> list[dict]:
    """같은 종목코드가 두 번 들어가는 것을 막는다 (먼저 온 쪽을 남긴다)."""
    seen: set[str] = set()
    out: list[dict] = []
    for s in stocks or []:
        code = str(s.get("code") or "").strip()
        key = code or str(s.get("name") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def get_stock_details_for_themes(themes: list[dict], analysis: dict | None = None) -> list[dict]:
    """
    테마별 종목을 증거 기반으로 선정하고 상세 데이터를 붙여 반환합니다.

    analysis 가 주어지면 개미승리·급등클러스터·인포스탁·유튜브·와우넷·텔레그램
    시그널을 합쳐 후보 풀을 만들고, 실측 시세 게이트와 점수로 상위 종목을
    고른다. analysis 가 없으면 LLM 이 지목한 종목만으로 동작한다(하위 호환).

    Args:
        themes: analyzer에서 추출된 테마 리스트
            [{"themeName": str, "headline": str, "relatedStocks": [str, ...], ...}]
        analysis: analyze_themes 결과 (시그널 원본 포함)

    Returns:
        프론트엔드용 완성된 테마 데이터 리스트
    """
    # 실행마다 프리마켓 상태를 초기화한다. Lambda 컨테이너는 재사용되므로
    # 이전 회차의 회로 차단 상태가 남으면 그날 내내 프리마켓 시세를 못 쓴다.
    reset_premarket_state()

    try:
        import theme_stocks as ts
    except ImportError:
        from . import theme_stocks as ts

    analysis = analysis or {}
    detail_cache: dict[str, dict | None] = {}

    def fetch_detail(code: str) -> dict | None:
        if code not in detail_cache:
            detail_cache[code] = get_stock_detail(code)
            time.sleep(0.1)  # 요청 간격
        return detail_cache[code]

    # 유사 테마 병합은 반드시 선정 전에. 아래 전역 종목 중복 제거가 먼저 돌면
    # 중복 테마가 서로 다른 종목을 받아 겹침 판정이 발동하지 않는다.
    before_merge = len(themes)
    themes = ts.merge_similar_themes(themes)
    if len(themes) < before_merge:
        print(f"[INFO] 유사 테마 {before_merge - len(themes)}개 병합 → {len(themes)}개")
        for t in themes:
            if t.get("mergedThemes"):
                print(f"  [병합] {t['themeName']} ← {', '.join(t['mergedThemes'])}")

    result_themes = _select_theme_stocks(themes, analysis, ts, fetch_detail, relaxed=False)

    # 조용한 날에도 탭이 비지 않게. 종목 조회는 캐시되어 재시도 비용이 거의 없다.
    if len(result_themes) < ts.MIN_THEMES:
        print(f"[INFO] 통과 테마 {len(result_themes)}개 — 게이트를 완화해 재선정")
        relaxed = _select_theme_stocks(themes, analysis, ts, fetch_detail, relaxed=True)
        if len(relaxed) > len(result_themes):
            for t in relaxed:
                t["gateRelaxed"] = True
            result_themes = relaxed

    return result_themes


def _select_theme_stocks(themes, analysis, ts, fetch_detail, relaxed: bool) -> list[dict]:
    """테마 리스트 → 선정 완료된 테마 리스트. relaxed 는 게이트 완화 여부."""
    result_themes = []
    used_codes: set[str] = set()   # 한 종목은 전체 테마 통틀어 1번만
    stats = {"pool": 0, "unresolved": 0, "noQuote": 0, "penny": 0,
             "illiquid": 0, "falling": 0, "duplicate": 0, "indexProduct": 0,
             "weakLead": 0, "unbackedDrop": 0}
    min_stocks = 1 if relaxed else ts.MIN_STOCKS_PER_THEME

    for theme in themes:
        theme_name = theme["themeName"]
        headline = theme.get("headline", "")

        print(f"\n[INFO] 테마 '{theme_name}' 종목 선정 중...")

        # 개미승리가 직접 제공한 종목 코드 매핑 (이름 검색보다 우선).
        # pop 이 아니라 get — 완화 재시도 때도 코드 매핑이 살아있어야 한다.
        direct_codes = theme.get("_antwinner_stock_codes") or {}

        candidates = ts.build_candidates(theme, analysis)
        stats["pool"] += len(candidates)

        scored = []
        for cand in candidates:
            code = cand.code or direct_codes.get(cand.name) or search_stock_code(cand.name)
            if not code:
                # LLM 이 지어낸 종목명은 여기서 걸러진다.
                stats["unresolved"] += 1
                print(f"  [!] {cand.name} 종목코드 없음 (출처: {'·'.join(sorted(cand.sources))})")
                continue
            if code in used_codes:
                stats["duplicate"] += 1
                continue

            detail = fetch_detail(code)
            if not detail:
                stats["noQuote"] += 1
                continue

            reject = ts.passes_gate(detail, relaxed=relaxed)
            if reject:
                stats[reject] += 1
                print(f"  [-] {cand.name} 제외 ({reject}: {detail.get('changeRate')}% / "
                      f"{detail.get('price')}원 / {detail.get('volume')})")
                continue

            score, reasons = ts.score_candidate(cand, detail)
            scored.append((score, reasons, cand, code, detail))

        # 점수 내림차순 — 등락률만 보던 기존 정렬을 대체한다.
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = scored[:ts.MAX_STOCKS_PER_THEME]

        # 가격 클러스터에서 승격된 테마는 클러스터 종목이 최소 1개는 게이트를
        # 통과해야 한다. 전멸했다는 것은 근거(대개 저가 급등 묶음)가 화면
        # 기준으로 존재하지 않는다는 뜻이고, 그대로 두면 테마명·설명은
        # 클러스터 것, 종목은 이름이 비슷한 다른 테마에서 차입한 짝짜꿍이 된다
        # (2026-08-10 실사고: '반도체 소부장' 환각 클러스터에 씨이랩 등 차입).
        cluster_stocks = {ts._norm_name(n) for n in (theme.get("_cluster_stocks") or []) if n}
        if cluster_stocks:
            def _in_cluster(x):
                return (ts._norm_name(x[2].name) in cluster_stocks
                        or ts._norm_name((x[4].get("name") or "")) in cluster_stocks)

            survivors = [x for x in scored if _in_cluster(x)]
            if not survivors:
                print(f"  [DROP] {theme_name}: 클러스터 근거 종목이 게이트에서 전멸 — "
                      f"차입 종목으로 테마를 유지하지 않음")
                continue
            # 근거 종목이 점수에서 밀려 화면에서 사라지면 결국 같은 짝짜꿍이다.
            # 최소 1개는 반드시 노출한다 (가장 점수 높은 생존 근거로 마지막 자리 교체).
            if not any(_in_cluster(x) for x in selected):
                selected = selected[:-1] + [survivors[0]]

        if len(selected) < min_stocks:
            print(f"  [DROP] {theme_name}: 유효 종목 {len(selected)}개 — 테마 제외")
            continue

        # 테마가 오늘 실제로 살아있는지 — 대장주가 의미 있게 올라야 한다.
        # 외부 근거가 하나도 없는 테마(전 종목 `뉴스분석` 단독)는 기준을 크게 올린다.
        # LLM 은 기사에서 그럴듯한 테마명을 언제든 만들어낼 수 있으므로, 시세가
        # 그 이름을 확인해 주지 않으면 카드로 내보내지 않는다.
        lead_rate = max(float(d.get("changeRate") or 0) for *_, d in selected)
        unbacked = ts.is_unbacked([x[2] for x in selected])
        if unbacked:
            min_lead = ts.UNBACKED_RELAXED_LEAD_RATE if relaxed else ts.UNBACKED_THEME_MIN_LEAD_RATE
        else:
            min_lead = ts.RELAXED_LEAD_RATE if relaxed else ts.THEME_MIN_LEAD_RATE
        if lead_rate < min_lead:
            print(f"  [DROP] {theme_name}: 최고 등락 {lead_rate:+.2f}% "
                  f"(< {min_lead:+.1f}%{' · 외부 근거 없음' if unbacked else ''}) "
                  f"— 오늘 주도 테마 아님")
            stats["unbackedDrop" if unbacked else "weakLead"] += 1
            continue

        stock_details = []
        total_volume = 0
        for score, reasons, cand, code, detail in selected:
            used_codes.add(code)
            bar_data = calculate_bar_data(
                open_price=detail.get("open", detail["price"]),
                high=detail.get("high", detail["price"]),
                low=detail.get("low", detail["price"]),
                current=detail["price"],
                prev_close=detail.get("prevClose", detail["price"]),
            )
            stock_details.append({
                "code": code,
                "name": detail["name"] or cand.name,
                "price": detail["price"],
                "time": detail.get("time", ""),
                "changeRate": detail["changeRate"],
                "volume": detail["volume"],
                # 당일 레인지 바에 실제 가격 눈금을 찍기 위해 OHLC 를 함께 내려보낸다.
                # barData 만으로는 0~100 정규화 위치뿐이라 라벨을 달 수 없다.
                "open": detail.get("open"),
                "high": detail.get("high"),
                "low": detail.get("low"),
                "prevClose": detail.get("prevClose"),
                "isTop": False,  # 아래에서 1위에만 설정
                "barData": bar_data,
                "score": score,
                "scoreReasons": reasons,
                "sources": sorted(cand.sources),
            })
            total_volume += detail.get("volumeRaw", 0)

        # 대장주 = 점수 1위 (등락률 1위가 아니라 근거가 가장 두꺼운 종목)
        stock_details[0]["isTop"] = True

        # 테마명이 종목 구성과 맞는지 사후 확인용. 판정만 남기고 동작은 바꾸지 않는다
        # — 분류가 '기타'인 소형주가 많으면 근거 없이 단정할 수 없기 때문이다.
        mix = ts.sector_mix(stock_details)
        if mix["dominant"] and mix["dominantRatio"] >= 0.5 and mix["unknownRatio"] <= 0.25:
            print(f"  [i] {theme_name}: 실제 업종 다수 = {mix['dominant']} ({mix['dominantRatio']:.0%})")

        result_themes.append({
            "themeName": theme_name,
            "totalVolume": format_volume(total_volume),
            "headline": headline,
            "headlineUrl": theme.get("headlineUrl", ""),
            "headlineLink": theme.get("headlineLink", {}),
            "headlineLinks": theme.get("headlineLinks", []),
            "headlineLinkSource": theme.get("headlineLinkSource", ""),
            "headlineLinkConfidence": theme.get("headlineLinkConfidence", ""),
            "representativeArticleIndex": int(theme.get("representativeArticleIndex", 0) or 0),
            "reasoning": theme.get("reasoning", ""),
            # 같은 종목이 한 테마에 두 번 들어가는 사고가 있었다 (실측 2026-08-11:
            # '건설 및 토목 자재' 테마에 금호건설(002990) 2회). 후보 풀을 여러
            # 시그널에서 합치는 구조라 같은 코드가 다른 경로로 두 번 올라올 수
            # 있다. 화면에서는 트리맵 면적이 두 배로 잡혀 테마 크기까지 틀어진다.
            "stocks": _dedupe_by_code(stock_details),
        })

        print(f"  [OK] {theme_name}: {len(stock_details)}개 선정 "
              f"(1위 {stock_details[0]['name']} {stock_details[0]['score']}점)")

    print(f"[INFO] 테마 선정 통계 (relaxed={relaxed}): {stats}")
    return result_themes


if __name__ == "__main__":
    # 테스트: 삼성전자 데이터 조회
    code = search_stock_code("삼성전자")
    if code:
        print(f"삼성전자 코드: {code}")
        detail = get_stock_detail(code)
        if detail:
            print(json.dumps(detail, ensure_ascii=False, indent=2))
    else:
        print("종목코드를 찾을 수 없습니다.")
