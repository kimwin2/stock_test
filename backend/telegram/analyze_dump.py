"""
fetch_dump.py 로 만든 raw 메시지 덤프를 분석해 다음을 출력합니다.
1) 기본 통계 (게시 빈도, 시간대 분포, 평균 길이, 미디어 비율)
2) GPT 분류 (메시지 타입별 분포 + 핵심 패턴 요약)
3) 대표 메시지 샘플 (각 카테고리 별 3개)

사용법:
    cd backend
    python -m telegram.analyze_dump --in telegram/dev/oscillation_raw.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter
from datetime import datetime
from statistics import median

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


GPT_MODEL = os.getenv("TG_ANALYZE_MODEL", "gpt-4o-mini")
BATCH_SIZE = int(os.getenv("TG_ANALYZE_BATCH", "80"))

CLASSIFY_PROMPT = """\
당신은 한국 주식 텔레그램 채널 분석 전문가입니다.
아래에 한 사람이 운영하는 채널의 메시지 묶음이 주어집니다. 형식은 한 줄에 하나, `[숫자ID] 본문` 입니다.

당신의 임무: **모든 메시지를 빠짐없이** 다음 카테고리 중 하나로 분류하세요.
- 시황: 코스피/코스닥/지수/시장 전반 코멘트
- 종목추천: 특정 종목 매수/관심/비중조절 시그널
- 수급분석: 외국인/기관/사모/투신/연금/거래원 매매동향, 업종별 수급 순위
- 실적공시: 기업 실적 발표, 매출/영업익/순이익 숫자, 공시 봇 형식
- 뉴스공유: 외부 뉴스 캡쳐/링크 공유 + 짧은 코멘트
- 차트분석: 차트 패턴, 신고가, 지지/저항, 기술적 지표
- 매매일지: 본인 매매 결과 보고 ("정리합니다", "이익확정", "비중축소" 등)
- 단신: 한 줄짜리 정리/메모/관찰
- 잡담: 인사/공지/광고/기타

출력은 JSON 배열만 반환. 다른 텍스트 절대 금지.
[{"id": 12345, "category": "수급분석"}, {"id": 12346, "category": "시황"}, ...]
모든 입력 ID에 대해 정확히 한 줄씩 출력해야 합니다.
"""

SUMMARY_PROMPT = """\
당신은 한국 주식 텔레그램 채널 분석 전문가입니다.
아래에 한 사람이 운영하는 채널의 메시지 카테고리 분포 + 무작위 샘플 메시지가 주어집니다.

분석할 것:
1. 이 사람의 "주력 활동" 1줄 요약
2. 정형화된 양식이 사용되는지, 사용된다면 어떤 카테고리에서 어떤 필드 구조인지
3. 정형화 양식의 실제 예시 1~2개 (입력에서 추출)
4. 사용자에게 자동화해서 보여주면 가치 있을 출력 항목 3~5개 (이름 + 근거)
5. 이 사람만의 독특한 표현/지표 (있으면)
6. 기타 관찰점

JSON 형식으로 응답:
{
  "main_activity": "...",
  "structured_format_observed": true|false,
  "structured_categories": [
    {"category": "실적공시", "fields": ["기업명","매출액","영업익","순이익","예상치대비"], "example": "..."}
  ],
  "recommended_outputs_for_users": [
    {"name": "...", "rationale": "...", "source_category": "수급분석"}
  ],
  "unique_jargon": ["업종쏠림지수", "거래대금 강도 과열권", ...],
  "notes": "..."
}
"""

FLOW_PROMPT = """\
당신은 한국 주식 텔레그램 채널 분석 전문가입니다.
한 사람이 매일 어떤 흐름으로 채널을 운영하는지 시간대별 패턴을 파악하려 합니다.
입력에는 (1) 시간대 × 카테고리 매트릭스 (2) 시간대별 대표 메시지가 주어집니다.

분석 목표: 이 사람의 "하루 운영 루틴"을 시간 흐름에 따라 서술하세요.
   - 새벽/아침/오전/점심/오후/장마감/저녁/밤  각 구간에서 무엇을 하는지
   - 어떤 카테고리가 어떤 시간대에 집중되는지
   - 시장 이벤트(장 시작/마감/미장)와의 연동
   - 그가 주로 다루는 정보의 "수집 → 가공 → 공유" 사이클이 보이면 그것도

