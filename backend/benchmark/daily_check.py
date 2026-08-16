"""매일 저녁 대조 — 그의 당일 관심종목이 우리 수급탭 후보와 일치했는가.

`compare.py` 는 섹터·현금비중·종목을 한 번에 재는 감사 도구다. 이건 그중
**종목 한 축만** 매일 보기 위한 것이고, 결정적으로 다르게 하는 게 하나 있다:
빗나간 종목마다 **어느 관문에서 떨어졌는지**를 붙인다.

적중률 숫자만 매일 읽으면 아무것도 안 고쳐진다. "0.31" 은 내일도 0.31 이다.
고칠 수 있는 건 "그가 산 파마리서치가 우리 유니버스엔 있었는데 섹터 상한에
걸려 잘렸다" 같은 문장이다. 처방이 관문마다 다르기 때문이다:

  유니버스 밖   → 시총 컷 밖. `EXPLICIT_SECTOR` 추가 검토 (universe.py)
  주도섹터 밖   → 그날 우리가 그 섹터를 주도로 안 봤다. 섹터 판정 문제
  섹터 상한     → 조건은 다 통과했는데 자리가 없었다. MAX_PER_SECTOR 문제
  조건 탈락     → 추세/빈집/점수 중 하나. 임계값 문제
  적중          → 없음

하루치로 로직을 흔들지 않는다(README 의 2일 규칙). 그래서 리포트 끝에
최근 N일 누적을 같이 찍는다 — 반복된 관문이 진짜 결함이다.

쓰는 법:
    cd backend
    python -m benchmark.daily_check                 # 오늘(KST)
    python -m benchmark.daily_check --date 2026-08-11
    python -m benchmark.daily_check --days 5        # 최근 5일 누적까지
    python -m benchmark.daily_check --out report.md

레퍼런스 출처 (위에서부터 먼저 잡히는 것을 쓴다):
    REFERENCE_DAILY_DIR   로컬 디렉터리 (기본 ~/repo/stock_chat/data/daily).
                          stock_chat 이 있는 기계에서는 이것만으로 끝난다.
    SHARE_PASSPHRASE      stock_chat 의 공개 배포 번들(GitHub Pages 의 core.enc)을
                          받아 복호화한다. CI 처럼 stock_chat 체크아웃이 없는 곳의
                          기본 경로. 베이스 주소는 STOCK_CHAT_BUNDLE_URL 로 바꾼다.
    REFERENCE_DAILY_URL   HTTP 베이스 URL — `<base>/YYYY-MM-DD.json` 로 받는다.
                          레퍼런스를 어딘가로 따로 실어 나를 때의 우회로.

경계는 compare.py 와 같다. 레퍼런스는 **정답지**일 뿐이고, 그 문장·수치는
제품에 실어 나르지 않는다. 이 모듈도 Lambda 에 배포되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .reference import (available_dates, collector_failure, daily_dir,
                            load_reference, staleness_days)
    from .stock_chat_bundle import BundleError, fetch as fetch_bundle
except ImportError:  # 스크립트로 직접 실행
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from benchmark.reference import (available_dates, collector_failure, daily_dir,
                                     load_reference, staleness_days)
    from benchmark.stock_chat_bundle import BundleError, fetch as fetch_bundle

KST = timezone(timedelta(hours=9))
S3_BASE = "https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com"
SNAPSHOT_URL = S3_BASE + "/history/flow/{date}.json"
CACHE_DIR = Path(__file__).resolve().parent / "_snapshots"
BUNDLE_CACHE = CACHE_DIR / "stock_chat"

# 번들이 이 시간보다 오래 묵었으면 stock_chat 파이프라인이 멈춰 있다는 뜻이다.
# hourly.yml 이 KST 05~24시 30분마다 도니 저녁 대조 시점엔 1시간 안쪽이 정상이다.
BUNDLE_STALE_HOURS = 6

# 관문 — 위에서부터 순서대로 판정한다. 순서가 곧 파이프라인 순서다.
GATE_HIT = "적중"
GATE_CONCENTRATION = "섹터 상한"
GATE_CONDITION = "조건 탈락"
GATE_NOT_LEADING = "주도섹터 밖"
GATE_NOT_UNIVERSE = "유니버스 밖"

GATE_FIX = {
    GATE_NOT_UNIVERSE: "시총 컷 밖 — universe.py 의 EXPLICIT_SECTOR 추가 검토",
    GATE_NOT_LEADING: "그날 이 섹터를 주도로 안 봤다 — 섹터 판정/강도 임계값",
    GATE_CONCENTRATION: "조건은 통과했는데 자리가 없었다 — 섹터당 상한",
    GATE_CONDITION: "추세(10MA)·빈집(osc<0)·점수 중 하나에서 탈락 — 임계값",
}


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


# ── 레퍼런스 ────────────────────────────────────────────────
def ensure_reference() -> dict | None:
    """stock_chat 체크아웃이 없으면 공개 배포 번들에서 레퍼런스를 펼친다.

    반환값은 번들 메타(`updatedAt` 등) 또는 None(로컬을 쓰거나 못 받았을 때).

    **날짜 하나만 받아오는 방식(`REFERENCE_DAILY_URL`)으로는 부족하다.** 그 경로는
    `available_dates()` 가 비어 있어서 `--days` 누적이 통째로 안 돈다 — 이 도구의
    핵심인 '2일 이상 반복된 격차' 절이 CI 에서 영원히 안 찍힌다는 뜻이다.
    번들은 전 기간이 한 파일에 들어있어 한 번 펼치면 그 문제가 없다.
    """
    if available_dates():
        return None  # 로컬(또는 이미 펼쳐둔 캐시)에 있다. 네트워크를 안 탄다.
    if not os.environ.get("SHARE_PASSPHRASE"):
        return None
    try:
        meta = fetch_bundle(BUNDLE_CACHE)
    except BundleError as exc:
        print(f"[!] stock_chat 번들 수신 실패: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[!] stock_chat 번들 수신 실패 ({type(exc).__name__}): {exc}", file=sys.stderr)
        return None
    if not meta.get("days"):
        return meta
    os.environ["REFERENCE_DAILY_DIR"] = str(BUNDLE_CACHE)
    return meta


def bundle_age_hours(meta: dict | None) -> float | None:
    """번들이 만들어진 지 몇 시간 됐는지. 시각을 못 읽으면 None."""
    stamp = (meta or {}).get("updatedAt")
    if not stamp:
        return None
    try:
        made = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if made.tzinfo is None:
        made = made.replace(tzinfo=KST)
    return (datetime.now(KST) - made).total_seconds() / 3600


def load_reference_any(date_str: str) -> dict | None:
    """로컬 디렉터리 우선, 없으면 REFERENCE_DAILY_URL.

    CI 에는 stock_chat 체크아웃이 없다. 레퍼런스 data/ 는 평문이라 커밋하지
    않는 정책이므로 git 으로는 가져올 수 없다 — URL 경로가 그 우회로다.
    """
    local = load_reference(date_str)
    if local is not None:
        return local

    base = (os.environ.get("REFERENCE_DAILY_URL") or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/{date_str}.json"
    tmp = CACHE_DIR / "reference"
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / f"{date_str}.json"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            dest.write_bytes(r.read())
    except Exception as exc:
        print(f"[!] 레퍼런스 원격 수신 실패 ({url}): {exc}", file=sys.stderr)
        return None
    # reference.load_reference 는 daily_dir() 만 보므로 잠시 그쪽을 가리킨다.
    old = os.environ.get("REFERENCE_DAILY_DIR")
    os.environ["REFERENCE_DAILY_DIR"] = str(tmp)
    try:
        return load_reference(date_str)
    finally:
        if old is None:
            os.environ.pop("REFERENCE_DAILY_DIR", None)
        else:
            os.environ["REFERENCE_DAILY_DIR"] = old


# ── 우리 스냅샷 ─────────────────────────────────────────────
def load_snapshot(date_str: str) -> tuple[dict | None, str]:
    """그날의 flow 스냅샷. (payload, 출처설명).

    **다른 날 스냅샷으로 폴백하지 않는다.** compare.py 는 최신본 폴백을
    허용하는데, 그건 감사용이라 경고를 읽고 판단할 사람이 있어서다.
    매일 자동으로 도는 이 경로에서 폴백하면 어제 시장으로 오늘을 채점하고도
    리포트는 멀쩡해 보인다. 없으면 없다고 말하고 끝낸다.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{date_str}.json"
    if cached.exists():
        return json.loads(cached.read_text()), f"캐시 {cached.name}"
    url = SNAPSHOT_URL.format(date=date_str)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            raw = r.read()
    except Exception as exc:
        return None, f"수신 실패 ({url}): {exc}"
    cached.write_bytes(raw)
    return json.loads(raw), f"S3 history/flow/{date_str}.json"


