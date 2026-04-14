import json
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from telegram.handler import lambda_handler


result = lambda_handler({"source": "local-manual"}, None)
print(json.dumps(result, ensure_ascii=False, indent=2))
