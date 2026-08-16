"""stock_chat 배포 번들 → 대조용 레퍼런스 파일.

레퍼런스 원문(`data/daily/*.json`)은 평문이라 stock_chat 레포에 커밋되지 않는다.
그래서 지금까지 대조 잡은 stock_chat 이 없는 곳(GitHub Actions)에서 항상
'레퍼런스 없음' 으로 끝났다.

그런데 stock_chat 은 **이미 매시간 그 데이터를 공개 주소로 내보내고 있다** —
GitHub Pages 의 `data/core.enc` 다. AES-256-GCM 으로 암호화돼 있어서 레포가
public 이어도 평문 노출이 없고, 공유 암호(`SHARE_PASSPHRASE`)를 아는 쪽만 읽는다.
그 파일 안의 `days[]` 가 우리가 필요한 전부다(`tickers`/`sectors`/`cash`).

즉 다리를 놓는 데 stock_chat 쪽 변경이 하나도 필요 없다. 이 레포에 공유 암호
시크릿 하나만 넣으면 된다. (S3 로 따로 실어 나르던 기존 계획은 stock_chat 에
워크플로와 AWS 자격증명을 추가로 심어야 했고, 그래서 아직 안 붙어 있었다.)

## 경계

- 번들에는 채널 원문·요약 문장이 다 들어있지만 **여기서 꺼내는 건 대조에 쓰는
  네 가지(`stance`/`cash.kr`/`sectors`/`tickers`)뿐**이다. 서술 문장은 디스크에
  쓰지 않는다. `compare.py`·`daily_check.py` 의 경계와 같다.
- `core.secrets` 에는 stock_chat 의 Gemini 키·GitHub 토큰이 들어있다.
  **절대 저장하거나 출력하지 않는다.** 아래 `_slim()` 이 허용 키만 통과시킨다.
- 이 모듈은 Lambda 에 배포되지 않는다. 제품 산출물에 안 들어간다.

쓰는 법:
    export SHARE_PASSPHRASE=...
    cd backend && python -m benchmark.stock_chat_bundle --out /tmp/ref
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import urllib.request
from base64 import b64decode
from pathlib import Path

# stock_chat 의 GitHub Pages. `web/` 이 통째로 올라가므로 번들은 `data/` 아래.
DEFAULT_BUNDLE_URL = "https://kimwin2.github.io/stock_chat"

# 번들 포맷 (stock_chat/pipeline/bundle.py 와 맞춰야 한다).
# 저쪽이 포맷을 바꾸면 조용히 쓰레기를 만들지 말고 여기서 멈춘다.
EXPECT_KDF = "PBKDF2"
EXPECT_HASH = "SHA-256"
EXPECT_CIPHER = "AES-GCM"
IV_BYTES = 12

# 하루치에서 꺼내 쓰는 키 — `reference.load_reference` 가 읽는 것과 정확히 같다.
# 화이트리스트로 두는 게 핵심이다. 블랙리스트로 두면 저쪽이 필드를 추가할 때마다
# 채널 원문이 하나씩 새어 나온다.
KEEP_KEYS = ("date", "stance", "cash", "sectors", "tickers", "message_count")


class BundleError(RuntimeError):
    pass


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "stock_test-benchmark"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256. stock_chat 의 `derive_key` 와 같은 값을 낸다."""
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, 32)