# ── 판정 ────────────────────────────────────────────────────
def _norm(name: str) -> str:
    return "".join(str(name or "").split())


_KRX_NAMES: set[str] | None = None


def krx_names() -> set[str] | None:
    """KRX 전체 상장 종목명. 확인할 방법이 없으면 None.

    레퍼런스는 미장도 함께 다룬다. 해외 종목을 '놓친 종목'으로 세면 적중률이
    구조적으로 낮게 나온다. 그렇다고 **이름에 한글이 있으면 국내** 같은 어림
    짐작을 쓰면 안 된다 — '애플'·'엔비디아'·'테슬라' 가 전부 국내로 잡힌다
    (실측: 그 한 줄 때문에 애플이 '유니버스 밖 미달 종목'으로 리포트에 올랐다).
    조용히 틀린 숫자는 대조를 안 하느니만 못하므로, 확인이 안 되면 확인이
    안 됐다고 말한다.
    """
    global _KRX_NAMES
    if _KRX_NAMES is not None:
        return _KRX_NAMES or None
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        _KRX_NAMES = {_norm(x) for x in df["Name"].dropna().tolist()}
    except Exception as exc:
        print(f"[!] KRX 상장 목록을 못 받았다 ({exc}). 국내/해외 구분을 생략한다.",
              file=sys.stderr)
        _KRX_NAMES = set()
    return _KRX_NAMES or None


