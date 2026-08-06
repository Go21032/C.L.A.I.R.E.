"""
smoke_test_pipe.py
--------------------
openwebui_pipe/support_ai_auto_pipe.py の`Pipe`クラスを、Open WebUIを介さずに
直接呼び出すスモークテスト。実際のOllama・実際のFAST/DEEP/CODE/CLARIFYモデルを使う。

目的: Open WebUIがまだこのPC上に導入されていないため、Open WebUI経由での動作確認は
できない。その代わり、Open WebUIが呼び出すのと同じ形の`body`辞書を組み立てて
`Pipe().pipe(body)`を直接呼ぶことで、「Open WebUIから呼ばれた場合に相当する経路」を
実機で最後まで通し、Pipeの実装に構文・importエラーの類がないか、実際に会話継続の
セッション保持が機能するかを確認する。

実行方法:
    python smoke_test_pipe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "openwebui_pipe"))

from support_ai_auto_pipe import Pipe  # noqa: E402


def make_body(text: str, chat_id: str) -> dict:
    return {"chat_id": chat_id, "messages": [{"role": "user", "content": text}]}


def main() -> None:
    pipe = Pipe()

    turns = [
        ("chat-smoke-A", "3ヶ月後の資格試験に向けて、平日2時間・休日4時間の学習計画を立てて"),
        ("chat-smoke-A", "土日はもう少し軽めにしてほしい"),
        ("chat-smoke-A", "ついでにこの前渡したスクリプトのバグも直して実装しといて"),
        ("chat-smoke-B", "今日の東京の天気を教えて"),
        ("chat-smoke-B", "あれ、どうすればいい?"),
    ]

    for chat_id, text in turns:
        print("=" * 70)
        print(f"[chat_id={chat_id}] 質問: {text}")
        result = pipe.pipe(make_body(text, chat_id))
        print(result[:300])
        print()


if __name__ == "__main__":
    main()
