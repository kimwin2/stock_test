import json
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from telegram.collector import collect_telegram_signals


payload, state = collect_telegram_signals(channel_username=os.getenv("TG_CHANNEL_USERNAME", "@faststocknews"))
print(json.dumps(payload, ensure_ascii=False, indent=2))
print(json.dumps(state, ensure_ascii=False, indent=2))
