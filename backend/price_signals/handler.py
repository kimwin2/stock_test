from __future__ import annotations

import json

from .collector import collect_price_theme_signals
from .store import save_price_signal_payload


def lambda_handler(event, context):
    payload = collect_price_theme_signals()
    target = save_price_signal_payload(payload)
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "price theme signal collection succeeded",
                "signalStore": target,
                "candidateCount": len(payload.get("candidates", [])),
                "moverCount": len(payload.get("movers", [])),
            },
            ensure_ascii=False,
        ),
    }
