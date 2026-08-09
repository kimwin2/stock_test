"""우리 flow 산출물 ↔ 참고 채널 대조 리포트.

세 가지를 본다.

  1. 현금비중  — 우리 권고와 레퍼런스의 그날 현금 %. 절대 차이.
  2. 섹터      — 그가 주목한 섹터 중 우리가 주도섹터로 잡은 비율(재현율).
                 못 잡은 것은 '사전에 아예 없음' 과 '있는데 순위 밖' 으로
                 나눈다. 전자는 코드 결함, 후자는 임계값 문제라 처방이 다르다.
  3. 종목      — 그가 언급한 종목 중 우리 유니버스/후보에 있는 비율.

출력은 "우리 로직을 어디서 고칠 것인가" 로 끝난다. 레퍼런스 문장은 옮기지 않는다.

사용:
    python -m benchmark.compare --date 2026-08-01
    python -m benchmark.compare --all
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

try:
    from benchmark.reference import load_reference, available_dates, family_of, staleness_days
    from flow_signals.universe import SECTOR_RULES
except ImportError:  # 패키지 상대 실행
    from .reference import load_reference, available_dates, family_of, staleness_days
    from ..flow_signals.universe import SECTOR_RULES

FLOW_S3_URL = "https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/flow_dashboard.json"
OUR_SECTORS = {name for name, _ in SECTOR_RULES}

# 국내 상장 여부 판정 캐시 — 네이버 조회는 느리고 결과가 잘 안 변한다.
_DOMESTIC_CACHE_PATH = Path(__file__).with_name("_domestic_cache.json")


def _load_cache() -> dict:
    if _DOMESTIC_CACHE_PATH.exists():
        try:
            return json.loads(_DOMESTIC_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


_DOMESTIC_CACHE = _load_cache()


def is_domestic(name: str) -> bool:
    """KRX 상장 종목명인가.

    레퍼런스는 미장 종목(애플·아마존 등)도 함께 언급한다. 우리는 국장
    전용이라 그걸 '놓친 종목'으로 세면 커버리지가 구조적으로 왜곡된다.
    이름으로 종목코드가 잡히면 국내, 아니면 스코프 밖으로 본다.
    """
    if name in _DOMESTIC_CACHE:
        return _DOMESTIC_CACHE[name]
    try:
        from stock_data import search_stock_code
    except ImportError:
        from ..stock_data import search_stock_code
    try:
        ok = bool(search_stock_code(name))
    except Exception:
        ok = False
    _DOMESTIC_CACHE[name] = ok
    try:
        _DOMESTIC_CACHE_PATH.write_text(json.dumps(_DOMESTIC_CACHE, ensure_ascii=False, indent=0))
    except Exception:
        pass
    return ok


FLOW_SNAPSHOT_URL = ("https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com"
                     "/history/flow/{date}.json")
SNAPSHOT_CACHE = Path(__file__).with_name("_snapshots")


def load_snapshot(date_str: str) -> dict | None:
    """그날의 flow 스냅샷. S3 에 있으면 받아서 로컬에 캐시한다.

    최신본만 보면 다른 날 시장과 견주게 되어 섹터 비교가 무의미해진다.
    파이프라인이 history/flow/YYYY-MM-DD.json 을 남기므로 그걸 쓴다.
    """
    SNAPSHOT_CACHE.mkdir(exist_ok=True)
    local = SNAPSHOT_CACHE / f"{date_str}.json"
    if local.exists():
        return json.loads(local.read_text())
    try:
        with urllib.request.urlopen(FLOW_SNAPSHOT_URL.format(date=date_str), timeout=60) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    local.write_text(json.dumps(data, ensure_ascii=False))
    return data


def load_flow(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text())
    with urllib.request.urlopen(FLOW_S3_URL, timeout=60) as r:
        return json.loads(r.read())


def compare_one(ref: dict, flow: dict) -> dict:
    out: dict = {"date": ref["date"]}
    # 날짜가 어긋나면 섹터·종목 비교는 성립하지 않는다 (다른 날 시장을 견주는 꼴).
    # 조용히 숫자만 뱉으면 그걸 근거로 로직을 잘못 고치게 되므로 명시한다.
    flow_date = (flow.get("updatedAt") or "")[:10]
    out["flowDate"] = flow_date
    out["dateMatched"] = (flow_date == ref["date"])

    # ── 1. 현금비중 ────────────────────────────────
    ours = (flow.get("cashRecommendation") or {}).get("cashPct")
    theirs = ref.get("cashEnd")
    # 부호를 살린다. 절댓값만 보면 "우리가 늘 공격적인가 보수적인가" 라는
    # 편향(bias)을 못 읽어서, 정작 고쳐야 할 방향을 알 수 없다.
    signed = (ours - theirs) if (ours is not None and theirs is not None) else None
    out["cash"] = {
        "ours": ours,
        "reference": theirs,
        "diff": (abs(signed) if signed is not None else None),
        "signed": signed,
    }

    # ── 2. 섹터 ────────────────────────────────────
    our_leading = [s for s in (flow.get("leadingSectorLabels") or []) if s]
    our_set = set(our_leading)
    # 우리 유니버스가 실제로 커버하는 섹터 (사전에 존재하는가)
    ref_pairs = list(zip(ref.get("sectorsRaw") or [], ref.get("sectors") or []))

    hit, family_hit, missed_rank, missed_dict = [], [], [], []
    for raw, norm in ref_pairs:
        if norm in our_set:
            hit.append(norm)
        elif family_of(norm) & our_set:
            # 같은 계열을 우리가 더 좁게(또는 넓게) 잡은 경우. 놓친 게 아니다.
            family_hit.append(f"{norm}~{sorted(family_of(norm) & our_set)[0]}")
        elif norm in OUR_SECTORS:
            missed_rank.append(norm)      # 사전엔 있는데 주도섹터로 안 뽑힘
        else:
            missed_dict.append(raw)       # 사전에 아예 없음 — 표현 자체가 불가능
    total = len(ref_pairs) or 1
    out["sector"] = {
        "referenceCount": len(ref_pairs),
        "ourLeading": our_leading,
        "hit": sorted(set(hit)),
        "familyHit": sorted(set(family_hit)),
        "missedByRank": sorted(set(missed_rank)),
        "missedByDictionary": sorted(set(missed_dict)),
        "recall": round(len(set(hit)) / total, 3),
        "recallFamily": round((len(set(hit)) + len(set(family_hit))) / total, 3),
    }

    # ── 3. 종목 ────────────────────────────────────
    uni = {(m.get("name") or "").strip() for m in (flow.get("universeMetadata") or [])}
    cand = {(c.get("name") or "").strip() for c in (flow.get("buyCandidates") or [])}
    all_tickers = [t.strip() for t in (ref.get("tickers") or []) if t and t.strip()]

    # 레퍼런스는 미장도 함께 다룬다. 우리는 국장 전용이므로 해외 종목을
    # '놓친 종목'으로 세면 재현율이 구조적으로 왜곡된다. 스코프 밖으로 분리.
    ref_tickers, offshore = [], []
    for t in all_tickers:
        (ref_tickers if is_domestic(t) else offshore).append(t)

    in_cand = [t for t in ref_tickers if t in cand]
    in_uni = [t for t in ref_tickers if t in uni and t not in cand]
    outside = [t for t in ref_tickers if t not in uni and t not in cand]
    n = len(ref_tickers) or 1
    out["ticker"] = {
        "referenceCount": len(ref_tickers),
        "offshore": offshore,
        "inCandidates": in_cand,
        "inUniverseOnly": in_uni,
        "outsideUniverse": outside,
        "universeCoverage": round((len(in_cand) + len(in_uni)) / n, 3),
        "candidateHitRate": round(len(in_cand) / n, 3),
    }

    out["stance"] = {"reference": ref.get("stance")}
    return out


def print_report(res: dict) -> None:
    c, s, t = res["cash"], res["sector"], res["ticker"]
    print(f"\n{'=' * 62}\n[{res['date']}] 대조 리포트\n{'=' * 62}")
    if not res.get("dateMatched"):
        print(f"   ⚠ flow 스냅샷 날짜가 {res.get('flowDate') or '?'} 라 레퍼런스({res['date']})와 다르다.")
        print("     현금비중 외 섹터·종목 비교는 참고용으로만 볼 것 (--flow-dir 로 같은 날 스냅샷 지정).")

    print("\n■ 현금비중")
    if c["diff"] is None:
        print(f"   우리 {c['ours']}% / 레퍼런스 {c['reference']}% — 한쪽이 비어 비교 불가")
    else:
        verdict = "일치" if c["diff"] <= 5 else ("근접" if c["diff"] <= 15 else "괴리")
        lean = "우리가 더 공격적" if c["signed"] < 0 else ("우리가 더 보수적" if c["signed"] > 0 else "")
        print(f"   우리 {c['ours']}%  ↔  레퍼런스 {c['reference']}%   차이 {c['signed']:+}p  [{verdict}] {lean}")

    print(f"\n■ 섹터  정확 {s['recall']:.0%} · 계열포함 {s['recallFamily']:.0%}"
          f"  ({len(s['hit'])}+{len(s['familyHit'])}/{s['referenceCount']})")
    print(f"   우리 주도섹터: {', '.join(s['ourLeading']) or '-'}")
    print(f"   정확 일치    : {', '.join(s['hit']) or '-'}")
    if s["familyHit"]:
        print(f"   계열 일치    : {', '.join(s['familyHit'])}")
    if s["missedByRank"]:
        print(f"   놓침(순위밖) : {', '.join(s['missedByRank'])}")
        print("       → 사전엔 있다. 주도섹터 선정 임계값/상한 문제.")
    if s["missedByDictionary"]:
        print(f"   놓침(사전없음): {', '.join(s['missedByDictionary'])}")
        print("       → 표현 자체가 불가능하다. SECTOR_RULES 보강이 필요한 구조적 결함.")

    print(f"\n■ 종목  유니버스 커버리지 {t['universeCoverage']:.0%} · 후보 적중 {t['candidateHitRate']:.0%}")
    if t["inCandidates"]:
        print(f"   후보에 있음   : {', '.join(t['inCandidates'])}")
    if t["inUniverseOnly"]:
        print(f"   유니버스만    : {', '.join(t['inUniverseOnly'][:12])}")
    if t["outsideUniverse"]:
        print(f"   유니버스 밖   : {', '.join(t['outsideUniverse'][:12])}")
        print("       → 국내 상장인데 우리 유니버스에 없다. 시총 컷 밖이거나 섹터 미분류.")
    if t["offshore"]:
        print(f"   스코프 밖(해외): {', '.join(t['offshore'][:10])}")


def print_actions(results: list[dict]) -> None:
    """여러 날을 모아 반복되는 결함만 추린다. 하루치 노이즈로 로직을 흔들지 않기 위함."""
    if not results:
        return
    from collections import Counter
    dict_gap = Counter(x for r in results for x in r["sector"]["missedByDictionary"])
    rank_gap = Counter(x for r in results for x in r["sector"]["missedByRank"])
    outside = Counter(x for r in results for x in r["ticker"]["outsideUniverse"])
    diffs = [r["cash"]["signed"] for r in results if r["cash"].get("signed") is not None]

    print(f"\n{'=' * 62}\n종합 — {len(results)}일 누적\n{'=' * 62}")
    if diffs:
        avg = sum(diffs) / len(diffs)
        bias = "우리가 일관되게 더 공격적" if avg < -2 else ("우리가 일관되게 더 보수적" if avg > 2 else "편향 없음")
        print(f"\n■ 현금비중 평균 편차 {avg:+.1f}p  (최대 괴리 {max(abs(d) for d in diffs)}p) — {bias}")
    matched = [r for r in results if r.get("dateMatched")]
    recalls = [r["sector"]["recall"] for r in results]
    fam = [r["sector"]["recallFamily"] for r in results]
    print(f"■ 섹터 재현율 평균 정확 {sum(recalls) / len(recalls):.0%} · "
          f"계열포함 {sum(fam) / len(fam):.0%}"
          + ("" if len(matched) == len(results) else f"  (날짜 일치 {len(matched)}/{len(results)}일 — 나머지는 참고값)"))
    covs = [r["ticker"]["universeCoverage"] for r in results]
    print(f"■ 종목 유니버스 커버리지 평균 {sum(covs) / len(covs):.0%}")

    print("\n■ 조치 후보 (반복 2회 이상만)")
    acted = False
    for name, cnt in dict_gap.most_common():
        if cnt >= 2:
            acted = True
            print(f"   [사전 누락] '{name}' {cnt}일 — SECTOR_RULES 에 추가 검토")
    for name, cnt in rank_gap.most_common():
        if cnt >= 2:
            acted = True
            print(f"   [순위 누락] '{name}' {cnt}일 — MAX_LEADING_SECTORS/강도 임계값 검토")
    for name, cnt in outside.most_common(8):
        if cnt >= 2:
            acted = True
            print(f"   [유니버스 밖] '{name}' {cnt}일 — EXPLICIT_SECTOR 등록 검토")
    if not acted:
        print("   반복 결함 없음")


def main() -> int:
    ap = argparse.ArgumentParser(description="참고 채널 대조 리포트")
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="가능한 모든 날짜")
    ap.add_argument("--flow", help="flow_dashboard.json 경로 (기본: S3 최신)")
    ap.add_argument("--flow-dir", help="날짜별 flow 스냅샷 디렉터리 (YYYY-MM-DD.json)")
    ap.add_argument("--strict", action="store_true",
                    help="같은 날 스냅샷이 없으면 건너뛴다 (최신본 대체 금지)")
    args = ap.parse_args()

    # 수집이 멈추면 낡은 데이터로 계속 대조하게 된다. 결론 내기 전에 먼저 알린다.
    stale = staleness_days()
    if stale is None:
        print("[!] 레퍼런스 데이터가 하나도 없습니다. stock_chat 수집을 먼저 돌리세요.", file=sys.stderr)
    elif stale >= 3:
        print(f"[!] 레퍼런스 최신 데이터가 {stale}일 묵었습니다 — 수집이 멈췄을 수 있습니다.\n"
              f"    cd ~/repo/stock_chat && python -m pipeline.run --weeks 1", file=sys.stderr)

    dates = available_dates() if args.all else ([args.date] if args.date else [])
    if not dates:
        print("대조할 날짜가 없습니다. --date 또는 --all 을 주세요.", file=sys.stderr)
        print(f"레퍼런스 디렉터리에 있는 날짜: {available_dates() or '(없음)'}", file=sys.stderr)
        return 1

    results = []
    for d in dates:
        ref = load_reference(d)
        if not ref:
            print(f"[skip] {d}: 레퍼런스 없음", file=sys.stderr)
            continue
        flow_path = args.flow
        if args.flow_dir:
            p = Path(args.flow_dir) / f"{d}.json"
            if not p.exists():
                print(f"[skip] {d}: flow 스냅샷 없음 ({p})", file=sys.stderr)
                continue
            flow_path = str(p)

        flow = None
        if not flow_path:
            # 같은 날 스냅샷이 S3 에 있으면 그걸 쓴다. 없으면 최신본으로 폴백하되
            # 날짜 불일치 경고가 뜨므로 결과를 오해할 일은 없다.
            flow = load_snapshot(d)
            if flow is None and not args.strict:
                print(f"[warn] {d}: 스냅샷 없음 — 최신본으로 대체", file=sys.stderr)
            elif flow is None:
                print(f"[skip] {d}: 스냅샷 없음 (--strict)", file=sys.stderr)
                continue
        if flow is None:
            try:
                flow = load_flow(flow_path)
            except Exception as e:
                print(f"[skip] {d}: flow 로드 실패 {e}", file=sys.stderr)
                continue
        res = compare_one(ref, flow)
        results.append(res)
        print_report(res)

    print_actions(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
