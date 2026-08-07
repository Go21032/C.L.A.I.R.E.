# -*- coding: utf-8 -*-
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "openwebui_pipe"))

from support_ai_auto_pipe import Pipe

def make_body(text, chat_id):
    return {"chat_id": chat_id, "messages": [{"role": "user", "content": text}]}

pipe = Pipe()
questions = [
    ("chat-verify5-1", "チャンク分割の上限値は何字に決めた?"),
    ("chat-verify5-2", "Ruri v2で意味が合致したときのdistanceはいくつだった?"),
    ("chat-verify5-3", "マツコ問題の最終的な原因は何だった?"),
    ("chat-verify5-4", "CODE_TRIGGERSを語幹で区切るようにした理由は?"),
]

for chat_id, text in questions:
    print("="*70)
    print(f"質問: {text}")
    result = pipe.pipe(make_body(text, chat_id))
    print(result[:600])
    print()
