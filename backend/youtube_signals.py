from __future__ import annotations

import re
from dataclasses import dataclass, asdict

try:
    import yt_dlp
except ImportError:  # Optional in local tests
    yt_dlp = None


CHANNEL_VIDEOS_URL = "https://www.youtube.com/@%EC%8B%AC%ED%94%8C%EA%B4%80%EC%8B%AC%EC%A2%85%EB%AA%A9TV/videos"
TITLE_PREFIXES = ("내일 관심테마!", "당일 관심테마!")


@dataclass
class YoutubeThemeSignal:
    signal_type: str
    title: str
    video_url: str
    upload_date: str
    sectors: list[str]
    stocks: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_playlist_entries(limit: int = 20) -> list[dict]:
    if yt_dlp is None:
        return []
    options = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": limit,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(CHANNEL_VIDEOS_URL, download=False)
    return info.get("entries", [])


def _split_sectors(title: str) -> list[str]:
    section = title.split("/", 1)[0]
    section = section.replace("내일 관심테마!", "").replace("당일 관심테마!", "").strip()
    return [part.strip() for part in section.split(",") if part.strip()]


def _extract_known_stocks(text: str, known_stocks: list[str]) -> list[str]:
    cleaned = re.sub(r"\s+", "", text)
    spans: list[tuple[int, int, str]] = []

    for stock in sorted(set(known_stocks), key=len, reverse=True):
        if not stock or len(stock) < 3:
            continue

        start = cleaned.find(stock)
        while start != -1:
            end = start + len(stock)
            if not any(not (end <= saved_start or start >= saved_end) for saved_start, saved_end, _ in spans):
                spans.append((start, end, stock))
            start = cleaned.find(stock, start + 1)

    ordered = []
    seen = set()
    for _, _, stock in sorted(spans, key=lambda item: item[0]):
        if stock not in seen:
            ordered.append(stock)
            seen.add(stock)

    return ordered


def _split_stocks(title: str, known_stocks: list[str]) -> list[str]:
    if "/" not in title:
        return []

    stock_text = title.split("/", 1)[1]
    stock_text = stock_text.replace(" ", "")
    stock_text = stock_text.replace(".", ",")
    stock_text = stock_text.replace("，", ",")
    stock_text = stock_text.replace("·", ",")

    known_stock_set = set(known_stocks)
    parsed = [
        part.strip() for part in stock_text.split(",")
        if part.strip() and part.strip() in known_stock_set
    ]
    known_matches = _extract_known_stocks(stock_text, known_stocks)

    combined = []
    for stock in parsed + known_matches:
        if stock not in combined:
            combined.append(stock)

    return combined


def fetch_latest_youtube_theme_signals(known_stocks: list[str], limit: int = 20) -> list[dict]:
    if yt_dlp is None:
        return []
    entries = _extract_playlist_entries(limit=limit)
    latest = {}

    for entry in entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue

        for prefix in TITLE_PREFIXES:
            if not title.startswith(prefix) or prefix in latest:
                continue

            video_id = entry.get("id")
            if not video_id:
                continue

            latest[prefix] = YoutubeThemeSignal(
                signal_type="내일" if prefix.startswith("내일") else "당일",
                title=title,
                video_url=entry.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                upload_date="",
                sectors=_split_sectors(title),
                stocks=_split_stocks(title, known_stocks),
            ).to_dict()

        if len(latest) == len(TITLE_PREFIXES):
            break

    return [latest[prefix] for prefix in TITLE_PREFIXES if prefix in latest]