def decrypt(blob: bytes, key: bytes) -> dict:
    """iv(12) || ciphertext+tag → gzip(JSON) → dict."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - 환경 문제
        raise BundleError(
            "cryptography 패키지가 필요하다 (`pip install cryptography`)."
        ) from exc

    if len(blob) <= IV_BYTES:
        raise BundleError(f"번들이 너무 짧다 ({len(blob)} bytes)")
    try:
        plain = AESGCM(key).decrypt(blob[:IV_BYTES], blob[IV_BYTES:], None)
    except Exception as exc:
        raise BundleError(
            "복호화 실패 — SHARE_PASSPHRASE 가 stock_chat 의 값과 다르거나 "
            f"번들이 손상됐다 ({type(exc).__name__})"
        ) from exc
    return json.loads(gzip.decompress(plain).decode("utf-8"))


def _slim(day: dict) -> dict:
    """대조에 쓰는 키만. 서술 문장·원문·이미지는 통과시키지 않는다."""
    out = {k: day[k] for k in KEEP_KEYS if k in day}
    # cash 는 kr 하위만 쓴다 (`load_reference` 가 cash.kr.start/end 만 읽는다).
    cash = out.get("cash") or {}
    kr = cash.get("kr") if isinstance(cash, dict) else None
    out["cash"] = {"kr": {k: (kr or {}).get(k) for k in ("start", "end")}}
    return out


def fetch(dest: Path, base_url: str | None = None, passphrase: str | None = None,
          days: int = 90, quiet: bool = False) -> dict:
    """번들을 받아 `dest/YYYY-MM-DD.json` 으로 펼친다.

    반환값의 `updatedAt` 은 **번들이 만들어진 시각**이다. 데이터 나이와 다르다 —
    수집기가 오늘 죽어도 어제 요약이 남아 있으면 데이터 나이는 1일이라 조용하다
    (2026-08-10 에 실제로 그랬다). 번들 시각은 그 침묵을 바로 깬다.
    """
    base = (base_url or os.environ.get("STOCK_CHAT_BUNDLE_URL") or DEFAULT_BUNDLE_URL).rstrip("/")
    secret = passphrase or os.environ.get("SHARE_PASSPHRASE") or ""
    if not secret:
        raise BundleError("SHARE_PASSPHRASE 가 없다. stock_chat 과 같은 값을 넣을 것.")

    manifest = json.loads(_get(f"{base}/data/manifest.json", timeout=30))
    kdf = manifest.get("kdf") or {}
    if kdf.get("name") != EXPECT_KDF or kdf.get("hash") != EXPECT_HASH \
            or manifest.get("cipher") != EXPECT_CIPHER:
        raise BundleError(
            f"번들 포맷이 바뀌었다 (kdf={kdf.get('name')}/{kdf.get('hash')}, "
            f"cipher={manifest.get('cipher')}). stock_chat/pipeline/bundle.py 를 볼 것."
        )
    if "core.enc" not in (manifest.get("files") or []):
        raise BundleError("번들에 core.enc 가 없다.")

    key = derive_key(secret, b64decode(kdf["salt"]), int(kdf["iterations"]))
    core = decrypt(_get(f"{base}/data/core.enc"), key)

    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    pending = 0
    for day in sorted(core.get("days") or [], key=lambda d: d.get("date") or "")[-days:]:
        date_str = day.get("date")
        if not date_str:
            continue
        # 요약이 아직 없는 날은 껍데기만 들어있다(tickers 가 빈 배열).
        # 그대로 쓰면 "관심종목 0개 · 적중 0%" 라는 멀쩡해 보이는 거짓 리포트가 된다.
        if day.get("pending"):
            pending += 1
            continue
        (dest / f"{date_str}.json").write_text(
            json.dumps(_slim(day), ensure_ascii=False), encoding="utf-8")
        written.append(date_str)

    meta = {
        "updatedAt": core.get("updated_at"),
        "days": len(written),
        "pendingSkipped": pending,
        "first": written[0] if written else None,
        "last": written[-1] if written else None,
        "source": base,
    }
    # 파일명이 날짜 패턴이 아니라 `available_dates()` 가 알아서 건너뛴다.
    (dest / "_bundle.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    if not quiet:
        print(f"[번들] {len(written)}일 ({meta['first']} ~ {meta['last']}) "
              f"· 생성 {meta['updatedAt']}"
              + (f" · 요약 대기 {pending}일 제외" if pending else ""), file=sys.stderr)
    return meta


def bundle_meta(dest: Path) -> dict | None:
    path = dest / "_bundle.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="stock_chat 배포 번들 → 레퍼런스 파일")
    ap.add_argument("--out", required=True, help="펼칠 디렉터리")
    ap.add_argument("--url", default=None, help=f"번들 베이스 (기본 {DEFAULT_BUNDLE_URL})")
    ap.add_argument("--days", type=int, default=90, help="최근 며칠분 (기본 90)")
    args = ap.parse_args()
    try:
        meta = fetch(Path(args.out), base_url=args.url, days=args.days)
    except BundleError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