def classify(tickers: list[str], flow: dict) -> list[dict]:
    """그의 관심종목 각각이 어느 관문에서 멈췄는지."""
    cand = {_norm(c.get("name")): c for c in (flow.get("buyCandidates") or [])}
    cand_order = {_norm(c.get("name")): i + 1
                  for i, c in enumerate(flow.get("buyCandidates") or [])}
    over = {_norm(o.get("name")): o for o in (flow.get("overflowCandidates") or [])}
    uni = {_norm(m.get("name")): m for m in (flow.get("universeMetadata") or [])}
    leading = set(flow.get("leadingSectorLabels") or [])

    rows = []
    for raw in tickers:
        n = _norm(raw)
        if n in cand:
            c = cand[n]
            rows.append({
                "name": raw, "gate": GATE_HIT, "sector": c.get("sector"),
                "detail": f"후보 {cand_order[n]}위 · 점수 {c.get('flowScore')} · "
                          f"빈집 하위 {round(c.get('oscPercentile') or 0)}%",
            })
            continue
        if n in over:
            o = over[n]
            rows.append({
                "name": raw, "gate": GATE_CONCENTRATION, "sector": o.get("sector"),
                "detail": f"점수 {o.get('flowScore')} · 빈집 하위 "
                          f"{round(o.get('oscPercentile') or 0)}% — 같은 섹터가 먼저 찼다",
            })
            continue
        if n in uni:
            sec = uni[n].get("sector")
            if sec in leading:
                rows.append({"name": raw, "gate": GATE_CONDITION, "sector": sec,
                             "detail": "주도섹터엔 들었으나 추세·빈집·점수에서 탈락"})
            else:
                rows.append({"name": raw, "gate": GATE_NOT_LEADING, "sector": sec,
                             "detail": f"우리 분류 '{sec}' — 그날 주도섹터 아님"})
            continue
        rows.append({"name": raw, "gate": GATE_NOT_UNIVERSE, "sector": None,
                     "detail": "유니버스(시총 상위) 밖"})
    return rows


def check_one(date_str: str) -> dict:
    ref = load_reference_any(date_str)
    if ref is None:
        return {"date": date_str, "status": "no_reference"}

    flow, source = load_snapshot(date_str)
    if flow is None:
        return {"date": date_str, "status": "no_snapshot", "snapshotNote": source}

    # 날짜 확인 — 스냅샷 안의 날짜가 다르면 다른 날 시장으로 채점하는 꼴이다.
    stamped = str(flow.get("updatedAt") or "")[:10]
    if stamped and stamped != date_str:
        return {"date": date_str, "status": "date_mismatch",
                "snapshotDate": stamped, "snapshotNote": source}

    all_t = [t.strip() for t in (ref.get("tickers") or []) if t and t.strip()]
    names = krx_names()
    if names is None:
        domestic, offshore, scope_note = all_t, [], "국내/해외 구분 못 함 (KRX 목록 수신 실패)"
    else:
        domestic = [t for t in all_t if _norm(t) in names]
        offshore = [t for t in all_t if _norm(t) not in names]
        scope_note = None

    rows = classify(domestic, flow)
    gates = Counter(r["gate"] for r in rows)
    n = len(domestic) or 1
    return {
        "date": date_str, "status": "ok", "snapshotNote": source,
        "scopeNote": scope_note,
        "referenceCount": len(domestic), "offshore": offshore,
        "rows": rows, "gates": dict(gates),
        "hitRate": round(gates[GATE_HIT] / n, 3),
        "reachRate": round((n - gates[GATE_NOT_UNIVERSE]) / n, 3),
        "ourCandidates": [c.get("name") for c in (flow.get("buyCandidates") or [])],
        "leadingSectors": flow.get("leadingSectorLabels") or [],
        "filterStats": flow.get("candidateFilterStats") or {},
    }


