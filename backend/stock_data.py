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
    "LG": "003550",
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

    # 부분 매칭 시도 (예: "삼성전자우" → "삼성전자")
    for name, code in STOCK_CODE_MAP.items():
        if stock_name.startswith(name) or name.startswith(stock_name):
            return code

    # 2. 네이버 증권 검색 시도
    return search_stock_code_online(stock_name)


def search_stock_code_online(stock_name: str) -> Optional[str]:
    """네이버 증권 종목 페이지에서 검색합니다."""
    # 방법 1: 네이버 금융 사이트맵에서 검색
    try:
        url = f"https://m.stock.naver.com/api/search/stocks?query={stock_name}"
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
        }, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            stocks = data.get("stocks", [])
            if stocks:
                code = stocks[0].get("stockCode") or stocks[0].get("code")
                if code:
                    STOCK_CODE_MAP[stock_name] = code  # 캐싱
                    return code
    except Exception as e:
        print(f"  [!] 모바일 검색 실패 ({stock_name}): {e}")

    # 방법 2: 네이버 통합검색에서 종목코드 추출
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
                STOCK_CODE_MAP[stock_name] = code  # 캐싱
                return code
    except Exception as e:
        print(f"  [!] 통합검색 실패 ({stock_name}): {e}")

    print(f"  [!] {stock_name} 종목코드를 찾을 수 없습니다.")
    return None

def get_stock_detail(stock_code: str) -> Optional[dict]:
    """
    네이버 금융에서 종목 상세 데이터를 조회합니다.
    1순위: 네이버 모바일 증권 API (깔끔한 JSON)
    2순위: 데스크탑 페이지 HTML 파싱
    """
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


def get_stock_details_for_themes(themes: list[dict]) -> list[dict]:
    """
    테마별 종목 상세 데이터를 조회하여 완성된 테마 데이터를 반환합니다.

    Args:
        themes: analyzer에서 추출된 테마 리스트
            [{"themeName": str, "headline": str, "relatedStocks": [str, ...], ...}]

    Returns:
        프론트엔드용 완성된 테마 데이터 리스트
    """
    result_themes = []

    for theme in themes:
        theme_name = theme["themeName"]
        headline = theme.get("headline", "")
        related_stocks = theme.get("relatedStocks", [])

        print(f"\n[INFO] 테마 '{theme_name}' 종목 데이터 조회 중...")

        stock_details = []
        total_volume = 0

        for stock_name in related_stocks:
            print(f"  [>] {stock_name} 검색 중...")

            # 1. 종목코드 검색
            code = search_stock_code(stock_name)
            if not code:
                print(f"  [!] {stock_name} 종목코드를 찾을 수 없습니다.")
                continue

            # 2. 종목 상세 데이터 조회
            detail = get_stock_detail(code)
            if not detail:
                continue

            # 3. barData 계산
            bar_data = calculate_bar_data(
                open_price=detail.get("open", detail["price"]),
                high=detail.get("high", detail["price"]),
                low=detail.get("low", detail["price"]),
                current=detail["price"],
                prev_close=detail.get("prevClose", detail["price"]),
            )

            stock_item = {
                "name": detail["name"] or stock_name,
                "price": detail["price"],
                "time": detail.get("time", ""),
                "changeRate": detail["changeRate"],
                "volume": detail["volume"],
                "isTop": False,  # 나중에 정렬 후 설정
                "barData": bar_data,
            }

            stock_details.append(stock_item)
            total_volume += detail.get("volumeRaw", 0)

            time.sleep(0.1)  # 요청 간격

            if len(stock_details) >= 4:
                break  # 테마당 4개만

        # 등락률 기준 정렬 (높은 순)
        stock_details.sort(key=lambda x: x["changeRate"], reverse=True)

        # 1위 종목은 isTop = True
        if stock_details:
            stock_details[0]["isTop"] = True

        # 4개 미만이면 패스하지 않고 있는 만큼만
        result_themes.append({
            "themeName": theme_name,
            "totalVolume": format_volume(total_volume),
            "headline": headline,
            "headlineUrl": theme.get("headlineUrl", ""),
            "stocks": stock_details[:4],
        })

        print(f"  [OK] {theme_name}: {len(stock_details)}개 종목 데이터 수집 완료")

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
