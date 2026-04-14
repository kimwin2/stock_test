from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class TelegramSignalItem:
    message_id: int
    posted_at: str
    text: str
    views: int = 0
    forwards: int = 0
    url: str = ""
    matched_stocks: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "messageId": self.message_id,
            "postedAt": self.posted_at,
            "text": self.text,
            "views": self.views,
            "forwards": self.forwards,
            "url": self.url,
            "matchedStocks": list(self.matched_stocks),
            "keywords": list(self.keywords),
            "score": self.score,
        }


@dataclass
class TelegramSignalPayload:
    channel: str
    collected_at: str
    window_minutes: int
    last_message_id: int = 0
    items: list[TelegramSignalItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "collectedAt": self.collected_at,
            "windowMinutes": self.window_minutes,
            "lastMessageId": self.last_message_id,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class TelegramState:
    channel: str
    last_message_id: int = 0
    last_collected_at: str = ""
    last_success_at: str = ""
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "lastMessageId": self.last_message_id,
            "lastCollectedAt": self.last_collected_at,
            "lastSuccessAt": self.last_success_at,
            "consecutiveFailures": self.consecutive_failures,
        }


def build_empty_signal_payload(channel: str, collected_at: str, window_minutes: int) -> dict:
    return TelegramSignalPayload(
        channel=channel,
        collected_at=collected_at,
        window_minutes=window_minutes,
        last_message_id=0,
        items=[],
    ).to_dict()


def build_default_state(channel: str) -> dict:
    return TelegramState(channel=channel).to_dict()
