from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RisingStockItem:
    name: str
    code: str
    market: str
    rank: int
    price: int
    change_rate: float
    diff_text: str = ""
    volume: int = 0
    volume_amount: int = 0
    bid_price: int = 0
    ask_price: int = 0
    bid_volume: int = 0
    ask_volume: int = 0
    per: str = ""
    roe: str = ""
    upper_limit: bool = False
    source_url: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "code": self.code,
            "market": self.market,
            "rank": self.rank,
            "price": self.price,
            "changeRate": self.change_rate,
            "diffText": self.diff_text,
            "volume": self.volume,
            "volumeAmount": self.volume_amount,
            "bidPrice": self.bid_price,
            "askPrice": self.ask_price,
            "bidVolume": self.bid_volume,
            "askVolume": self.ask_volume,
            "per": self.per,
            "roe": self.roe,
            "upperLimit": self.upper_limit,
            "sourceUrl": self.source_url,
        }


@dataclass
class PriceThemeCandidate:
    theme_name: str
    score: float
    matched_stocks: list[str] = field(default_factory=list)
    matched_articles: list[str] = field(default_factory=list)
    matched_telegram_messages: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "themeName": self.theme_name,
            "score": self.score,
            "matchedStocks": list(self.matched_stocks),
            "matchedArticles": list(self.matched_articles),
            "matchedTelegramMessages": list(self.matched_telegram_messages),
            "keywords": list(self.keywords),
            "reasoning": self.reasoning,
        }


@dataclass
class PriceSignalPayload:
    collected_at: str
    markets: list[str]
    movers: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "collectedAt": self.collected_at,
            "markets": list(self.markets),
            "movers": list(self.movers),
            "candidates": list(self.candidates),
        }
