import json
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from price_signals.collector import collect_price_theme_signals
from price_signals.store import save_price_signal_payload


payload = collect_price_theme_signals()
save_path = save_price_signal_payload(payload)
print(json.dumps(payload, ensure_ascii=False, indent=2))
print(f"\nSaved to: {save_path}")