# ── 리포트 ──────────────────────────────────────────────────
def render(res: dict, history: list[dict] | None = None,
           bundle: dict | None = None) -> str:
    L: list[str] = []
    d = res["date"]
    L.append(f"# 관심종목 대조 — {d}")
    L.append("")

    if res["status"] == "no_reference":
        L.append("**레퍼런스 없음.** 그날 stock_chat 데이터가 없어 대조하지 못했다.")
        L.append("")
        if bundle:
            L.append(f"- 번들은 받았다 ({bundle.get('first')} ~ {bundle.get('last')}, "
                     f"{bundle.get('days')}일) — 그중 {d} 가 없다")
            L.append("- 주말·공휴일이면 정상이다. 평일인데 없으면 수집이 멈춘 것이다")
        elif os.environ.get("SHARE_PASSPHRASE"):
            L.append("- 번들을 못 받았다. 위 스텝 로그의 수신 실패 사유를 볼 것")
        else:
            L.append("- `SHARE_PASSPHRASE` 시크릿이 없어 stock_chat 번들을 못 읽는다 "
                     "(stock_chat 과 같은 값을 이 레포 시크릿에 넣으면 붙는다)")
        L.append("- 수집이 멈췄는지 먼저 확인할 것 (초대 링크 만료가 반복된다)")
        L.append("- 복구: `cd ~/repo/stock_chat && python -m pipeline.run --weeks 1`")
        L.append(f"- 로컬 경로: `{daily_dir()}`")
        return "\n".join(L)

    if res["status"] == "no_snapshot":
        L.append("**우리 스냅샷 없음.** 그날 flow 산출물을 못 받았다.")
        L.append(f"- {res.get('snapshotNote')}")
        L.append("- flow 는 평일 8~20시만 돈다. 주말·공휴일이면 정상이다.")
        return "\n".join(L)

    if res["status"] == "date_mismatch":
        L.append(f"**날짜 불일치.** 스냅샷이 {res.get('snapshotDate')} 라 {d} 와 다르다.")
        L.append("다른 날 시장으로 채점하면 결론이 통째로 틀리므로 여기서 멈춘다.")
        return "\n".join(L)

    g = res["gates"]
    n = res["referenceCount"]
    hit = g.get(GATE_HIT, 0)
    L.append(f"**그의 관심종목 {n}종목 중 {hit}개가 우리 후보에도 있었다 "
             f"(적중 {res['hitRate']:.0%} · 유니버스 도달 {res['reachRate']:.0%})**")
    L.append("")

    # 관문별 요약 — 어디서 새는지가 처방을 정한다
    L.append("## 어느 관문에서 갈렸나")
    L.append("")
    L.append("| 관문 | 종목 수 | 처방 |")
    L.append("|---|---:|---|")
    for gate in (GATE_HIT, GATE_CONCENTRATION, GATE_CONDITION,
                 GATE_NOT_LEADING, GATE_NOT_UNIVERSE):
        if g.get(gate):
            L.append(f"| {gate} | {g[gate]} | {GATE_FIX.get(gate, '—')} |")
    L.append("")

    L.append("## 종목별")
    L.append("")
    L.append("| 종목 | 관문 | 우리 분류 | 내용 |")
    L.append("|---|---|---|---|")
    order = {GATE_HIT: 0, GATE_CONCENTRATION: 1, GATE_CONDITION: 2,
             GATE_NOT_LEADING: 3, GATE_NOT_UNIVERSE: 4}
    for r in sorted(res["rows"], key=lambda x: order.get(x["gate"], 9)):
        L.append(f"| {r['name']} | {r['gate']} | {r['sector'] or '-'} | {r['detail']} |")
    L.append("")

    if res.get("offshore"):
        L.append(f"> 해외 종목 {len(res['offshore'])}개는 스코프 밖이라 뺐다: "
                 f"{', '.join(res['offshore'])}")
        L.append("")
    if res.get("scopeNote"):
        L.append(f"> ⚠ {res['scopeNote']} — 미장 종목이 '유니버스 밖'으로 섞여 "
                 f"적중률이 실제보다 낮게 보일 수 있다.")
        L.append("")

    fs = res.get("filterStats") or {}
    L.append("## 그날 우리 화면")
    L.append("")
    L.append(f"- 주도 업종: {', '.join(res['leadingSectors']) or '-'}")
    L.append(f"- 후보 {len(res['ourCandidates'])}종목: "
             f"{', '.join(res['ourCandidates'][:12])}"
             f"{' …' if len(res['ourCandidates']) > 12 else ''}")
    if fs:
        L.append(f"- 필터: {fs.get('beforeFilter')} → {fs.get('afterFilter')} "
                 f"(추세 {fs.get('droppedByTrend')} / 빈집 {fs.get('droppedByVacancy')} / "
                 f"점수 {fs.get('droppedByScore')} / 섹터상한 {fs.get('droppedByConcentration')})")
    L.append("")

    # 누적 — 하루치로 로직을 흔들지 않기 위한 장치
    if history:
        ok = [h for h in history if h.get("status") == "ok"]
        if ok:
            L.append(f"## 최근 {len(ok)}일 누적")
            L.append("")
            tot = sum(h["referenceCount"] for h in ok) or 1
            agg = Counter()
            for h in ok:
                agg.update(h["gates"])
            L.append(f"- 적중 {agg[GATE_HIT]}/{tot} ({agg[GATE_HIT] / tot:.0%})")
            for gate in (GATE_CONCENTRATION, GATE_CONDITION,
                         GATE_NOT_LEADING, GATE_NOT_UNIVERSE):
                if agg.get(gate):
                    L.append(f"- {gate} {agg[gate]}건")
            L.append("")
            # 2일 이상 반복된 종목만 조치 후보
            miss_days = Counter()
            for h in ok:
                for r in h["rows"]:
                    if r["gate"] != GATE_HIT:
                        miss_days[(r["name"], r["gate"])] += 1
            repeated = [(k, v) for k, v in miss_days.items() if v >= 2]
            if repeated:
                L.append("**2일 이상 반복된 격차 — 여기부터 본다**")
                L.append("")
                for (name, gate), cnt in sorted(repeated, key=lambda x: -x[1]):
                    L.append(f"- {name} · {gate} · {cnt}일")
            else:
                L.append("2일 이상 반복된 격차 없음 — 오늘 결과로 로직을 고치지 말 것.")
            L.append("")

    # 레퍼런스 신선도 — 낡은 정답지로 채점하면 대조를 안 하느니만 못하다
    stale = staleness_days()
    fail = collector_failure()
    age = bundle_age_hours(bundle)
    if fail:
        L.append(f"> ⚠ 레퍼런스 수집기 마지막 실행 실패: {fail}")
    elif stale is not None and stale >= 3:
        L.append(f"> ⚠ 레퍼런스가 {stale}일 묵었다. 수집부터 다시 돌릴 것.")
    if age is not None and age >= BUNDLE_STALE_HOURS:
        # 데이터 나이와 다른 신호다. 수집기가 오늘 죽어도 어제 요약이 남아 있으면
        # 나이는 1일이라 위 경고가 안 뜬다 (2026-08-10 실사고).
        L.append(f"> ⚠ stock_chat 번들이 {age:.0f}시간 전 것이다 "
                 f"({bundle.get('updatedAt')}). 저쪽 파이프라인이 멈췄는지 볼 것.")
    elif bundle:
        L.append(f"> 레퍼런스 출처: stock_chat 배포 번들 "
                 f"({bundle.get('days')}일 · 생성 {bundle.get('updatedAt')})")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="당일 관심종목 vs 우리 수급탭 후보 대조")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--days", type=int, default=5, help="누적 표본 일수 (기본 5)")
    ap.add_argument("--out", default=None, help="마크다운 저장 경로")
    ap.add_argument("--json", dest="as_json", action="store_true", help="JSON 으로 출력")
    args = ap.parse_args()

    date_str = args.date or today_kst()
    bundle = ensure_reference()
    res = check_one(date_str)

    history: list[dict] = []
    if args.days > 1:
        have = set(available_dates())
        y, m, d = (int(x) for x in date_str.split("-"))
        cur = datetime(y, m, d, tzinfo=KST)
        for i in range(1, args.days):
            prev = (cur - timedelta(days=i)).strftime("%Y-%m-%d")
            if prev in have:
                history.append(check_one(prev))
    if res.get("status") == "ok":
        history.insert(0, res)

    if args.as_json:
        print(json.dumps({"today": res, "history": history, "bundle": bundle},
                         ensure_ascii=False, indent=2))
        return 0

    md = render(res, history, bundle)
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n[저장] {args.out}", file=sys.stderr)
    # 체크 작업은 결과가 나빠도 실패로 두지 않는다. 대조를 못 한 경우만 1.
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