JSON 형식 응답:
{
  "daily_routine": [
    {
      "time_band": "06:00-09:00",
      "label": "새벽/장 시작 전",
      "activities": ["미장 마감 정리", "당일 일정 공지"],
      "dominant_categories": ["시황", "단신"],
      "narrative": "한 단락 서술"
    }
  ],
  "info_cycle": "그가 정보를 어떻게 수집해서 가공하고 공유하는지 한 단락",
  "key_observations": ["관찰점1", "관찰점2", ...]
}
"""


def _load_dump(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _basic_stats(messages: list[dict]) -> dict:
    if not messages:
        return {"count": 0}

    parsed_dates: list[datetime] = []
    text_lengths: list[int] = []
    media_count = 0
    text_only_count = 0
    empty_text_count = 0
    forwards_total = 0
    views_total = 0

    hour_counter: Counter[int] = Counter()
    weekday_counter: Counter[str] = Counter()
    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]

    for m in messages:
        if m.get("date"):
            try:
                dt = datetime.fromisoformat(m["date"])
                parsed_dates.append(dt)
                hour_counter[dt.hour] += 1
                weekday_counter[weekday_names[dt.weekday()]] += 1
            except ValueError:
                pass

        text = (m.get("text") or "").strip()
        if text:
            text_lengths.append(len(text))
        else:
            empty_text_count += 1

        if m.get("media"):
            media_count += 1
        elif text:
            text_only_count += 1

        forwards_total += m.get("forwards") or 0
        views_total += m.get("views") or 0

    span_days = None
    posts_per_day = None
    if len(parsed_dates) >= 2:
        span_seconds = (max(parsed_dates) - min(parsed_dates)).total_seconds()
        span_days = round(span_seconds / 86400, 2)
        if span_days > 0:
            posts_per_day = round(len(messages) / span_days, 2)

    return {
        "count": len(messages),
        "earliest": min(parsed_dates).isoformat() if parsed_dates else None,
        "latest": max(parsed_dates).isoformat() if parsed_dates else None,
        "span_days": span_days,
        "posts_per_day": posts_per_day,
        "text_length_median": int(median(text_lengths)) if text_lengths else 0,
        "text_length_max": max(text_lengths) if text_lengths else 0,
        "with_media": media_count,
        "text_only": text_only_count,
        "empty_text": empty_text_count,
        "views_total": views_total,
        "forwards_total": forwards_total,
        "by_hour": dict(sorted(hour_counter.items())),
        "by_weekday": {d: weekday_counter.get(d, 0) for d in weekday_names},
    }


def _build_batch_input(messages: list[dict], max_chars_per_msg: int = 400) -> str:
    lines = []
    for m in messages:
        text = (m.get("text") or "").strip().replace("\n", " ")
        if not text:
            media = m.get("media") or ""
            text = f"<미디어만:{media}>" if media else "<빈본문>"
        if len(text) > max_chars_per_msg:
            text = text[:max_chars_per_msg] + "..."
        lines.append(f"[{m.get('id')}] {text}")
    return "\n".join(lines)


def _classify_batch(client, batch: list[dict]) -> dict[int, str]:
    user_input = _build_batch_input(batch)
    resp = client.chat.completions.create(
        model=GPT_MODEL,
        temperature=0,
        max_tokens=8000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CLASSIFY_PROMPT},
            {
                "role": "user",
                "content": (
                    "응답은 반드시 {\"results\": [{\"id\": ..., \"category\": ...}, ...]} 형식이어야 합니다.\n\n"
                    + user_input
                ),
            },
        ],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[!] 배치 JSON 파싱 실패: {e}\n응답 길이: {len(content)} chars")
        return {}

    if isinstance(parsed, list):
        items = parsed
    else:
        items = (
            parsed.get("results")
            or parsed.get("messages")
            or parsed.get("classifications")
            or parsed.get("items")
            or parsed.get("data")
            or []
        )

    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            mid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        cat = str(item.get("category") or "잡담").strip() or "잡담"
        result[mid] = cat
    return result


def _classify_all(messages: list[dict]) -> dict[int, str]:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        print("[!] OPENAI_API_KEY 가 없거나 openai 미설치 — 분류 건너뜀.")
        return {}

    client = OpenAI()
    classifications: dict[int, str] = {}
    total_batches = (len(messages) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        batch = messages[start : start + BATCH_SIZE]
        print(f"[*] 분류 배치 {batch_idx + 1}/{total_batches} ({len(batch)}개)...")
        result = _classify_batch(client, batch)
        classifications.update(result)
        missing = [m["id"] for m in batch if m["id"] not in result]
        if missing:
            print(f"    [!] 누락 {len(missing)}개 → 잡담으로 처리")
            for mid in missing:
                classifications[mid] = "잡담"
    return classifications


def _summarize_with_gpt(messages: list[dict], classifications: dict[int, str], category_counts: Counter) -> dict | None:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return None

    by_id = {m["id"]: m for m in messages}
    samples_per_cat = {}
    for cat in category_counts:
        ids_in_cat = [mid for mid, c in classifications.items() if c == cat and (by_id.get(mid, {}).get("text") or "").strip()]
        random.seed(42)
        random.shuffle(ids_in_cat)
        samples_per_cat[cat] = ids_in_cat[:8]

    sample_lines = []
    for cat, ids in samples_per_cat.items():
        sample_lines.append(f"\n## [{cat}] ({category_counts[cat]}개)")
        for mid in ids:
            text = (by_id[mid].get("text") or "").replace("\n", " ")[:300]
            sample_lines.append(f"  [{mid}] {text}")

    counts_summary = "\n".join(f"- {cat}: {n}개" for cat, n in category_counts.most_common())

    user_msg = f"""\
=== 카테고리 분포 ({sum(category_counts.values())}개 메시지) ===
{counts_summary}

=== 카테고리별 샘플 메시지 ===
{chr(10).join(sample_lines)}
"""

    print(f"[*] 요약 GPT 호출 ({GPT_MODEL})...")
    client = OpenAI()
    resp = client.chat.completions.create(
        model=GPT_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[!] 요약 응답 JSON 파싱 실패: {e}\n{content[:300]}")
        return None


def _print_stats(stats: dict) -> None:
    print("=" * 60)
    print("기본 통계")
    print("=" * 60)
    print(f"총 메시지: {stats['count']}개")
    print(f"기간: {stats.get('earliest', '-')} ~ {stats.get('latest', '-')}")
    print(f"기간(일): {stats.get('span_days', '-')}일,  하루 평균: {stats.get('posts_per_day', '-')}개")
    print(f"본문 길이 (중앙값/최대): {stats.get('text_length_median', 0)} / {stats.get('text_length_max', 0)}")
    print(
        f"미디어 포함: {stats.get('with_media', 0)}개 / 텍스트만: {stats.get('text_only', 0)}개 / "
        f"본문없음: {stats.get('empty_text', 0)}개"
    )
    print(f"누적 조회수: {stats.get('views_total', 0)},  누적 전달: {stats.get('forwards_total', 0)}")

    print("\n시간대별 분포(KST):")
    by_hour = stats.get("by_hour", {})
    for h in range(0, 24):
        n = by_hour.get(h, 0)
        if n > 0:
            bar = "█" * min(40, n)
            print(f"  {h:02d}시: {bar} {n}")

    print("\n요일별 분포:")
    for d, n in (stats.get("by_weekday") or {}).items():
        bar = "█" * min(40, n)
        print(f"  {d}: {bar} {n}")


def _print_classification_summary(classifications: dict[int, str], total: int) -> Counter:
    counts: Counter = Counter(classifications.values())
    print("\n" + "=" * 60)
    print("카테고리 분류 결과 (전체 메시지 대상)")
    print("=" * 60)
    print(f"분류된 메시지: {len(classifications)}/{total}")
    for cat, n in counts.most_common():
        bar = "█" * min(40, n // max(1, total // 40))
        print(f"  • {cat:8s}: {bar} {n}개 ({n / total * 100:.1f}%)")
    return counts


def _print_examples(classifications: dict[int, str], messages: list[dict], top_n: int = 5) -> None:
    by_id = {m["id"]: m for m in messages}
    print("\n" + "=" * 60)
    print(f"카테고리별 대표 메시지 (각 {top_n}개)")
    print("=" * 60)

    grouped: dict[str, list[int]] = {}
    for mid, cat in classifications.items():
        grouped.setdefault(cat, []).append(mid)

    for cat in sorted(grouped, key=lambda c: -len(grouped[c])):
        ids = grouped[cat]
        candidates = [
            mid for mid in ids
            if (by_id.get(mid, {}).get("text") or "").strip()
        ]
        random.seed(42)
        random.shuffle(candidates)
        print(f"\n  [{cat}] {len(ids)}개")
        for mid in candidates[:top_n]:
            msg = by_id[mid]
            text = (msg.get("text") or "").replace("\n", " ")[:180]
            print(f"    [{mid}] {text}")


TIME_BANDS = [
    ("새벽전(00-05)", lambda h: 0 <= h < 6),
    ("새벽(06-08)", lambda h: 6 <= h < 9),
    ("오전(09-11)", lambda h: 9 <= h < 12),
    ("점심(12-14)", lambda h: 12 <= h < 15),
    ("오후(15-17)", lambda h: 15 <= h < 18),
    ("저녁(18-20)", lambda h: 18 <= h < 21),
    ("밤(21-23)", lambda h: 21 <= h < 24),
]


def _band_for_hour(hour: int) -> str:
    for label, predicate in TIME_BANDS:
        if predicate(hour):
            return label
    return "기타"


def _build_flow_matrix(classifications: dict[int, str], messages: list[dict]) -> dict:
    by_id = {m["id"]: m for m in messages}
    matrix: dict[str, Counter] = {label: Counter() for label, _ in TIME_BANDS}
    samples: dict[str, list[tuple[int, str, str]]] = {label: [] for label, _ in TIME_BANDS}

    for mid, cat in classifications.items():
        msg = by_id.get(mid)
        if not msg or not msg.get("date"):
            continue
        try:
            dt = datetime.fromisoformat(msg["date"])
        except ValueError:
            continue
        band = _band_for_hour(dt.hour)
        matrix[band][cat] += 1
        text = (msg.get("text") or "").replace("\n", " ").strip()
        if text and len(samples[band]) < 100:
            samples[band].append((mid, cat, text[:200]))

    return {"matrix": matrix, "samples": samples}


def _print_flow_matrix(flow: dict) -> None:
    matrix = flow["matrix"]
    cats = sorted({c for cnt in matrix.values() for c in cnt.keys()}, key=lambda c: -sum(m.get(c, 0) for m in matrix.values()))
    if "잡담" in cats:
        cats = [c for c in cats if c != "잡담"] + ["잡담"]

    print("\n" + "=" * 60)
    print("시간대별 카테고리 분포 (잡담 제외 강조)")
    print("=" * 60)

    header = f"{'시간대':<14}" + "".join(f"{c:<8}" for c in cats) + " | 합계"
    print(header)
    print("-" * len(header))

    for label, _ in TIME_BANDS:
        cnt = matrix[label]
        total = sum(cnt.values())
        if total == 0:
            continue
        row = f"{label:<14}"
        for c in cats:
            n = cnt.get(c, 0)
            row += f"{n:<8}"
        row += f" | {total}"
        print(row)


def _summarize_flow_with_gpt(flow: dict) -> dict | None:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return None

    matrix = flow["matrix"]
    samples = flow["samples"]

    cats = sorted({c for cnt in matrix.values() for c in cnt.keys()})
    matrix_lines = ["시간대 | " + " | ".join(cats)]
    for label, _ in TIME_BANDS:
        cnt = matrix[label]
        if sum(cnt.values()) == 0:
            continue
        matrix_lines.append(f"{label} | " + " | ".join(str(cnt.get(c, 0)) for c in cats))

    sample_lines = []
    for label, _ in TIME_BANDS:
        smp = [s for s in samples[label] if s[1] != "잡담"][:8]
        if not smp:
            continue
        sample_lines.append(f"\n## {label}")
        for mid, cat, text in smp:
            sample_lines.append(f"  [{cat}] {text}")

    user_msg = (
        "=== 시간대 × 카테고리 매트릭스 ===\n"
        + "\n".join(matrix_lines)
        + "\n\n=== 시간대별 대표 메시지 (잡담 제외) ===\n"
        + "\n".join(sample_lines)
    )

    print("[*] 흐름 분석 GPT 호출...")
    client = OpenAI()
    resp = client.chat.completions.create(
        model=GPT_MODEL,
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": FLOW_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[!] 흐름 분석 응답 파싱 실패: {e}\n{content[:300]}")
        return None


def _print_flow_summary(flow_report: dict) -> None:
    if not flow_report:
        return
    print("\n" + "=" * 60)
    print("하루 운영 흐름")
    print("=" * 60)

    for band in flow_report.get("daily_routine", []):
        print(f"\n▌ {band.get('time_band')} — {band.get('label')}")
        cats = band.get("dominant_categories") or []
        if cats:
            print(f"  주력: {', '.join(cats)}")
        acts = band.get("activities") or []
        if acts:
            for act in acts:
                print(f"   • {act}")
        narr = band.get("narrative")
        if narr:
            print(f"  → {narr}")

    cycle = flow_report.get("info_cycle")
    if cycle:
        print("\n[정보 수집·가공·공유 사이클]")
        print(f"  {cycle}")

    obs = flow_report.get("key_observations") or []
    if obs:
        print("\n[핵심 관찰]")
        for o in obs:
            print(f"  • {o}")


def _print_summary(report: dict) -> None:
    print("\n" + "=" * 60)
    print("종합 요약")
    print("=" * 60)
    print(f"\n주력 활동: {report.get('main_activity', '-')}")

    if report.get("structured_format_observed"):
        print("\n정형화된 양식 사용: 예")
        for sc in report.get("structured_categories") or []:
            fields = ", ".join(sc.get("fields") or [])
            print(f"  • [{sc.get('category')}] 필드: {fields}")
            ex = (sc.get("example") or "").strip()
            if ex:
                print(f"      예시: {ex[:200]}")
    else:
        print("\n정형화된 양식 사용: 아니오 (자유 서술 위주)")

    print("\n자동화 시 사용자에게 보여줄 출력 후보:")
    for rec in report.get("recommended_outputs_for_users") or []:
        print(f"  • {rec.get('name', '-')}: {rec.get('rationale', '-')} (출처: {rec.get('source_category', '-')})")

    jargon = report.get("unique_jargon") or []
    if jargon:
        print(f"\n이 사람만의 표현/지표: {', '.join(jargon)}")

    notes = report.get("notes")
    if notes:
        print(f"\n관찰 메모: {notes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", default=None)
    parser.add_argument("--no-gpt", action="store_true")
    args = parser.parse_args()

    dump = _load_dump(args.in_path)
    messages = dump.get("messages", [])
    print(f"\n채널: {dump.get('channel_title', '-')} ({dump.get('count', 0)}개 메시지)")
    print(f"덤프 시각: {dump.get('fetched_at', '-')}\n")

    stats = _basic_stats(messages)
    _print_stats(stats)

    if args.no_gpt:
        return

    classifications = _classify_all(messages)
    if not classifications:
        return

    counts = _print_classification_summary(classifications, len(messages))
    _print_examples(classifications, messages, top_n=5)

    flow = _build_flow_matrix(classifications, messages)
    _print_flow_matrix(flow)

    summary = _summarize_with_gpt(messages, classifications, counts)
    if summary:
        _print_summary(summary)

    flow_report = _summarize_flow_with_gpt(flow)
    if flow_report:
        _print_flow_summary(flow_report)

    serializable_flow = {
        "matrix": {label: dict(cnt) for label, cnt in flow["matrix"].items()},
    }

    out_path = args.out_path or args.in_path.replace(".json", "_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "stats": stats,
                "category_counts": dict(counts),
                "classifications": {str(k): v for k, v in classifications.items()},
                "summary": summary,
                "flow_matrix": serializable_flow["matrix"],
                "flow_report": flow_report,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n[OK] 분석 리포트 저장: {out_path}")


if __name__ == "__main__":
    main()
